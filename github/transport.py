"""HTTP transport abstraction. Production uses httpx; tests plug in a fake.

Redirects are never followed automatically: the GitHub client follows them manually so every
hop can be checked against the host allow-list (SSRF protection) and the Authorization header
is never forwarded to a storage host.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Dict, Mapping, Optional, Protocol

from errors import TransportError


@dataclass
class HttpResponse:
    status: int
    headers: Dict[str, str]  # lower-cased keys
    body: bytes = b""


class StreamResponse(Protocol):
    status: int
    headers: Dict[str, str]

    def aiter_bytes(self) -> AsyncIterator[bytes]: ...


class Transport(Protocol):
    async def request(
        self, method: str, url: str, headers: Mapping[str, str], params: Optional[Mapping[str, object]] = None
    ) -> HttpResponse: ...

    def open_stream(self, url: str, headers: Mapping[str, str]): ...  # async context manager -> StreamResponse

    async def aclose(self) -> None: ...


class _HttpxStream:
    def __init__(self, response, httpx_module):
        self._r = response
        self._httpx = httpx_module
        self.status = response.status_code
        self.headers = {k.lower(): v for k, v in response.headers.items()}

    async def aiter_bytes(self):
        try:
            async for chunk in self._r.aiter_bytes(1024 * 1024):
                yield chunk
        except self._httpx.HTTPError as exc:
            raise TransportError(f"network error while downloading: {type(exc).__name__}") from None


class HttpxTransport:
    def __init__(self, timeout: float = 30.0, read_timeout: float = 60.0):
        import httpx  # imported lazily so unit tests do not need it

        self._httpx = httpx
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, read=read_timeout),
            follow_redirects=False,
        )

    async def request(self, method, url, headers, params=None) -> HttpResponse:
        try:
            r = await self._client.request(method, url, headers=dict(headers), params=params)
        except self._httpx.HTTPError as exc:
            raise TransportError(f"network error: {type(exc).__name__}") from None
        return HttpResponse(r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.content)

    @asynccontextmanager
    async def open_stream(self, url, headers):
        try:
            async with self._client.stream("GET", url, headers=dict(headers)) as r:
                yield _HttpxStream(r, self._httpx)
        except self._httpx.HTTPError as exc:
            raise TransportError(f"network error: {type(exc).__name__}") from None

    async def aclose(self) -> None:
        await self._client.aclose()
