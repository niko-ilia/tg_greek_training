"""shared dictionary: words lose user_id, cards gain it

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-18 22:10:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cards", sa.Column("user_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE cards SET user_id = words.user_id FROM words WHERE words.id = cards.word_id"
    )
    op.alter_column("cards", "user_id", nullable=False)
    op.create_foreign_key(
        "cards_user_id_fkey", "cards", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.drop_constraint("cards_word_id_card_type_example_id_key", "cards")
    op.create_unique_constraint(
        "cards_user_id_word_id_card_type_example_id_key",
        "cards",
        ["user_id", "word_id", "card_type", "example_id"],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_index("ix_cards_due", table_name="cards")
    op.create_index("ix_cards_user_id_due", "cards", ["user_id", "due"])

    op.drop_constraint("words_user_id_lemma_key_key", "words")
    op.drop_constraint("words_user_id_fkey", "words")
    op.drop_column("words", "user_id")
    op.create_unique_constraint("words_lemma_key_key", "words", ["lemma_key"])


def downgrade() -> None:
    """Only safe while every word has cards of a single user.

    Words nobody has cards for cannot get an owner and are deleted.
    """
    op.execute(
        "DELETE FROM words WHERE NOT EXISTS "
        "(SELECT 1 FROM cards WHERE cards.word_id = words.id)"
    )
    op.drop_constraint("words_lemma_key_key", "words")
    op.add_column("words", sa.Column("user_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE words SET user_id = (SELECT min(user_id) FROM cards WHERE cards.word_id = words.id)"
    )
    op.alter_column("words", "user_id", nullable=False)
    op.create_foreign_key(
        "words_user_id_fkey", "words", "users", ["user_id"], ["id"], ondelete="CASCADE"
    )
    op.create_unique_constraint(
        "words_user_id_lemma_key_key", "words", ["user_id", "lemma_key"]
    )

    op.drop_index("ix_cards_user_id_due", table_name="cards")
    op.create_index("ix_cards_due", "cards", ["due"])
    op.drop_constraint("cards_user_id_word_id_card_type_example_id_key", "cards")
    op.create_unique_constraint(
        "cards_word_id_card_type_example_id_key",
        "cards",
        ["word_id", "card_type", "example_id"],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_constraint("cards_user_id_fkey", "cards")
    op.drop_column("cards", "user_id")
