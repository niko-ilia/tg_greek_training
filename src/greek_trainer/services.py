"""Use cases on top of the database: adding words, picking and grading cards."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import fsrs
from sqlalchemy import case, except_, exists, func, literal, select, union_all
from sqlalchemy.dialects.postgresql import insert
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


async def deal_missing_cards(session: AsyncSession, user: User, now: datetime) -> None:
    """Create the learner's cards for every dictionary word they don't have yet.

    Runs on each of the learner's updates instead of when a word is added: a word
    and a user created in concurrent transactions don't see each other, and this
    way they still meet on the learner's next message.
    """
    card_type = Card.__table__.c.card_type.type
    no_example = literal(None, Card.__table__.c.example_id.type)
    wanted = union_all(
        *(
            select(Word.id, literal(kind, card_type), no_example)
            for kind in (CardType.RECOGNITION, CardType.RECALL)
        ),
        select(Example.word_id, literal(CardType.CLOZE, card_type), Example.id).where(
            Example.cloze_target.is_not(None)
        ),
    )
    owned = select(Card.word_id, Card.card_type, Card.example_id).where(
        Card.user_id == user.id
    )
    missing = except_(wanted, owned).subquery()

    fresh = Card()
    apply_fsrs(fresh, new_fsrs_card(now))
    fsrs_columns = ["state", "step", "stability", "difficulty", "due", "last_review"]
    rows = select(
        literal(user.id),
        *missing.c,
        *(literal(getattr(fresh, c), Card.__table__.c[c].type) for c in fsrs_columns),
        literal(0),
    ).order_by(*missing.c)  # next_card breaks ties by id: recognition before recall
    columns = ["user_id", "word_id", "card_type", "example_id", *fsrs_columns]
    # DO NOTHING: two concurrent updates of one learner may deal the same cards.
    await session.execute(
        insert(Card).from_select([*columns, "version"], rows).on_conflict_do_nothing()
    )


async def add_word(session: AsyncSession, draft: WordDraft) -> Word:
    """Store a word with its examples in the shared dictionary.

    Learners get its cards from `deal_missing_cards`.

    Raises:
        DuplicateWordError: the dictionary already has this word.
    """
    key = lemma_key(draft.lemma)
    # Serializes concurrent adds of one word, so the second sees the first and
    # gets a DuplicateWordError instead of a unique violation.
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(key))))
    duplicate = await session.scalar(select(Word.id).where(Word.lemma_key == key))
    if duplicate is not None:
        raise DuplicateWordError(f"Слово «{draft.lemma}» уже есть в словаре.")

    examples = [
        Example(text_el=e.text_el, text_ru=e.text_ru, cloze_target=e.cloze_target)
        for e in draft.examples
    ]
    word = Word(
        lemma=draft.lemma,
        lemma_key=key,
        translation=draft.translation,
        notes=draft.notes,
        examples=examples,
    )
    session.add(word)
    await session.flush()
    return word


async def _new_cards_started(session: AsyncSession, user: User, since: datetime) -> int:
    stmt = (
        select(func.count())
        .select_from(ReviewLog)
        .join(Card, Card.id == ReviewLog.card_id)
        .where(
            Card.user_id == user.id,
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
            sibling.user_id == Card.user_id,
            sibling.word_id == Card.word_id,
            sibling.id != Card.id,
            ReviewLog.reviewed_at >= day_start,
        )
    )
    is_new = Card.last_review.is_(None)
    stmt = (
        select(Card)
        .where(Card.user_id == user.id, Card.due <= now, ~sibling_reviewed_today)
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
    stmt = select(func.min(Card.due)).where(
        Card.user_id == user.id, Card.last_review.is_not(None)
    )
    return await session.scalar(stmt)


async def due_count(session: AsyncSession, user: User, now: datetime) -> int:
    stmt = (
        select(func.count())
        .select_from(Card)
        .where(Card.user_id == user.id, Card.due <= now, Card.last_review.is_not(None))
    )
    return (await session.scalar(stmt)) or 0


async def get_card(session: AsyncSession, user: User, card_id: int) -> Card | None:
    stmt = (
        select(Card)
        .where(Card.id == card_id, Card.user_id == user.id)
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
    user_cards = select(Card.id).where(Card.user_id == user.id).subquery()
    words = await session.scalar(select(func.count()).select_from(Word))
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
