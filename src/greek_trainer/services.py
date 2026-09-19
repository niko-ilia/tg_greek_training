"""Use cases on top of the database: adding words, picking and grading cards."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import fsrs
from sqlalchemy import (
    Select,
    case,
    except_,
    exists,
    func,
    literal,
    or_,
    select,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from greek_trainer.config import Settings
from greek_trainer.db.models import (
    Card,
    CardType,
    Example,
    ReviewLog,
    UsageEvent,
    User,
    Word,
)
from greek_trainer.domain.greek import lemma_key
from greek_trainer.domain.settings_input import SettingsChange
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
    """Find the learner by Telegram id, creating them on first contact.

    A new learner's first tap arrives as two concurrent updates (chat status and
    /start); ON CONFLICT DO NOTHING lets the second one wait for the first and
    reuse its row instead of failing on the unique key.
    """
    by_id = select(User).where(User.telegram_id == telegram_id)
    user = await session.scalar(by_id)
    if user is not None:
        return user
    await session.execute(
        insert(User)
        .values(
            telegram_id=telegram_id,
            timezone=settings.timezone,
            reminder_time=settings.reminder_time,
            daily_new_words=settings.daily_new_words,
            daily_review_budget=settings.daily_review_budget,
        )
        .on_conflict_do_nothing(index_elements=[User.telegram_id])
    )
    user = await session.scalar(by_id)
    assert user is not None
    return user


@dataclass(frozen=True)
class Visit:
    """What the bot learned about the learner from one update."""

    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None
    kind: str
    action: str


def record_visit(
    session: AsyncSession, user: User, visit: Visit, now: datetime
) -> None:
    """Refresh the learner's profile and log the update in the usage journal."""
    user.username = visit.username
    user.first_name = visit.first_name
    user.last_name = visit.last_name
    user.language_code = visit.language_code
    user.last_seen_at = now
    user.blocked_at = None
    session.add(
        UsageEvent(
            user_id=user.id, occurred_at=now, kind=visit.kind, action=visit.action
        )
    )


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


# Like Anki's "learn ahead limit": when nothing is due, a learning step that is
# due this soon is shown now rather than making the learner wait.
LEARN_AHEAD = timedelta(minutes=20)
# How far ahead the review forecast looks. A new card rated Good returns in 2-3
# days, so a forecast of tomorrow alone would not see the load it creates.
FORECAST_DAYS = 7
# "Struggling" = at least this many "Забыл" among the last STRUGGLE_WINDOW answers today.
STRUGGLE_WINDOW = 10
STRUGGLE_AGAIN = 3


@dataclass(frozen=True)
class Pace:
    """Whether the learner's own answers leave room for new material today."""

    new_words_today: int
    new_words_cap: int | None
    forecast_peak: int
    budget: int
    struggling: bool
    overridden: bool

    @property
    def allows_new_cards(self) -> bool:
        return self.overridden or (
            not self.struggling and self.forecast_peak < self.budget
        )

    @property
    def allows_new_words(self) -> bool:
        under_cap = (
            self.new_words_cap is None or self.new_words_today < self.new_words_cap
        )
        return self.allows_new_cards and (self.overridden or under_cap)


async def _new_words_started(
    session: AsyncSession, user: User, day_start: datetime
) -> int:
    """Words whose first card was answered today (answers in /check do not count)."""
    earlier_card = aliased(Card)
    reviewed_before = exists(
        select(ReviewLog.id)
        .join(earlier_card, earlier_card.id == ReviewLog.card_id)
        .where(
            earlier_card.user_id == user.id,
            earlier_card.word_id == Card.word_id,
            ReviewLog.reviewed_at < day_start,
        )
    )
    stmt = (
        select(func.count(func.distinct(Card.word_id)))
        .select_from(ReviewLog)
        .join(Card, Card.id == ReviewLog.card_id)
        .where(
            Card.user_id == user.id,
            ReviewLog.state_before.is_(None),
            ReviewLog.is_triage.is_(False),
            ReviewLog.reviewed_at >= day_start,
            ~reviewed_before,
        )
    )
    return (await session.scalar(stmt)) or 0


