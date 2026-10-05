from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.enums.message_sender_type import MessageSenderType
from infrastructure.database.base import Base


class MessageModel(Base):
    """
    Таблица сообщений между пользователями и экспертами.
    """

    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(
        primary_key=True
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    expert_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "experts.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    sender_type: Mapped[MessageSenderType] = mapped_column(
        Enum(
            MessageSenderType,
            name="message_sender_type",
            native_enum=False,
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )