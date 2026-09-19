from datetime import UTC, datetime, timedelta

import fsrs
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from greek_trainer.config import Settings
from greek_trainer.db.models import Card, CardType, ReviewLog, User, Word
from greek_trainer.domain.srs import build_scheduler, learning_day_start
from greek_trainer.domain.word_input import parse_word
from greek_trainer.errors import DuplicateWordError
from greek_trainer.services import (
    LEARN_AHEAD,
    MIN_REPEAT_GAP,
    STRUGGLE_AGAIN,
    add_word,
    deal_missing_cards,
    get_or_create_user,
    get_pace,
    get_stats,
    next_card,
    next_due_at,
    override_pace_today,
    record_review,
    repeat_gap_ends_at,
)

NOW = datetime(2026, 9, 18, 9, 0, tzinfo=UTC)
XERO = "ξέρω\nзнать\nΔεν *ξέρω*. | Я не знаю.\nΞέρω. | Я знаю."
SCHEDULER = build_scheduler(0.9)


async def _add(session: AsyncSession, user: User, text: str) -> Word:
    word = await add_word(session, parse_word(text))
    await deal_missing_cards(session, user, NOW)
    return word


async def _cards(session: AsyncSession, user: User) -> list[Card]:
    stmt = select(Card).where(Card.user_id == user.id).order_by(Card.id)
    return list(await session.scalars(stmt))


async def test_dealing_creates_each_card_once(
    session: AsyncSession, user: User
) -> None:
    await _add(session, user, XERO)
    await deal_missing_cards(session, user, NOW + timedelta(days=1))
    cards = await _cards(session, user)
    types = sorted(card.card_type for card in cards)
    assert types == [CardType.CLOZE, CardType.RECALL, CardType.RECOGNITION]
    assert all(card.due == NOW and card.last_review is None for card in cards)
    assert all(card.version == 0 and card.state == 1 for card in cards)


async def test_progress_is_per_learner_on_shared_words(
    session: AsyncSession, user: User, settings: Settings
) -> None:
    await _add(session, user, "ναι\nда")
    other = await get_or_create_user(session, 2, settings)
    await deal_missing_cards(session, other, NOW)
    # A newcomer gets cards for the words that were already there.
    assert (await get_stats(session, other, NOW)).cards == 2

    mine = await next_card(session, user, NOW)
    assert mine is not None
    record_review(session, SCHEDULER, mine, fsrs.Rating.Easy, NOW)
    await session.flush()

    theirs = await next_card(session, other, NOW)
    assert theirs is not None and theirs.id != mine.id
    assert theirs.word_id == mine.word_id and theirs.last_review is None
    assert (await get_stats(session, other, NOW)).reviewed_today == 0


async def test_duplicate_word_ignores_accents(
    session: AsyncSession, user: User
) -> None:
    await add_word(session, parse_word(XERO))
    with pytest.raises(DuplicateWordError):
        await add_word(session, parse_word("Ξερω\nзнать"))


async def test_siblings_are_buried_after_a_review(
    session: AsyncSession, user: User
) -> None:
    await _add(session, user, XERO)
    card = await next_card(session, user, NOW)
    assert card is not None and card.card_type is CardType.RECOGNITION

    record_review(session, SCHEDULER, card, fsrs.Rating.Good, NOW)
    await session.flush()

    later = NOW + timedelta(hours=1)
    again = await next_card(session, user, later)
    assert again is not None and again.id == card.id  # still in learning steps

    record_review(session, SCHEDULER, again, fsrs.Rating.Good, later)
    await session.flush()
    assert card.state == fsrs.State.Review.value
    assert await next_card(session, user, later) is None

    tomorrow = NOW + timedelta(days=1)
    sibling = await next_card(session, user, tomorrow)
    assert sibling is not None and sibling.card_type is not CardType.RECOGNITION


