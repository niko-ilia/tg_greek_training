"""Review session: show a card, take the answer, record the FSRS rating."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import fsrs
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InaccessibleMessage, Message
from sqlalchemy.ext.asyncio import AsyncSession

from greek_trainer.bot.render import (
    RATING_LABELS,
    NextCard,
    Rate,
    ShowAnswer,
    answer_text,
    expected_answer,
    format_interval,
    question_text,
    rating_keyboard,
    show_answer_keyboard,
)
from greek_trainer.bot.tts import send_pronunciation
from greek_trainer.config import Settings
from greek_trainer.db.models import Card, CardType, User
from greek_trainer.domain.greek import check_answer, check_translation
from greek_trainer.domain.srs import preview_intervals
from greek_trainer.services import get_card, next_card, next_due_at, record_review

router = Router(name="review")


class Review(StatesGroup):
    waiting_for_answer = State()


def _pronounced_text(card: Card) -> str:
    """What to read aloud once the answer is known: the whole sentence for cloze."""
    if card.card_type is CardType.CLOZE:
        assert card.example is not None
        return card.example.text_el
    return card.word.lemma


async def show_next_card(
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    settings: Settings,
) -> None:
    now = datetime.now(UTC)
    card = await next_card(session, user, now)
    if card is None:
        await state.clear()
        upcoming = await next_due_at(session, user)
        text = "🎉 На сейчас всё повторено."
        if upcoming is not None:
            local = upcoming.astimezone(ZoneInfo(user.timezone))
            text += f"\nСледующее повторение: {local:%d.%m %H:%M}."
        await bot.send_message(chat_id, text)
        return

    await state.set_state(Review.waiting_for_answer)
    data = {"card_id": card.id, "version": card.version, "shown_at": now.isoformat()}
    if card.card_type is CardType.RECOGNITION:
        await send_pronunciation(
            bot, session, chat_id, card.word.lemma, settings.tts_voice
        )
        question = await bot.send_message(
            chat_id, question_text(card), reply_markup=show_answer_keyboard(card)
        )
        data["question_message_id"] = question.message_id
    else:
        await bot.send_message(chat_id, question_text(card))
    await state.set_data(data)


async def _drop_show_answer_button(
    bot: Bot, chat_id: int, data: dict[str, Any]
) -> None:
    """The typed answer replaces the button; a stale one would only say "already done"."""
    message_id = data.get("question_message_id")
    if message_id is None:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=message_id, reply_markup=None
        )
    except TelegramBadRequest:
        pass


@router.message(Command("review"))
async def review_command(
    message: Message,
    bot: Bot,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    settings: Settings,
) -> None:
    await show_next_card(bot, message.chat.id, state, session, user, settings)


@router.callback_query(NextCard.filter())
async def next_callback(
    query: CallbackQuery,
    bot: Bot,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    settings: Settings,
) -> None:
    await query.answer()
    await show_next_card(bot, query.from_user.id, state, session, user, settings)


@router.callback_query(ShowAnswer.filter())
async def show_answer(
    query: CallbackQuery,
    callback_data: ShowAnswer,
    session: AsyncSession,
    user: User,
    fsrs_scheduler: fsrs.Scheduler,
) -> None:
    card = await get_card(session, user, callback_data.card_id)
    if card is None or card.version != callback_data.version:
        await query.answer("Эта карточка уже пройдена.")
        return
    await query.answer()
    if query.message is not None and not isinstance(query.message, InaccessibleMessage):
        previews = preview_intervals(fsrs_scheduler, card, datetime.now(UTC))
        await query.message.edit_text(
            answer_text(card, verdict=None, typed=None),
            reply_markup=rating_keyboard(card, previews),
        )


@router.message(Review.waiting_for_answer, F.text, ~F.text.startswith("/"))
async def typed_answer(
    message: Message,
    bot: Bot,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    settings: Settings,
    fsrs_scheduler: fsrs.Scheduler,
) -> None:
    assert message.text is not None
    data = await state.get_data()
    card = await get_card(session, user, data["card_id"])
    if card is None or card.version != data["version"]:
        await state.clear()
        await message.answer("Карточка устарела, запусти /review заново.")
        return
    await state.update_data(answer_text=message.text)
    if card.card_type is CardType.RECOGNITION:
        verdict = check_translation(message.text, card.word.translation)
        await _drop_show_answer_button(bot, message.chat.id, data)
    else:
        verdict = check_answer(message.text, expected_answer(card))
        await send_pronunciation(
            bot, session, message.chat.id, _pronounced_text(card), settings.tts_voice
        )
    previews = preview_intervals(fsrs_scheduler, card, datetime.now(UTC))
    await message.answer(
        answer_text(card, verdict=verdict, typed=message.text),
        reply_markup=rating_keyboard(card, previews),
    )


@router.callback_query(Rate.filter())
async def rate(
    query: CallbackQuery,
    callback_data: Rate,
    bot: Bot,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    settings: Settings,
    fsrs_scheduler: fsrs.Scheduler,
) -> None:
    card = await get_card(session, user, callback_data.card_id)
    if card is None or card.version != callback_data.version:
        await query.answer("Уже оценено.")
        return

    now = datetime.now(UTC)
    data = await state.get_data()
    same_card = data.get("card_id") == card.id
    duration_ms = None
    if same_card and "shown_at" in data:
        shown_at = datetime.fromisoformat(data["shown_at"])
        duration_ms = int((now - shown_at).total_seconds() * 1000)
    rating = fsrs.Rating(callback_data.rating)
    record_review(
        session,
        fsrs_scheduler,
        card,
        rating,
        now,
        duration_ms=duration_ms,
        answer_text=data.get("answer_text") if same_card else None,
    )
    await session.flush()
    await query.answer()

    if query.message is not None and not isinstance(query.message, InaccessibleMessage):
        await query.message.edit_reply_markup(reply_markup=None)
        await query.message.answer(
            f"{RATING_LABELS[rating]} → следующий раз через "
            f"{format_interval(card.due - now)}"
        )
    await show_next_card(bot, query.from_user.id, state, session, user, settings)
