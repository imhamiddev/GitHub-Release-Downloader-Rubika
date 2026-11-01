import asyncio
import hashlib

import pytest

from errors import DownloadError, IntegrityError, JobCancelled, TooLarge
from github.client import GitHubClient
from storage.cache import TTLCache
from tests.conftest import FakeStream, no_sleep
from transfers.downloader import Downloader
from transfers.verifier import sha256_file, verify_sha256, verify_size

URL = "https://api.github.com/repos/o/r/releases/assets/1"
DATA = b"0123456789" * 100


def mk(transport, retries=3):
    return Downloader(GitHubClient(transport, None, TTLCache(1), sleep=no_sleep), retries, 0.01, no_sleep)


def ok_stream(data=DATA, status=200, extra=None):
    h = {"content-length": str(len(data))}
    h.update(extra or {})
    return FakeStream(status, h, [data[i:i + 100] for i in range(0, len(data), 100)])


@pytest.mark.anyio
async def test_streams_to_disk_and_hashes(transport, tmp_path):
    transport.add_stream(ok_stream())
    seen = []
    res = await mk(transport).download(URL, tmp_path / "f.bin", expected_size=len(DATA), max_bytes=10**6,
                                       progress=lambda d, t: seen.append((d, t)))
    assert (tmp_path / "f.bin").read_bytes() == DATA
    assert res.sha256 == hashlib.sha256(DATA).hexdigest() == await sha256_file(res.path)
    assert seen[-1] == (len(DATA), len(DATA))
    assert not (tmp_path / "f.bin.part").exists()


@pytest.mark.anyio
async def test_resumes_after_dropped_connection(transport, tmp_path):
    transport.add_stream(FakeStream(200, {"content-length": str(len(DATA))},
                                    [DATA[i:i + 100] for i in range(0, len(DATA), 100)], fail_after=4))
    rest = DATA[400:]
    transport.add_stream(FakeStream(206, {"content-length": str(len(rest)),
                                          "content-range": f"bytes 400-{len(DATA)-1}/{len(DATA)}"},
                                    [rest[i:i + 100] for i in range(0, len(rest), 100)]))
    res = await mk(transport).download(URL, tmp_path / "f.bin", expected_size=len(DATA), max_bytes=10**6)
    assert (tmp_path / "f.bin").read_bytes() == DATA
    assert res.sha256 == hashlib.sha256(DATA).hexdigest()
    assert transport.streams[1][1]["Range"] == "bytes=400-"


@pytest.mark.anyio
async def test_gives_up_after_retries(transport, tmp_path):
    for _ in range(3):
        transport.add_stream(FakeStream(200, {"content-length": "1000"}, [b"x" * 10], fail_after=0))
    with pytest.raises(DownloadError):
        await mk(transport, retries=2).download(URL, tmp_path / "f", expected_size=None, max_bytes=10**6)


@pytest.mark.anyio
async def test_size_limit_enforced_upfront_and_while_streaming(transport, tmp_path):
    with pytest.raises(TooLarge):
        await mk(transport).download(URL, tmp_path / "a", expected_size=5000, max_bytes=1000)
    transport.add_stream(FakeStream(200, {}, [b"x" * 600, b"x" * 600]))  # no content-length
    with pytest.raises(TooLarge):
        await mk(transport).download(URL, tmp_path / "b", expected_size=None, max_bytes=1000)


@pytest.mark.anyio
async def test_cancel_stops_download(transport, tmp_path):
    ev = asyncio.Event()
    ev.set()
    transport.add_stream(ok_stream())
    with pytest.raises(JobCancelled):
        await mk(transport).download(URL, tmp_path / "f", expected_size=len(DATA), max_bytes=10**6, cancel=ev)


def test_verification_rules():
    verify_size(10, 10)
    verify_size(10, None)
    with pytest.raises(IntegrityError):
        verify_size(9, 10)
    assert verify_sha256("ab", "AB") is True
    assert verify_sha256("ab", None) is False
    with pytest.raises(IntegrityError):
        verify_sha256("ab", "cd")
