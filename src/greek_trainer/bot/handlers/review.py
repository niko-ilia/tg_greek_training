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
from aiogram.filters import Command, StateFilter
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

from greek_trainer.bot.ack import ack
from greek_trainer.bot.render import (
    EXERCISE_MARKS,
    RATING_LABELS,
    NextCard,
    PaceOverride,
    Rate,
    ShowAnswer,
    answer_text,
    expected_answer,
    format_interval,
    pace_text,
    question_text,
    rating_keyboard,
    session_end_keyboard,
    show_answer_keyboard,
    word_text,
)
from greek_trainer.bot.tts import send_voice
from greek_trainer.config import Settings
from greek_trainer.db.models import Card, CardType, User
from greek_trainer.domain.greek import Verdict, check_answer, check_translation
from greek_trainer.domain.srs import preview_intervals
from greek_trainer.services import (
    get_card,
    get_pace,
    next_card,
    next_due_at,
    override_pace_today,
    record_review,
    repeat_gap_ends_at,
)

router = Router(name="review")

CAPTION_LIMIT = 1024
CONFETTI_EFFECT_ID = "5046509860389126442"
REACTIONS = {
    Verdict.CORRECT: "👍",
    Verdict.ACCENT_ONLY: "🤔",
    Verdict.WRONG: "👎",
}


class Review(StatesGroup):
    waiting_for_answer = State()


def _pronounced_text(card: Card) -> str:
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


def _already_applied(err: TelegramBadRequest) -> bool:
    """A repeated tap or a Telegram retry: the message already shows this text."""
    return "message is not modified" in err.message


async def _edit_card(
    message: Message, text: str, markup: InlineKeyboardMarkup | None
) -> bool:
    """Edit the card in place; False means the caller must send `text` anew."""
    is_caption = message.voice is not None
    if is_caption and len(text) > CAPTION_LIMIT:
        return False
    try:
        if is_caption:
            await message.edit_caption(caption=text, reply_markup=markup)
        else:
            await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as err:
        return _already_applied(err)
    return True


async def _replace_card(
    bot: Bot, message: Message, text: str, markup: InlineKeyboardMarkup | None
) -> None:
    """Show `text` on the card; if it can't be edited, retire its buttons and resend."""
    if await _edit_card(message, text, markup):
        return
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await bot.send_message(message.chat.id, text, reply_markup=markup)


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
    is_caption = bool(data.get("card_has_caption"))
    if is_caption and len(text) > CAPTION_LIMIT:
        return False
    try:
        if is_caption:
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
    except TelegramBadRequest as err:
        return _already_applied(err)
    return True


async def _drop_markup_by_id(bot: Bot, chat_id: int, data: dict[str, Any]) -> None:
    message_id = data.get("card_message_id")
    if message_id is None:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=message_id, reply_markup=None
        )
    except TelegramBadRequest:
        pass


