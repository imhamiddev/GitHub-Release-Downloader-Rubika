"""Job manager: queue, concurrency, progress, split, deliver to Saved Messages, cancel, retry.

Pipeline per job:  queued -> downloading -> verifying -> (splitting) -> sending -> done
Every transition is written to SQLite first, so a restart can resume without re-sending parts
that were already delivered (see transfers/recovery.py).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Callable, Dict, Optional

from config import Config
from errors import (
    BridgeError,
    DownloadError,
    IntegrityError,
    JobCancelled,
    QuotaExceeded,
    SendError,
    TooLarge,
)
from github.assets import AssetEntry, parse_digest
from github.repository import RepoRef
from rubika.saved_messages import Messenger, SavedMessages
from storage.database import Database, Job
from transfers.downloader import Downloader
from transfers.verifier import verify_sha256, verify_size
from utils import formatting as fmt
from utils.file_utils import (
    create_encrypted_zip,
    dir_size,
    free_bytes,
    random_name,
    random_password,
    remove_quietly,
    split_into_volumes,
)

log = logging.getLogger(__name__)

PROGRESS_INTERVAL = 4.0  # seconds between status-message edits


class JobManager:
    def __init__(
        self,
        cfg: Config,
        db: Database,
        downloader: Downloader,
        messenger: Messenger,
        chat: SavedMessages,
        sleep: Callable[[float], "asyncio.Future"] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.cfg, self.db, self.downloader = cfg, db, downloader
        self.messenger, self.chat = messenger, chat
        self._sleep, self._clock = sleep, clock
        self._download_sem = asyncio.Semaphore(cfg.max_concurrent_downloads)
        self._send_lock = asyncio.Lock()  # one Rubika upload at a time
        self._tasks: Dict[int, asyncio.Task] = {}
        self._cancel: Dict[int, asyncio.Event] = {}
        self._status_msg: Dict[int, Optional[str]] = {}
        self._last_edit: Dict[int, float] = {}
        self._edit_broken: set = set()  # jobs whose status message Rubika refused to edit

    # ---- helpers ---------------------------------------------------------------
    def job_dir(self, job_id: int) -> Path:
        return self.cfg.download_dir / f"job-{job_id}"

    def is_running(self, job_id: int) -> bool:
        t = self._tasks.get(job_id)
        return bool(t and not t.done())

    async def _status(self, job: Job, text: str, force: bool = False, final: bool = False) -> None:
        """Keep ONE status message per job and edit it in place.

        If Rubika will not edit it, later progress is dropped instead of flooding the chat with a
        new message per update; only a ``final`` update (done / failed / cancelled) is sent as new.
        """
        now = self._clock()
        if not (force or final) and now - self._last_edit.get(job.id, 0.0) < PROGRESS_INTERVAL:
            return
        self._last_edit[job.id] = now
        mid = self._status_msg.get(job.id)
        if mid and job.id not in self._edit_broken:
            if await self.chat.edit(mid, text):
                return
            self._edit_broken.add(job.id)
            log.warning("job %s: Rubika refused to edit the status message; further progress will not be shown", job.id)
        if mid and not final:
            return
        self._status_msg[job.id] = await self.chat.reply(text)

    def _check_capacity(self, expected: Optional[int], factor: float = 2.1) -> None:
        cfg = self.cfg
        expected = expected or 0
        need = int(expected * factor)
        if free_bytes(cfg.download_dir) - need < cfg.min_free_mb * 1024 * 1024:
            raise QuotaExceeded("not enough free disk space for this download")
        if dir_size(cfg.download_dir) + need > cfg.max_storage_mb * 1024 * 1024:
            raise QuotaExceeded(f"storage quota of {cfg.max_storage_mb} MB would be exceeded")

    # ---- public API ------------------------------------------------------------
    async def submit(self, ref: RepoRef, tag: str, entry: AssetEntry) -> tuple[Job, bool]:
        """Queue one asset. Returns (job, created); created=False if the same asset is already active."""
        if not entry.available:
            raise DownloadError(f"{entry.name} is not available for download")
        if entry.size is not None and entry.size > self.cfg.max_download_bytes:
            raise TooLarge(
                f"{entry.name} is {fmt.format_size(entry.size)}, above the {self.cfg.max_download_size_mb} MB limit"
            )
        job, created = await self.db.create_job(
            repo=ref.full_name,
            tag=tag,
            asset_key=entry.key,
            name=entry.name,
            size=entry.size,
            url=entry.url,
            digest=entry.digest,
        )
        if created:
            self.start(job.id)
        return job, created

    def start(self, job_id: int) -> None:
        if self.is_running(job_id):
            return
        self._cancel[job_id] = asyncio.Event()
        self._status_msg.pop(job_id, None)  # every (re)start gets its own status message
        self._edit_broken.discard(job_id)
        task = asyncio.create_task(self._run(job_id), name=f"job-{job_id}")
        self._tasks[job_id] = task

    async def cancel(self, job_id: int) -> bool:
        job = await self.db.get_job(job_id)
        if job is None or not job.active:
            return False
        ev = self._cancel.get(job_id)
        if ev is not None and self.is_running(job_id):
            ev.set()  # the running task notices, cleans up and reports
            return True
        # not running in this process (e.g. waiting for /retry after a restart)
        await self.db.update_job(job_id, status="cancelled")
        remove_quietly(self.job_dir(job_id))
        return True

    async def cancel_all(self) -> int:
        count = 0
        for job in await self.db.jobs_with_status(("queued", "downloading", "verifying", "splitting", "sending")):
            if await self.cancel(job.id):
                count += 1
        return count

    async def retry(self, job_id: int) -> Job:
        job = await self.db.get_job(job_id)
        if job is None:
            raise BridgeError(f"No job #{job_id}.")
        if job.status not in ("failed", "cancelled") and not (job.active and not self.is_running(job_id)):
            raise BridgeError(f"Job #{job_id} is {job.status}; nothing to retry.")
        await self.db.update_job(job_id, status="queued", error=None, send_inflight=None)
        self.start(job_id)
        return await self.db.get_job(job_id)

    async def shutdown(self) -> None:
        """Stop tasks without touching job rows, so the next start can recover them."""
        for t in list(self._tasks.values()):
            t.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def wait(self, job_id: int) -> None:
        t = self._tasks.get(job_id)
        if t:
            await asyncio.gather(t, return_exceptions=True)

    # ---- pipeline -------------------------------------------------------------
    async def _run(self, job_id: int) -> None:
        cancel = self._cancel.setdefault(job_id, asyncio.Event())
        job = await self.db.get_job(job_id)
        try:
            if job is None:
                return
            stage = self._entry_stage(job)
            if stage == "download":
                await self._status(job, fmt.status_text("🕒", job, "Queued"), force=True)
                async with self._download_sem:
                    job = await self._download_stage(job, cancel)
                stage = "prepare"
            if stage == "prepare":
                job = await self._prepare_stage(job, cancel)
                stage = "send"
            if stage == "send":
                await self.db.update_job(job.id, status="sending")
                job = await self.db.get_job(job.id)
                async with self._send_lock:
                    job = await self._send_stage(job, cancel)
            await self._finish(job)
        except JobCancelled:
            await self._on_cancelled(job_id)
        except asyncio.CancelledError:
            raise  # shutdown: leave the row as-is for recovery
        except BridgeError as exc:
            await self._fail(job_id, str(exc))
        except Exception:
            log.exception("job %s crashed", job_id)
            await self._fail(job_id, "unexpected internal error (see server log)")
        finally:
            self._tasks.pop(job_id, None)

    @staticmethod
    def _entry_stage(job: Job) -> str:
        """Pick where to (re)start, based on what is already safely on disk."""
        parts = job.parts
        if parts and any(Path(p["path"]).exists() for p in parts[job.parts_sent :]):
            return "send"
        if parts and job.parts_sent >= len(parts):
            return "send"  # everything was already delivered; _send_stage is a no-op
        if job.local_path and Path(job.local_path).exists():
            return "prepare"
        return "download"

    async def _download_stage(self, job: Job, cancel: asyncio.Event) -> Job:
        if cancel.is_set():
            raise JobCancelled("cancelled")
        self._check_capacity(job.size)
        alias = job.alias or random_name()  # the file never exists on disk under its real name
        dest = self.job_dir(job.id) / alias
        await self.db.update_job(job.id, status="downloading", alias=alias)
        job = await self.db.get_job(job.id)
        await self._status(job, fmt.status_text("⬇️", job, "Starting…"), force=True)

        async def progress(done: int, total: Optional[int]) -> None:
            await self.db.update_job(job.id, bytes_done=done)
            await self._status(job, fmt.status_text("⬇️", job, fmt.progress_bar(done, total)))

        result = await self.downloader.download(
            job.url,
            dest,
            expected_size=job.size,
            max_bytes=self.cfg.max_download_bytes,
            progress=progress,
            cancel=cancel,
        )
        await self.db.update_job(job.id, status="verifying", bytes_done=result.size)
        verify_size(result.size, job.size)
        verified = verify_sha256(result.sha256, parse_digest(job.digest))
        log.info("job %s downloaded %s bytes, sha256 %s", job.id, result.size, "verified" if verified else "unverified")
        await self.db.update_job(job.id, local_path=str(result.path), sha256=result.sha256, status="splitting")
        return await self.db.get_job(job.id)

    async def _prepare_stage(self, job: Job, cancel: asyncio.Event) -> Job:
        """Always wrap the download in a password-protected ZIP with a random name; big files get volumes."""
        src = Path(job.local_path or "")
        if not src.exists():
            raise DownloadError("the downloaded file disappeared from disk; use /retry")
        size = src.stat().st_size
        split = size > self.cfg.split_threshold_mb * 1024 * 1024
        password = job.password or random_password()
        if not job.password:
            await self.db.update_job(job.id, password=password)  # keep it for captions after a restart
        await self._status(job, fmt.status_text("🗜", job, "Packing…"), force=True)
        self._check_capacity(size, 1.05)  # the original is already on disk; the ZIP needs one more copy
        parts_dir = self.job_dir(job.id) / "zip"
        remove_quietly(parts_dir)  # leftovers of an interrupted attempt
        zip_path = parts_dir / f"{random_name()}.zip"
        await asyncio.to_thread(create_encrypted_zip, src, zip_path, job.alias or src.name, password, cancel)
        if cancel.is_set():
            raise JobCancelled("cancelled")
        if split:
            remove_quietly(src)  # the ZIP now holds everything: keeps peak disk use at ~2x the file
            paths = await asyncio.to_thread(split_into_volumes, zip_path, self.cfg.part_size_mb * 1024 * 1024, cancel)
        else:
            paths = [zip_path]
        parts = [{"path": str(p), "size": p.stat().st_size} for p in paths]
        await self.db.update_job(
            job.id, parts_json=json.dumps(parts), parts_sent=0, send_inflight=None,
            split_method="zip-volumes" if len(parts) > 1 else "zip", status="sending",
        )
        remove_quietly(src)
        return await self.db.get_job(job.id)

    def _caption(self, job: Job, index: int, total: int) -> str:
        """No name, repo, tag or hash: a job number and the archive password (look the file up with /downloads)."""
        head = f"🗜 #{job.id}" + (f" · part {index + 1}/{total}" if total > 1 else "")
        lines = [head, f"🔑 password: `{job.password}`"]
        if total > 1 and index == 0:
            lines.append("ℹ️ put all parts in one folder and open the .001 file with 7-Zip/WinRAR")
        return "\n".join(lines)

    async def _send_stage(self, job: Job, cancel: asyncio.Event) -> Job:
        parts = job.parts
        total = len(parts)
        index = job.parts_sent
        while index < total:
            if cancel.is_set():
                raise JobCancelled("cancelled")
            path = Path(parts[index]["path"])
            if not path.exists():
                raise SendError(f"part {index + 1} is missing on disk; use /retry")
            what = f"Uploading part {index + 1}/{total}…" if total > 1 else "Uploading…"
            await self._status(job, fmt.status_text("📤", job, what), force=True)
            # Mark the part as in flight. If we crash now we cannot know whether it arrived, so
            # recovery asks the user instead of silently risking a duplicate file.
            await self.db.update_job(job.id, send_inflight=index)
            await self._send_with_retry(job, path, self._caption(job, index, total), index, total, cancel)
            await self.db.update_job(job.id, parts_sent=index + 1, send_inflight=None)
            remove_quietly(path)
            index += 1
            if index < total:
                await self._sleep(self.cfg.send_delay_seconds)
        return await self.db.get_job(job.id)

    async def _send_with_retry(self, job, path: Path, caption: str, index: int, total: int, cancel) -> None:
        attempt = 0
        while True:
            last_pct = -10

            async def progress(total_bytes: int, sent: int, _job=job) -> None:
                nonlocal last_pct
                pct = sent * 100 // max(total_bytes, 1)
                if pct - last_pct >= 10:
                    last_pct = pct
                    await self._status(
                        _job,
                        f"📤 #{_job.id} {fmt.md_safe(_job.name)}\nPart {index + 1}/{total}: {fmt.progress_bar(sent, total_bytes)}",
                    )

            upload = asyncio.create_task(self.messenger.send_file(str(path), caption, progress))
            try:
                while True:
                    done, _ = await asyncio.wait({upload}, timeout=1)
                    if done:
                        upload.result()
                        return
                    if cancel.is_set():
                        raise JobCancelled("cancelled")
            except JobCancelled:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempt += 1
                if attempt > self.cfg.max_retries:
                    raise SendError(f"upload failed after {self.cfg.max_retries} retries: {type(exc).__name__}") from None
                delay = min(5 * 2 ** (attempt - 1), 120)
                log.warning("upload of job %s part %s failed (%s); retry %s in %ss", job.id, index + 1, type(exc).__name__, attempt, delay)
                await self._status(job, f"⚠️ #{job.id} upload error, retry {attempt}/{self.cfg.max_retries} in {delay}s", force=True)
                await self._sleep(delay)
            finally:
                if not upload.done():
                    upload.cancel()
                    await asyncio.gather(upload, return_exceptions=True)

    async def _finish(self, job: Job) -> None:
        job = await self.db.get_job(job.id)
        await self.db.update_job(job.id, status="done", error=None, send_inflight=None, local_path=None)
        remove_quietly(self.job_dir(job.id))
        total = len(job.parts) or 1
        extra = f" in {total} parts" if total > 1 else ""
        first = Path(job.parts[0]["path"]).name if job.parts else "?"
        base = first.rsplit(".", 1)[0] + ".zip" if first.rsplit(".", 1)[-1].isdigit() else first
        await self._status(
            job,
            f"✅ #{job.id} delivered{extra}\n"
            f"📦 archive: {base}{'.001…' if total > 1 else ''}\n"
            f"📄 inside: {job.alias or '?'}  ⇒ original name: {fmt.md_safe(job.name)}",
            force=True,
        )
        self._status_msg.pop(job.id, None)

    async def _fail(self, job_id: int, reason: str) -> None:
        job = await self.db.get_job(job_id)
        await self.db.update_job(job_id, status="failed", error=reason)
        if job:
            await self._status(
                job, f"❌ #{job_id} {fmt.md_safe(job.name)} failed: {fmt.md_safe(reason)}\nUse /retry {job_id} to try again.", force=True
            )

    async def _on_cancelled(self, job_id: int) -> None:
        job = await self.db.get_job(job_id)
        await self.db.update_job(job_id, status="cancelled", error=None)
        remove_quietly(self.job_dir(job_id))
        if job:
            await self._status(job, f"🚫 #{job_id} {fmt.md_safe(job.name)} cancelled.", force=True)
