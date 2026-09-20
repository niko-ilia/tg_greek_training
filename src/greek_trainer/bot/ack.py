"""Answering a callback query, best effort."""

import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery

logger = logging.getLogger(__name__)


async def ack(
    query: CallbackQuery, text: str | None = None, *, show_alert: bool = False
) -> None:
    """Clear the button's spinner.

    Only the spinner rides on this call, so no failure of it may abort the
    handler: the tap would be lost and the update's transaction rolled back
    along with the review it recorded. A query Telegram has already expired
    ("query is too old") is the common case, a timeout or a 5xx the rest.
    """
    try:
        await query.answer(text, show_alert=show_alert)
    except TelegramAPIError as err:
        logger.warning("Callback query %s not answered: %s", query.id, err)
