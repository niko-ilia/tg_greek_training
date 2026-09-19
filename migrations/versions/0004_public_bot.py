"""public bot: user profile, usage_events, rating-driven pace

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-19 11:55:03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_USER_COLUMNS = [
    sa.Column("username", sa.String(length=64), nullable=True),
    sa.Column("first_name", sa.String(length=128), nullable=True),
    sa.Column("last_name", sa.String(length=128), nullable=True),
    sa.Column("language_code", sa.String(length=16), nullable=True),
    sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column(
        "daily_review_budget",
        sa.Integer(),
        server_default=sa.text("150"),
        nullable=False,
    ),
    sa.Column("pace_override_on", sa.Date(), nullable=True),
]
# The old limit counted cards; a word has 2-3 of them, so old values do not carry over.
_DEFAULT_NEW_WORDS = 30
_OLD_DEFAULT_NEW_CARDS = 20


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_events_occurred_at", "usage_events", ["occurred_at"])
    op.create_index(
        "ix_usage_events_user_id_occurred_at",
        "usage_events",
        ["user_id", "occurred_at"],
    )
    for column in _USER_COLUMNS:
        op.add_column("users", column)
    op.alter_column(
        "users", "daily_new_cards", new_column_name="daily_new_words", nullable=True
    )
    op.execute(f"UPDATE users SET daily_new_words = {_DEFAULT_NEW_WORDS}")


def downgrade() -> None:
    op.execute(
        f"UPDATE users SET daily_new_words = {_OLD_DEFAULT_NEW_CARDS} "
        "WHERE daily_new_words IS NULL"
    )
    op.alter_column(
        "users", "daily_new_words", new_column_name="daily_new_cards", nullable=False
    )
    for column in reversed(_USER_COLUMNS):
        op.drop_column("users", column.name)
    op.drop_index("ix_usage_events_user_id_occurred_at", table_name="usage_events")
    op.drop_index("ix_usage_events_occurred_at", table_name="usage_events")
    op.drop_table("usage_events")
