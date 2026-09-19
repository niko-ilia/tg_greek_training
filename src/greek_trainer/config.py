"""Application settings loaded from environment variables."""

from __future__ import annotations

from datetime import time
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """The part of the config that migrations need, without the bot token."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://greek:greek@127.0.0.1:5447/greek_trainer"


class Settings(DatabaseSettings):
    """Runtime configuration; see `.env.example` for every variable."""

    bot_token: str
    allowed_telegram_ids: Annotated[frozenset[int], NoDecode, Field(min_length=1)]
    timezone: str = "Europe/Nicosia"
    reminder_time: time = time(19, 0)
    daily_new_cards: int = 20
    desired_retention: float = 0.9
    tts_voice: str = "el-GR-AthinaNeural"

    @field_validator("allowed_telegram_ids", mode="before")
    @classmethod
    def _split_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.split(",") if part.strip()]
        return value


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
