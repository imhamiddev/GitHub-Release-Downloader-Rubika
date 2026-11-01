"""Filesystem helpers: safe names, disk usage, atomic writes and file splitting."""
from __future__ import annotations

import os
import re
import secrets
import shutil
import string
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


def random_name(nbytes: int = 10) -> str:
    """Unguessable, meaningless name (20 hex chars) used for the downloaded file and the archive."""
    return secrets.token_hex(nbytes)


def random_password(length: int = 16) -> str:
    """Random letters+digits only (safe to copy, no characters that Rubika markdown would eat)."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def create_encrypted_zip(src: Path, zip_path: Path, arcname: str, password: str, cancel=None) -> Path:
    """Store ``src`` (no recompression) in an AES-256 encrypted ZIP at ``zip_path``.

    Uses pyzipper (WinZip AES-256, opened by 7-Zip, WinRAR, ZArchiver...). Blocking I/O: call it
    through ``asyncio.to_thread``. A partial file is removed on any failure.
    """
    try:
        import pyzipper
    except ImportError:
        raise DownloadError("pyzipper is not installed. Run: pip install -r requirements.txt") from None
    if not password:
        raise DownloadError("internal error: no archive password")
    if cancel is not None and cancel.is_set():
        raise JobCancelled("cancelled")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with pyzipper.AESZipFile(zip_path, "w", compression=pyzipper.ZIP_STORED, encryption=pyzipper.WZ_AES) as zf:
            zf.setpassword(password.encode("utf-8"))
            zf.setencryption(pyzipper.WZ_AES, nbits=256)
            zf.write(src, arcname=arcname)
    except BaseException:
        remove_quietly(zip_path)
        raise
    return zip_path


def split_into_volumes(zip_path: Path, part_bytes: int, cancel=None, chunk: int = 1024 * 1024) -> List[Path]:
    """Cut a finished ZIP into ``<name>.zip.001``, ``.002`` ... raw volumes (7-Zip/WinRAR open ``.001``).

    The ZIP is deleted once every volume is written; if it already fits in one volume it is kept
    as a plain ``.zip``. Blocking I/O: call it through ``asyncio.to_thread``.
    """
    if zip_path.stat().st_size <= part_bytes:
        return [zip_path]
    volumes: List[Path] = []
    try:
        with open(zip_path, "rb") as fin:
            while True:
                first = fin.read(min(chunk, part_bytes))
                if not first:
                    break
                vol = zip_path.with_name(f"{zip_path.name}.{len(volumes) + 1:03d}")
                volumes.append(vol)
                with open(vol, "wb") as fout:
                    written = fout.write(first)
                    while written < part_bytes:
                        if cancel is not None and cancel.is_set():
                            raise JobCancelled("cancelled")
                        data = fin.read(min(chunk, part_bytes - written))
                        if not data:
                            break
                        written += fout.write(data)
                    fout.flush()
                    os.fsync(fout.fileno())
    except BaseException:
        for v in volumes:
            remove_quietly(v)
        raise
    remove_quietly(zip_path)
    return volumes
