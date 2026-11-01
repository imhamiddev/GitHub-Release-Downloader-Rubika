"""Message text builders. Output starts with an emoji, never with "/", so the bot's own replies
can never look like commands."""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Iterable, List, Optional, Sequence

from github.assets import AssetEntry
from github.releases import Release, ReleasePage
from github.repository import RepoInfo

ZWSP = "​"
_MD_TOKENS = re.compile(r"(\*\*|__|--|~~|\|\||```)")


def md_safe(text: Optional[str]) -> str:
    """Neutralise Rubika markdown tokens in dynamic text (rubpy parses ** __ ~~ -- || ` [..](..))."""
    if not text:
        return ""
    text = text.replace("`", "'")
    text = _MD_TOKENS.sub(lambda m: m.group(0)[0] + ZWSP + m.group(0)[1:], text)
    text = text.replace("](", "]" + ZWSP + "(")
    return re.sub(r"(?m)^>", ZWSP + ">", text)


def format_size(n: Optional[int]) -> str:
    if n is None:
        return "size n/a"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"  # pragma: no cover


def format_date(iso: Optional[str]) -> str:
    if not iso:
        return "unknown date"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return iso


def format_day(iso: Optional[str]) -> str:
    """Date only (2026-11-01): the time of day and the timezone are noise in a release list."""
    if not iso:
        return "unknown date"
    return iso[:10] if re.match(r"\d{4}-\d{2}-\d{2}", iso) else iso


def format_count(n: Optional[int]) -> str:
    """65128 -> 65.1k, 1234567 -> 1.2M."""
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def progress_bar(done: int, total: Optional[int], width: int = 10) -> str:
    if not total:
        return f"{format_size(done)}"
    frac = max(0.0, min(1.0, done / total))
    filled = int(frac * width)
    return f"{'█' * filled}{'░' * (width - filled)} {frac * 100:.0f}% · {format_size(done)} / {format_size(total)}"


def truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def changelog_excerpt(body: str, limit: int = 700) -> str:
    body = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return md_safe(truncate(body, limit)) if body else "(no description)"


def flags(rel: Release) -> str:
    """Icons only (the legend is printed once under the list)."""
    out = []
    if rel.is_latest:
        out.append("⭐")
    if rel.prerelease:
        out.append("🧪")
    if rel.draft:
        out.append("📝")
    return " ".join(out)


def repo_info_text(info: RepoInfo) -> str:
    """Name, stars and language on one line, then the description. Nothing else is needed to pick a release."""
    head = [f"📦 {md_safe(info.full_name)}", f"⭐ {format_count(info.stars)}"]
    if info.language:
        head.append(md_safe(info.language))
    if info.archived:
        head.append("🗄 archived")
    lines = [" · ".join(head)]
    if info.description:
        lines.append(md_safe(truncate(info.description, 140)))
    return "\n".join(lines)


def release_page_text(repo: str, page: ReleasePage, header: Optional[str] = None) -> str:
    """One line per release: number, tag, flags, date. ``header`` is the repo info when sent by /get."""
    kind = "all" if page.include_prereleases else "stable only"
    title = f"📋 Releases · {kind} · page {page.page}" if header else f"📋 {md_safe(repo)} · releases · {kind} · page {page.page}"
    top = [header, ""] if header else []
    if not page.items:
        return "\n".join(top + [f"{title}\nNothing here."])
    lines = top + [title, ""]
    seen_flags = set()
    for i, rel in enumerate(page.items, 1):
        name = md_safe(truncate(rel.name, 40)) if rel.name and rel.name != rel.tag else ""
        label = md_safe(rel.tag) + (f" — {name}" if name else "")
        icons = flags(rel)
        seen_flags.update(icons.split())
        lines.append(f"{i}. {label}" + (f" {icons}" if icons else "") + f" · {format_day(rel.published_at)}")
    legend = [txt for icon, txt in (("⭐", "⭐ latest"), ("🧪", "🧪 pre-release"), ("📝", "📝 draft")) if icon in seen_flags]
    if legend:
        lines += ["", " · ".join(legend)]
    nav = ["/open N"]
    if page.has_next:
        nav.append(f"/page {page.page + 1}")
    if page.page > 1:
        nav.append(f"/page {page.page - 1}")
    nav.append("/filter stable" if page.include_prereleases else "/filter all")
    lines += ["", "➡️ " + " · ".join(nav)]
    return "\n".join(lines)


