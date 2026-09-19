"""Review session: show a card, take the answer, record the FSRS rating.

Each card lives in one message: the question (a voice message with a caption
when audio is available) is edited into the answer with the rating buttons,
then into the result, so a session does not flood the chat.
"""

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
from aiogram.types import (
    CallbackQuery,
    ForceReply,
    InlineKeyboardMarkup,
    Message,
    ReactionTypeEmoji,
)
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
    word_text,
)
from greek_trainer.bot.tts import send_voice
from greek_trainer.config import Settings
from greek_trainer.db.models import Card, CardType, User
from greek_trainer.domain.greek import Verdict, check_answer, check_translation
from greek_trainer.domain.srs import preview_intervals
from greek_trainer.services import get_card, next_card, next_due_at, record_review

router = Router(name="review")

# Telegram limits media captions; longer answers go as a separate text message.
CAPTION_LIMIT = 1024
# Telegram's public "confetti" message effect, private chats only.
CONFETTI_EFFECT_ID = "5046509860389126442"
REACTIONS = {
    Verdict.CORRECT: "👍",
    Verdict.ACCENT_ONLY: "🤔",
    Verdict.WRONG: "👎",
}


class Review(StatesGroup):
    waiting_for_answer = State()


def _pronounced_text(card: Card) -> str:
    """What to read aloud once the answer is known: the whole sentence for cloze."""
    if card.card_type is CardType.CLOZE:
        assert card.example is not None
        return card.example.text_el
    return card.word.lemma


async def _send_card(
    bot: Bot,
    session: AsyncSession,
    chat_id: int,
    spoken: str,
    text: str,
    markup: InlineKeyboardMarkup | None,
    voice: str,
) -> tuple[Message, bool]:
    """Voice message with `text` as caption; plain text when audio fails.

    Returns the message and whether its text lives in a caption.
    """
    if len(text) <= CAPTION_LIMIT:
        sent = await send_voice(
            bot, session, chat_id, spoken, voice, caption=text, reply_markup=markup
        )
        if sent is not None:
            return sent, True
    else:
        await send_voice(bot, session, chat_id, spoken, voice)
    return await bot.send_message(chat_id, text, reply_markup=markup), False


async def _edit_card(
    message: Message, text: str, markup: InlineKeyboardMarkup | None
) -> bool:
    try:
        if message.voice is not None:
            await message.edit_caption(caption=text, reply_markup=markup)
        else:
            await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        return False
    return True


async def _edit_card_by_id(
    bot: Bot,
    chat_id: int,
    data: dict[str, Any],
    text: str,
    markup: InlineKeyboardMarkup | None,
) -> bool:
    message_id = data.get("card_message_id")
    if message_id is None:
        return False
    try:
        if data.get("card_has_caption"):
            await bot.edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
                caption=text,
                reply_markup=markup,
            )
        else:
            await bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, reply_markup=markup
            )
    except TelegramBadRequest:
        return False
    return True


async def _react(message: Message, verdict: Verdict) -> None:
    try:
        await message.react([ReactionTypeEmoji(emoji=REACTIONS[verdict])])
    except TelegramBadRequest:
        pass


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
        try:
            await bot.send_message(chat_id, text, message_effect_id=CONFETTI_EFFECT_ID)
        except TelegramBadRequest:
            await bot.send_message(chat_id, text)
        return

    await state.set_state(Review.waiting_for_answer)
    data = {"card_id": card.id, "version": card.version, "shown_at": now.isoformat()}
    if card.card_type is CardType.RECOGNITION:
        sent, has_caption = await _send_card(
            bot,
            session,
            chat_id,
            card.word.lemma,
            question_text(card),
            show_answer_keyboard(card),
            settings.tts_voice,
        )
    else:
        sent = await bot.send_message(
            chat_id,
            question_text(card),
            reply_markup=ForceReply(input_field_placeholder="Напиши по-гречески"),
        )
        has_caption = False
    data["card_message_id"] = sent.message_id
    data["card_has_caption"] = has_caption
    await state.set_data(data)


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
    if isinstance(query.message, Message):
        previews = preview_intervals(fsrs_scheduler, card, datetime.now(UTC))
        await _edit_card(
            query.message,
            answer_text(card, verdict=None, typed=None),
            rating_keyboard(card, previews),
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
    else:
        verdict = check_answer(message.text, expected_answer(card))
    await _react(message, verdict)

    previews = preview_intervals(fsrs_scheduler, card, datetime.now(UTC))
    text = answer_text(card, verdict=verdict, typed=message.text)
    markup = rating_keyboard(card, previews)
    if card.card_type is CardType.RECOGNITION:
        if not await _edit_card_by_id(bot, message.chat.id, data, text, markup):
            await message.answer(text, reply_markup=markup)
    else:
        await _send_card(
            bot,
            session,
            message.chat.id,
            _pronounced_text(card),
            text,
            markup,
            settings.tts_voice,
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

    if isinstance(query.message, Message):
        result = (
            f"{RATING_LABELS[rating]} → следующий раз через "
            f"{format_interval(card.due - now)}"
        )
        await _edit_card(query.message, f"{word_text(card.word)}\n\n{result}", None)
    await show_next_card(bot, query.from_user.id, state, session, user, settings)
