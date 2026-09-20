"""/start, /help, /add, /stats, /settings, and the learner blocking the bot."""

from __future__ import annotations

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message
from sqlalchemy.ext.asyncio import AsyncSession

from greek_trainer.bot.ack import ack
from greek_trainer.bot.render import (
    Setting,
    next_card_keyboard,
    settings_keyboard,
    settings_text,
    stats_text,
    word_text,
)
from greek_trainer.config import Settings
from greek_trainer.db.models import User
from greek_trainer.domain.settings_input import parse_settings
from greek_trainer.domain.word_input import parse_word
from greek_trainer.errors import AppError
from greek_trainer.services import add_word, apply_settings, get_stats

router = Router(name="words")

HELP = """<b>Γεια σου!</b> Я помогаю запоминать греческие слова.

/review – повторение (карточки по алгоритму FSRS)
/check – быстро отметить слова, которые уже знаешь
/stats – статистика
/settings – темп новых слов, бюджет повторений, напоминание, часовой пояс"""

ADMIN_HELP = """<b>Для администраторов</b>
/add – добавить слово в общий словарь, оно появится у всех учеников.

<b>Формат /add</b>
<code>ξέρω
знать; уметь
ξέρω να + глагол = уметь
Δεν *ξέρω* πού είναι η στάση. | Я не знаю, где остановка.</code>

1-я строка – слово, 2-я – перевод. Строка с «|» – пример, слово в *звездочках* \
станет упражнением «вставь пропущенное». Остальные строки – заметки."""


class AddWord(StatesGroup):
    waiting_for_word = State()


def is_admin(settings: Settings, user: User) -> bool:
    return user.telegram_id in settings.admin_telegram_ids


@router.message(CommandStart())
@router.message(Command("help"))
async def start(message: Message, settings: Settings, user: User) -> None:
    text = f"{HELP}\n\n{ADMIN_HELP}" if is_admin(settings, user) else HELP
    await message.answer(text)


@router.my_chat_member()
async def chat_member_changed(event: ChatMemberUpdated, user: User) -> None:
    # The middleware already cleared blocked_at, so "unblocked" needs no handling.
    if event.new_chat_member.status == ChatMemberStatus.KICKED:
        user.blocked_at = datetime.now(UTC)


@router.message(Command("add"))
async def add_command(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    settings: Settings,
) -> None:
    if not is_admin(settings, user):
        await message.answer(
            "Добавлять слова в общий словарь могут только администраторы."
        )
        return
    if command.args:
        await _add(message, command.args, session, user)
        return
    await state.set_state(AddWord.waiting_for_word)
    await message.answer("Пришли слово в формате из /help.")


@router.message(AddWord.waiting_for_word, F.text, ~F.text.startswith("/"))
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
        apply_settings(user, change, datetime.now(UTC))
    await message.answer(settings_text(user), reply_markup=settings_keyboard(user))


@router.callback_query(Setting.filter())
async def setting_button(
    query: CallbackQuery, callback_data: Setting, user: User
) -> None:
    value = callback_data.value
    if callback_data.key == "remind" and value.isdecimal() and len(value) == 4:
        value = f"{value[:2]}:{value[2:]}"
    try:
        # Callback data is client-supplied: it goes through the same validation as text.
        change = parse_settings(f"{callback_data.key} {value}")
    except AppError as err:
        await ack(query, str(err), show_alert=True)
        return
    apply_settings(user, change, datetime.now(UTC))
    await ack(query, "Сохранено")
    if isinstance(query.message, Message):
        try:
            await query.message.edit_text(
                settings_text(user), reply_markup=settings_keyboard(user)
            )
        except TelegramBadRequest:
            pass


@router.message(Command("stats"))
async def stats(message: Message, session: AsyncSession, user: User) -> None:
    await message.answer(stats_text(await get_stats(session, user, datetime.now(UTC))))
