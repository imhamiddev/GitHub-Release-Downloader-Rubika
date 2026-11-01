import json

import pytest

from config import Config
from errors import InvalidInput
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
from tests.conftest import no_sleep
from transfers.downloader import Downloader
from transfers.manager import JobManager

RELEASE = {
    "id": 9, "tag_name": "2.3.14", "name": "2.3.14", "prerelease": False, "draft": False,
    "published_at": "2026-10-28T03:06:00Z", "author": {"login": "bot"}, "body": "* fix crash\n* new icon",
    "assets": [
        {"id": 1, "name": "app_2.3.14_arm64.apk", "size": 2048, "state": "uploaded"},
        {"id": 2, "name": "app_2.3.14_arm64.apk.sig", "size": 171, "state": "uploaded"},
        {"id": 3, "name": "app_2.3.14_x86.apk", "size": 4096, "state": "uploaded"},
    ],
}


def jr(data):
    return HttpResponse(200, {}, json.dumps(data).encode())


async def build(tmp_path, transport, messenger):
    cfg = Config.from_env({"DOWNLOAD_DIR": str(tmp_path / "dl"), "DATABASE_PATH": str(tmp_path / "t.db")}, load_dotenv=False)
    db = Database(cfg.database_path)
    await db.init()
    gh = GitHubClient(transport, None, TTLCache(300), sleep=no_sleep)
    chat = SavedMessages(messenger)
    jobs = JobManager(cfg, db, Downloader(gh), messenger, chat, sleep=no_sleep)
    ctx = Context(cfg, db, gh, RepositoryService(gh), ReleaseService(gh), jobs, chat)
    handler = CommandHandler(ctx)

    async def say(text, n=[0]):
        n[0] += 1
        await handler.handle(IncomingMessage(f"v{n[0]}", "me-guid", "me-guid", text))

    return ctx, say


@pytest.mark.anyio
async def test_signatures_hidden_by_default_and_toggled_with_extras(tmp_path, transport, messenger):
    ctx, say = await build(tmp_path, transport, messenger)
    transport.add_response(jr(RELEASE))
    await say("/latest o/r")
    clean = messenger.texts[-1]
    assert ".sig" not in clean.replace("signature", "") and "1 signature/checksum" in clean
    assert [e.name for e in ctx.nav.listing if not e.is_source] == ["app_2.3.14_arm64.apk", "app_2.3.14_x86.apk"]

    await say("/extras")
    full = messenger.texts[-1]
    assert "arm64.apk.sig" in full and "/extras to hide" in full
    assert len([e for e in ctx.nav.listing if not e.is_source]) == 3

    await say("/extras")
    assert "arm64.apk.sig" not in messenger.texts[-1].replace("signature", "")


@pytest.mark.anyio
async def test_notes_command_shows_changelog_on_demand(tmp_path, transport, messenger):
    ctx, say = await build(tmp_path, transport, messenger)
    transport.add_response(jr(RELEASE))
    await say("/latest o/r")
    assert "fix crash" not in messenger.texts[-1]  # not in the default view
    await say("/notes")
    assert "fix crash" in messenger.texts[-1] and "changelog" in messenger.texts[-1]


@pytest.mark.anyio
async def test_notes_and_extras_need_an_open_release(tmp_path, transport, messenger):
    ctx, say = await build(tmp_path, transport, messenger)
    await say("/notes")
    await say("/extras")
    assert len(messenger.texts) == 2 and all(t.startswith("⚠️") for t in messenger.texts)
