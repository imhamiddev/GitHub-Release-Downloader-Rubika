"""Configuration loaded from environment variables / .env. No secrets live in source code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

# Rubika upload parts: same proven values as the telegram-to-rubika project.
PART_SIZE_MB = 95
SPLIT_THRESHOLD_MB = 100


def _int(env: Mapping[str, str], name: str, default: int, minimum: int = 0) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer (got {raw!r})") from None
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


@dataclass(frozen=True)
class Config:
    rubika_phone: str = field(repr=False)
    github_token: Optional[str] = field(repr=False)
    download_dir: Path
    database_path: Path
    session_path: Path
    max_download_size_mb: int
    max_storage_mb: int
    max_concurrent_downloads: int
    cache_ttl: int
    log_level: str
    min_free_mb: int = 300
    part_size_mb: int = PART_SIZE_MB
    split_threshold_mb: int = SPLIT_THRESHOLD_MB
    page_size: int = 5
    max_retries: int = 5
    send_delay_seconds: int = 3

    @property
    def max_download_bytes(self) -> int:
        return self.max_download_size_mb * 1024 * 1024

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None, load_dotenv: bool = True) -> "Config":
        if env is None:
            if load_dotenv:
                try:
                    from dotenv import load_dotenv as _load

                    _load()
                except ImportError:  # python-dotenv is optional at runtime
                    pass
            env = os.environ

        token = (env.get("GITHUB_TOKEN") or "").strip() or None
        data_dir = Path(env.get("DATA_DIR") or "./data")
        cfg = cls(
            rubika_phone=(env.get("RUBIKA_PHONE") or "").strip(),
            github_token=token,
            download_dir=Path(env.get("DOWNLOAD_DIR") or "./downloads"),
            database_path=Path(env.get("DATABASE_PATH") or data_dir / "github_bridge.db"),
            session_path=Path(env.get("RUBIKA_SESSION") or data_dir / "rubika_session"),
            # Blank MAX_DOWNLOAD_SIZE_MB => same 2000 MB default as telegram-to-rubika.
            max_download_size_mb=_int(env, "MAX_DOWNLOAD_SIZE_MB", 2000, 1),
            max_storage_mb=_int(env, "MAX_STORAGE_MB", 6000, 1),
            max_concurrent_downloads=_int(env, "MAX_CONCURRENT_DOWNLOADS", 2, 1),
            cache_ttl=_int(env, "CACHE_TTL", 300, 0),
            log_level=(env.get("LOG_LEVEL") or "INFO").strip().upper(),
            min_free_mb=_int(env, "MIN_FREE_MB", 300, 0),
            page_size=_int(env, "PAGE_SIZE", 5, 1),
            max_retries=_int(env, "MAX_RETRIES", 5, 0),
            send_delay_seconds=_int(env, "SEND_DELAY_SECONDS", 3, 0),
        )
        return cfg
