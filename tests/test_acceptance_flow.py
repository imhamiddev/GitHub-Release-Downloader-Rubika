"""The acceptance scenario: /get -> browse -> open -> asset list -> pick -> file arrives."""
import json

import pytest

from config import Config
from github.client import GitHubClient
from github.releases import ReleaseService
from github.repository import RepositoryService
from github.transport import HttpResponse
from handlers.command_handler import CommandHandler
from handlers.context import Context
from rubika.message_listener import IncomingMessage
from rubika.saved_messages import SavedMessages
from storage.cache import TTLCache
from storage.database import Database
from tests.conftest import FakeStream, caption_password, no_sleep, unzip_all
from transfers.downloader import Downloader
from transfers.manager import JobManager

APK = b"APKDATA" * 100


def jr(data, headers=None):
    return HttpResponse(200, {k.lower(): v for k, v in (headers or {}).items()}, json.dumps(data).encode())


RELEASE = {"id": 9, "tag_name": "2.0.0", "name": "v2.0", "prerelease": False, "draft": False,
           "published_at": "2025-05-05T10:00:00Z", "author": {"login": "dust"}, "body": "**bold** changelog",
           "assets": [{"id": 55, "name": "v2rayNG_2.0.0.apk", "size": len(APK), "download_count": 12,
                       "content_type": "application/vnd.android.package-archive", "state": "uploaded", "digest": None}]}


@pytest.mark.anyio
async def test_get_browse_open_asset_download(tmp_path, transport, messenger):
    cfg = Config.from_env({"DOWNLOAD_DIR": str(tmp_path / "dl"), "DATABASE_PATH": str(tmp_path / "t.db"),
                           "SEND_DELAY_SECONDS": "0"}, load_dotenv=False)
    db = Database(cfg.database_path)
    await db.init()
    gh = GitHubClient(transport, None, TTLCache(300), sleep=no_sleep)
    chat = SavedMessages(messenger)
    jobs = JobManager(cfg, db, Downloader(gh, 1, 0, no_sleep), messenger, chat, sleep=no_sleep)
    ctx = Context(cfg, db, gh, RepositoryService(gh), ReleaseService(gh), jobs, chat)
    h = CommandHandler(ctx)

    async def say(text, n=[0]):
        n[0] += 1
        await h.handle(IncomingMessage(f"c{n[0]}", "me-guid", "me-guid", text))

    # /get -> repository info, then the first release page
    transport.add_response(jr({"name": "v2rayNG", "full_name": "2dust/v2rayNG", "owner": {"login": "2dust"},
                               "description": "A V2Ray client", "stargazers_count": 1234, "forks_count": 56,
                               "language": "Kotlin", "default_branch": "master",
                               "html_url": "https://github.com/2dust/v2rayNG"}))
    transport.add_response(jr([RELEASE]))
    transport.add_response(jr({"id": 9}))
    await say("/get https://github.com/2dust/v2rayNG")
    assert len(messenger.texts) == 1  # repo info and releases arrive as ONE message
    info = page = messenger.texts[0]
    assert "2dust/v2rayNG" in info and "1.2k" in info and "Kotlin" in info
    assert "master" not in info and "Forks" not in info and "github.com" not in info  # noise is gone
    assert "2.0.0" in page and "⭐" in page and "/open N" in page
    assert "👤" not in page and "assets" not in page and "UTC" not in page  # author, asset count, clock time

    # /open 1 -> release detail with the complete asset list
    await say("/open 1")
    detail = messenger.texts[1]
    assert "v2rayNG_2.0.0.apk" in detail and "700 B" in detail and "/asset zip" in detail
    assert "ID 55" not in detail and "changelog" not in detail.lower()  # no ids, no changelog wall
    assert "**bold**" not in detail  # markdown neutralised

    # /asset 1 -> the real file is delivered
    transport.add_stream(FakeStream(302, {"location": "https://objects.githubusercontent.com/x"}))
    transport.add_stream(FakeStream(200, {"content-length": str(len(APK))}, [APK]))
    await say("/asset 1")
    await jobs.wait(1)
    pw = caption_password(messenger.files[0][1])
    assert pw and list(unzip_all([messenger.files[0][2]], pw).values()) == [APK]
    assert pw not in "\n".join(messenger.texts)  # the password goes only in the file caption
    assert "v2rayNG" not in messenger.files[0][0]
    assert "delivered" in (messenger.edits[-1][1] if messenger.edits else messenger.texts[-1])

    await say("/downloads")
    assert "#1" in messenger.texts[-1] and "done" in messenger.texts[-1]
    await say("/status")
    assert "running" in messenger.texts[-1]


@pytest.mark.anyio
async def test_errors_are_reported_not_raised(tmp_path, transport, messenger):
    cfg = Config.from_env({"DOWNLOAD_DIR": str(tmp_path / "dl"), "DATABASE_PATH": str(tmp_path / "t.db")}, load_dotenv=False)
    db = Database(cfg.database_path)
    await db.init()
    gh = GitHubClient(transport, None, TTLCache(300), sleep=no_sleep)
    chat = SavedMessages(messenger)
    jobs = JobManager(cfg, db, Downloader(gh), messenger, chat, sleep=no_sleep)
    h = CommandHandler(Context(cfg, db, gh, RepositoryService(gh), ReleaseService(gh), jobs, chat))
    for i, text in enumerate(["/get https://evil.com/a/b", "/asset 1", "/open 3", "/nope", "/page x", "/back"]):
        await h.handle(IncomingMessage(f"e{i}", "me-guid", "me-guid", text))
    assert len(messenger.texts) == 6 and all(t.startswith(("⚠️", "❓")) for t in messenger.texts)
