"""Integrity checks for downloaded files."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Optional

from errors import IntegrityError


def _sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            data = fh.read(chunk)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


async def sha256_file(path: Path) -> str:
    return await asyncio.to_thread(_sha256_file, path)


def verify_size(actual: int, expected: Optional[int]) -> None:
    if expected is not None and expected > 0 and actual != expected:
        raise IntegrityError(f"size mismatch: downloaded {actual} bytes, GitHub reports {expected}")


def verify_sha256(actual_hex: str, expected_hex: Optional[str]) -> bool:
    """Return True when a digest was available and matched, False when none was available.

    Raises IntegrityError on mismatch.
    """
    if not expected_hex:
        return False
    if actual_hex.lower() != expected_hex.lower():
        raise IntegrityError("SHA-256 mismatch: the downloaded file does not match GitHub's digest")
    return True
