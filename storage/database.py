"""SQLite persistence: jobs, processed command ids and navigation state.

All access is serialised behind one lock and run in a worker thread, so the event loop
never blocks on disk. Job rows are what makes recovery after a restart possible.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

ACTIVE_STATUSES = ("queued", "downloading", "verifying", "splitting", "sending")
TERMINAL_STATUSES = ("done", "failed", "cancelled")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    tag TEXT NOT NULL,
    asset_key TEXT NOT NULL,
    name TEXT NOT NULL,
    size INTEGER,
    url TEXT NOT NULL,
    digest TEXT,
    status TEXT NOT NULL,
    bytes_done INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    local_path TEXT,
    sha256 TEXT,
    parts_json TEXT,
    parts_sent INTEGER NOT NULL DEFAULT 0,
    send_inflight INTEGER,
    split_method TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_active
    ON jobs(repo, tag, asset_key)
    WHERE status IN ('queued','downloading','verifying','splitting','sending');
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id TEXT PRIMARY KEY,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class Job:
    id: int
    repo: str
    tag: str
    asset_key: str
    name: str
    size: Optional[int]
    url: str
    digest: Optional[str]
    status: str
    bytes_done: int
    error: Optional[str]
    local_path: Optional[str]
    sha256: Optional[str]
    parts_json: Optional[str]
    parts_sent: int
    send_inflight: Optional[int]
    split_method: Optional[str]
    created_at: float
    updated_at: float

    @property
    def parts(self) -> List[Dict[str, Any]]:
        return json.loads(self.parts_json) if self.parts_json else []

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES


_JOB_COLUMNS = [f.name for f in fields(Job)]


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

    # ---- lifecycle -------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        return self._conn

    async def _run(self, fn):
        def call():
            with self._lock:
                return fn(self._connect())

        return await asyncio.to_thread(call)

    async def init(self) -> None:
        await self._run(lambda c: None)

    async def close(self) -> None:
        def close(c):
            c.close()
            self._conn = None

        if self._conn is not None:
            await self._run(close)

    # ---- processed commands (replay protection) -------------------------
    async def claim_message(self, message_id: str) -> bool:
        """True the first time a message id is seen; False for any replay."""

        def op(c):
            cur = c.execute(
                "INSERT OR IGNORE INTO processed_messages(message_id, ts) VALUES (?, ?)",
                (str(message_id), time.time()),
            )
            return cur.rowcount == 1

        return await self._run(op)

    async def purge_old_messages(self, older_than_seconds: float = 30 * 86400) -> None:
        await self._run(
            lambda c: c.execute("DELETE FROM processed_messages WHERE ts < ?", (time.time() - older_than_seconds,))
        )

    # ---- kv ----------------------------------------------------------------
    async def kv_get(self, key: str) -> Optional[str]:
        def op(c):
            row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None

        return await self._run(op)

    async def kv_set(self, key: str, value: str) -> None:
        await self._run(lambda c: c.execute("INSERT OR REPLACE INTO kv(key, value) VALUES (?, ?)", (key, value)))

    # ---- jobs --------------------------------------------------------------
    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        return Job(**{k: row[k] for k in _JOB_COLUMNS})

    async def create_job(
        self,
        *,
        repo: str,
        tag: str,
        asset_key: str,
        name: str,
        size: Optional[int],
        url: str,
        digest: Optional[str],
    ) -> tuple[Job, bool]:
        """Insert a queued job. Returns (job, created); created=False when an identical job is active."""

        def op(c):
            row = c.execute(
                "SELECT * FROM jobs WHERE repo=? AND tag=? AND asset_key=? AND status IN "
                "('queued','downloading','verifying','splitting','sending')",
                (repo, tag, asset_key),
            ).fetchone()
            if row:
                return self._row_to_job(row), False
            now = time.time()
            cur = c.execute(
                "INSERT INTO jobs(repo, tag, asset_key, name, size, url, digest, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,'queued',?,?)",
                (repo, tag, asset_key, name, size, url, digest, now, now),
            )
            row = c.execute("SELECT * FROM jobs WHERE id=?", (cur.lastrowid,)).fetchone()
            return self._row_to_job(row), True

        return await self._run(op)

    async def get_job(self, job_id: int) -> Optional[Job]:
        def op(c):
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return self._row_to_job(row) if row else None

        return await self._run(op)

    async def update_job(self, job_id: int, **values: Any) -> None:
        if not values:
            return
        for key in values:
            if key not in _JOB_COLUMNS or key == "id":
                raise KeyError(key)
        values["updated_at"] = time.time()
        assignments = ", ".join(f"{k}=?" for k in values)

        await self._run(lambda c: c.execute(f"UPDATE jobs SET {assignments} WHERE id=?", (*values.values(), job_id)))

    async def list_jobs(self, limit: int = 10) -> List[Job]:
        def op(c):
            rows = c.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [self._row_to_job(r) for r in rows]

        return await self._run(op)

    async def jobs_with_status(self, statuses: tuple[str, ...]) -> List[Job]:
        marks = ",".join("?" for _ in statuses)

        def op(c):
            rows = c.execute(f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY id", statuses).fetchall()
            return [self._row_to_job(r) for r in rows]

        return await self._run(op)
