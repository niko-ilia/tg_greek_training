from datetime import UTC, datetime, timedelta

import fsrs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from greek_trainer.db.models import Card, ReviewLog, User
from greek_trainer.domain.srs import build_scheduler
from greek_trainer.domain.word_input import parse_word
from greek_trainer.services import (
    add_word,
    advance_check,
    count_unchecked_words,
    deal_missing_cards,
    get_stats,
    mark_word_known,
    next_card,
    next_unchecked_word,
)

NOW = datetime(2026, 9, 19, 9, 0, tzinfo=UTC)
SCHEDULER = build_scheduler(0.9)


async def test_check_walks_unseen_words_and_spares_the_daily_limit(
    session: AsyncSession, user: User
) -> None:
    user.daily_new_words = 1
    for text in ("ναι\nда\nΝαι, *ναι*. | Да, да.", "όχι\nнет"):
        await add_word(session, parse_word(text))
    await deal_missing_cards(session, user, NOW)

    assert await count_unchecked_words(session, user) == 2
    first = await next_unchecked_word(session, user)
    assert first is not None and first.lemma == "ναι"
    assert await mark_word_known(session, SCHEDULER, user, first, NOW)
    assert not await mark_word_known(session, SCHEDULER, user, first, NOW)
    await session.flush()

    cards = list(await session.scalars(select(Card).where(Card.word_id == first.id)))
    assert len(cards) == 3
    assert all(
        card.last_review == NOW and card.due > NOW + timedelta(days=1) for card in cards
    )
    assert all(card.state == fsrs.State.Review.value for card in cards)
    logs = list(await session.scalars(select(ReviewLog)))
    assert len(logs) == 3 and all(log.is_triage for log in logs)

    assert await count_unchecked_words(session, user) == 1
    second = await next_unchecked_word(session, user)
    assert second is not None and second.lemma == "όχι"
    assert await advance_check(session, user, second)
    assert not await advance_check(session, user, second)
    assert not await mark_word_known(session, SCHEDULER, user, first, NOW)  # stale
    assert await next_unchecked_word(session, user) is None
    assert (await get_stats(session, user, NOW)).reviewed_today == 0

    # Triage did not spend today's new-card budget: όχι still comes as new.
    card = await next_card(session, user, NOW)
    assert card is not None and card.word_id == second.id and card.last_review is None
