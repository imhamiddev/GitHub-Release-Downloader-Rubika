"""/get — repository info plus the first page of releases."""
from __future__ import annotations

from typing import List

from errors import InvalidInput
from github.repository import parse_repo_ref
from handlers.context import Context, Nav
from handlers.release_handler import show_release_page
from utils import formatting as fmt


async def cmd_get(ctx: Context, args: List[str]) -> None:
    if not args:
        raise InvalidInput("Usage: /get owner/repo   or   /get https://github.com/owner/repo")
    ref = parse_repo_ref(args[0])
    info = await ctx.repos.get(ref)
    ctx.nav = Nav(repo=ref)
    await show_release_page(ctx, 1, header=fmt.repo_info_text(info))  # one message: info + releases


async def show_repo(ctx: Context) -> None:
    info = await ctx.repos.get(ctx.nav.repo)
    await ctx.chat.reply(fmt.repo_info_text(info))
