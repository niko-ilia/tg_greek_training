"""Daily "time to review" nudge at each learner's reminder time."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from greek_trainer.bot.render import next_card_keyboard
from greek_trainer.db.models import User
from greek_trainer.services import deal_missing_cards, due_count, next_card

log = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 60


async def send_due_reminders(bot: Bot, session: AsyncSession, now: datetime) -> None:
    """Remind every learner whose reminder time has passed today.

    One learner's Telegram error must not roll back the others: their
    `last_reminded_on` would be lost and they would be reminded every minute.
    """
    # ponytail: sequential sends, fine below Telegram's ~30 msg/s; batch or throttle
    # when the user base outgrows one minute of sends.
    users = await session.scalars(
        select(User).where(User.reminder_time.is_not(None), User.blocked_at.is_(None))
    )
    for user in users:
        local = now.astimezone(ZoneInfo(user.timezone))
        assert user.reminder_time is not None
        if user.last_reminded_on == local.date() or local.time() < user.reminder_time:
            continue
        await deal_missing_cards(session, user, now)
        if await next_card(session, user, now) is None:
            continue
        due = await due_count(session, user, now)
        text = (
            f"⏰ Пора повторить греческий: {due} карточек ждут."
            if due
            else "⏰ Пора учить греческий: есть новые слова."
        )
        try:
            await bot.send_message(
                user.telegram_id, text, reply_markup=next_card_keyboard("Начать")
            )
        except TelegramForbiddenError:
            user.blocked_at = now
            continue
        except TelegramAPIError as err:
            log.warning("Reminder to user %s failed: %s", user.id, err)
            continue
        user.last_reminded_on = local.date()


async def run_reminders(
    bot: Bot, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    while True:
        try:
            async with session_factory() as session, session.begin():
                await send_due_reminders(bot, session, datetime.now(UTC))
        except SQLAlchemyError as err:
            log.warning("Reminder check failed: %s", err)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
