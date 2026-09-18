from datetime import UTC, datetime

from greek_trainer.domain.srs import learning_day_start


def test_day_rolls_over_at_4am_local() -> None:
    # Nicosia is UTC+3 in September.
    late_night = datetime(2026, 9, 18, 23, 30, tzinfo=UTC)  # 02:30 local, 19th
    assert learning_day_start(late_night, "Europe/Nicosia").day == 18
    morning = datetime(2026, 9, 19, 2, 0, tzinfo=UTC)  # 05:00 local, 19th
    assert learning_day_start(morning, "Europe/Nicosia").day == 19
