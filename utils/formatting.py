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
    out = []
    if rel.is_latest:
        out.append("⭐ latest stable")
    if rel.prerelease:
        out.append("🧪 pre-release")
    if rel.draft:
        out.append("📝 draft")
    return " · ".join(out)


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


def release_page_text(repo: str, page: ReleasePage) -> str:
    kind = "all releases" if page.include_prereleases else "stable only"
    if not page.items:
        return f"📋 {repo} — no releases on page {page.page} ({kind})."
    lines = [f"📋 Releases of {md_safe(repo)} — page {page.page} ({kind})", ""]
    for i, rel in enumerate(page.items, 1):
        title = md_safe(rel.name) if rel.name and rel.name != rel.tag else ""
        head = f"{i}. {md_safe(rel.tag)}" + (f" — {title}" if title else "")
        lines.append(head)
        meta = f"   📅 {format_date(rel.published_at)} · 👤 {md_safe(rel.author) or 'n/a'} · 📎 {len(rel.assets)} assets"
        lines.append(meta)
        f = flags(rel)
        if f:
            lines.append(f"   {f}")
    nav = ["/open N"]
    if page.has_next:
        nav.append(f"/page {page.page + 1}")
    if page.page > 1:
        nav.append(f"/page {page.page - 1}")
    nav.append("/filter stable|all")
    lines += ["", "➡️ " + "  ".join(nav)]
    return "\n".join(lines)


def release_detail_text(repo: str, rel: Release, listing: Sequence[AssetEntry]) -> str:
    lines = [
        f"🏷 {md_safe(rel.name or rel.tag)}",
        f"📦 {md_safe(repo)} @ {md_safe(rel.tag)}",
        f"📅 {format_date(rel.published_at)} · 👤 {md_safe(rel.author) or 'n/a'}",
        f"📎 {len(rel.assets)} assets",
    ]
    f = flags(rel)
    if f:
        lines.append(f)
    lines += ["", "📝 Changelog:", changelog_excerpt(rel.body), "", "📂 Assets:"]
    lines += asset_lines(listing)
    lines += ["", "➡️ /asset 1 3 (by number, several allowed) · /download <asset ID> · /back"]
    return "\n".join(lines)


def asset_lines(listing: Sequence[AssetEntry]) -> List[str]:
    out = []
    for e in listing:
        status = "✅ available" if e.available else "⛔ not available"
        kind = e.content_type or "unknown type"
        if e.is_source:
            out.append(f"{e.index}. {md_safe(e.name)} — source archive · {kind} · {status}")
        else:
            out.append(
                f"{e.index}. {md_safe(e.name)}\n"
                f"   {format_size(e.size)} · {md_safe(kind)} · ⬇️ {e.download_count or 0} · ID {e.key} · {status}"
            )
    return out


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
