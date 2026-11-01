import asyncio
import hashlib
import json
from dataclasses import replace

import pytest

from config import Config
from github.assets import AssetEntry
from github.client import GitHubClient
from github.repository import RepoRef
from rubika.saved_messages import SavedMessages
from storage.cache import TTLCache
from storage.database import Database
from pathlib import Path

from tests.conftest import FakeStream, caption_password, is_random_name, no_sleep, unzip_all
from transfers.downloader import Downloader
from transfers.manager import JobManager
from transfers.recovery import recover_jobs

REF = RepoRef("o", "r")
DATA = b"hello release " * 50


def cfg(tmp_path, **over):
    base = Config.from_env({"DOWNLOAD_DIR": str(tmp_path / "dl"), "DATABASE_PATH": str(tmp_path / "t.db"),
                            "MAX_DOWNLOAD_SIZE_MB": "50", "SEND_DELAY_SECONDS": "0", "MAX_RETRIES": "2"},
                           load_dotenv=False)
    return replace(base, **over)


def entry(digest=None, size=len(DATA), key="7"):
    return AssetEntry(1, key, "app.apk", size, "application/x", 3, True, digest,
                      f"https://api.github.com/repos/o/r/releases/assets/{key}")


async def build(tmp_path, transport, messenger, **over):
    c = cfg(tmp_path, **over)
    db = Database(c.database_path)
    await db.init()
    gh = GitHubClient(transport, None, TTLCache(1), sleep=no_sleep)
    mgr = JobManager(c, db, Downloader(gh, 1, 0.0, no_sleep), messenger, SavedMessages(messenger),
                     sleep=no_sleep)
    return c, db, mgr


def stream(data=DATA):
    return FakeStream(200, {"content-length": str(len(data))}, [data])


@pytest.mark.anyio
async def test_full_flow_delivers_original_file_and_cleans_up(tmp_path, transport, messenger):
    c, db, mgr = await build(tmp_path, transport, messenger)
    transport.add_stream(stream())
    digest = "sha256:" + hashlib.sha256(DATA).hexdigest()
    job, created = await mgr.submit(REF, "v1", entry(digest))
    assert created
    await mgr.wait(job.id)
    j = await db.get_job(job.id)
    assert j.status == "done"
    assert len(messenger.files) == 1
    path, caption, blob = messenger.files[0]
    inner = unzip_all([blob])  # always a ZIP, and the original bytes are inside
    assert list(inner.values()) == [DATA]
    assert Path(path).name.endswith(".zip") and is_random_name(Path(path).name)
    (member,) = inner
    assert is_random_name(member) and "app" not in member and "apk" not in member
    assert j.alias == member
    assert "app" not in caption and "o/r" not in caption and "v1" not in caption and hashlib.sha256(DATA).hexdigest() not in caption
    assert not (c.download_dir / f"job-{job.id}").exists()


@pytest.mark.anyio
async def test_digest_mismatch_fails_and_sends_nothing(tmp_path, transport, messenger):
    _, db, mgr = await build(tmp_path, transport, messenger)
    transport.add_stream(stream())
    job, _ = await mgr.submit(REF, "v1", entry("sha256:" + "0" * 64))
    await mgr.wait(job.id)
    j = await db.get_job(job.id)
    assert j.status == "failed" and "SHA-256" in j.error
    assert messenger.files == []


@pytest.mark.anyio
async def test_size_mismatch_fails(tmp_path, transport, messenger):
    _, db, mgr = await build(tmp_path, transport, messenger)
    transport.add_stream(stream())
    job, _ = await mgr.submit(REF, "v1", entry(size=len(DATA) + 1))
    await mgr.wait(job.id)
    assert (await db.get_job(job.id)).status == "failed"


@pytest.mark.anyio
async def test_duplicate_active_job_not_queued_twice(tmp_path, transport, messenger):
    _, db, mgr = await build(tmp_path, transport, messenger)
    transport.add_stream(stream())
    j1, c1 = await mgr.submit(REF, "v1", entry())
    j2, c2 = await mgr.submit(REF, "v1", entry())
    assert c1 and not c2 and j1.id == j2.id
    await mgr.wait(j1.id)


@pytest.mark.anyio
async def test_too_large_rejected_before_queueing(tmp_path, transport, messenger):
    from errors import TooLarge

    _, _, mgr = await build(tmp_path, transport, messenger)
    with pytest.raises(TooLarge):
        await mgr.submit(REF, "v1", entry(size=60 * 1024 * 1024))


