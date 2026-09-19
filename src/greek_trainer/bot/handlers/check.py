"""/check: a fast pass over unseen words to mark the ones the learner already knows."""

from __future__ import annotations

from datetime import UTC, datetime

import fsrs
from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from greek_trainer.bot.render import Check, check_keyboard, check_text
from greek_trainer.db.models import User, Word
from greek_trainer.services import advance_check, mark_word_known, next_unchecked_word

router = Router(name="check")

INTRO = (
    "Покажу слова, которые ты ещё не повторял. «Знаю» – карточки слова вернутся "
    "примерно через неделю для проверки, «Учить» – слово пойдёт в обычное обучение."
)


async def _edit(message: Message | None, text: str | None) -> None:
    """Edit the prompt; a Telegram retry or a racing tap may have edited it already."""
    if message is None:
        return
    try:
        if text is None:
            await message.edit_reply_markup(reply_markup=None)
        else:
            await message.edit_text(text)
    except TelegramBadRequest:
        pass


async def show_next_unchecked(
    bot: Bot, chat_id: int, session: AsyncSession, user: User
) -> None:
    word = await next_unchecked_word(session, user)
    if word is None:
        await bot.send_message(chat_id, "Все слова просмотрены. Дальше /review.")
        return
    await bot.send_message(chat_id, check_text(word), reply_markup=check_keyboard(word))


@router.message(Command("check"))
async def check_command(
    message: Message, bot: Bot, state: FSMContext, session: AsyncSession, user: User
) -> None:
    await state.clear()
    await message.answer(INTRO)
    await show_next_unchecked(bot, message.chat.id, session, user)


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
        await _edit(message, None)
        await bot.send_message(query.from_user.id, "Продолжить можно командой /check.")
        return

    word = await session.get(Word, callback_data.word_id)
    if word is None:
        await query.answer("Слова уже нет.")
        return
    now = datetime.now(UTC)
    if callback_data.action == "know":
        accepted = await mark_word_known(session, fsrs_scheduler, user, word, now)
        verdict = "✅ Знаю"
    else:
        accepted = await advance_check(session, user, word)
        verdict = "📚 Учить"
    if not accepted:
        await query.answer("Уже отмечено.")
        return
    await session.flush()
    await query.answer()
    await _edit(message, f"{check_text(word)}\n{verdict}")
    await show_next_unchecked(bot, query.from_user.id, session, user)
