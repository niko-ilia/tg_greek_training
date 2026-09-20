"""Answering a callback query, best effort."""

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery


async def ack(
    query: CallbackQuery, text: str | None = None, *, show_alert: bool = False
) -> None:
    """Clear the button's spinner.

    A query Telegram has already expired ("query is too old") must not abort the
    handler: the tap would be lost and the update's transaction rolled back.
    """
    try:
        await query.answer(text, show_alert=show_alert)
    except TelegramBadRequest:
        pass
