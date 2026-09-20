from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import fsrs
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from greek_trainer.bot.handlers.review import (
    CAPTION_LIMIT,
    _replace_card,
    next_callback,
    rate,
)
from greek_trainer.bot.render import Rate
from greek_trainer.config import Settings
from greek_trainer.db.models import ReviewLog, User
from greek_trainer.domain.srs import build_scheduler
from greek_trainer.domain.word_input import parse_word
from greek_trainer.services import add_word, deal_missing_cards, next_card


def _voice_card(edit_error: Exception | None = None) -> MagicMock:
    message = MagicMock()
    message.chat.id = 42
    message.edit_caption = AsyncMock(side_effect=edit_error)
    message.edit_reply_markup = AsyncMock()
    return message


def _bad_request(text: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=MagicMock(), message=text)


async def test_edits_the_caption_in_place() -> None:
    bot, message = MagicMock(send_message=AsyncMock()), _voice_card()
    await _replace_card(bot, message, "answer", None)
    message.edit_caption.assert_awaited_once()
    bot.send_message.assert_not_awaited()


async def test_too_long_for_a_caption_goes_out_as_text() -> None:
    bot, message = MagicMock(send_message=AsyncMock()), _voice_card()
    await _replace_card(bot, message, "x" * (CAPTION_LIMIT + 1), None)
    message.edit_caption.assert_not_awaited()
    message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    bot.send_message.assert_awaited_once()


async def test_failed_edit_falls_back_to_a_new_message() -> None:
    bot = MagicMock(send_message=AsyncMock())
    message = _voice_card(_bad_request("Bad Request: message to edit not found"))
    await _replace_card(bot, message, "answer", None)
    bot.send_message.assert_awaited_once()


async def test_repeated_tap_does_not_duplicate_the_answer() -> None:
    bot = MagicMock(send_message=AsyncMock())
    message = _voice_card(_bad_request("Bad Request: message is not modified"))
    await _replace_card(bot, message, "answer", None)
    bot.send_message.assert_not_awaited()


def _tapped_message() -> MagicMock:
    message = MagicMock(spec=Message)
    message.message_id = 7
    message.voice = None
    message.edit_text = AsyncMock()
    message.edit_reply_markup = AsyncMock()
    return message


async def _tap_next(
    message: MagicMock,
    state: AsyncMock,
    session: AsyncSession,
    user: User,
    settings: Settings,
    answer_error: Exception | None = None,
) -> tuple[MagicMock, MagicMock]:
    bot = MagicMock(send_message=AsyncMock())
    query = MagicMock(message=message, answer=AsyncMock(side_effect=answer_error))
    query.from_user.id = 42
    await next_callback(query, bot, state, session, user, settings)
    return bot, query


async def test_next_on_an_empty_session_edits_its_own_message(
    session: AsyncSession, user: User, settings: Settings
) -> None:
    message = _tapped_message()
    state = AsyncMock(get_data=AsyncMock(return_value={"end_message_id": 7}))
    bot, query = await _tap_next(message, state, session, user, settings)
    message.edit_text.assert_awaited_once()
    bot.send_message.assert_not_awaited()
    # The edit may be a no-op ("not modified"), so the tap answers with a toast.
    query.answer.assert_awaited_once_with("Карточка ещё не готова", show_alert=False)


async def test_next_leaves_a_message_it_did_not_write_alone(
    session: AsyncSession, user: User, settings: Settings
) -> None:
    message = _tapped_message()
    state = AsyncMock(get_data=AsyncMock(return_value={}))
    bot, _ = await _tap_next(message, state, session, user, settings)
    message.edit_text.assert_not_awaited()
    bot.send_message.assert_awaited_once()


async def test_a_tap_whose_query_expired_still_updates_the_card(
    session: AsyncSession, user: User, settings: Settings
) -> None:
    message = _tapped_message()
    state = AsyncMock(get_data=AsyncMock(return_value={"end_message_id": 7}))
    stale = _bad_request("Bad Request: query is too old and response timeout expired")
    await _tap_next(message, state, session, user, settings, answer_error=stale)
    message.edit_text.assert_awaited_once()


async def test_a_rating_survives_a_query_that_expired(
    session: AsyncSession, user: User, settings: Settings
) -> None:
    now = datetime.now(UTC)
    await add_word(session, parse_word("ναι\nда"))
    await deal_missing_cards(session, user, now)
    card = await next_card(session, user, now)
    assert card is not None

    message = _tapped_message()
    stale = _bad_request("Bad Request: query is too old and response timeout expired")
    query = MagicMock(message=message, answer=AsyncMock(side_effect=stale))
    query.from_user.id = 42
    await rate(
        query,
        Rate(card_id=card.id, version=card.version, rating=fsrs.Rating.Good.value),
        MagicMock(send_message=AsyncMock()),
        AsyncMock(get_data=AsyncMock(return_value={})),
        session,
        user,
        settings,
        build_scheduler(0.9),
    )

    logged = await session.scalars(
        select(ReviewLog).where(ReviewLog.card_id == card.id)
    )
    assert len(list(logged)) == 1 and card.version == 1
