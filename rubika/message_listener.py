"""Incoming message filtering: only commands I typed myself in Saved Messages, once each."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from rubika.saved_messages import Messenger
from storage.database import Database

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IncomingMessage:
    message_id: str
    chat_guid: str
    author_guid: str
    text: str


def incoming_from_update(update) -> Optional[IncomingMessage]:
    """Build an IncomingMessage from a rubpy ``Update``; None when it is not a plain text message."""
    try:
        text = update.text
        message_id = update.message_id
        chat = update.object_guid
        author = update.author_guid
    except Exception:
        return None
    if not isinstance(text, str) or not message_id or not chat or not author:
        return None
    return IncomingMessage(str(message_id), str(chat), str(author), text)


class MessageListener:
    def __init__(
        self,
        messenger: Messenger,
        db: Database,
        dispatch: Callable[[IncomingMessage], Awaitable[None]],
    ):
        self.messenger = messenger
        self.db = db
        self.dispatch = dispatch

    def is_command_for_me(self, msg: IncomingMessage) -> bool:
        me = self.messenger.my_guid
        if not me or msg.chat_guid != me:  # not the Saved Messages conversation
            return False
        if msg.author_guid != me:  # only my own account may control the app
            return False
        if self.messenger.is_own_message(msg.message_id):  # never react to our own replies
            return False
        return msg.text.lstrip().startswith("/")

    async def handle(self, msg: IncomingMessage) -> bool:
        """Return True when the message was accepted and dispatched."""
        if not self.is_command_for_me(msg):
            return False
        if not await self.db.claim_message(msg.message_id):  # replay / duplicate delivery
            log.debug("duplicate message %s ignored", msg.message_id)
            return False
        await self.dispatch(msg)
        return True
