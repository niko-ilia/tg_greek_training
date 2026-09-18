"""Application settings loaded from environment variables."""

from __future__ import annotations

from datetime import time

from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """The part of the config that migrations need, without the bot token."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://greek:greek@127.0.0.1:5447/greek_trainer"


class Settings(DatabaseSettings):
    """Runtime configuration; see `.env.example` for every variable."""

    bot_token: str
    owner_telegram_id: int
    timezone: str = "Europe/Nicosia"
    reminder_time: time = time(19, 0)
    daily_new_cards: int = 10
    desired_retention: float = 0.9
    tts_voice: str = "el-GR-AthinaNeural"


def load_settings() -> Settings:
    """Read settings from the environment (and `.env` when present)."""
    return Settings()  # type: ignore[call-arg]
