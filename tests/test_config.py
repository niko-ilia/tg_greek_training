import pytest

from greek_trainer.config import Settings


@pytest.mark.parametrize("env_name", ["ADMIN_TELEGRAM_IDS", "ALLOWED_TELEGRAM_IDS"])
def test_admin_ids_come_from_either_env_name(
    monkeypatch: pytest.MonkeyPatch, env_name: str
) -> None:
    monkeypatch.setenv(env_name, "1, 2,")
    assert Settings(bot_token="t").admin_telegram_ids == {1, 2}


def test_no_admins_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_TELEGRAM_IDS", raising=False)
    monkeypatch.delenv("ALLOWED_TELEGRAM_IDS", raising=False)
    assert Settings(bot_token="t", _env_file=None).admin_telegram_ids == frozenset()  # type: ignore[call-arg]
