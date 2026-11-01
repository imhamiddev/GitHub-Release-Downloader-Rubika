import pytest

from rubika.message_listener import IncomingMessage, MessageListener
from storage.database import Database


async def make(tmp_path, messenger):
    db = Database(tmp_path / "t.db")
    await db.init()
    got = []

    async def dispatch(m):
        got.append(m)

    return MessageListener(messenger, db, dispatch), got, db


def msg(text="/help", mid="1", chat="me-guid", author="me-guid"):
    return IncomingMessage(mid, chat, author, text)


@pytest.mark.anyio
async def test_accepts_own_command_in_saved_messages(tmp_path, messenger):
    lis, got, _ = await make(tmp_path, messenger)
    assert await lis.handle(msg()) is True and len(got) == 1


@pytest.mark.anyio
async def test_ignores_other_chats_and_other_authors(tmp_path, messenger):
    lis, got, _ = await make(tmp_path, messenger)
    assert not await lis.handle(msg(chat="someone-else", mid="2"))
    assert not await lis.handle(msg(author="stranger", mid="3"))
    assert not await lis.handle(msg(chat="g0group", author="stranger", mid="4"))
    assert got == []


@pytest.mark.anyio
async def test_ignores_non_commands_and_own_outgoing(tmp_path, messenger):
    lis, got, _ = await make(tmp_path, messenger)
    assert not await lis.handle(msg(text="hello", mid="5"))
    own = await messenger.send_text("/looks like a command")
    assert not await lis.handle(msg(text="/looks like a command", mid=own))
    assert got == []


@pytest.mark.anyio
async def test_duplicate_delivery_processed_once_even_across_restart(tmp_path, messenger):
    lis, got, db = await make(tmp_path, messenger)
    assert await lis.handle(msg(mid="9"))
    assert not await lis.handle(msg(mid="9"))
    await db.close()
    lis2, got2, _ = await make(tmp_path, messenger)  # same database file => "restart"
    assert not await lis2.handle(msg(mid="9"))
    assert len(got) == 1 and got2 == []
