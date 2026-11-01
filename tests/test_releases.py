import json

import pytest

from errors import InvalidInput, NotFound
from github.assets import build_listing, count_extras, is_extra_asset, parse_digest, resolve_selectors, Asset
from github.client import GitHubClient
from github.releases import ReleaseService
from github.repository import RepoRef
from github.transport import HttpResponse
from storage.cache import TTLCache
from tests.conftest import no_sleep

REF = RepoRef("o", "r")


def rel(i, pre=False, assets=0):
    return {"id": i, "tag_name": f"v{i}", "name": f"Release {i}", "prerelease": pre, "draft": False,
            "published_at": "2025-01-02T03:04:05Z", "author": {"login": "dev"}, "body": "notes",
            "assets": [{"id": 100 * i + a, "name": f"f{a}.apk", "size": 10, "download_count": 1,
                        "content_type": "application/x", "state": "uploaded",
                        "digest": "sha256:" + "a" * 64} for a in range(assets)]}


def resp(data, link=None, status=200):
    h = {"link": link} if link else {}
    return HttpResponse(status, h, json.dumps(data).encode())


NEXT = '<https://api.github.com/x>; rel="next"'


def svc(transport):
    return ReleaseService(GitHubClient(transport, None, TTLCache(300), sleep=no_sleep))


@pytest.mark.anyio
async def test_pagination_across_github_pages(transport):
    transport.add_response(resp([rel(i) for i in range(10, 7, -1)], link=NEXT))  # 3 items, more
    transport.add_response(resp([rel(i) for i in range(7, 4, -1)]))  # 3 items, end
    transport.add_response(resp({"id": 10}))  # latest
    p = await svc(transport).get_page(REF, 1, 5)
    assert [r.tag for r in p.items] == ["v10", "v9", "v8", "v7", "v6"]
    assert p.has_next is True
    assert p.items[0].is_latest and not p.items[1].is_latest


@pytest.mark.anyio
async def test_last_page_has_no_next(transport):
    transport.add_response(resp([rel(2), rel(1)]))
    transport.add_response(resp({"id": 2}))
    p = await svc(transport).get_page(REF, 1, 5)
    assert len(p.items) == 2 and p.has_next is False


@pytest.mark.anyio
async def test_stable_filter_excludes_prereleases(transport):
    transport.add_response(resp([rel(3, pre=True), rel(2), rel(1)]))
    transport.add_response(resp({"id": 2}))
    p = await svc(transport).get_page(REF, 1, 5, include_prereleases=False)
    assert [r.tag for r in p.items] == ["v2", "v1"]


@pytest.mark.anyio
async def test_latest_and_missing(transport):
    transport.add_response(resp(rel(5)))
    r = await svc(transport).get_latest(REF)
    assert r.tag == "v5" and r.is_latest
    transport.add_response(resp({}, status=404))
    with pytest.raises(NotFound):
        await svc(transport).get_latest(RepoRef("o", "empty"))


@pytest.mark.anyio
async def test_get_by_tag_validates_tag(transport):
    with pytest.raises(InvalidInput):
        await svc(transport).get_by_tag(REF, "a b")
    transport.add_response(resp(rel(1)))
    transport.add_response(resp({"id": 1}))
    r = await svc(transport).get_by_tag(REF, "v1")
    assert r.is_latest


def test_listing_includes_assets_and_source_archives():
    r = rel(1, assets=2)
    assets = [Asset.from_api(a) for a in r["assets"]]
    lst = build_listing("o", "r", "v1", assets)
    assert [e.index for e in lst] == [1, 2, 3, 4]
    assert lst[0].url == "https://api.github.com/repos/o/r/releases/assets/100"
    assert lst[2].is_source and lst[2].name.endswith(".zip")
    assert lst[3].url.endswith("/tarball/v1")


def test_resolve_selectors_by_position_id_and_source():
    assets = [Asset.from_api(a) for a in rel(1, assets=2)["assets"]]
    lst = build_listing("o", "r", "v1", assets)
    assert [e.key for e in resolve_selectors(lst, ["1", "101", "zip", "1"])] == ["100", "101", "zipball"]
    with pytest.raises(InvalidInput):
        resolve_selectors(lst, ["999999"])
    with pytest.raises(InvalidInput):
        resolve_selectors(lst, ["abc"])


def test_parse_digest():
    assert parse_digest("sha256:" + "A" * 64) == "a" * 64
    assert parse_digest(None) is None
    assert parse_digest("sha1:abc") is None
    assert parse_digest("sha256:zz") is None


def _asset(i, name):
    return Asset.from_api({"id": i, "name": name, "size": 10, "state": "uploaded"})


def test_signatures_and_checksums_are_recognised_as_extras():
    for name in ("app.apk.sig", "key.asc", "app.zip.sha256", "SHA256SUMS", "app.intoto.jsonl", "x.sbom"):
        assert is_extra_asset(name), name
    for name in ("app.apk", "tool-linux-amd64.tar.gz", "setup.exe", "signature-tool.zip"):
        assert not is_extra_asset(name), name


def test_clean_listing_hides_extras_and_renumbers():
    assets = [_asset(1, "a.apk"), _asset(2, "a.apk.sig"), _asset(3, "b.apk"), _asset(4, "pub.asc")]
    assert count_extras(assets) == 2
    clean = build_listing("o", "r", "v1", assets, include_extras=False)
    assert [(e.index, e.name) for e in clean[:2]] == [(1, "a.apk"), (2, "b.apk")]
    assert [e.is_source for e in clean] == [False, False, True, True]
    full = build_listing("o", "r", "v1", assets)
    assert len(full) == 6
