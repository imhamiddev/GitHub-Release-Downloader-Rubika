"""Logging setup with secret redaction (GitHub token, Rubika phone)."""
from __future__ import annotations

import logging
from typing import Iterable


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str]):
        super().__init__()
        self._secrets = [s for s in secrets if s and len(s) >= 4]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        msg = record.getMessage()
        for s in self._secrets:
            if s in msg:
                msg = msg.replace(s, "***")
        record.msg, record.args = msg, ()
        return True


def setup_logging(level: str, secrets: Iterable[str] = ()) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    flt = RedactingFilter(secrets)
    for handler in logging.getLogger().handlers:
        handler.addFilter(flt)
    # httpx logs full request URLs at INFO; keep it quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)
