import pytest
from pydantic import ValidationError

from greek_trainer.config import Settings


def test_allowed_ids_are_parsed_from_a_comma_list() -> None:
    settings = Settings(bot_token="t", allowed_telegram_ids="1, 2,")  # type: ignore[arg-type]
    assert settings.allowed_telegram_ids == {1, 2}


def test_empty_allowlist_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(bot_token="t", allowed_telegram_ids="")  # type: ignore[arg-type]
