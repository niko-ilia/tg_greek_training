"""Bridge between the ORM `Card` and the `fsrs` scheduler."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import fsrs

from greek_trainer.db.models import Card

# Like Anki, a "day" rolls over at 04:00 local time, so a late-night session
# still counts towards the day it started in.
DAY_ROLLOVER = time(4, 0)


def build_scheduler(desired_retention: float) -> fsrs.Scheduler:
    return fsrs.Scheduler(desired_retention=desired_retention)


def new_fsrs_card(now: datetime) -> fsrs.Card:
    return fsrs.Card(due=now)


def to_fsrs(card: Card) -> fsrs.Card:
    return fsrs.Card(
        card_id=card.id,
        state=fsrs.State(card.state),
        step=card.step,
        stability=card.stability,
        difficulty=card.difficulty,
        due=card.due,
        last_review=card.last_review,
    )


def apply_fsrs(card: Card, scheduled: fsrs.Card) -> None:
    card.state = scheduled.state.value
    card.step = scheduled.step
    card.stability = scheduled.stability
    card.difficulty = scheduled.difficulty
    card.due = scheduled.due
    card.last_review = scheduled.last_review


def preview_intervals(
    scheduler: fsrs.Scheduler, card: Card, now: datetime
) -> dict[fsrs.Rating, timedelta]:
    """How far each rating would push the next review, for the rating buttons."""
    previews = {}
    for rating in fsrs.Rating:
        scheduled, _ = scheduler.review_card(to_fsrs(card), rating, review_datetime=now)
        previews[rating] = scheduled.due - now
    return previews


def learning_day_start(now: datetime, tz_name: str) -> datetime:
    local = now.astimezone(ZoneInfo(tz_name))
    start = local.replace(
        hour=DAY_ROLLOVER.hour, minute=DAY_ROLLOVER.minute, second=0, microsecond=0
    )
    if local < start:
        start -= timedelta(days=1)
    return start
