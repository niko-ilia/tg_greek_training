from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.types import User as TelegramUser
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from greek_trainer.config import Settings
from greek_trainer.services import get_or_create_user

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class OwnerOnlyMiddleware(BaseMiddleware):
    """Personal bot: updates from anyone but the owner are dropped silently."""

    def __init__(self, owner_telegram_id: int) -> None:
        self.owner_telegram_id = owner_telegram_id

    async def __call__(
        self, handler: Handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        sender: TelegramUser | None = data.get("event_from_user")
        if sender is None or sender.id != self.owner_telegram_id:
            return None
        return await handler(event, data)


class DbSessionMiddleware(BaseMiddleware):
    """One transaction per update; handlers get `session` and the learner `user`."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings

    async def __call__(
        self, handler: Handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        sender: TelegramUser = data["event_from_user"]
        async with self.session_factory() as session, session.begin():
            data["session"] = session
            data["user"] = await get_or_create_user(session, sender.id, self.settings)
            return await handler(event, data)
