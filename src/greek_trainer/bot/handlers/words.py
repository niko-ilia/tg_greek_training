"""/start, /help, /add, /stats, /settings."""

from __future__ import annotations

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from greek_trainer.bot.render import (
    next_card_keyboard,
    settings_text,
    stats_text,
    word_text,
)
from greek_trainer.db.models import User
from greek_trainer.domain.settings_input import parse_settings
from greek_trainer.domain.word_input import parse_word
from greek_trainer.errors import AppError
from greek_trainer.services import add_word, get_stats

router = Router(name="words")

HELP = """<b>Γεια σου!</b> Я помогаю запоминать греческие слова.

/review – повторение (карточки по алгоритму FSRS)
/check – быстро отметить слова, которые уже знаешь
/add – добавить слово
/stats – статистика
/settings – лимит новых карточек и время напоминания

<b>Формат /add</b>
<code>ξέρω
знать; уметь
ξέρω να + глагол = уметь
Δεν *ξέρω* πού είναι η στάση. | Я не знаю, где остановка.</code>

1-я строка – слово, 2-я – перевод. Строка с «|» – пример, слово в *звездочках* \
станет упражнением «вставь пропущенное». Остальные строки – заметки."""


class AddWord(StatesGroup):
    waiting_for_word = State()


@router.message(CommandStart())
@router.message(Command("help"))
async def start(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("add"))
async def add_command(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    session: AsyncSession,
    user: User,
) -> None:
    if command.args:
        await _add(message, command.args, session, user)
        return
    await state.set_state(AddWord.waiting_for_word)
    await message.answer("Пришли слово в формате из /help.")


@router.message(AddWord.waiting_for_word, F.text)
async def add_from_state(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    assert message.text is not None
    await state.clear()
    await _add(message, message.text, session, user)


async def _add(message: Message, text: str, session: AsyncSession, user: User) -> None:
    try:
        async with session.begin_nested():
            word = await add_word(session, parse_word(text))
    except AppError as err:
        await message.answer(f"⚠️ {err}")
        return
    cloze = sum(example.cloze_target is not None for example in word.examples)
    await message.answer(
        f"Добавлено:\n\n{word_text(word)}\n\n"
        f"Карточек: {2 + cloze} (узнавание, вспоминание"
        f"{f', пропуски в примерах: {cloze}' if cloze else ''}).",
        reply_markup=next_card_keyboard("Начать повторение"),
    )


@router.message(Command("settings"))
async def settings(message: Message, command: CommandObject, user: User) -> None:
    if command.args:
        try:
            change = parse_settings(command.args)
        except AppError as err:
            await message.answer(f"⚠️ {err}")
            return
        if change.daily_new_cards is not None:
            user.daily_new_cards = change.daily_new_cards
        if change.reminders_off:
            user.reminder_time = None
        elif change.reminder_time is not None:
            user.reminder_time = change.reminder_time
    await message.answer(settings_text(user))


@router.message(Command("stats"))
async def stats(message: Message, session: AsyncSession, user: User) -> None:
    await message.answer(stats_text(await get_stats(session, user, datetime.now(UTC))))
