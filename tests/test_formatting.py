from github.assets import Asset, build_listing
from github.releases import Release
from github.repository import RepoInfo, RepoRef
from utils import formatting as fmt


def make_release(names, **kw):
    assets = [{"id": 100 + i, "name": n, "size": 1024 * 1024 * (i + 1), "state": "uploaded"} for i, n in enumerate(names)]
    d = {"id": 1, "tag_name": "2.3.14", "name": "2.3.14", "published_at": "2026-10-28T03:06:25Z",
         "author": {"login": "github-actions[bot]"}, "body": "", "prerelease": False, "draft": False, "assets": assets}
    d.update(kw)
    return Release.from_api(d)


def test_common_prefix_cuts_at_separator_and_needs_two_names():
    names = ["v2rayNG_2.3.14_fdroid_arm64-v8a.apk", "v2rayNG_2.3.14_fdroid_x86.apk"]
    assert fmt.common_prefix(names) == "v2rayNG_2.3.14_fdroid_"
    assert fmt.common_prefix(names[:1]) == ""
    assert fmt.common_prefix(["a.apk", "b.apk"]) == ""  # too short to be worth hiding
    for group in (["tool_1.zip", "tool_1.zip.x"], ["same_name.apk", "same_name.apk"], ["abc_def", "abc_def_x"]):
        pre = fmt.common_prefix(group)
        assert all(n[len(pre):] for n in group)  # a shortened name is never empty


def test_detail_is_compact_without_ids_types_or_download_counts():
    rel = make_release(["app_2.3.14_arm64-v8a.apk", "app_2.3.14_x86.apk", "app_2.3.14_x86.apk.sig"])
    rel.is_latest = True
    lst = rel.listing(RepoRef("o", "r"), include_extras=False)
    text = fmt.release_detail_text("o/r", rel, lst, hidden=rel.extras_count)
    assert "📂 Files · app_2.3.14_…" in text
    assert "1. arm64-v8a.apk · 1.0 MB" in text and "2. x86.apk · 2.0 MB" in text
    assert ".sig" not in text.replace("signature", "")  # signature file is hidden
    assert "1 signature/checksum file(s) hidden — /extras" in text
    assert "ID " not in text and "application/" not in text and "available" not in text and "⬇️" not in text
    assert "/asset zip" in text and "Changelog" not in text and "⭐" in text


def test_unavailable_file_is_flagged_and_source_archives_not_listed_as_rows():
    rel = make_release(["a.apk"])
    rel.assets[0] = Asset.from_api({"id": 5, "name": "a.apk", "size": 10, "state": "starter"})
    lst = build_listing("o", "r", "2.3.14", rel.assets)
    text = fmt.release_detail_text("o/r", rel, lst)
    assert "1. a.apk · 10 B · ⛔ unavailable" in text
    assert "source archive" not in text and "r-2.3.14.zip" not in text


def test_notes_text():
    assert "no changelog" in fmt.release_notes_text(make_release([]))
    rel = make_release([], body="<!-- hidden -->\n\n* fixed things " + "x" * 3000)
    out = fmt.release_notes_text(rel, limit=100)
    assert "hidden" not in out and len(out) < 200 and out.endswith("…")


def test_repo_info_is_two_short_lines():
    info = RepoInfo(full_name="2dust/v2rayNG", name="v2rayNG", owner="2dust", description="A V2Ray client",
                    stars=65128, forks=8503, language="Kotlin", default_branch="master",
                    url="https://github.com/2dust/v2rayNG", archived=False)
    assert fmt.repo_info_text(info) == "📦 2dust/v2rayNG · ⭐ 65.1k · Kotlin\nA V2Ray client"


def test_short_helpers():
    assert fmt.format_day("2026-11-01T06:59:12Z") == "2026-11-01"
    assert fmt.format_count(999) == "999" and fmt.format_count(65128) == "65.1k" and fmt.format_count(2_500_000) == "2.5M"
    assert fmt.progress_bar(8 * 1024 * 1024, 31 * 1024 * 1024) == "██░░░░░░░░ 26% · 8.0 MB / 31.0 MB"