def common_prefix(names: Sequence[str]) -> str:
    """Shared leading part of several file names, cut after a separator (_ - . space).

    ["app_1.2_arm64.apk", "app_1.2_x86.apk"] -> "app_1.2_". Returns "" when it would not help.
    """
    if len(names) < 2:
        return ""
    pre = names[0]
    for n in names[1:]:
        k = 0
        while k < min(len(pre), len(n)) and pre[k] == n[k]:
            k += 1
        pre = pre[:k]
    cut = max(pre.rfind(c) for c in "_-. ") + 1
    pre = pre[:cut]
    return pre if len(pre) >= 5 and all(len(n) > len(pre) for n in names) else ""


def asset_lines(listing: Sequence[AssetEntry], prefix: str = "") -> List[str]:
    """``3. arm64-v8a.apk · 31.0 MB`` per file. Source archives are summarised elsewhere."""
    out = []
    for e in listing:
        if e.is_source:
            continue
        name = e.name[len(prefix):] if prefix and e.name.startswith(prefix) else e.name
        extra = "" if e.available else " · ⛔ unavailable"
        out.append(f"{e.index}. {md_safe(name)} · {format_size(e.size)}{extra}")
    return out


def release_detail_text(
    repo: str, rel: Release, listing: Sequence[AssetEntry], hidden: int = 0, extras_shown: bool = False
) -> str:
    """Title, files with sizes, and the few commands that make sense next. No changelog (see /notes)."""
    title = f"🏷 {md_safe(rel.tag)}"
    if rel.name and rel.name != rel.tag:
        title += f" — {md_safe(truncate(rel.name, 60))}"
    icons = flags(rel)
    if icons:
        title += f" {icons}"
    files = [e for e in listing if not e.is_source]
    prefix = common_prefix([e.name for e in files])
    lines = [title, f"📦 {md_safe(repo)} · {format_day(rel.published_at)}", ""]
    if files:
        lines.append("📂 Files" + (f" · {md_safe(prefix)}…" if prefix else ""))
        lines += asset_lines(listing, prefix)
    else:
        lines.append("📂 No uploaded files in this release.")
    if any(e.key == "zipball" for e in listing):
        lines.append("📚 Source code: /asset zip · /asset tar")
    if hidden:
        lines.append(f"➕ {hidden} signature/checksum file(s) hidden — /extras")
    elif extras_shown and rel.extras_count:
        lines.append("➖ showing signatures/checksums — /extras to hide")
    lines += ["", "➡️ /asset 1 3 · /notes · /back"]
    return "\n".join(lines)


def release_notes_text(rel: Release, limit: int = 1500) -> str:
    title = f"📝 {md_safe(rel.tag)} — changelog"
    if not (rel.body or "").strip():
        return f"{title}\n\nThis release has no changelog."
    return f"{title}\n\n{changelog_excerpt(rel.body, limit)}"


JOB_ICONS = {
    "queued": "🕒",
    "downloading": "⬇️",
    "verifying": "🔍",
    "splitting": "✂️",
    "sending": "📤",
    "done": "✅",
    "failed": "❌",
    "cancelled": "🚫",
}


def job_line(job) -> str:
    icon = JOB_ICONS.get(job.status, "•")
    line = f"{icon} #{job.id} {md_safe(job.name)} — {job.status}"
    if job.status == "downloading" and job.size:
        line += f" {job.bytes_done * 100 // job.size}%"
    if job.status == "sending" and job.parts:
        line += f" part {min(job.parts_sent + 1, len(job.parts))}/{len(job.parts)}"
    if job.error and job.status in ("failed",):
        line += f"\n   ⚠️ {md_safe(truncate(job.error, 200))}"
    return line


def help_text() -> str:
    return (
        "📖 GitHub Release Downloader — commands (send them here, in Saved Messages)\n\n"
        "Browse\n"
        "/get owner/repo — repository info + releases\n"
        "/releases owner/repo [stable|all] — browse all releases\n"
        "/latest owner/repo — latest stable release\n"
        "/release owner/repo tag — open a release (or /release tag inside a repo)\n"
        "/open N — open item N of the current list\n"
        "/page N — go to page N · /filter stable|all · /back\n\n"
        "Download\n"
        "/asset 1 3 — download assets by number (zip / tar = source archives)\n"
        "/download ID — download by GitHub asset ID\n"
        "/downloads — active and recent downloads\n"
        "/cancel [ID] — cancel one download (no ID: everything active)\n"
        "/retry ID — resume a failed download\n\n"
        "Other\n"
        "/status — server status and storage · /help\n\n"
        "🗜 Every file arrives as an AES-256 ZIP with a random name; its password is in the caption (several parts if big)."
    )


def now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
