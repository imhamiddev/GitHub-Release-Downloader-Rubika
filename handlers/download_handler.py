"""Downloads: /asset /download /downloads /cancel /retry /status."""
from __future__ import annotations

import shutil
import time
from typing import List

from errors import BridgeError, InvalidInput
from github.assets import resolve_selectors
from handlers.context import Context
from utils import formatting as fmt
from utils.file_utils import dir_size, free_bytes


async def _queue(ctx: Context, selectors: List[str]) -> None:
    nav = ctx.nav
    if nav.release is None or nav.repo is None:
        raise InvalidInput("Open a release first (/latest owner/repo or /release owner/repo tag).")
    entries = resolve_selectors(nav.listing, selectors)
    lines = []
    for entry in entries:
        try:
            job, created = await ctx.jobs.submit(nav.repo, nav.release.tag, entry)
        except BridgeError as exc:
            lines.append(f"❌ {fmt.md_safe(entry.name)}: {fmt.md_safe(str(exc))}")
            continue
        if created:
            lines.append(f"🕒 #{job.id} queued: {fmt.md_safe(entry.name)}")
        else:
            lines.append(f"↩️ #{job.id} {fmt.md_safe(entry.name)} is already {job.status} — not queued again")
    await ctx.chat.reply("\n".join(lines))


async def cmd_asset(ctx: Context, args: List[str]) -> None:
    if not args:
        raise InvalidInput("Usage: /asset 1 3   (numbers from the asset list; zip / tar for source archives)")
    await _queue(ctx, args)


async def cmd_download(ctx: Context, args: List[str]) -> None:
    if not args or not all(a.isdigit() for a in args):
        raise InvalidInput("Usage: /download <asset ID>")
    await _queue(ctx, args)


async def cmd_downloads(ctx: Context, args: List[str]) -> None:
    jobs = await ctx.db.list_jobs(10)
    if not jobs:
        await ctx.chat.reply("📭 No downloads yet.")
        return
    await ctx.chat.reply("📥 Downloads (newest first)\n" + "\n".join(fmt.job_line(j) for j in jobs))


async def cmd_cancel(ctx: Context, args: List[str]) -> None:
    if not args:
        n = await ctx.jobs.cancel_all()
        await ctx.chat.reply(f"🚫 Cancelling {n} active download(s)." if n else "Nothing is running.")
        return
    if not args[0].isdigit():
        raise InvalidInput("Usage: /cancel [job ID]")
    ok = await ctx.jobs.cancel(int(args[0]))
    await ctx.chat.reply(f"🚫 Cancelling #{args[0]}…" if ok else f"#{args[0]} is not an active download.")


async def cmd_retry(ctx: Context, args: List[str]) -> None:
    if not args or not args[0].isdigit():
        raise InvalidInput("Usage: /retry <job ID>")
    job = await ctx.jobs.retry(int(args[0]))
    await ctx.chat.reply(f"🔁 #{job.id} {fmt.md_safe(job.name)} restarted.")


async def cmd_status(ctx: Context, args: List[str]) -> None:
    cfg = ctx.cfg
    used = dir_size(cfg.download_dir) if cfg.download_dir.exists() else 0
    free = free_bytes(cfg.download_dir)
    active = len(await ctx.db.jobs_with_status(("queued", "downloading", "verifying", "splitting", "sending")))
    rate = ctx.github.rate
    rate_txt = "n/a" if rate.remaining is None else f"{rate.remaining}/{rate.limit}"
    up = int(time.time() - ctx.started_at)
    lines = [
        "🟢 GitHub Release Downloader is running",
        f"⏱ Uptime: {up // 3600}h {(up % 3600) // 60}m",
        f"📥 Active downloads: {active} (max {cfg.max_concurrent_downloads} at once)",
        f"💾 Storage used: {fmt.format_size(used)} of {cfg.max_storage_mb} MB quota · free disk {fmt.format_size(free)}",
        f"🐙 GitHub API: {'token set' if ctx.github.has_token else 'no token (60 req/h)'} · remaining {rate_txt}",
        f"✂️ Split: {'7-Zip' if ctx.jobs.sevenzip_bin else 'plain binary split (7-Zip not found)'} above {cfg.split_threshold_mb} MB",
    ]
    await ctx.chat.reply("\n".join(lines))


async def cmd_help(ctx: Context, args: List[str]) -> None:
    await ctx.chat.reply(fmt.help_text())
