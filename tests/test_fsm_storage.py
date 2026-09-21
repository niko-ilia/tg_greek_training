from datetime import UTC, datetime, timedelta

from aiogram.fsm.storage.base import StorageKey
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from greek_trainer.bot.fsm import STATE_TTL, DbStorage
from greek_trainer.db.models import FsmState

KEY = StorageKey(bot_id=1, chat_id=42, user_id=42)


async def test_state_and_data_outlive_the_storage_object(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    storage = DbStorage(session_factory)
    await storage.set_state(KEY, "Review:waiting_for_answer")
    await storage.update_data(KEY, {"card_id": 7, "version": 2})

    # A restart drops everything but the database.
    restarted = DbStorage(session_factory)
    assert await restarted.get_state(KEY) == "Review:waiting_for_answer"
    assert await restarted.get_data(KEY) == {"card_id": 7, "version": 2}


async def test_an_unknown_key_starts_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    storage = DbStorage(session_factory)
    other = StorageKey(bot_id=1, chat_id=43, user_id=43)
    assert await storage.get_state(other) is None
    assert await storage.get_data(other) == {}


async def test_clearing_leaves_no_state_behind(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    storage = DbStorage(session_factory)
    await storage.set_state(KEY, "Review:waiting_for_answer")
    await storage.set_data(KEY, {"card_id": 7})
    await storage.set_state(KEY, None)
    await storage.set_data(KEY, {})

    assert await storage.get_state(KEY) is None
    assert await storage.get_data(KEY) == {}


async def test_a_state_older_than_the_ttl_is_gone(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    storage = DbStorage(session_factory)
    await storage.set_state(KEY, "AddWord:waiting_for_word")
    await storage.set_data(KEY, {"card_id": 7})

    stale = datetime.now(UTC) - STATE_TTL - timedelta(minutes=1)
    async with session_factory() as session, session.begin():
        await session.execute(update(FsmState).values(updated_at=stale))

    assert await storage.get_state(KEY) is None
    assert await storage.get_data(KEY) == {}
