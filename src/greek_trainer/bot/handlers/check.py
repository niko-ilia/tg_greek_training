"""/check: a fast pass over unseen words to mark the ones the learner already knows.

The whole pass lives in one message that is edited from word to word.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape

import fsrs
from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from greek_trainer.bot.render import Check, check_keyboard, check_text
from greek_trainer.db.models import User, Word
from greek_trainer.services import (
    advance_check,
    count_unchecked_words,
    mark_word_known,
    next_unchecked_word,
)

router = Router(name="check")

INTRO = (
    "Покажу слова, которые ты ещё не повторял. «Знаю» – карточки слова вернутся "
    "примерно через неделю для проверки, «Учить» – слово пойдёт в обычное обучение."
)
DONE = "Все слова просмотрены. Дальше /review."
STOPPED = "Проверка остановлена. Продолжить: /check."


async def _check_view(
    session: AsyncSession, user: User, previous: str | None
) -> tuple[str, InlineKeyboardMarkup | None]:
    word = await next_unchecked_word(session, user)
    if word is None:
        return DONE, None
    remaining = await count_unchecked_words(session, user)
    return check_text(word, remaining, previous), check_keyboard(word)


async def _edit(
    message: Message | None, text: str, markup: InlineKeyboardMarkup | None
) -> bool:
    """Edit the pass message; a Telegram retry or a racing tap may have done it already."""
    if message is None:
        return False
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        return False
    return True


@router.message(Command("check"))
async def check_command(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    await state.clear()
    text, markup = await _check_view(session, user, None)
    if markup is not None:
        await message.answer(INTRO)
    await message.answer(text, reply_markup=markup)


@router.callback_query(Check.filter())
async def check_callback(
    query: CallbackQuery,
    callback_data: Check,
    bot: Bot,
    session: AsyncSession,
    user: User,
    fsrs_scheduler: fsrs.Scheduler,
) -> None:
    message = query.message if isinstance(query.message, Message) else None
    if callback_data.action == "stop":
        await query.answer()
        if not await _edit(message, STOPPED, None):
            await bot.send_message(query.from_user.id, STOPPED)
        return

    word = await session.get(Word, callback_data.word_id)
    if word is None:
        await query.answer("Слова уже нет.")
        return
    now = datetime.now(UTC)
    if callback_data.action == "know":
        accepted = await mark_word_known(session, fsrs_scheduler, user, word, now)
        mark = "✅"
    else:
        accepted = await advance_check(session, user, word)
        mark = "📚"
    if not accepted:
        await query.answer("Уже отмечено.")
        return
    await session.flush()
    await query.answer()
    text, markup = await _check_view(session, user, f"{mark} {escape(word.lemma)}")
    if not await _edit(message, text, markup):
        await bot.send_message(query.from_user.id, text, reply_markup=markup)
