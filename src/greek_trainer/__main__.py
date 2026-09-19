"""Entry point: `python -m greek_trainer`."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from greek_trainer.bot.handlers import check, review, words
from greek_trainer.bot.middlewares import AllowedUsersMiddleware, DbSessionMiddleware
from greek_trainer.bot.reminders import run_reminders
from greek_trainer.config import load_settings
from greek_trainer.db.session import create_engine, create_session_factory
from greek_trainer.domain.srs import build_scheduler

COMMANDS = [
    BotCommand(command="review", description="Повторение"),
    BotCommand(command="check", description="Отметить знакомые слова"),
    BotCommand(command="add", description="Добавить слово"),
    BotCommand(command="stats", description="Статистика"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="help", description="Как пользоваться"),
]


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
        settings=settings, fsrs_scheduler=build_scheduler(settings.desired_retention)
    )
    dp.update.outer_middleware(AllowedUsersMiddleware(settings.allowed_telegram_ids))
    dp.update.middleware(DbSessionMiddleware(session_factory, settings))
    dp.include_routers(words.router, review.router, check.router)

    reminders: asyncio.Task[None] | None = None
    try:
        await bot.set_my_commands(COMMANDS)
        reminders = asyncio.create_task(
            run_reminders(bot, session_factory, settings.allowed_telegram_ids)
        )
        await dp.start_polling(bot)
    finally:
        if reminders is not None:
            reminders.cancel()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
