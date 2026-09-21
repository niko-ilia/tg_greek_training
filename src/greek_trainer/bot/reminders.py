"""Daily "time to review" nudge at each learner's reminder time."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from greek_trainer.bot.render import MODE_LABELS, next_card_keyboard
from greek_trainer.db.models import User
from greek_trainer.services import deal_missing_cards, due_count, next_card

log = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 60


async def _claim_reminder(
    session: AsyncSession, user_id: int, now: datetime
) -> tuple[int, str] | None:
    """Mark today's reminder as sent and return (chat id, text), or None if not due.

    SKIP LOCKED: a row locked by the learner's own update in progress (they are
    in the bot right now) or by a second reminder loop (two containers during a
    rolling update) is left for the next minute instead of stalling everyone
    after them.
    """
    user = await session.get(User, user_id, with_for_update={"skip_locked": True})
    if user is None or user.reminder_time is None or user.blocked_at is not None:
        return None
    local = now.astimezone(ZoneInfo(user.timezone))
    if user.last_reminded_on == local.date() or local.time() < user.reminder_time:
        return None
    await deal_missing_cards(session, user, now)
    # Past the chosen exercise: a learner whose mode ran dry needs the nudge most.
    if await next_card(session, user, now, learn_ahead=False, ignore_mode=True) is None:
        return None
    user.last_reminded_on = local.date()
    due = await due_count(session, user, now, ignore_mode=True)
    text = (
        f"⏰ Пора повторить греческий: {due} карточек ждут."
        if due
        else "⏰ Пора учить греческий: есть новые слова."
    )
    if user.exercise_mode is not None:
        text += f"\nРежим: {MODE_LABELS[user.exercise_mode]}, сменить /mode"
    return user.telegram_id, text


async def send_due_reminders(
    bot: Bot, session_factory: async_sessionmaker[AsyncSession], now: datetime
) -> None:
    """Remind every learner whose reminder time has passed today.

    Each learner gets a short transaction that claims the reminder and commits
    before the message goes out, so no row lock is held across Telegram calls
    and no failure can make anyone's reminder repeat. A lost send means no
    reminder that day, which beats a duplicate every minute.
    """
    # ponytail: sequential sends, fine below Telegram's ~30 msg/s; batch or throttle
    # when the user base outgrows one minute of sends.
    async with session_factory() as session:
        user_ids = list(
            await session.scalars(
                select(User.id).where(
                    User.reminder_time.is_not(None), User.blocked_at.is_(None)
                )
            )
        )
    for user_id in user_ids:
        try:
            async with session_factory() as session, session.begin():
                claimed = await _claim_reminder(session, user_id, now)
        except SQLAlchemyError as err:
            log.warning("Reminder claim for user %s failed: %s", user_id, err)
            continue
        if claimed is None:
            continue
        chat_id, text = claimed
        try:
            await bot.send_message(
                chat_id, text, reply_markup=next_card_keyboard("Начать")
            )
        except TelegramForbiddenError:
            async with session_factory() as session, session.begin():
                await session.execute(
                    update(User).where(User.id == user_id).values(blocked_at=now)
                )
        except TelegramAPIError as err:
            log.warning("Reminder to user %s failed: %s", user_id, err)


async def run_reminders(
    bot: Bot, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    while True:
        try:
            await send_due_reminders(bot, session_factory, datetime.now(UTC))
        except SQLAlchemyError as err:
            log.warning("Reminder check failed: %s", err)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
