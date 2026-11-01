"""Release browsing: /releases /latest /release /open /page /filter /back."""
from __future__ import annotations

from typing import List, Optional

from errors import InvalidInput
from github.releases import Release
from github.repository import RepoRef, parse_repo_ref
from handlers.context import Context, Nav
from utils import formatting as fmt


def _repo_or_current(ctx: Context, token: Optional[str]) -> RepoRef:
    if token:
        return parse_repo_ref(token)
    if ctx.nav.repo is None:
        raise InvalidInput("Open a repository first: /get owner/repo")
    return ctx.nav.repo


async def show_release_page(ctx: Context, page: int, header: Optional[str] = None) -> None:
    nav = ctx.nav
    rp = await ctx.releases.get_page(nav.repo, page, ctx.cfg.page_size, nav.include_pre)
    if page > 1 and not rp.items:
        raise InvalidInput(f"There is no page {page}.")
    nav.page, nav.items, nav.has_next, nav.release, nav.listing = page, rp.items, rp.has_next, None, []
    await ctx.chat.reply(fmt.release_page_text(nav.repo.full_name, rp, header))


async def show_release(ctx: Context, rel: Release) -> None:
    nav = ctx.nav
    nav.release = rel
    nav.listing = rel.listing(nav.repo, include_extras=nav.show_extras)
    hidden = 0 if nav.show_extras else rel.extras_count
    await ctx.chat.reply(fmt.release_detail_text(nav.repo.full_name, rel, nav.listing, hidden, nav.show_extras))


async def cmd_releases(ctx: Context, args: List[str]) -> None:
    mode = None
    rest = []
    for a in args:
        if a.lower() in ("stable", "all"):
            mode = a.lower()
        else:
            rest.append(a)
    ref = _repo_or_current(ctx, rest[0] if rest else None)
    include_pre = ctx.nav.include_pre if mode is None else mode == "all"
    ctx.nav = Nav(repo=ref, include_pre=include_pre)
    await show_release_page(ctx, 1)


async def cmd_latest(ctx: Context, args: List[str]) -> None:
    ref = _repo_or_current(ctx, args[0] if args else None)
    rel = await ctx.releases.get_latest(ref)
    ctx.nav = Nav(repo=ref)
    await show_release(ctx, rel)


async def cmd_release(ctx: Context, args: List[str]) -> None:
    if not args:
        raise InvalidInput("Usage: /release owner/repo tag   (or /release tag inside an open repository)")
    if len(args) >= 2:
        ref, tag = parse_repo_ref(args[0]), args[1]
    else:
        ref, tag = _repo_or_current(ctx, None), args[0]
    rel = await ctx.releases.get_by_tag(ref, tag)
    if ctx.nav.repo != ref:
        ctx.nav = Nav(repo=ref)
    await show_release(ctx, rel)


async def cmd_open(ctx: Context, args: List[str]) -> None:
    if not args or not args[0].isdigit():
        raise InvalidInput("Usage: /open N  (N = number in the release list)")
    n = int(args[0])
    if not ctx.nav.items:
        raise InvalidInput("There is no release list open. Use /releases owner/repo")
    if not 1 <= n <= len(ctx.nav.items):
        raise InvalidInput(f"Choose a number between 1 and {len(ctx.nav.items)}.")
    await show_release(ctx, ctx.nav.items[n - 1])


async def cmd_page(ctx: Context, args: List[str]) -> None:
    if ctx.nav.repo is None:
        raise InvalidInput("Open a repository first: /get owner/repo")
    if not args or not args[0].isdigit() or int(args[0]) < 1:
        raise InvalidInput("Usage: /page N")
    await show_release_page(ctx, int(args[0]))


async def cmd_filter(ctx: Context, args: List[str]) -> None:
    if ctx.nav.repo is None:
        raise InvalidInput("Open a repository first: /get owner/repo")
    if not args or args[0].lower() not in ("stable", "all"):
        raise InvalidInput("Usage: /filter stable|all")
    ctx.nav.include_pre = args[0].lower() == "all"
    await show_release_page(ctx, 1)


async def cmd_back(ctx: Context, args: List[str]) -> None:
    nav = ctx.nav
    if nav.release is not None and nav.items:
        await show_release_page(ctx, nav.page)
    elif nav.release is not None and nav.repo:
        await show_release_page(ctx, 1)
    elif nav.items:
        from handlers.repository_handler import show_repo

        nav.items = []
        await show_repo(ctx)
    else:
        raise InvalidInput("Nothing to go back to.")
