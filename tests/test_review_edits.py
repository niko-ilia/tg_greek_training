from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramBadRequest

from greek_trainer.bot.handlers.review import CAPTION_LIMIT, _replace_card


def _voice_card(edit_error: Exception | None = None) -> MagicMock:
    message = MagicMock()
    message.chat.id = 42
    message.edit_caption = AsyncMock(side_effect=edit_error)
    message.edit_reply_markup = AsyncMock()
    return message


def _bad_request(text: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=MagicMock(), message=text)


async def test_edits_the_caption_in_place() -> None:
    bot, message = MagicMock(send_message=AsyncMock()), _voice_card()
    await _replace_card(bot, message, "answer", None)
    message.edit_caption.assert_awaited_once()
    bot.send_message.assert_not_awaited()


async def test_too_long_for_a_caption_goes_out_as_text() -> None:
    bot, message = MagicMock(send_message=AsyncMock()), _voice_card()
    await _replace_card(bot, message, "x" * (CAPTION_LIMIT + 1), None)
    message.edit_caption.assert_not_awaited()
    message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
    bot.send_message.assert_awaited_once()


async def test_failed_edit_falls_back_to_a_new_message() -> None:
    bot = MagicMock(send_message=AsyncMock())
    message = _voice_card(_bad_request("Bad Request: message to edit not found"))
    await _replace_card(bot, message, "answer", None)
    bot.send_message.assert_awaited_once()


async def test_repeated_tap_does_not_duplicate_the_answer() -> None:
    bot = MagicMock(send_message=AsyncMock())
    message = _voice_card(_bad_request("Bad Request: message is not modified"))
    await _replace_card(bot, message, "answer", None)
    bot.send_message.assert_not_awaited()
