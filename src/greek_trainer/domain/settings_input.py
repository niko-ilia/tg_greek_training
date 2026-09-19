"""Parsing of `/settings` arguments (typed text or a settings-menu button).

Keyword form: `words 30|off`, `budget 200`, `remind 19:30|off`, `tz Europe/Moscow`.
Bare forms kept for convenience: a number sets words, HH:MM sets the reminder,
`off` turns reminders off.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from greek_trainer.errors import ParseError

WORDS_RANGE = range(1, 201)
BUDGET_RANGE = range(20, 1001)
_OFF = ("off", "выкл", "нет")
_HINT = (
    "Примеры: /settings words 30, /settings words off, /settings budget 200, "
    "/settings remind 19:30, /settings remind off, /settings tz Europe/Moscow."
)


@dataclass(frozen=True)
class SettingsChange:
    daily_new_words: int | None = None
    no_word_ceiling: bool = False
    review_budget: int | None = None
    reminder_time: time | None = None
    reminders_off: bool = False
    timezone: str | None = None


def _number(token: str, allowed: range, what: str) -> int:
    if not token.isdecimal() or int(token) not in allowed:
        raise ParseError(f"{what}: от {allowed.start} до {allowed.stop - 1}.")
    return int(token)


def _clock(token: str) -> time:
    try:
        return datetime.strptime(token, "%H:%M").time()
    except ValueError:
        raise ParseError(f"Время напоминания в виде ЧЧ:ММ, а не «{token}».") from None


def _zone(token: str) -> str:
    try:
        ZoneInfo(token)
    except (ZoneInfoNotFoundError, ValueError):
        raise ParseError(
            f"Не знаю часовой пояс «{token}». Пример: Europe/Moscow, Asia/Bangkok."
        ) from None
    return token


def parse_settings(args: str) -> SettingsChange:
    tokens = args.split()
    change = SettingsChange()
    while tokens:
        token = tokens.pop(0)
        key = token.lower()
        if key in ("words", "слов", "слова") and tokens:
            value = tokens.pop(0)
            if value.lower() in _OFF:
                change = replace(change, no_word_ceiling=True)
            else:
                words = _number(value, WORDS_RANGE, "Новых слов в день")
                change = replace(change, daily_new_words=words)
        elif key in ("budget", "бюджет") and tokens:
            budget = _number(tokens.pop(0), BUDGET_RANGE, "Бюджет повторений в день")
            change = replace(change, review_budget=budget)
        elif key in ("remind", "напоминание") and tokens:
            value = tokens.pop(0)
            if value.lower() in _OFF:
                change = replace(change, reminders_off=True)
            else:
                change = replace(change, reminder_time=_clock(value))
        elif key in ("tz", "пояс") and tokens:
            change = replace(change, timezone=_zone(tokens.pop(0)))
        elif token.isdecimal():
            change = replace(
                change, daily_new_words=_number(token, WORDS_RANGE, "Новых слов в день")
            )
        elif key in _OFF:
            change = replace(change, reminders_off=True)
        elif ":" in token:
            change = replace(change, reminder_time=_clock(token))
        else:
            raise ParseError(f"Не понял «{token}». {_HINT}")
    return change
