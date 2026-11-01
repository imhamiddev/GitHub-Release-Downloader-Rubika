"""One-time interactive login (phone number + SMS code), same flow as telegram-to-rubika.

``python main.py login`` runs this. rubpy asks for the SMS code on the terminal, then writes
the session file; afterwards the service starts without any login prompt.
"""
from __future__ import annotations

import logging
from pathlib import Path

from config import Config
from errors import BridgeError
from rubika.session_manager import has_session, protect_session, session_file

log = logging.getLogger(__name__)


async def interactive_login(cfg: Config) -> str:
    """Log in (or confirm an existing session) and return the account GUID."""
    try:
        from rubpy import Client
    except ImportError:
        raise BridgeError("rubpy is not installed. Run: pip install -r requirements.txt") from None

    cfg.session_path.parent.mkdir(parents=True, exist_ok=True)
    if has_session(cfg.session_path):
        print(f"A session already exists at {session_file(cfg.session_path)}; verifying it...")
    elif not cfg.rubika_phone:
        raise BridgeError("Set RUBIKA_PHONE in .env first (e.g. 989123456789).")

    client = Client(name=str(cfg.session_path), phone_number=cfg.rubika_phone or None)
    try:
        # start() performs phone + SMS login only when the session has no credentials yet.
        await client.start(phone_number=cfg.rubika_phone or None)
        me = await client.get_me()
        guid = me.user.user_guid
    finally:
        try:
            await client.disconnect()
        except Exception:  # pragma: no cover - best effort
            pass
    protect_session(cfg.session_path)
    return guid
