from datetime import time

import pytest

from greek_trainer.domain.settings_input import parse_settings
from greek_trainer.errors import ParseError


def test_limit_and_time() -> None:
    change = parse_settings("20 9:30")
    assert change.daily_new_cards == 20 and change.reminder_time == time(9, 30)
    assert not change.reminders_off


def test_off_and_limits() -> None:
    assert parse_settings("off").reminders_off
    with pytest.raises(ParseError):
        parse_settings("0")
    with pytest.raises(ParseError):
        parse_settings("25:00")
    with pytest.raises(ParseError):
        parse_settings("²")