@pytest.mark.anyio
async def test_large_file_is_split_and_every_part_sent_in_order(tmp_path, transport, messenger):
    big = b"".join(bytes([i % 251]) * 1024 for i in range(2500))  # ~2.4 MB
    _, db, mgr = await build(tmp_path, transport, messenger, split_threshold_mb=1, part_size_mb=1)
    transport.add_stream(stream(big))
    job, _ = await mgr.submit(REF, "v1", entry(size=len(big)))
    await mgr.wait(job.id)
    j = await db.get_job(job.id)
    assert j.status == "done" and j.split_method == "zip-volumes"
    assert len(messenger.files) == 3
    names = [Path(f[0]).name for f in messenger.files]
    base = names[0][:-4]
    assert names == [f"{base}.001", f"{base}.002", f"{base}.003"] and all(is_random_name(n) for n in names)
    assert list(unzip_all([f[2] for f in messenger.files]).values()) == [big]  # volumes join into one ZIP
    assert all(len(f[2]) <= 1024 * 1024 for f in messenger.files)
    assert "part 1/3" in messenger.files[0][1] and "part 3/3" in messenger.files[2][1]


@pytest.mark.anyio
async def test_upload_retried_without_duplicates(tmp_path, transport, messenger):
    _, db, mgr = await build(tmp_path, transport, messenger)
    messenger.fail_files = 2
    transport.add_stream(stream())
    job, _ = await mgr.submit(REF, "v1", entry())
    await mgr.wait(job.id)
    assert (await db.get_job(job.id)).status == "done"
    assert len(messenger.files) == 1


@pytest.mark.anyio
async def test_upload_gives_up_after_max_retries(tmp_path, transport, messenger):
    _, db, mgr = await build(tmp_path, transport, messenger)
    messenger.fail_files = 99
    transport.add_stream(stream())
    job, _ = await mgr.submit(REF, "v1", entry())
    await mgr.wait(job.id)
    j = await db.get_job(job.id)
    assert j.status == "failed" and messenger.files == []
    messenger.fail_files = 0  # /retry resumes from the file already on disk (no new download)
    await mgr.retry(job.id)
    await mgr.wait(job.id)
    assert (await db.get_job(job.id)).status == "done" and len(messenger.files) == 1
    assert list(unzip_all([messenger.files[0][2]]).values()) == [DATA]


@pytest.mark.anyio
async def test_cancel_before_start_cleans_up(tmp_path, transport, messenger):
    c, db, mgr = await build(tmp_path, transport, messenger)
    transport.add_stream(stream())
    job, _ = await mgr.submit(REF, "v1", entry())
    assert await mgr.cancel(job.id)
    await mgr.wait(job.id)
    assert (await db.get_job(job.id)).status == "cancelled"
    assert messenger.files == [] and not (c.download_dir / f"job-{job.id}").exists()


@pytest.mark.anyio
async def test_recovery_resumes_without_resending_delivered_parts(tmp_path, transport, messenger):
    c, db, mgr = await build(tmp_path, transport, messenger)
    d = c.download_dir / "job-1" / "parts"
    d.mkdir(parents=True)
    (d / "p1").write_bytes(b"AAA")  # part 1 was delivered earlier and deleted; part 2 remains
    (d / "p2").write_bytes(b"BBB")
    job, _ = await db.create_job(repo="o/r", tag="v1", asset_key="7", name="app.apk", size=6,
                                 url="https://api.github.com/repos/o/r/releases/assets/7", digest=None)
    await db.update_job(job.id, status="sending", split_method="raw", parts_sent=1, local_path=None,
                        parts_json=json.dumps([{"path": str(d / "p1"), "size": 3}, {"path": str(d / "p2"), "size": 3}]))
    (d / "p1").unlink()
    report = await recover_jobs(db, mgr)
    assert report.resumed == [job.id]
    await mgr.wait(job.id)
    assert [f[2] for f in messenger.files] == [b"BBB"]  # recovery sends what is left, as-is
    assert (await db.get_job(job.id)).status == "done"


@pytest.mark.anyio
async def test_recovery_never_auto_resends_inflight_part(tmp_path, transport, messenger):
    c, db, mgr = await build(tmp_path, transport, messenger)
    f = c.download_dir / "job-1" / "app.apk"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"DATA")
    job, _ = await db.create_job(repo="o/r", tag="v1", asset_key="7", name="app.apk", size=4, url="https://api.github.com/x", digest=None)
    await db.update_job(job.id, status="sending", send_inflight=0, parts_sent=0, local_path=str(f),
                        parts_json=json.dumps([{"path": str(f), "size": 4}]))
    report = await recover_jobs(db, mgr)
    assert report.needs_decision == [job.id] and messenger.files == []
    assert (await db.get_job(job.id)).status == "failed"
    assert str(job.id) in report.text()


@pytest.mark.anyio
async def test_recovery_restarts_interrupted_download(tmp_path, transport, messenger):
    c, db, mgr = await build(tmp_path, transport, messenger)
    job, _ = await db.create_job(repo="o/r", tag="v1", asset_key="7", name="app.apk", size=len(DATA),
                                 url="https://api.github.com/repos/o/r/releases/assets/7", digest=None)
    await db.update_job(job.id, status="downloading")
    transport.add_stream(stream())
    await recover_jobs(db, mgr)
    await mgr.wait(job.id)
    assert (await db.get_job(job.id)).status == "done" and len(messenger.files) == 1
    assert list(unzip_all([messenger.files[0][2]]).values()) == [DATA]
