"""Rubika *user account* client built on rubpy (not a bot).

Verified against rubpy 7.3.5 sources:
  * ``Client(name=<path>)`` keeps the session in ``<path>.rp`` and ``start()`` reuses it;
  * ``client.send_message('me', ...)`` / ``send_document('me', path, caption=..., callback=...)``
    target Saved Messages; ``callback(total, uploaded)`` reports upload progress;
  * ``client.edit_message('me', message_id, text)`` edits a message;
  * ``@client.on_message_updates()`` delivers ``Update`` objects (``text``, ``message_id``,
    ``object_guid``, ``author_guid``).
rubpy's user-account API has no inline keyboards/callbacks, so navigation is command based.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Awaitable, Callable, Deque, Optional, Set

from config import Config
from errors import NotLoggedIn, SendError
from rubika.message_listener import IncomingMessage, incoming_from_update
from rubika.session_manager import has_session, protect_session

log = logging.getLogger(__name__)

OnMessage = Callable[[IncomingMessage], Awaitable[None]]


class RubpyMessenger:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.my_guid: str = ""
        self._client = None
        self._on_message: Optional[OnMessage] = None
        self._own_ids: Set[str] = set()
        self._own_order: Deque[str] = deque()
        self._tasks: Set[asyncio.Task] = set()
        self._stopping = False

    # ---- connection ----------------------------------------------------------
    async def connect(self) -> None:
        try:
            from rubpy import Client
        except ImportError:
            raise NotLoggedIn("rubpy is not installed. Run: pip install -r requirements.txt") from None
        if not has_session(self.cfg.session_path):
            raise NotLoggedIn("No Rubika session found. Run `python main.py login` once first.")
        client = Client(name=str(self.cfg.session_path), timeout=60, max_retries=self.cfg.max_retries)
        if self._on_message is not None:
            client.on_message_updates()(self._dispatch)
        await client.start()  # session exists -> no phone/SMS prompt
        self._client = client
        self.my_guid = client.guid
        protect_session(self.cfg.session_path)
        log.info("Connected to Rubika as account %s…", self.my_guid[:6])

    async def run_forever(self, on_message: OnMessage, on_connected: Optional[Callable[[], Awaitable[None]]] = None) -> None:
        """Receive updates until stopped; reconnect with backoff if the connection drops."""
        self._on_message = on_message
        delay = 2.0
        while not self._stopping:
            try:
                await self.connect()
                delay = 2.0
                if on_connected is not None:
                    asyncio.create_task(on_connected())
                await self._client.get_updates()  # blocks while connected
                log.warning("Rubika update loop ended; reconnecting")
            except NotLoggedIn:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Rubika connection error (%s); retrying in %.0fs", type(exc).__name__, delay)
            await self._close_client()
            if self._stopping:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120.0)

    async def stop(self) -> None:
        self._stopping = True
        await self._close_client()

    async def _close_client(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None

    # ---- incoming ------------------------------------------------------------
    async def _dispatch(self, update) -> None:
        msg = incoming_from_update(update)
        if msg is None or self._on_message is None:
            return
        task = asyncio.create_task(self._safe_handle(msg))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _safe_handle(self, msg: IncomingMessage) -> None:
        try:
            await self._on_message(msg)
        except Exception:
            log.exception("error while handling a message")

    # ---- outgoing ------------------------------------------------------------
    def is_own_message(self, message_id: str) -> bool:
        return str(message_id) in self._own_ids

    def _remember(self, message_id: Optional[str]) -> None:
        if not message_id:
            return
        mid = str(message_id)
        self._own_ids.add(mid)
        self._own_order.append(mid)
        while len(self._own_order) > 2000:
            self._own_ids.discard(self._own_order.popleft())

    def _require(self):
        if self._client is None:
            raise SendError("Not connected to Rubika right now.")
        return self._client

    async def send_text(self, text: str) -> Optional[str]:
        client = self._require()
        result = await client.send_message("me", text)
        mid = getattr(result, "message_id", None)
        self._remember(mid)
        return str(mid) if mid else None

    async def edit_text(self, message_id: str, text: str) -> None:
        client = self._require()
        await client.edit_message("me", message_id, text)

    async def send_file(self, path: str, caption: str, progress=None) -> None:
        client = self._require()
        kwargs = {"caption": caption}
        if progress is not None:
            kwargs["callback"] = progress
        result = await client.send_document("me", path, **kwargs)
        self._remember(getattr(result, "message_id", None))
