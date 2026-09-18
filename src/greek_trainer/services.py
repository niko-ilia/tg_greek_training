"""Use cases on top of the database: adding words, picking and grading cards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import fsrs
from sqlalchemy import case, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from greek_trainer.config import Settings
from greek_trainer.db.models import Card, CardType, Example, ReviewLog, User, Word
from greek_trainer.domain.greek import lemma_key
from greek_trainer.domain.srs import (
    apply_fsrs,
    learning_day_start,
    new_fsrs_card,
    to_fsrs,
)
from greek_trainer.domain.word_input import WordDraft
from greek_trainer.errors import DuplicateWordError


async def get_or_create_user(
    session: AsyncSession, telegram_id: int, settings: Settings
) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(
            telegram_id=telegram_id,
            timezone=settings.timezone,
            reminder_time=settings.reminder_time,
            daily_new_cards=settings.daily_new_cards,
        )
        session.add(user)
        await session.flush()
    return user


def _new_card(
    card_type: CardType, now: datetime, example: Example | None = None
) -> Card:
    card = Card(card_type=card_type, example=example, version=0)
    apply_fsrs(card, new_fsrs_card(now))
    return card


async def add_word(
    session: AsyncSession, user: User, draft: WordDraft, now: datetime
) -> Word:
    """Store a word with its examples and create its cards.

    Raises:
        DuplicateWordError: the learner already has this word.
    """
    key = lemma_key(draft.lemma)
    duplicate = await session.scalar(
        select(Word.id).where(Word.user_id == user.id, Word.lemma_key == key)
    )
    if duplicate is not None:
        raise DuplicateWordError(f"Слово «{draft.lemma}» уже есть в словаре.")

    examples = [
        Example(text_el=e.text_el, text_ru=e.text_ru, cloze_target=e.cloze_target)
        for e in draft.examples
    ]
    cards = [_new_card(CardType.RECOGNITION, now), _new_card(CardType.RECALL, now)]
    cards += [_new_card(CardType.CLOZE, now, e) for e in examples if e.cloze_target]
    word = Word(
        user_id=user.id,
        lemma=draft.lemma,
        lemma_key=key,
        translation=draft.translation,
        notes=draft.notes,
        examples=examples,
        cards=cards,
    )
    session.add(word)
    await session.flush()
    return word


async def _new_cards_started(session: AsyncSession, user: User, since: datetime) -> int:
    stmt = (
        select(func.count())
        .select_from(ReviewLog)
        .join(Card, Card.id == ReviewLog.card_id)
        .join(Word, Word.id == Card.word_id)
        .where(
            Word.user_id == user.id,
            ReviewLog.state_before.is_(None),
            ReviewLog.reviewed_at >= since,
        )
    )
    return (await session.scalar(stmt)) or 0


async def next_card(session: AsyncSession, user: User, now: datetime) -> Card | None:
    """Pick the next due card.

    Order: cards already in learning steps, then reviews by due date, then new
    cards in the order words were added. Siblings are buried: once any card of a
    word was reviewed today, the word's other cards wait until tomorrow, so
    recognition does not give away the answer to the recall drill.
    """
    day_start = learning_day_start(now, user.timezone)
    sibling = aliased(Card)
    sibling_reviewed_today = exists(
        select(ReviewLog.id)
        .join(sibling, sibling.id == ReviewLog.card_id)
        .where(
            sibling.word_id == Card.word_id,
            sibling.id != Card.id,
            ReviewLog.reviewed_at >= day_start,
        )
    )
    is_new = Card.last_review.is_(None)
    stmt = (
        select(Card)
        .join(Word, Word.id == Card.word_id)
        .where(Word.user_id == user.id, Card.due <= now, ~sibling_reviewed_today)
        .options(selectinload(Card.word).selectinload(Word.examples))
        .options(selectinload(Card.example))
        .order_by(
            case(
                (is_new, 2),
                (Card.state == fsrs.State.Review.value, 1),
                else_=0,
            ),
            case((is_new, Card.word_id), else_=0),
            Card.due,
            Card.id,
        )
        .limit(1)
    )
    if await _new_cards_started(session, user, day_start) >= user.daily_new_cards:
        stmt = stmt.where(~is_new)
    return await session.scalar(stmt)


async def next_due_at(session: AsyncSession, user: User) -> datetime | None:
    stmt = (
        select(func.min(Card.due))
        .join(Word, Word.id == Card.word_id)
        .where(Word.user_id == user.id, Card.last_review.is_not(None))
    )
    return await session.scalar(stmt)


async def due_count(session: AsyncSession, user: User, now: datetime) -> int:
    stmt = (
        select(func.count())
        .select_from(Card)
        .join(Word, Word.id == Card.word_id)
        .where(Word.user_id == user.id, Card.due <= now, Card.last_review.is_not(None))
    )
    return (await session.scalar(stmt)) or 0


async def get_card(session: AsyncSession, user: User, card_id: int) -> Card | None:
    stmt = (
        select(Card)
        .join(Word, Word.id == Card.word_id)
        .where(Card.id == card_id, Word.user_id == user.id)
        .options(selectinload(Card.word).selectinload(Word.examples))
        .options(selectinload(Card.example))
    )
    return await session.scalar(stmt)


def record_review(
    session: AsyncSession,
    scheduler: fsrs.Scheduler,
    card: Card,
    rating: fsrs.Rating,
    now: datetime,
    duration_ms: int | None = None,
    answer_text: str | None = None,
) -> None:
    state_before = None if card.last_review is None else card.state
    scheduled, _ = scheduler.review_card(to_fsrs(card), rating, review_datetime=now)
    apply_fsrs(card, scheduled)
    card.version += 1
    session.add(
        ReviewLog(
            card_id=card.id,
            rating=rating.value,
            reviewed_at=now,
            review_duration_ms=duration_ms,
            state_before=state_before,
            answer_text=answer_text,
        )
    )


@dataclass(frozen=True)
class Stats:
    words: int
    cards: int
    new_cards: int
    due_now: int
    reviewed_today: int
    retention_30d: float | None


async def get_stats(session: AsyncSession, user: User, now: datetime) -> Stats:
    day_start = learning_day_start(now, user.timezone)
    user_cards = (
        select(Card.id)
        .join(Word, Word.id == Card.word_id)
        .where(Word.user_id == user.id)
    ).subquery()
    words = await session.scalar(
        select(func.count()).select_from(Word).where(Word.user_id == user.id)
    )
    cards = await session.scalar(select(func.count()).select_from(user_cards))
    new_cards = await session.scalar(
        select(func.count())
        .select_from(Card)
        .where(Card.id.in_(select(user_cards.c.id)), Card.last_review.is_(None))
    )
    logs = select(ReviewLog).where(ReviewLog.card_id.in_(select(user_cards.c.id)))
    reviewed_today = await session.scalar(
        select(func.count()).select_from(
            logs.where(ReviewLog.reviewed_at >= day_start).subquery()
        )
    )
    # Retention counts only answers on cards in the Review state, as FSRS defines it.
    matured = logs.where(
        ReviewLog.reviewed_at >= now - timedelta(days=30),
        ReviewLog.state_before == fsrs.State.Review.value,
    ).subquery()
    total, passed = (
        await session.execute(
            select(func.count(), func.count().filter(matured.c.rating > 1)).select_from(
                matured
            )
        )
    ).one()
    return Stats(
        words=words or 0,
        cards=cards or 0,
        new_cards=new_cards or 0,
        due_now=await due_count(session, user, now),
        reviewed_today=reviewed_today or 0,
        retention_30d=passed / total if total else None,
    )
