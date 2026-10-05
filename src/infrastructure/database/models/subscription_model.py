from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from core.enums.subscription_status import SubscriptionStatus
from infrastructure.database.base import Base


class SubscriptionModel(Base):
    """
    Таблица подписок пользователей на экспертов.
    """

    __tablename__ = "subscriptions"

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