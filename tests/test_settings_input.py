from datetime import time

import pytest

from greek_trainer.domain.settings_input import parse_settings
from greek_trainer.errors import ParseError


def test_keyword_form() -> None:
    change = parse_settings("words 40 budget 200 remind 7:30 tz Europe/Moscow")
    assert change.daily_new_words == 40 and change.review_budget == 200
    assert change.reminder_time == time(7, 30) and change.timezone == "Europe/Moscow"


def test_bare_form_and_off() -> None:
    change = parse_settings("20 9:30")
    assert change.daily_new_words == 20 and change.reminder_time == time(9, 30)
    assert parse_settings("off").reminders_off
    assert parse_settings("words off").no_word_ceiling
    assert parse_settings("remind off").reminders_off


def test_mode_by_name_in_both_languages() -> None:
    assert parse_settings("mode recall").exercise_mode == "recall"
    assert parse_settings("режим узнавание").exercise_mode == "recognition"
    assert parse_settings("mode all").all_modes


@pytest.mark.parametrize(
    "args",
    [
        "0",
        "words 999",
        "budget 5",
        "25:00",
        "²",
        "tz Mars/Base",
        "tz ../etc",
        "x",
        "mode listening",
    ],
)
def test_rejects_bad_values(args: str) -> None:
    with pytest.raises(ParseError):
        parse_settings(args)
