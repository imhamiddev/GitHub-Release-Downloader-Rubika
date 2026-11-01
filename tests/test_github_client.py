import json

import pytest

from errors import NotFound, RateLimited, UnsafeURL
from github.client import GitHubClient, validate_download_url
from github.transport import HttpResponse
from storage.cache import TTLCache
from tests.conftest import FakeStream, no_sleep


def jr(data, status=200, headers=None):
    return HttpResponse(status, {k.lower(): v for k, v in (headers or {}).items()}, json.dumps(data).encode())


def make(transport, token=None, **kw):
    return GitHubClient(transport, token, TTLCache(300), sleep=no_sleep, wall_clock=lambda: 1000.0, **kw)


@pytest.mark.anyio
async def test_token_header_only_when_configured(transport):
    transport.add_response(jr({"a": 1}))
    await make(transport).get_json("/repos/a/b")
    assert "Authorization" not in transport.requests[0][2]
    transport.add_response(jr({"a": 1}))
    await make(transport, "tok").get_json("/repos/a/b")
    assert transport.requests[1][2]["Authorization"] == "Bearer tok"


@pytest.mark.anyio
async def test_etag_revalidation_uses_304(transport):
    c = make(transport)
    c.cache.ttl = 0  # always stale -> forces revalidation
    transport.add_response(jr({"v": 1}, headers={"ETag": '"x"'}))
    assert await c.get_json("/repos/a/b") == {"v": 1}
    transport.add_response(HttpResponse(304, {}, b""))
    assert await c.get_json("/repos/a/b") == {"v": 1}
    assert transport.requests[1][2]["If-None-Match"] == '"x"'


@pytest.mark.anyio
async def test_fresh_cache_skips_network(transport):
    c = make(transport)
    transport.add_response(jr({"v": 1}))
    await c.get_json("/repos/a/b")
    await c.get_json("/repos/a/b")
    assert len(transport.requests) == 1


@pytest.mark.anyio
async def test_pagination_flag_from_link_header(transport):
    transport.add_response(jr([1], headers={"Link": '<https://api.github.com/x?page=2>; rel="next"'}))
    page = await make(transport).get_json_page("/repos/a/b/releases", {"page": 1})
    assert page.has_next is True


@pytest.mark.anyio
async def test_404_maps_to_notfound(transport):
    transport.add_response(jr({}, 404))
    with pytest.raises(NotFound):
        await make(transport).get_json("/repos/a/b")


@pytest.mark.anyio
async def test_rate_limit_long_wait_raises_and_hides_token(transport):
    transport.add_response(jr({}, 403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999"}))
    with pytest.raises(RateLimited) as ei:
        await make(transport, "secret-token").get_json("/repos/a/b")
    assert "secret-token" not in str(ei.value)


@pytest.mark.anyio
async def test_short_rate_limit_wait_retries(transport):
    transport.add_response(jr({}, 403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1005"}))
    transport.add_response(jr({"ok": True}))
    assert await make(transport).get_json("/repos/a/b") == {"ok": True}


@pytest.mark.anyio
async def test_5xx_retried(transport):
    transport.add_response(jr({}, 502))
    transport.add_response(jr({"ok": 1}))
    assert await make(transport).get_json("/repos/a/b") == {"ok": 1}


@pytest.mark.anyio
async def test_non_repos_path_rejected(transport):
    with pytest.raises(UnsafeURL):
        await make(transport).get_json("/user")


@pytest.mark.parametrize(
    "url",
    [
        "http://api.github.com/x",
        "https://evil.com/x",
        "https://github.com.evil.com/x",
        "https://127.0.0.1/x",
        "https://user@github.com/x",
        "https://api.github.com:8443/x",
        "file:///etc/passwd",
    ],
)
def test_unsafe_urls_rejected(url):
    with pytest.raises(UnsafeURL):
        validate_download_url(url)


@pytest.mark.parametrize(
    "url",
    ["https://api.github.com/repos/a/b/releases/assets/1", "https://objects.githubusercontent.com/x",
     "https://release-assets.githubusercontent.com/x", "https://codeload.github.com/a/b/zip/refs/tags/v1"],
)
def test_github_urls_allowed(url):
    assert validate_download_url(url) == url


@pytest.mark.anyio
async def test_download_redirect_drops_auth_and_blocks_foreign_hosts(transport):
    c = make(transport, "tok")
    transport.add_stream(FakeStream(302, {"location": "https://objects.githubusercontent.com/file"}))
    transport.add_stream(FakeStream(200, {"content-length": "1"}, [b"x"]))
    async with c.open_download("https://api.github.com/repos/a/b/releases/assets/1") as r:
        assert r.status == 200
    assert transport.streams[0][1]["Authorization"] == "Bearer tok"
    assert "Authorization" not in transport.streams[1][1]

    transport.add_stream(FakeStream(302, {"location": "https://evil.example/steal"}))
    with pytest.raises(UnsafeURL):
        async with c.open_download("https://api.github.com/repos/a/b/releases/assets/1"):
            pass