async def test_daily_ceiling_counts_words_not_cards(
    session: AsyncSession, user: User
) -> None:
    user.daily_new_words = 1
    await _add(session, user, "ναι\nда")
    await _add(session, user, "όχι\nнет")

    first = await next_card(session, user, NOW)
    assert first is not None and first.word.lemma == "ναι"
    record_review(session, SCHEDULER, first, fsrs.Rating.Easy, NOW)
    await session.flush()

    assert await next_card(session, user, NOW) is None
    assert await next_card(session, user, NOW, ignore_pace=True) is not None
    # Tomorrow ναι's second card is not a new word, so it is not held back by the ceiling.
    tomorrow = await next_card(session, user, NOW + timedelta(days=1))
    assert tomorrow is not None
    assert (tomorrow.word.lemma, tomorrow.card_type) == ("ναι", CardType.RECALL)


async def test_record_review_logs_and_bumps_version(
    session: AsyncSession, user: User
) -> None:
    await _add(session, user, "ναι\nда")
    card = (await _cards(session, user))[0]
    record_review(session, SCHEDULER, card, fsrs.Rating.Again, NOW, answer_text="ναί")
    await session.flush()

    assert card.version == 1 and card.last_review == NOW
    log = await session.scalar(select(ReviewLog).where(ReviewLog.card_id == card.id))
    assert log is not None
    assert log.state_before is None and log.answer_text == "ναί"

    stats = await get_stats(session, user, NOW)
    assert (stats.words, stats.cards, stats.reviewed_today) == (1, 2, 1)
    assert stats.retention_30d is None


async def test_override_lifts_the_brakes_for_today_only(
    session: AsyncSession, user: User
) -> None:
    user.daily_new_words = 1
    await _add(session, user, "ναι\nда")
    await _add(session, user, "όχι\nнет")
    first = await next_card(session, user, NOW)
    assert first is not None
    record_review(session, SCHEDULER, first, fsrs.Rating.Easy, NOW)
    await session.flush()
    assert await next_card(session, user, NOW) is None

    assert await override_pace_today(session, user, NOW)
    assert not await override_pace_today(session, user, NOW)  # double tap
    card = await next_card(session, user, NOW)
    assert card is not None and card.word.lemma == "όχι"
    assert not (await get_pace(session, user, NOW + timedelta(days=1))).overridden


async def test_forgetting_known_words_pauses_new_words(
    session: AsyncSession, user: User
) -> None:
    yesterday = NOW - timedelta(days=1)
    for lemma in ("ένα", "δύο", "τρία", "τέσσερα"):
        await add_word(session, parse_word(f"{lemma}\nчисло"))
    await deal_missing_cards(session, user, yesterday)
    for _ in range(STRUGGLE_AGAIN):
        card = await next_card(session, user, yesterday)
        assert card is not None and card.last_review is None
        record_review(session, SCHEDULER, card, fsrs.Rating.Good, yesterday)
        await session.flush()
    for _ in range(STRUGGLE_AGAIN):
        card = await next_card(session, user, NOW)
        assert card is not None and card.last_review is not None
        record_review(session, SCHEDULER, card, fsrs.Rating.Again, NOW)
        await session.flush()

    pace = await get_pace(session, user, NOW)
    assert pace.struggling and not pace.allows_new_cards
    # The forgotten cards themselves keep coming back: learning is never blocked.
    again = await next_card(session, user, NOW + timedelta(minutes=2))
    assert again is not None and again.last_review is not None


async def test_forgetting_brand_new_words_is_not_struggling(
    session: AsyncSession, user: User
) -> None:
    for lemma in ("ένα", "δύο", "τρία", "τέσσερα"):
        await _add(session, user, f"{lemma}\nчисло")
    for _ in range(STRUGGLE_AGAIN):
        card = await next_card(session, user, NOW)
        assert card is not None and card.last_review is None
        record_review(session, SCHEDULER, card, fsrs.Rating.Again, NOW)
        await session.flush()

    assert not (await get_pace(session, user, NOW)).struggling
    fourth = await next_card(session, user, NOW)
    assert fourth is not None and fourth.word.lemma == "τέσσερα"


async def test_a_heavy_week_ahead_pauses_new_words(
    session: AsyncSession, user: User
) -> None:
    user.daily_review_budget = 1
    await _add(session, user, "ναι\nда")
    await _add(session, user, "όχι\nнет")
    card = await next_card(session, user, NOW)
    assert card is not None
    record_review(session, SCHEDULER, card, fsrs.Rating.Good, NOW)
    await session.flush()

    pace = await get_pace(session, user, NOW)
    assert pace.forecast_peak >= 1 and not pace.allows_new_cards


