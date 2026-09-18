from datetime import UTC, datetime, timedelta

import fsrs
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from greek_trainer.db.models import CardType, ReviewLog, User
from greek_trainer.domain.srs import build_scheduler
from greek_trainer.domain.word_input import parse_word
from greek_trainer.errors import DuplicateWordError
from greek_trainer.services import (
    add_word,
    get_stats,
    next_card,
    record_review,
)

NOW = datetime(2026, 9, 18, 9, 0, tzinfo=UTC)
XERO = "ξέρω\nзнать\nΔεν *ξέρω*. | Я не знаю.\nΞέρω. | Я знаю."
SCHEDULER = build_scheduler(0.9)


async def test_add_word_creates_cards(session: AsyncSession, user: User) -> None:
    word = await add_word(session, user, parse_word(XERO), NOW)
    types = sorted(card.card_type for card in word.cards)
    assert types == [CardType.CLOZE, CardType.RECALL, CardType.RECOGNITION]
    assert all(card.due == NOW and card.last_review is None for card in word.cards)


async def test_duplicate_word_ignores_accents(
    session: AsyncSession, user: User
) -> None:
    await add_word(session, user, parse_word(XERO), NOW)
    with pytest.raises(DuplicateWordError):
        await add_word(session, user, parse_word("Ξερω\nзнать"), NOW)


async def test_siblings_are_buried_after_a_review(
    session: AsyncSession, user: User
) -> None:
    await add_word(session, user, parse_word(XERO), NOW)
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


async def test_daily_new_card_limit(session: AsyncSession, user: User) -> None:
    user.daily_new_cards = 1
    await add_word(session, user, parse_word("ναι\nда"), NOW)
    await add_word(session, user, parse_word("όχι\nнет"), NOW)

    first = await next_card(session, user, NOW)
    assert first is not None and first.word.lemma == "ναι"
    record_review(session, SCHEDULER, first, fsrs.Rating.Easy, NOW)
    await session.flush()

    assert await next_card(session, user, NOW) is None
    # New cards follow the order words were added: ναι's recall card goes first.
    tomorrow = await next_card(session, user, NOW + timedelta(days=1))
    assert tomorrow is not None
    assert (tomorrow.word.lemma, tomorrow.card_type) == ("ναι", CardType.RECALL)


async def test_record_review_logs_and_bumps_version(
    session: AsyncSession, user: User
) -> None:
    word = await add_word(session, user, parse_word("ναι\nда"), NOW)
    card = word.cards[0]
    record_review(session, SCHEDULER, card, fsrs.Rating.Again, NOW, answer_text="ναί")
    await session.flush()

    assert card.version == 1 and card.last_review == NOW
    log = await session.scalar(select(ReviewLog).where(ReviewLog.card_id == card.id))
    assert log is not None
    assert log.state_before is None and log.answer_text == "ναί"

    stats = await get_stats(session, user, NOW)
    assert (stats.words, stats.cards, stats.reviewed_today) == (1, 2, 1)
    assert stats.retention_30d is None
