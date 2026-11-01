"""Filesystem helpers: safe names, disk usage, atomic writes and file splitting."""
from __future__ import annotations

import os
import re
import secrets
import shutil
import string
import zipfile
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
class VolumeWriter:
    """Write-only, non-seekable stream that rolls over into ``<base>.001``, ``.002`` ... volumes.

    Handing it to ``zipfile.ZipFile`` produces a normal ZIP that is already cut into raw
    volumes, which 7-Zip / WinRAR open from the ``.001`` file. Peak disk use is the volumes only.
    """

    def __init__(self, base: Path, part_bytes: Optional[int], cancel=None):
        self.base, self.part_bytes, self._cancel = base, part_bytes, cancel
        self.paths: List[Path] = []
        self._fh = None
        self._in_part = 0
        self._pos = 0

    def _open_next(self) -> None:
        if self._fh:
            self._fh.close()
        path = self.base if self.part_bytes is None else self.base.with_name(f"{self.base.name}.{len(self.paths) + 1:03d}")
        self._fh = open(path, "wb")
        self.paths.append(path)
        self._in_part = 0

    def write(self, data) -> int:
        if self._cancel is not None and self._cancel.is_set():
            raise JobCancelled("cancelled")
        view = memoryview(data)
        total = len(view)
        offset = 0
        while offset < total:
            if self._fh is None or (self.part_bytes is not None and self._in_part >= self.part_bytes):
                self._open_next()
            room = (total - offset) if self.part_bytes is None else min(total - offset, self.part_bytes - self._in_part)
            self._fh.write(view[offset : offset + room])
            self._in_part += room
            offset += room
        self._pos += total
        return total

    def tell(self) -> int:
        return self._pos

    def flush(self) -> None:
        if self._fh:
            self._fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._fh.close()
            self._fh = None


def zip_to_volumes(
    src: Path, out_dir: Path, zip_base: str, arcname: str, part_bytes: Optional[int], cancel=None
) -> List[Path]:
    """Store ``src`` (no recompression) in a ZIP named ``<zip_base>.zip``, split into volumes if asked.

    Returns the files to upload, in order: ``[x.zip]`` or ``[x.zip.001, x.zip.002, ...]``.
    Runs blocking I/O: call it through ``asyncio.to_thread``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / f"{zip_base}.zip"
    writer = VolumeWriter(base, part_bytes, cancel)
    try:
        with zipfile.ZipFile(writer, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            zf.write(src, arcname=arcname, compress_type=zipfile.ZIP_STORED)
        writer.close()
    except BaseException:
        writer.close()
        for p in writer.paths:
            remove_quietly(p)
        raise
    paths = writer.paths
    if part_bytes is not None and len(paths) == 1:  # fit in one volume: plain .zip
        paths[0].rename(base)
        paths = [base]
    return paths
