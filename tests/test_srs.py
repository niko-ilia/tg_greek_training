from datetime import datetime
from zoneinfo import ZoneInfo

from greek_trainer.domain.srs import learning_day_start


def test_day_rolls_over_at_4am_local() -> None:
    nicosia = ZoneInfo("Europe/Nicosia")
    late_night = datetime(2026, 9, 19, 2, 30, tzinfo=nicosia)
    assert learning_day_start(late_night, "Europe/Nicosia").day == 18
    morning = datetime(2026, 9, 19, 5, 0, tzinfo=nicosia)
    assert learning_day_start(morning, "Europe/Nicosia").day == 19
