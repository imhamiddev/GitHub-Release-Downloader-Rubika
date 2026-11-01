"""GitHub REST API client: ETag caching, pagination, rate limits, retries and safe downloads."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping, Optional
from urllib.parse import urlencode, urljoin, urlparse

from errors import GitHubError, NotFound, RateLimited, TransportError, UnsafeURL
from github.transport import Transport
from storage.cache import TTLCache

log = logging.getLogger(__name__)

API_BASE = "https://api.github.com"
API_HOST = "api.github.com"
_EXACT_HOSTS = {"api.github.com", "github.com", "codeload.github.com", "objects.githubusercontent.com"}
_SUFFIX_HOSTS = (".githubusercontent.com",)  # e.g. release-assets.githubusercontent.com
_REDIRECTS = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 5


def validate_download_url(url: str) -> str:
    """Only https URLs on GitHub-owned hosts are ever requested."""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if p.scheme != "https" or p.username or p.password or p.port not in (None, 443):
        raise UnsafeURL("refusing a non-HTTPS or malformed URL")
    if host not in _EXACT_HOSTS and not host.endswith(_SUFFIX_HOSTS):
        raise UnsafeURL(f"refusing to contact non-GitHub host: {host or '?'}")
    return url


@dataclass
class Page:
    data: Any
    has_next: bool


@dataclass
class RateInfo:
    limit: Optional[int] = None
    remaining: Optional[int] = None
    reset: Optional[float] = None


_NEXT_RE = re.compile(r'<[^>]+>\s*;\s*rel="next"')


class GitHubClient:
    def __init__(
        self,
        transport: Transport,
        token: Optional[str] = None,
        cache: Optional[TTLCache] = None,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        max_rate_wait: float = 30.0,
        sleep: Callable[[float], Any] = asyncio.sleep,
        wall_clock: Callable[[], float] = time.time,
    ):
        self.transport = transport
        self._token = token
        self.cache = cache or TTLCache(300)
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.max_rate_wait = max_rate_wait
        self._sleep = sleep
        self._wall = wall_clock
        self.rate = RateInfo()

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    # ---- headers ------------------------------------------------------------
    def _headers(self, *, accept: str = "application/vnd.github+json", auth: bool = True) -> Dict[str, str]:
        h = {
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-bridge-userbot",
        }
        if auth and self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _note_rate(self, headers: Mapping[str, str]) -> None:
        def num(name):
            try:
                return float(headers[name])
            except (KeyError, ValueError):
                return None

        limit, remaining, reset = num("x-ratelimit-limit"), num("x-ratelimit-remaining"), num("x-ratelimit-reset")
        if limit is not None:
            self.rate = RateInfo(int(limit), None if remaining is None else int(remaining), reset)

    def _rate_wait(self, status: int, headers: Mapping[str, str]) -> Optional[tuple[float, Optional[float]]]:
        """Return (seconds_to_wait, reset_epoch) when the response is a rate-limit response."""
        retry_after = headers.get("retry-after")
        if retry_after:
            try:
                secs = float(retry_after)
                return secs, self._wall() + secs
            except ValueError:
                pass
        if headers.get("x-ratelimit-remaining") == "0":
            try:
                reset = float(headers.get("x-ratelimit-reset", ""))
                return max(0.0, reset - self._wall()), reset
            except ValueError:
                return 60.0, None
        if status == 429:
            return 60.0, None
        return None

    def _rate_error(self, reset: Optional[float]) -> RateLimited:
        when = ""
        if reset:
            when = " Resets at " + datetime.fromtimestamp(reset, timezone.utc).strftime("%H:%M UTC") + "."
        hint = "" if self._token else " Set GITHUB_TOKEN to raise the limit."
        return RateLimited(f"GitHub API rate limit reached.{when}{hint}", reset)

    def _delay(self, attempt: int) -> float:
        return min(self.backoff_base * (2**attempt), 30.0)

    # ---- JSON API -----------------------------------------------------------
    async def get_json_page(
        self, path: str, params: Optional[Mapping[str, object]] = None, use_cache: bool = True
    ) -> Page:
        if not path.startswith("/repos/"):
            raise UnsafeURL("only /repos/... API paths are allowed")
        key = path + "?" + urlencode(sorted((params or {}).items()))
        entry = self.cache.get(key) if use_cache else None
        if entry and self.cache.is_fresh(entry):
            return Page(entry.value, entry.extra.get("has_next", False))

        headers = self._headers()
        if entry and entry.etag:
            headers["If-None-Match"] = entry.etag
        resp = await self._request(API_BASE + path, headers, params)
        if resp.status == 304 and entry:
            self.cache.touch(key)
            return Page(entry.value, entry.extra.get("has_next", False))
        try:
            data = json.loads(resp.body)
        except ValueError:
            raise GitHubError("GitHub returned an unreadable response") from None
        has_next = bool(_NEXT_RE.search(resp.headers.get("link", "")))
        if use_cache:
            self.cache.put(key, data, resp.headers.get("etag"), {"has_next": has_next})
        return Page(data, has_next)

    async def get_json(self, path: str, params: Optional[Mapping[str, object]] = None, use_cache: bool = True):
        return (await self.get_json_page(path, params, use_cache)).data

    async def _request(self, url: str, headers: Mapping[str, str], params):
        for attempt in range(self.max_retries + 1):
            try:
                resp = await self.transport.request("GET", url, headers, params)
            except TransportError:
                if attempt >= self.max_retries:
                    raise
                await self._sleep(self._delay(attempt))
                continue
            self._note_rate(resp.headers)
            status = resp.status
            if status in (200, 304):
                return resp
            if status == 404:
                raise NotFound("Not found on GitHub (check the repository, tag or asset).")
            if status == 401:
                raise GitHubError("GitHub rejected the configured token (401). Check GITHUB_TOKEN.")
            if status in (403, 429):
                wait = self._rate_wait(status, resp.headers)
                if wait is None:
                    raise GitHubError("GitHub denied access to this resource (403).")
                secs, reset = wait
                if secs <= self.max_rate_wait and attempt < self.max_retries:
                    await self._sleep(secs + 1)
                    continue
                raise self._rate_error(reset)
            if status >= 500 and attempt < self.max_retries:
                await self._sleep(self._delay(attempt))
                continue
            raise GitHubError(f"GitHub API error (HTTP {status}).")
        raise GitHubError("GitHub request failed")  # pragma: no cover

    # ---- downloads ----------------------------------------------------------
    @asynccontextmanager
    async def open_download(self, url: str, range_start: int = 0):
        """Open a streaming download, following redirects manually and safely.

        The Authorization header is only sent to api.github.com, never to the storage host the
        API redirects to. Yields the final stream response (status 200 or 206).
        """
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            validate_download_url(current)
            host = (urlparse(current).hostname or "").lower()
            headers = self._headers(accept="application/octet-stream", auth=(host == API_HOST))
            if range_start > 0:
                headers["Range"] = f"bytes={range_start}-"
            async with self.transport.open_stream(current, headers) as resp:
                status = resp.status
                if status in _REDIRECTS:
                    location = resp.headers.get("location")
                    if not location:
                        raise GitHubError("GitHub redirect without a location")
                    current = urljoin(current, location)
                    continue
                if status == 404:
                    raise NotFound("Asset not found on GitHub.")
                if status in (401,):
                    raise GitHubError("GitHub rejected the configured token (401).")
                if status in (403, 429):
                    self._note_rate(resp.headers)
                    wait = self._rate_wait(status, resp.headers)
                    if wait:
                        raise self._rate_error(wait[1])
                    raise GitHubError("GitHub denied the download (403).")
                if status in (200, 206):
                    yield resp
                    return
                if status == 416:  # requested range not satisfiable -> caller restarts from zero
                    raise GitHubError("range-not-satisfiable")
                raise GitHubError(f"GitHub download failed (HTTP {status}).")
        raise GitHubError("too many redirects while downloading")
