"""Locating, checking and protecting the persistent Rubika session file.

rubpy stores the session in a SQLite file named ``<session_path>.rp`` (auth key, private key,
guid). Whoever holds that file controls the account, so it is chmod 600 and git-ignored.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def session_file(session_path: Path) -> Path:
    s = str(session_path)
    return Path(s if s.endswith(".rp") else s + ".rp")


def has_session(session_path: Path) -> bool:
    """True when the session file exists and already contains a logged-in account."""
    f = session_file(session_path)
    if not f.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT auth FROM session LIMIT 1").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return bool(row and row[0])


def protect_session(session_path: Path) -> None:
    f = session_file(session_path)
    for candidate in (f, f.with_name(f.name + "-journal"), f.with_name(f.name + "-wal")):
        try:
            if candidate.exists():
                os.chmod(candidate, 0o600)
        except OSError:
            pass
