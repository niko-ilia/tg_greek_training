"""/start, /help, /add, /stats."""

from __future__ import annotations

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from greek_trainer.bot.render import next_card_keyboard, stats_text, word_text
from greek_trainer.db.models import CardType, User
from greek_trainer.domain.word_input import parse_word
from greek_trainer.errors import AppError
from greek_trainer.services import add_word, get_stats

router = Router(name="words")

HELP = """<b>Γεια σου!</b> Я помогаю запоминать греческие слова.

/review – повторение (карточки по алгоритму FSRS)
/add – добавить слово
/stats – статистика

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
            word = await add_word(session, user, parse_word(text), datetime.now(UTC))
    except AppError as err:
        await message.answer(f"⚠️ {err}")
        return
    cloze = sum(card.card_type is CardType.CLOZE for card in word.cards)
    await message.answer(
        f"Добавлено:\n\n{word_text(word)}\n\n"
        f"Карточек: {len(word.cards)} (узнавание, вспоминание"
        f"{f', пропуски в примерах: {cloze}' if cloze else ''}).",
        reply_markup=next_card_keyboard("Начать повторение"),
    )


@router.message(Command("stats"))
async def stats(message: Message, session: AsyncSession, user: User) -> None:
    await message.answer(stats_text(await get_stats(session, user, datetime.now(UTC))))
