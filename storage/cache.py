"""Small in-memory TTL cache that also remembers ETags for conditional GitHub requests."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass
class CacheEntry:
    value: Any
    etag: Optional[str]
    expires_at: float
    extra: Dict[str, Any] = field(default_factory=dict)


class TTLCache:
    def __init__(self, ttl: float, max_entries: int = 512, clock: Callable[[], float] = time.monotonic):
        self.ttl = ttl
        self.max_entries = max_entries
        self._clock = clock
        self._data: Dict[str, CacheEntry] = {}

    def get(self, key: str) -> Optional[CacheEntry]:
        """Return the entry even if expired (callers revalidate with its ETag)."""
        return self._data.get(key)

    def is_fresh(self, entry: CacheEntry) -> bool:
        return self._clock() < entry.expires_at

    def put(self, key: str, value: Any, etag: Optional[str] = None, extra: Optional[dict] = None) -> None:
        if len(self._data) >= self.max_entries:
            oldest = min(self._data, key=lambda k: self._data[k].expires_at)
            self._data.pop(oldest, None)
        self._data[key] = CacheEntry(value, etag, self._clock() + self.ttl, extra or {})

    def touch(self, key: str) -> None:
        entry = self._data.get(key)
        if entry:
            entry.expires_at = self._clock() + self.ttl

    def clear(self) -> None:
        self._data.clear()