async def test_a_learning_step_due_soon_is_shown_instead_of_waiting(
    session: AsyncSession, user: User
) -> None:
    await _add(session, user, "ναι\nда")
    card = await next_card(session, user, NOW)
    assert card is not None
    record_review(session, SCHEDULER, card, fsrs.Rating.Good, NOW)
    await session.flush()
    later = NOW + MIN_REPEAT_GAP + timedelta(minutes=1)
    assert card.due > later  # the next learning step is still ahead

    early = await next_card(session, user, later)
    assert early is not None and early.id == card.id
    assert card.due - later <= LEARN_AHEAD


async def test_a_card_just_answered_is_not_asked_again_right_away(
    session: AsyncSession, user: User
) -> None:
    await _add(session, user, "έχω\nиметь")
    card = await next_card(session, user, NOW)
    assert card is not None
    record_review(session, SCHEDULER, card, fsrs.Rating.Again, NOW)
    await session.flush()

    # Its answer is still on screen: nothing to show until the step is due.
    assert await next_card(session, user, NOW + timedelta(seconds=10)) is None
    back = await next_card(session, user, card.due)
    assert back is not None and back.id == card.id


async def test_a_buried_overdue_card_does_not_loop_the_session(
    session: AsyncSession, user: User
) -> None:
    two_days_ago, yesterday = NOW - timedelta(days=2), NOW - timedelta(days=1)
    await add_word(session, parse_word("ναι\nда"))
    await deal_missing_cards(session, user, two_days_ago)
    recognition, recall = await _cards(session, user)
    record_review(session, SCHEDULER, recognition, fsrs.Rating.Good, two_days_ago)
    record_review(session, SCHEDULER, recall, fsrs.Rating.Good, yesterday)
    await session.flush()
    # Both are overdue learning steps today; answering one buries the other.
    assert recognition.due < NOW and recall.due < NOW
    record_review(session, SCHEDULER, recognition, fsrs.Rating.Good, NOW)
    await session.flush()

    assert await next_card(session, user, NOW) is None
    assert await repeat_gap_ends_at(session, user, NOW) is None  # no "Дальше" loop
    tomorrow_start = learning_day_start(NOW, user.timezone) + timedelta(days=1)
    assert await next_due_at(session, user, NOW) == tomorrow_start


async def test_just_forgotten_card_says_when_it_returns(
    session: AsyncSession, user: User
) -> None:
    await _add(session, user, "έχω\nиметь")
    card = await next_card(session, user, NOW)
    assert card is not None
    record_review(session, SCHEDULER, card, fsrs.Rating.Again, NOW)
    await session.flush()
    assert await repeat_gap_ends_at(session, user, NOW) == card.due


async def test_a_new_word_forgotten_on_every_step_is_not_struggling(
    session: AsyncSession, user: User
) -> None:
    await _add(session, user, "δύσκολο\nтрудный")
    await _add(session, user, "εύκολο\nлёгкий")
    card = await next_card(session, user, NOW)
    assert card is not None
    for minute in range(STRUGGLE_AGAIN):
        record_review(
            session, SCHEDULER, card, fsrs.Rating.Again, NOW + timedelta(minutes=minute)
        )
        await session.flush()

    later = NOW + timedelta(minutes=STRUGGLE_AGAIN)
    assert not (await get_pace(session, user, later)).struggling


async def test_second_day_cards_of_a_started_word_do_not_spend_the_ceiling(
    session: AsyncSession, user: User
) -> None:
    user.daily_new_words = 1
    await _add(session, user, "ναι\nда")
    await _add(session, user, "όχι\nнет")
    first = await next_card(session, user, NOW)
    assert first is not None and first.word.lemma == "ναι"
    record_review(session, SCHEDULER, first, fsrs.Rating.Easy, NOW)
    await session.flush()

    tomorrow = NOW + timedelta(days=1)
    recall = await next_card(session, user, tomorrow)
    assert recall is not None and recall.word.lemma == "ναι"
    record_review(session, SCHEDULER, recall, fsrs.Rating.Easy, tomorrow)
    await session.flush()
    new_word = await next_card(session, user, tomorrow)
    assert new_word is not None and new_word.word.lemma == "όχι"
