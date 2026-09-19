"""Races that need real concurrent transactions, so no per-test rollback here.

Each test commits its own rows and deletes them afterwards.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from greek_trainer.bot.reminders import send_due_reminders
from greek_trainer.config import Settings
from greek_trainer.db.models import User, Word
from greek_trainer.domain.greek import lemma_key
from greek_trainer.domain.word_input import parse_word
from greek_trainer.services import add_word, get_or_create_user

TELEGRAM_ID = 987_654_321
WORD = "το ζάρι\nкубик"
# 19:30 in Nicosia (UTC+3), after the default 19:00 reminder.
EVENING = datetime(2026, 9, 19, 16, 30, tzinfo=UTC)


@pytest.fixture
async def factory(
    engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    async with factory() as session, session.begin():
        await session.execute(delete(User).where(User.telegram_id == TELEGRAM_ID))
        key = lemma_key(parse_word(WORD).lemma)
        await session.execute(delete(Word).where(Word.lemma_key == key))


async def test_two_first_updates_create_one_learner(
    factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    async def first_contact() -> int:
        async with factory() as session, session.begin():
            user = await get_or_create_user(session, TELEGRAM_ID, settings)
            await asyncio.sleep(0.2)  # keep the transaction open while the other runs
            return user.id

    ids = await asyncio.gather(first_contact(), first_contact())
    assert ids[0] == ids[1]
    async with factory() as session:
        count = len(
            list(
                await session.scalars(
                    select(User).where(User.telegram_id == TELEGRAM_ID)
                )
            )
        )
    assert count == 1


async def test_a_learner_busy_in_the_bot_does_not_stall_reminders(
    factory: async_sessionmaker[AsyncSession], settings: Settings
) -> None:
    async with factory() as session, session.begin():
        await add_word(session, parse_word(WORD))
        await get_or_create_user(session, TELEGRAM_ID, settings)
    bot = MagicMock(send_message=AsyncMock())

    async with factory() as busy, busy.begin():
        await busy.scalar(
            select(User).where(User.telegram_id == TELEGRAM_ID).with_for_update()
        )
        await asyncio.wait_for(send_due_reminders(bot, factory, EVENING), timeout=5)
        bot.send_message.assert_not_awaited()

    await send_due_reminders(bot, factory, EVENING)
    bot.send_message.assert_awaited_once()
