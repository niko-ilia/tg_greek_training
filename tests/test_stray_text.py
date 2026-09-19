from datetime import UTC, datetime
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TelegramUser

from greek_trainer.bot.handlers import check, review, words


async def test_text_with_no_card_waiting_gets_a_hint() -> None:
    dp = Dispatcher()
    dp.include_routers(words.router, review.router, check.router)
    bot = AsyncMock(spec=Bot)
    bot.id = 1
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=42, type="private"),
        from_user=TelegramUser(id=42, is_bot=False, first_name="L"),
        text="ξέρω",
    )

    await dp.feed_update(bot, Update(update_id=1, message=message))

    sent = bot.call_args.args[0]
    assert isinstance(sent, SendMessage)
    assert "/review" in sent.text
