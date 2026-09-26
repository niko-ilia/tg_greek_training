"""Application settings loaded from environment variables."""

from __future__ import annotations

from datetime import time
from typing import Annotated

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """The part of the config that migrations need, without the bot token."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://greek:greek@127.0.0.1:5447/greek_trainer"


class Settings(DatabaseSettings):
    """Runtime configuration; see `.env.example` for every variable."""

    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True
    )

    bot_token: str
    # ALLOWED_TELEGRAM_IDS is the pre-public name, kept so existing deploys keep working.
    admin_telegram_ids: Annotated[frozenset[int], NoDecode] = Field(
        default=frozenset(),
        validation_alias=AliasChoices("ADMIN_TELEGRAM_IDS", "ALLOWED_TELEGRAM_IDS"),
    )
    timezone: str = "Europe/Nicosia"
    reminder_time: time = time(19, 0)
    daily_new_words: int = 30
    daily_review_budget: int = 150
    desired_retention: float = 0.9
    tts_voice: str = "el-GR-AthinaNeural"

    @field_validator("admin_telegram_ids", mode="before")
    @classmethod
    def _split_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.split(",") if part.strip()]
        return value


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # required fields come from the env
