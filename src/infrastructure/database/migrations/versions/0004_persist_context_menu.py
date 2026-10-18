"""Persist the active Telegram context-menu anchor."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("menu_chat_id", sa.BigInteger(), nullable=True))
    op.add_column("users", sa.Column("menu_message_id", sa.BigInteger(), nullable=True))
    op.add_column("users", sa.Column("menu_message_kind", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "menu_message_kind")
    op.drop_column("users", "menu_message_id")
    op.drop_column("users", "menu_chat_id")
