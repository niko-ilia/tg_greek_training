"""Parsing of `/settings` arguments: a daily limit, a reminder time, or `off`."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, time

from greek_trainer.errors import ParseError


@dataclass(frozen=True)
class SettingsChange:
    daily_new_cards: int | None = None
    reminder_time: time | None = None
    reminders_off: bool = False


def parse_settings(args: str) -> SettingsChange:
    change = SettingsChange()
    for token in args.split():
        if token.isdigit():
            limit = int(token)
            if not 1 <= limit <= 100:
                raise ParseError("Новых карточек в день: от 1 до 100.")
            change = replace(change, daily_new_cards=limit)
        elif token.lower() in ("off", "выкл"):
            change = replace(change, reminders_off=True)
        else:
            try:
                reminder = datetime.strptime(token, "%H:%M").time()
            except ValueError:
                raise ParseError(
                    f"Не понял «{token}». Примеры: /settings 20, "
                    "/settings 20 19:30, /settings off."
                ) from None
            change = replace(change, reminder_time=reminder)
    return change
