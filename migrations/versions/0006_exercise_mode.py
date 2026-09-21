"""exercise mode: serve one kind of card at a time

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-21 20:17:36.345332

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The card_type enum is already there from 0001; only the column is new.
    op.add_column(
        "users",
        sa.Column(
            "exercise_mode",
            postgresql.ENUM(name="card_type", create_type=False),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "exercise_mode")
