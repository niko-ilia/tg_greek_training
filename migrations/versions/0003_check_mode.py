"""check mode: users.check_cursor, review_logs.is_triage

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-19 09:44:58

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "review_logs",
        sa.Column(
            "is_triage", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.add_column("users", sa.Column("check_cursor", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "check_cursor")
    op.drop_column("review_logs", "is_triage")
