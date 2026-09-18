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

from sqlalchemy import (
    BigInteger,
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
    func,
)
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
    daily_new_cards: Mapped[int] = mapped_column(Integer)
    last_reminded_on: Mapped[date | None] = mapped_column(Date)
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


class TtsCache(Base):
    """Telegram file_id of already-uploaded audio, so each text is synthesized once."""

    __tablename__ = "tts_cache"

    voice: Mapped[str] = mapped_column(String(64), primary_key=True)
    text: Mapped[str] = mapped_column(Text, primary_key=True)
    telegram_file_id: Mapped[str] = mapped_column(String(255))
