from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import fsrs
import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError
from aiogram.types import Update
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from greek_trainer.bot.middlewares import describe_update
from greek_trainer.bot.reminders import send_due_reminders
from greek_trainer.config import Settings
from greek_trainer.db.models import UsageEvent, User
from greek_trainer.domain.srs import build_scheduler
from greek_trainer.domain.word_input import parse_word
from greek_trainer.services import (
    Visit,
    add_word,
    deal_missing_cards,
    get_or_create_user,
    next_card,
    record_review,
    record_visit,
)

SCHEDULER = build_scheduler(0.9)

# 19:30 in Nicosia (UTC+3), after the default 19:00 reminder.
EVENING = datetime(2026, 9, 19, 16, 30, tzinfo=UTC)
_CHAT = {"id": 5, "type": "private"}
_FROM = {"id": 5, "is_bot": False, "first_name": "Ν"}


def _message(**fields: object) -> Update:
    message = {"message_id": 1, "date": 0, "chat": _CHAT, "from": _FROM, **fields}
    return Update.model_validate({"update_id": 1, "message": message})


@pytest.mark.parametrize(
    ("update", "expected"),
    [
        (_message(text="/review"), ("command", "review")),
        (_message(text="/check@yasas_bot now"), ("command", "check")),
        (_message(text="семья"), ("text", "text")),
        (
            _message(voice={"file_id": "f", "file_unique_id": "u", "duration": 1}),
            ("message", "voice"),
        ),
        (
            Update.model_validate(
                {
                    "update_id": 1,
                    "callback_query": {
                        "id": "q",
                        "from": _FROM,
                        "chat_instance": "c",
                        "data": "rate:12:3:3",
                    },
                }
            ),
            ("button", "rate"),
        ),
        (
            Update.model_validate(
                {
                    "update_id": 1,
                    "my_chat_member": {
                        "chat": _CHAT,
                        "from": _FROM,
                        "date": 0,
                        "old_chat_member": {"status": "member", "user": _FROM},
                        "new_chat_member": {
                            "status": "kicked",
                            "user": _FROM,
                            "until_date": 0,
                        },
                    },
                }
            ),
            ("status", "kicked"),
        ),
    ],
)
def test_describe_update_never_keeps_the_text(
    update: Update, expected: tuple[str, str]
) -> None:
    assert describe_update(update) == expected


async def test_visit_refreshes_profile_and_logs_an_event(
    session: AsyncSession, user: User
) -> None:
    user.blocked_at = EVENING
    visit = Visit("niko", "Ν", None, "ru", "command", "review")
    record_visit(session, user, visit, EVENING)
    await session.flush()
    assert (user.username, user.last_seen_at, user.blocked_at) == (
        "niko",
        EVENING,
        None,
    )
    event = await session.scalar(
        select(UsageEvent).where(UsageEvent.user_id == user.id)
    )
    assert event is not None and (event.kind, event.action) == ("command", "review")


async def test_a_learner_who_blocked_the_bot_does_not_stall_the_others(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    user: User,
    settings: Settings,
) -> None:
    await add_word(session, parse_word("ναι\nда"))
    other = await get_or_create_user(session, 2, settings)
    await session.commit()
    blocked = TelegramForbiddenError(method=MagicMock(), message="Forbidden: blocked")

    async def send(chat_id: int, *args: object, **kwargs: object) -> None:
        if chat_id == user.telegram_id:
            raise blocked

    bot = MagicMock(send_message=AsyncMock(side_effect=send))
    await send_due_reminders(bot, session_factory, EVENING)
    assert bot.send_message.await_count == 2
    await session.refresh(user)
    await session.refresh(other)
    assert user.blocked_at == EVENING
    assert other.blocked_at is None and other.last_reminded_on is not None

    bot.send_message.reset_mock()
    await send_due_reminders(bot, session_factory, EVENING)
    bot.send_message.assert_not_awaited()


async def test_a_failed_send_is_not_retried_every_minute(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    user: User,
) -> None:
    await add_word(session, parse_word("ναι\nда"))
    await session.commit()
    offline = TelegramNetworkError(method=MagicMock(), message="timeout")
    bot = MagicMock(send_message=AsyncMock(side_effect=offline))

    await send_due_reminders(bot, session_factory, EVENING)
    await send_due_reminders(bot, session_factory, EVENING)
    assert bot.send_message.await_count == 1
    await session.refresh(user)
    assert user.last_reminded_on is not None and user.blocked_at is None


async def test_no_reminder_for_a_learning_step_that_is_not_due_yet(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    user: User,
) -> None:
    await add_word(session, parse_word("ναι\nда"))
    await deal_missing_cards(session, user, EVENING)
    card = await next_card(session, user, EVENING)
    assert card is not None
    record_review(session, SCHEDULER, card, fsrs.Rating.Good, EVENING)
    await session.commit()
    assert card.due > EVENING  # its next learning step is minutes away

    bot = MagicMock(send_message=AsyncMock())
    await send_due_reminders(bot, session_factory, EVENING)
    bot.send_message.assert_not_awaited()
