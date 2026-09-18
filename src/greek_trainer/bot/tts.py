"""Greek pronunciation via Microsoft Edge neural voices (free, no API key)."""

from __future__ import annotations

import logging

import aiohttp
import edge_tts
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from greek_trainer.db.models import TtsCache

log = logging.getLogger(__name__)

# Slightly slower than native speed, which is easier to follow at A1.
_RATE = "-10%"


async def synthesize(text: str, voice: str) -> bytes:
    communicate = edge_tts.Communicate(text, voice, rate=_RATE)
    chunks = [
        chunk["data"]
        async for chunk in communicate.stream()
        if chunk["type"] == "audio" and "data" in chunk
    ]
    return b"".join(chunks)


def _file_id(message: Message) -> str | None:
    # Telegram may deliver an MP3 sent as voice back as audio or a document.
    media = message.voice or message.audio or message.document
    return media.file_id if media else None


async def send_pronunciation(
    bot: Bot, session: AsyncSession, chat_id: int, text: str, voice: str
) -> None:
    """Send `text` as a voice message; audio problems never break a review."""
    cached = await session.get(TtsCache, (voice, text))
    try:
        if cached is not None:
            await bot.send_voice(chat_id, cached.telegram_file_id)
            return
        audio = await synthesize(text, voice)
        message = await bot.send_voice(
            chat_id, BufferedInputFile(audio, filename="greek.mp3")
        )
    except (edge_tts.exceptions.EdgeTTSException, aiohttp.ClientError) as err:
        log.warning("TTS failed for %r: %s", text, err)
        return
    except TelegramAPIError as err:
        log.warning("Sending pronunciation failed for %r: %s", text, err)
        return
    file_id = _file_id(message)
    if file_id is not None:
        session.add(TtsCache(voice=voice, text=text, telegram_file_id=file_id))
