"""Command parsing and dispatch."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from errors import BridgeError, InvalidInput
from handlers import download_handler as dl
from handlers import release_handler as rl
from handlers import repository_handler as rp
from handlers.context import Context
from rubika.message_listener import IncomingMessage

log = logging.getLogger(__name__)

Handler = Callable[[Context, List[str]], Awaitable[None]]

COMMANDS: Dict[str, Handler] = {
    "start": dl.cmd_help,
    "help": dl.cmd_help,
    "get": rp.cmd_get,
    "releases": rl.cmd_releases,
    "latest": rl.cmd_latest,
    "release": rl.cmd_release,
    "open": rl.cmd_open,
    "page": rl.cmd_page,
    "filter": rl.cmd_filter,
    "back": rl.cmd_back,
    "notes": rl.cmd_notes,
    "extras": rl.cmd_extras,
    "asset": dl.cmd_asset,
    "download": dl.cmd_download,
    "downloads": dl.cmd_downloads,
    "cancel": dl.cmd_cancel,
    "retry": dl.cmd_retry,
    "status": dl.cmd_status,
}

MAX_COMMAND_LEN = 600


def parse_command(text: str) -> Optional[Tuple[str, List[str]]]:
    """'/get  2dust/v2rayNG' -> ('get', ['2dust/v2rayNG']); None when it is not a command."""
    text = (text or "").strip()
    if not text.startswith("/") or len(text) > MAX_COMMAND_LEN:
        return None
    first, *rest = text.split()
    name = first[1:].split("@", 1)[0].lower()
    if not name or not name.replace("_", "").isalnum():
        return None
    return name, rest


class CommandHandler:
    def __init__(self, ctx: Context):
        self.ctx = ctx

    async def handle(self, msg: IncomingMessage) -> None:
        parsed = parse_command(msg.text)
        if parsed is None:
            return
        name, args = parsed
        handler = COMMANDS.get(name)
        if handler is None:
            await self.ctx.chat.reply(f"❓ Unknown command /{name[:30]}. Send /help for the list.")
            return
        try:
            await handler(self.ctx, args)
        except BridgeError as exc:
            await self.ctx.chat.reply(f"⚠️ {exc}")
        except Exception:
            log.exception("command /%s failed", name)
            await self.ctx.chat.reply("⚠️ Something went wrong on the server. Details are in the server log.")
