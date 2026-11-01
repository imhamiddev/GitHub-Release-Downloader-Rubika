"""Entry point.

    python main.py login   # one time: phone number + SMS code, creates the session file
    python main.py         # run the service (no prompts once logged in)
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from config import Config
from errors import BridgeError
from github.client import GitHubClient
from github.releases import ReleaseService
from github.repository import RepositoryService
from github.transport import HttpxTransport
from handlers.command_handler import CommandHandler
from handlers.context import Context
from rubika.authentication import interactive_login
from rubika.client import RubpyMessenger
from rubika.message_listener import MessageListener
from rubika.saved_messages import SavedMessages
from storage.cache import TTLCache
from storage.database import Database
from transfers.downloader import Downloader
from transfers.manager import JobManager
from transfers.recovery import recover_jobs
from utils.file_utils import find_sevenzip
from utils.logging_utils import setup_logging

log = logging.getLogger("github-bridge")


async def run(cfg: Config) -> None:
    cfg.download_dir.mkdir(parents=True, exist_ok=True)
    db = Database(cfg.database_path)
    await db.init()
    await db.purge_old_messages()

    transport = HttpxTransport()
    github = GitHubClient(transport, cfg.github_token, TTLCache(cfg.cache_ttl))
    messenger = RubpyMessenger(cfg)
    chat = SavedMessages(messenger)
    sevenzip = find_sevenzip(cfg.sevenzip_bin)
    if sevenzip is None:
        log.warning("7-Zip not found; files above %s MB will use a plain binary split", cfg.split_threshold_mb)
    jobs = JobManager(cfg, db, Downloader(github, cfg.max_retries), messenger, chat, sevenzip)
    ctx = Context(cfg, db, github, RepositoryService(github), ReleaseService(github), jobs, chat)
    listener = MessageListener(messenger, db, CommandHandler(ctx).handle)

    async def on_connected() -> None:
        report = await recover_jobs(db, jobs)
        if report.text():
            await chat.reply(report.text())

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass

    runner = asyncio.create_task(messenger.run_forever(listener.handle, on_connected))
    waiter = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({runner, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if runner.done():
            runner.result()
    finally:
        log.info("shutting down")
        await jobs.shutdown()  # keep job rows so the next start can recover them
        await messenger.stop()
        runner.cancel()
        waiter.cancel()
        await asyncio.gather(runner, waiter, return_exceptions=True)
        await transport.aclose()
        await db.close()


def main(argv: list[str]) -> int:
    cfg = Config.from_env()
    setup_logging(cfg.log_level, [cfg.github_token or "", cfg.rubika_phone])
    try:
        if len(argv) > 1 and argv[1] == "login":
            guid = asyncio.run(interactive_login(cfg))
            print(f"Logged in. Session saved (account {guid[:6]}…). Now run: python main.py")
            return 0
        asyncio.run(run(cfg))
        return 0
    except BridgeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