def _tapped(query: CallbackQuery) -> Message | None:
    return query.message if isinstance(query.message, Message) else None


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
    replace: Message | None = None,
    on_demand: bool = False,
) -> None:
    """`replace` is the message whose button was tapped: it is reused, not duplicated."""
    now = datetime.now(UTC)
    card = await next_card(session, user, now, on_demand=on_demand)
    if card is None:
        await state.clear()
        upcoming = await next_due_at(session, user, now)
        held_back = await next_card(session, user, now, ignore_pace=True) is not None
        comeback = await repeat_gap_ends_at(session, user, now)
        returns_soon = comeback is not None
        if held_back:
            text = pace_text(await get_pace(session, user, now))
        elif returns_soon:
            text = "⏳ Сейчас повторять нечего, кроме только что пройденных слов."
        else:
            text = "🎉 На сейчас всё повторено."
        if comeback is not None:
            text += (
                f"\nСледующая карточка через {format_interval(comeback - now)}, "
                "жми «Дальше», когда будешь готов."
            )
        elif upcoming is not None:
            local = upcoming.astimezone(ZoneInfo(user.timezone))
            text += f"\nСледующее повторение: {local:%d.%m %H:%M}."
        markup = session_end_keyboard(held_back=held_back, returns_soon=returns_soon)
        if replace is not None:
            await _replace_card(bot, replace, text, markup)
            await state.set_data({"end_message_id": replace.message_id})
            return
        if markup is not None:
            sent = await bot.send_message(chat_id, text, reply_markup=markup)
            await state.set_data({"end_message_id": sent.message_id})
            return
        try:
            await bot.send_message(chat_id, text, message_effect_id=CONFETTI_EFFECT_ID)
        except TelegramBadRequest:
            await bot.send_message(chat_id, text)
        return

    if replace is not None:
        try:
            await replace.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass

    # Store the card before sending: synthesis takes seconds, and an answer typed
    # meanwhile must be checked against this card, not the previous one.
    await state.set_data(
        {"card_id": card.id, "version": card.version, "shown_at": now.isoformat()}
    )
    await state.set_state(Review.waiting_for_answer)
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
    await state.update_data(
        card_message_id=sent.message_id, card_has_caption=has_caption
    )


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
    # A tap while nothing is due edits the message into the same text, which
    # Telegram refuses as "not modified": without the toast the button looks dead.
    ready = (
        await next_card(session, user, datetime.now(UTC), on_demand=True) is not None
    )
    await ack(query, None if ready else "Карточка ещё не готова")
    # The same button also sits under the /add confirmation and the reminder: those
    # texts must survive, only the message this handler wrote may be edited away.
    message = _tapped(query)
    if message is not None and message.message_id != (await state.get_data()).get(
        "end_message_id"
    ):
        message = None
    await show_next_card(
        bot,
        query.from_user.id,
        state,
        session,
        user,
        settings,
        replace=message,
        on_demand=True,
    )


@router.callback_query(PaceOverride.filter())
async def pace_override(
    query: CallbackQuery,
    bot: Bot,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    settings: Settings,
) -> None:
    if not await override_pace_today(session, user, datetime.now(UTC)):
        await ack(query, "Уже продолжаем.")
        return
    await session.flush()
    await ack(query, "Сегодня без ограничений")
    await show_next_card(
        bot,
        query.from_user.id,
        state,
        session,
        user,
        settings,
        replace=_tapped(query),
        on_demand=True,
    )


@router.callback_query(ShowAnswer.filter())
async def show_answer(
    query: CallbackQuery,
    callback_data: ShowAnswer,
    bot: Bot,
    session: AsyncSession,
    user: User,
    fsrs_scheduler: fsrs.Scheduler,
) -> None:
    card = await get_card(session, user, callback_data.card_id)
    if card is None or card.version != callback_data.version:
        await ack(query, "Эта карточка уже пройдена.")
        return
    await ack(query)
    if isinstance(query.message, Message):
        previews = preview_intervals(fsrs_scheduler, card, datetime.now(UTC))
        await _replace_card(
            bot,
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
    # State and data are written separately, so the state may name a card the
    # data does not carry yet.
    card_id = data.get("card_id")
    card = None if card_id is None else await get_card(session, user, card_id)
    if card is None or card.version != data.get("version"):
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
            await _drop_markup_by_id(bot, message.chat.id, data)
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


@router.message(StateFilter(None), F.text, ~F.text.startswith("/"))
async def stray_text(message: Message) -> None:
    # No card is waiting: the session ended, or the state aged out of STATE_TTL.
    await message.answer("Сейчас я не жду ответа. Продолжить: /review")


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
        await ack(query, "Уже оценено.")
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
    await ack(query)

    if isinstance(query.message, Message):
        result = (
            f"{RATING_LABELS[rating]} → следующий раз через "
            f"{format_interval(card.due - now)}"
        )
        await _replace_card(
            bot,
            query.message,
            f"{word_text(card.word, EXERCISE_MARKS[card.card_type])}\n\n{result}",
            None,
        )
    await show_next_card(bot, query.from_user.id, state, session, user, settings)
