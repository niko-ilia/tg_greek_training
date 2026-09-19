from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import Chat, TelegramObject, Update
from aiogram.types import User as TelegramUser
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from greek_trainer.config import Settings
from greek_trainer.services import (
    Visit,
    deal_missing_cards,
    get_or_create_user,
    record_visit,
)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]

_ACTION_LENGTH = 64


class PrivateChatsOnlyMiddleware(BaseMiddleware):
    """Cards and answers are per learner, so group chats and senderless updates are dropped."""

    async def __call__(
        self, handler: Handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        sender: TelegramUser | None = data.get("event_from_user")
        chat: Chat | None = data.get("event_chat")
        if sender is None or chat is None or chat.type != ChatType.PRIVATE:
            return None
        return await handler(event, data)


def _name(value: object) -> str:
    """aiogram hands content types and statuses out as enums; the journal wants the value."""
    return str(value.value if isinstance(value, Enum) else value)[:_ACTION_LENGTH]


def describe_update(update: Update) -> tuple[str, str]:
    """Kind and action for the usage journal; message text is never recorded."""
    if update.message is not None:
        text = update.message.text
        if text is not None and text.startswith("/"):
            command = text.split()[0][1:].split("@")[0]
            return "command", command[:_ACTION_LENGTH] or "/"
        if text is not None:
            return "text", "text"
        return "message", _name(update.message.content_type)
    if update.callback_query is not None:
        prefix = (update.callback_query.data or "").split(":")[0]
        return "button", prefix[:_ACTION_LENGTH] or "unknown"
    if update.my_chat_member is not None:
        return "status", _name(update.my_chat_member.new_chat_member.status)
    return "other", _name(update.event_type)


class DbSessionMiddleware(BaseMiddleware):
    """One transaction per update; handlers get `session` and the learner `user`.

    Every update also refreshes the learner's profile and adds a usage event.
    """

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings

    async def __call__(
        self, handler: Handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        sender: TelegramUser = data["event_from_user"]
        now = datetime.now(UTC)
        async with self.session_factory() as session, session.begin():
            data["session"] = session
            user = await get_or_create_user(session, sender.id, self.settings)
            if isinstance(event, Update):
                kind, action = describe_update(event)
                visit = Visit(
                    username=sender.username,
                    first_name=sender.first_name,
                    last_name=sender.last_name,
                    language_code=sender.language_code,
                    kind=kind,
                    action=action,
                )
                record_visit(session, user, visit, now)
            await deal_missing_cards(session, user, now)
            data["user"] = user
            return await handler(event, data)