async def _forecast_peak(session: AsyncSession, user: User, day_start: datetime) -> int:
    """Busiest of the next FORECAST_DAYS days, in reviews, per the FSRS schedule.

    Cards still in learning steps have no long-term due date yet; each is
    counted once on top of the peak, as it will land on one of those days.
    """
    horizon = day_start + timedelta(days=FORECAST_DAYS + 1)
    in_review = Card.state == fsrs.State.Review.value
    dues = await session.scalars(
        select(Card.due).where(Card.user_id == user.id, in_review, Card.due < horizon)
    )
    per_day = Counter((due - day_start) // timedelta(days=1) for due in dues)
    peak = max((per_day[day] for day in range(1, FORECAST_DAYS + 1)), default=0)
    learning = await session.scalar(
        select(func.count()).where(
            Card.user_id == user.id, Card.last_review.is_not(None), ~in_review
        )
    )
    return peak + (learning or 0)


async def _struggling(session: AsyncSession, user: User, day_start: datetime) -> bool:
    recent = await session.scalars(
        select(ReviewLog.rating)
        .join(Card, Card.id == ReviewLog.card_id)
        .where(
            Card.user_id == user.id,
            ReviewLog.is_triage.is_(False),
            ReviewLog.reviewed_at >= day_start,
        )
        .order_by(ReviewLog.reviewed_at.desc(), ReviewLog.id.desc())
        .limit(STRUGGLE_WINDOW)
    )
    return sum(rating == fsrs.Rating.Again.value for rating in recent) >= STRUGGLE_AGAIN


async def get_pace(session: AsyncSession, user: User, now: datetime) -> Pace:
    day_start = learning_day_start(now, user.timezone)
    return Pace(
        new_words_today=await _new_words_started(session, user, day_start),
        new_words_cap=user.daily_new_words,
        forecast_peak=await _forecast_peak(session, user, day_start),
        budget=user.daily_review_budget,
        struggling=await _struggling(session, user, day_start),
        overridden=user.pace_override_on == day_start.date(),
    )


async def override_pace_today(session: AsyncSession, user: User, now: datetime) -> bool:
    """Lift the pacing brakes for today; False on a double tap or a stale button."""
    day = learning_day_start(now, user.timezone).date()
    lifted = await session.scalar(
        update(User)
        .where(
            User.id == user.id,
            or_(User.pace_override_on.is_(None), User.pace_override_on != day),
        )
        .values(pace_override_on=day)
        .returning(User.id)
    )
    if lifted is None:
        return False
    user.pace_override_on = day
    return True


async def next_card(
    session: AsyncSession,
    user: User,
    now: datetime,
    *,
    ignore_pace: bool = False,
    learn_ahead: bool = True,
) -> Card | None:
    """Pick the next card to show.

    Order: cards already in learning steps, then reviews by due date, then new
    cards in the order words were added. New cards come only while `Pace`
    allows them: the learner's own ratings drive the forecast and the struggle
    check. Siblings are buried: once any card of a word was reviewed today, the
    word's other cards wait until tomorrow, so recognition does not give away
    the answer to the recall drill. When nothing is due, a learning step due
    within `LEARN_AHEAD` is shown early.

    `ignore_pace` answers "would a new card come if the pace allowed it";
    `learn_ahead=False` answers "is anything due right now".
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
    word_started = exists(
        select(sibling.id).where(
            sibling.user_id == Card.user_id,
            sibling.word_id == Card.word_id,
            sibling.last_review.is_not(None),
        )
    )
    is_new = Card.last_review.is_(None)
    stmt = (
        select(Card)
        .where(Card.user_id == user.id, ~sibling_reviewed_today)
        .options(selectinload(Card.word).selectinload(Word.examples))
        .options(selectinload(Card.example))
        .order_by(
            case((Card.state == fsrs.State.Review.value, 1), else_=0),
            case((is_new, Card.word_id), else_=0),
            Card.due,
            Card.id,
        )
        .limit(1)
    )
    card = await session.scalar(stmt.where(~is_new, Card.due <= now))
    if card is not None:
        return card

    new_cards: Select[tuple[Card]] | None = stmt.where(is_new, Card.due <= now)
    if not ignore_pace:
        pace = await get_pace(session, user, now)
        if not pace.allows_new_cards:
            new_cards = None
        elif not pace.allows_new_words:
            new_cards = stmt.where(is_new, Card.due <= now, word_started)
    if new_cards is not None:
        card = await session.scalar(new_cards)
    if card is not None or ignore_pace or not learn_ahead:
        return card

    in_learning = Card.last_review.is_not(None) & (
        Card.state != fsrs.State.Review.value
    )
    return await session.scalar(stmt.where(in_learning, Card.due <= now + LEARN_AHEAD))


def apply_settings(user: User, change: SettingsChange, now: datetime) -> None:
    if change.no_word_ceiling:
        user.daily_new_words = None
    elif change.daily_new_words is not None:
        user.daily_new_words = change.daily_new_words
    if change.review_budget is not None:
        user.daily_review_budget = change.review_budget
    if change.timezone is not None:
        user.timezone = change.timezone
    if change.reminders_off:
        user.reminder_time = None
    elif change.reminder_time is not None:
        user.reminder_time = change.reminder_time
    if change.reminder_time is not None or change.timezone is not None:
        _skip_reminder_already_past(user, now)


def _skip_reminder_already_past(user: User, now: datetime) -> None:
    """A reminder time already past today would fire within a minute; start tomorrow."""
    if user.reminder_time is None:
        return
    local = now.astimezone(ZoneInfo(user.timezone))
    if user.reminder_time <= local.time():
        user.last_reminded_on = local.date()


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
    is_triage: bool = False,
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
            is_triage=is_triage,
        )
    )


def _unchecked_words(user: User) -> Select[tuple[Word]]:
    """Words the learner has never reviewed, after `check_cursor`.

    Words answered in /check with "know" or "learn" sit below the cursor, so
    they are not offered again until the cursor is reset.
    """
    seen = exists(
        select(Card.id).where(
            Card.word_id == Word.id,
            Card.user_id == user.id,
            Card.last_review.is_not(None),
        )
    )
    stmt = select(Word).where(~seen)
    if user.check_cursor is not None:
        stmt = stmt.where(Word.id > user.check_cursor)
    return stmt


async def next_unchecked_word(session: AsyncSession, user: User) -> Word | None:
    return await session.scalar(_unchecked_words(user).order_by(Word.id).limit(1))


async def count_unchecked_words(session: AsyncSession, user: User) -> int:
    stmt = select(func.count()).select_from(_unchecked_words(user).subquery())
    return (await session.scalar(stmt)) or 0


async def advance_check(session: AsyncSession, user: User, word: Word) -> bool:
    """Move the /check cursor past the word.

    Returns False when the cursor is already there: a double tap or a stale
    button. The conditional UPDATE makes two concurrent taps race safely.
    """
    moved = await session.scalar(
        update(User)
        .where(
            User.id == user.id,
            or_(User.check_cursor.is_(None), User.check_cursor < word.id),
        )
        .values(check_cursor=word.id)
        .returning(User.id)
    )
    if moved is None:
        return False
    user.check_cursor = word.id
    return True


async def mark_word_known(
    session: AsyncSession,
    scheduler: fsrs.Scheduler,
    user: User,
    word: Word,
    now: datetime,
) -> bool:
    """Rate every card of the word Easy, so they return in about a week as a check-up.

    Returns False when the word was already answered in /check.
    """
    if not await advance_check(session, user, word):
        return False
    cards = await session.scalars(
        select(Card).where(Card.word_id == word.id, Card.user_id == user.id)
    )
    for card in cards:
        record_review(session, scheduler, card, fsrs.Rating.Easy, now, is_triage=True)
    return True


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
            logs.where(
                ReviewLog.reviewed_at >= day_start, ReviewLog.is_triage.is_(False)
            ).subquery()
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
