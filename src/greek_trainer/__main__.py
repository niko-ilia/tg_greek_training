"""Entry point: `python -m greek_trainer`."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
)

from greek_trainer.bot.fsm import DbStorage
from greek_trainer.bot.handlers import check, review, words
from greek_trainer.bot.middlewares import (
    DbSessionMiddleware,
    PrivateChatsOnlyMiddleware,
)
from greek_trainer.bot.reminders import run_reminders
from greek_trainer.config import load_settings
from greek_trainer.db.session import create_engine, create_session_factory
from greek_trainer.domain.srs import build_scheduler

COMMANDS = [
    BotCommand(command="review", description="Повторение"),
    BotCommand(command="check", description="Отметить знакомые слова"),
    BotCommand(command="stats", description="Статистика"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="help", description="Как пользоваться"),
]
ADMIN_COMMANDS = [
    *COMMANDS[:2],
    BotCommand(command="add", description="Добавить слово"),
    *COMMANDS[2:],
]


async def set_commands(bot: Bot, admin_telegram_ids: frozenset[int]) -> None:
    await bot.set_my_commands(COMMANDS, scope=BotCommandScopeAllPrivateChats())
    for admin_id in admin_telegram_ids:
        try:
            await bot.set_my_commands(
                ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except TelegramBadRequest as err:
            # The admin has not opened the bot yet; commands come on the next start.
            logging.warning("Admin commands for %s not set: %s", admin_id, err)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = load_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    bot = Bot(
        settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher(
        storage=DbStorage(session_factory),
        settings=settings,
        fsrs_scheduler=build_scheduler(settings.desired_retention),
    )
    dp.update.outer_middleware(PrivateChatsOnlyMiddleware())
    dp.update.middleware(DbSessionMiddleware(session_factory, settings))
    dp.include_routers(words.router, review.router, check.router)

    reminders: asyncio.Task[None] | None = None
    try:
        await set_commands(bot, settings.admin_telegram_ids)
        reminders = asyncio.create_task(run_reminders(bot, session_factory))
        await dp.start_polling(bot)
    finally:
        if reminders is not None:
            reminders.cancel()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
