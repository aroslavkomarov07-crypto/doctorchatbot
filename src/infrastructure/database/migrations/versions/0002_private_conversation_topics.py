"""Add private-chat topics for client-expert conversations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from src.infrastructure.database.models import ConversationTopic

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    topic_table = ConversationTopic.metadata.tables[ConversationTopic.__tablename__]
    topic_table.create(bind=bind, checkfirst=True)

    inspector = sa.inspect(bind)
    existing = {column["name"] for column in inspector.get_columns("conversation_messages")}
    columns = (
        sa.Column("source_topic_id", sa.Integer(), nullable=True),
        sa.Column("destination_topic_id", sa.Integer(), nullable=True),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("source_telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("destination_telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("failure_code", sa.String(length=120), nullable=True),
    )
    for column in columns:
        if column.name not in existing:
            op.add_column("conversation_messages", column)

    inspector = sa.inspect(bind)
    foreign_keys = {item["name"] for item in inspector.get_foreign_keys("conversation_messages")}
    if "fk_conv_messages_source_topic" not in foreign_keys:
        op.create_foreign_key(
            "fk_conv_messages_source_topic",
            "conversation_messages",
            "conversation_topics",
            ["source_topic_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if "fk_conv_messages_destination_topic" not in foreign_keys:
        op.create_foreign_key(
            "fk_conv_messages_destination_topic",
            "conversation_messages",
            "conversation_topics",
            ["destination_topic_id"],
            ["id"],
            ondelete="SET NULL",
        )

    inspector = sa.inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("conversation_messages")}
    if "ix_conversation_messages_source_topic_id" not in indexes:
        op.create_index(
            "ix_conversation_messages_source_topic_id",
            "conversation_messages",
            ["source_topic_id"],
        )
    if "ix_conversation_messages_destination_topic_id" not in indexes:
        op.create_index(
            "ix_conversation_messages_destination_topic_id",
            "conversation_messages",
            ["destination_topic_id"],
        )
    if "ix_conversation_messages_source_telegram" not in indexes:
        op.create_index(
            "ix_conversation_messages_source_telegram",
            "conversation_messages",
            ["source_chat_id", "source_telegram_message_id"],
        )

    uniques = {item["name"] for item in inspector.get_unique_constraints("conversation_messages")}
    if "uq_conversation_messages_source_chat_id" not in uniques:
        op.create_unique_constraint(
            "uq_conversation_messages_source_chat_id",
            "conversation_messages",
            ["source_chat_id", "source_telegram_message_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("conversation_messages"):
        uniques = {
            item["name"] for item in inspector.get_unique_constraints("conversation_messages")
        }
        if "uq_conversation_messages_source_chat_id" in uniques:
            op.drop_constraint(
                "uq_conversation_messages_source_chat_id",
                "conversation_messages",
                type_="unique",
            )
        indexes = {item["name"] for item in inspector.get_indexes("conversation_messages")}
        for name in (
            "ix_conversation_messages_source_telegram",
            "ix_conversation_messages_destination_topic_id",
            "ix_conversation_messages_source_topic_id",
        ):
            if name in indexes:
                op.drop_index(name, table_name="conversation_messages")
        foreign_keys = {
            item["name"] for item in inspector.get_foreign_keys("conversation_messages")
        }
        for name in (
            "fk_conv_messages_destination_topic",
            "fk_conv_messages_source_topic",
        ):
            if name in foreign_keys:
                op.drop_constraint(name, "conversation_messages", type_="foreignkey")
        existing = {column["name"] for column in inspector.get_columns("conversation_messages")}
        for name in (
            "failure_code",
            "destination_telegram_message_id",
            "source_telegram_message_id",
            "source_chat_id",
            "destination_topic_id",
            "source_topic_id",
        ):
            if name in existing:
                op.drop_column("conversation_messages", name)
    topic_table = ConversationTopic.metadata.tables[ConversationTopic.__tablename__]
    topic_table.drop(bind=bind, checkfirst=True)
