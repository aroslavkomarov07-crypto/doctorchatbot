from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from core.enums.subscription_status import SubscriptionStatus
from infrastructure.database.base import Base


class SubscriptionModel(Base):
    """
    Таблица подписок пользователей на экспертов.
    """

    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint("message_limit IS NULL OR message_limit > 0", name="ck_sub_message_limit"),
        CheckConstraint("messages_sent >= 0", name="ck_sub_messages_sent"),
    )

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

    start_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    end_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(
            SubscriptionStatus,
            name="subscription_status",
            native_enum=False,
        ),
        nullable=False,
        default=SubscriptionStatus.PENDING,
    )
    tariff_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tariffs.id", ondelete="SET NULL"), nullable=True
    )
    message_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    messages_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
