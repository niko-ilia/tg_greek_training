"""ORM models.

The dictionary (`Word`, `Example`) is shared by every learner. Each learner
gets their own `Card`s per word (one per exercise type, plus one cloze card per
example that marks a target form). FSRS scheduling state lives on the card, and
every answer is kept in `ReviewLog` so the FSRS parameters can later be
optimized on the learner's own history.
"""

from __future__ import annotations

import enum
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class CardType(enum.StrEnum):
    RECOGNITION = "recognition"
    RECALL = "recall"
    CLOZE = "cloze"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    timezone: Mapped[str] = mapped_column(String(64))
    reminder_time: Mapped[time | None] = mapped_column(Time)
    # Ceiling on brand-new words per learning day; None means no ceiling.
    daily_new_words: Mapped[int | None] = mapped_column(Integer)
    # New cards stop while the forecast peak of daily reviews reaches this.
    daily_review_budget: Mapped[int] = mapped_column(Integer, server_default="150")
    # "Всё равно дальше": the pacing brakes are off for this learning day.
    pace_override_on: Mapped[date | None] = mapped_column(Date)
    last_reminded_on: Mapped[date | None] = mapped_column(Date)
    # Only this exercise is served; NULL mixes all of them.
    exercise_mode: Mapped[CardType | None] = mapped_column(
        Enum(CardType, name="card_type", values_callable=lambda e: [m.value for m in e])
    )
    # Highest word id answered in /check ("know" or "learn"); the next pass continues after it.
    check_cursor: Mapped[int | None] = mapped_column(Integer)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    last_name: Mapped[str | None] = mapped_column(String(128))
    language_code: Mapped[str | None] = mapped_column(String(16))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when Telegram reports the learner blocked the bot; cleared on their next update.
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Word(Base):
    __tablename__ = "words"

    id: Mapped[int] = mapped_column(primary_key=True)
    lemma: Mapped[str] = mapped_column(String(200))
    # Accent- and case-insensitive form, so "ξερω" and "Ξέρω" are one word.
    lemma_key: Mapped[str] = mapped_column(String(200), unique=True)
    translation: Mapped[str] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    examples: Mapped[list[Example]] = relationship(
        back_populates="word", cascade="all, delete-orphan", order_by="Example.id"
    )
    cards: Mapped[list[Card]] = relationship(
        back_populates="word", cascade="all, delete-orphan"
    )


class Example(Base):
    __tablename__ = "examples"
    __table_args__ = (Index("ix_examples_word_id", "word_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    word_id: Mapped[int] = mapped_column(ForeignKey("words.id", ondelete="CASCADE"))
    text_el: Mapped[str] = mapped_column(Text)
    text_ru: Mapped[str] = mapped_column(Text)
    # The exact surface form hidden in cloze drills; None means no cloze card.
    cloze_target: Mapped[str | None] = mapped_column(String(200))

    word: Mapped[Word] = relationship(back_populates="examples")


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = (
        # NULLS NOT DISTINCT: a learner has exactly one recognition and one
        # recall card per word.
        UniqueConstraint(
            "user_id",
            "word_id",
            "card_type",
            "example_id",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "(card_type = 'cloze') = (example_id IS NOT NULL)",
            name="cloze_has_example",
        ),
        CheckConstraint("state IN (1, 2, 3)", name="fsrs_state"),
        Index("ix_cards_user_id_due", "user_id", "due"),
        Index("ix_cards_word_id", "word_id"),
        Index("ix_cards_example_id", "example_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    word_id: Mapped[int] = mapped_column(ForeignKey("words.id", ondelete="CASCADE"))
    card_type: Mapped[CardType] = mapped_column(
        Enum(CardType, name="card_type", values_callable=lambda e: [m.value for m in e])
    )
    example_id: Mapped[int | None] = mapped_column(
        ForeignKey("examples.id", ondelete="CASCADE")
    )

    # FSRS state, mirrors fsrs.Card.
    state: Mapped[int] = mapped_column(SmallInteger)
    step: Mapped[int | None] = mapped_column(SmallInteger)
    stability: Mapped[float | None] = mapped_column(Float)
    difficulty: Mapped[float | None] = mapped_column(Float)
    due: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_review: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Bumped on every review; stale rating buttons carry an old version.
    version: Mapped[int] = mapped_column(Integer, default=0)

    word: Mapped[Word] = relationship(back_populates="cards")
    example: Mapped[Example | None] = relationship()


class ReviewLog(Base):
    __tablename__ = "review_logs"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 4", name="fsrs_rating"),
        Index("ix_review_logs_reviewed_at", "reviewed_at"),
        Index("ix_review_logs_card_id", "card_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"))
    rating: Mapped[int] = mapped_column(SmallInteger)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    review_duration_ms: Mapped[int | None] = mapped_column(Integer)
    # FSRS state before this answer; None means the card was seen for the first time.
    state_before: Mapped[int | None] = mapped_column(SmallInteger)
    answer_text: Mapped[str | None] = mapped_column(Text)
    # "I already know this word" from /check; excluded from the daily new-word limit.
    is_triage: Mapped[bool] = mapped_column(Boolean, server_default=false())


class UsageEvent(Base):
    """One Telegram update from a learner: what they did and when, never the text."""

    __tablename__ = "usage_events"
    __table_args__ = (
        Index("ix_usage_events_user_id_occurred_at", "user_id", "occurred_at"),
        Index("ix_usage_events_occurred_at", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # command | button | text | message | status | other
    kind: Mapped[str] = mapped_column(String(16))
    # Command name, callback prefix, content type or chat member status.
    action: Mapped[str] = mapped_column(String(64))


class TtsCache(Base):
    """Telegram file_id of already-uploaded audio, so each text is synthesized once."""

    __tablename__ = "tts_cache"

    voice: Mapped[str] = mapped_column(String(64), primary_key=True)
    text: Mapped[str] = mapped_column(Text, primary_key=True)
    telegram_file_id: Mapped[str] = mapped_column(String(255))


class FsmState(Base):
    """The conversation state aiogram keeps per chat, so a restart does not
    forget the card the learner is answering."""

    __tablename__ = "fsm_states"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    state: Mapped[str | None] = mapped_column(String(128))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
