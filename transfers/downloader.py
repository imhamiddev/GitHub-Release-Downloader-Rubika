"""Streaming downloader with resume, retries (exponential backoff), size limits and cancellation."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional, Union

from errors import DownloadError, GitHubError, JobCancelled, NotFound, RateLimited, TooLarge, TransportError, UnsafeURL
from github.client import GitHubClient

log = logging.getLogger(__name__)

ProgressCb = Callable[[int, Optional[int]], Union[None, Awaitable[None]]]


@dataclass
class DownloadResult:
    path: Path
    size: int
    sha256: str


class Downloader:
    def __init__(
        self,
        client: GitHubClient,
        max_retries: int = 5,
        backoff_base: float = 2.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.client = client
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._sleep = sleep

    async def download(
        self,
        url: str,
        dest: Path,
        *,
        expected_size: Optional[int],
        max_bytes: int,
        progress: Optional[ProgressCb] = None,
        cancel: Optional[asyncio.Event] = None,
    ) -> DownloadResult:
        """Download ``url`` to ``dest`` (via ``dest.part``) and return its size and SHA-256.

        The body is streamed to disk in chunks, never held in memory. A partial file left by a
        previous attempt (or a previous run) is resumed with a Range request.
        """
        if expected_size is not None and expected_size > max_bytes:
            raise TooLarge(f"file is {expected_size // (1024 * 1024)} MB, above the {max_bytes // (1024 * 1024)} MB limit")
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")

        attempt = 0
        while True:
            try:
                return await self._attempt(url, dest, part, expected_size, max_bytes, progress, cancel)
            except (JobCancelled, TooLarge, NotFound, RateLimited, UnsafeURL):
                raise
            except (TransportError, GitHubError, OSError) as exc:
                if isinstance(exc, GitHubError) and "range-not-satisfiable" in str(exc):
                    part.unlink(missing_ok=True)  # restart from zero
                    continue
                if isinstance(exc, GitHubError) and not _retryable(exc):
                    raise
                attempt += 1
                if attempt > self.max_retries:
                    raise DownloadError(f"download failed after {self.max_retries} retries: {exc}") from None
                delay = min(self.backoff_base * (2 ** (attempt - 1)), 60.0)
                log.warning("download attempt %s failed (%s); retrying in %.0fs", attempt, type(exc).__name__, delay)
                await self._sleep(delay)

    async def _attempt(self, url, dest, part, expected_size, max_bytes, progress, cancel) -> DownloadResult:
        offset = part.stat().st_size if part.exists() else 0
        if expected_size is not None and offset > expected_size:
            part.unlink()
            offset = 0
        hasher = hashlib.sha256()
        if offset:
            await asyncio.to_thread(_hash_into, hasher, part)

        async with self.client.open_download(url, range_start=offset) as resp:
            if offset and resp.status != 206:
                # Server ignored the Range header: start over from the beginning.
                offset, hasher = 0, hashlib.sha256()
            total = _total_size(resp, offset, expected_size)
            if total is not None and total > max_bytes:
                raise TooLarge(f"file is {total // (1024 * 1024)} MB, above the {max_bytes // (1024 * 1024)} MB limit")
            done = offset
            mode = "ab" if offset else "wb"
            with open(part, mode) as fh:
                async for chunk in resp.aiter_bytes():
                    if cancel is not None and cancel.is_set():
                        raise JobCancelled("cancelled")
                    done += len(chunk)
                    if done > max_bytes:
                        raise TooLarge(f"download exceeded the {max_bytes // (1024 * 1024)} MB limit")
                    hasher.update(chunk)
                    await asyncio.to_thread(fh.write, chunk)
                    if progress is not None:
                        r = progress(done, total)
                        if asyncio.iscoroutine(r):
                            await r
                fh.flush()
                os.fsync(fh.fileno())

        size = part.stat().st_size
        if total is not None and size != total:
            raise TransportError(f"connection closed early ({size} of {total} bytes)")
        os.replace(part, dest)
        return DownloadResult(dest, size, hasher.hexdigest())


def _hash_into(hasher, path: Path) -> None:
    with open(path, "rb") as fh:
        while True:
            data = fh.read(1024 * 1024)
            if not data:
                break
            hasher.update(data)


def _total_size(resp, offset: int, expected: Optional[int]) -> Optional[int]:
    headers = resp.headers
    if resp.status == 206:
        cr = headers.get("content-range", "")
        if "/" in cr and cr.rsplit("/", 1)[1].isdigit():
            return int(cr.rsplit("/", 1)[1])
    cl = headers.get("content-length")
    if cl and cl.isdigit() and not headers.get("content-encoding"):
        return int(cl) + (offset if resp.status == 206 else 0)
    return expected


def _retryable(exc: GitHubError) -> bool:
    msg = str(exc)
    return "HTTP 5" in msg or "failed (HTTP 429" in msg
