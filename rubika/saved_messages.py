"""Thin helper for talking to the user's own Saved Messages chat."""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, List, Optional, Protocol

log = logging.getLogger(__name__)

MAX_CHUNK = 3500  # rubpy itself splits at 4200; stay comfortably below


class Messenger(Protocol):
    """What the rest of the app needs from Rubika (implemented by RubpyMessenger and test fakes)."""

    my_guid: str

    async def send_text(self, text: str) -> Optional[str]: ...

    async def edit_text(self, message_id: str, text: str) -> None: ...

    async def send_file(
        self, path: str, caption: str, progress: Optional[Callable[[int, int], Awaitable[None]]] = None
    ) -> None: ...

    def is_own_message(self, message_id: str) -> bool: ...


def split_text(text: str, limit: int = MAX_CHUNK) -> List[str]:
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit and current:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


class SavedMessages:
    def __init__(self, messenger: Messenger):
        self.messenger = messenger

    async def reply(self, text: str) -> Optional[str]:
        last = None
        for chunk in split_text(text):
            last = await self.messenger.send_text(chunk)
        return last

    async def edit(self, message_id: Optional[str], text: str) -> bool:
        """Edit a status message; failures are non-fatal (progress is best effort)."""
        if not message_id:
            return False
        try:
            await self.messenger.edit_text(message_id, text[:3900])
            return True
        except Exception as exc:
            log.debug("edit failed: %s", type(exc).__name__)
            return False
