"""Filesystem helpers: safe names, disk usage, atomic writes and file splitting."""
from __future__ import annotations

import asyncio
import glob
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from errors import DownloadError, JobCancelled

_BAD_CHARS = re.compile(r"[^\w.\-+() ]", re.UNICODE)


def safe_filename(name: str, default: str = "file", max_len: int = 150) -> str:
    """Return a name that is safe to create on disk (no separators, no leading dot/dash)."""
    name = os.path.basename((name or "").replace("\\", "/"))
    name = _BAD_CHARS.sub("_", name).strip(" .")
    name = name.lstrip("-")
    if not name:
        name = default
    if len(name) > max_len:
        stem, ext = os.path.splitext(name)
        name = stem[: max_len - len(ext)] + ext
    return name


def dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def free_bytes(path: Path) -> int:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free


def remove_quietly(*paths: Optional[os.PathLike]) -> None:
    for p in paths:
        if not p:
            continue
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def find_sevenzip(explicit: Optional[str] = None) -> Optional[str]:
    candidates = [explicit or "", "7za", "7z", "7zz", "/usr/bin/7za", "/usr/local/bin/7za"]
    for c in candidates:
        if not c:
            continue
        if os.path.isabs(c):
            if os.path.isfile(c) and os.access(c, os.X_OK):
                return c
        else:
            found = shutil.which(c)
            if found:
                return found
    return None


@dataclass
class SplitResult:
    parts: List[Path]
    method: str  # "7z" or "raw"


async def _run_cancellable(cmd: List[str], cwd: Path, cancel: Optional[asyncio.Event]) -> int:
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(cwd), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    try:
        while True:
            try:
                await asyncio.wait_for(proc.wait(), timeout=1)
                break
            except asyncio.TimeoutError:
                if cancel is not None and cancel.is_set():
                    proc.kill()
                    await proc.wait()
                    raise JobCancelled("cancelled")
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.kill()
        raise
    return proc.returncode


def _raw_split(src: Path, out_dir: Path, part_bytes: int) -> List[Path]:
    parts: List[Path] = []
    with open(src, "rb") as fh:
        index = 1
        while True:
            chunk_left = part_bytes
            out = out_dir / f"{src.name}.part{index:03d}"
            written = 0
            with open(out, "wb") as dst:
                while chunk_left > 0:
                    data = fh.read(min(1024 * 1024, chunk_left))
                    if not data:
                        break
                    dst.write(data)
                    written += len(data)
                    chunk_left -= len(data)
            if written == 0:
                out.unlink(missing_ok=True)
                break
            parts.append(out)
            index += 1
    return parts


async def split_file(
    src: Path,
    out_dir: Path,
    part_size_mb: int,
    sevenzip_bin: Optional[str],
    cancel: Optional[asyncio.Event] = None,
) -> SplitResult:
    """Split ``src`` into <= part_size_mb parts.

    Uses ``7z a -mx=0 -vNm`` exactly like telegram-to-rubika (store-only, so no recompression);
    falls back to a plain binary split when 7-Zip is not installed.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if sevenzip_bin:
        base = out_dir / (src.name + ".7z")
        for old in glob.glob(glob.escape(str(base)) + "*"):
            remove_quietly(old)
        cmd = [sevenzip_bin, "a", "-mx=0", "-bsp0", "-bso0", f"-v{part_size_mb}m", str(base), "--", src.name]
        rc = await _run_cancellable(cmd, src.parent, cancel)
        parts = sorted(Path(p) for p in glob.glob(glob.escape(str(base)) + ".*"))
        if not parts and base.exists():
            parts = [base]
        if rc not in (0, 1) or not parts:
            for p in parts:
                remove_quietly(p)
            raise DownloadError("7-Zip failed to split the file")
        return SplitResult(parts, "7z")
    loop = asyncio.get_running_loop()
    parts = await loop.run_in_executor(None, _raw_split, src, out_dir, part_size_mb * 1024 * 1024)
    if not parts:
        raise DownloadError("could not split the file")
    return SplitResult(parts, "raw")
