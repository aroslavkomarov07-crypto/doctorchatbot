from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database.base import Base

from core.enums.payment_status import PaymentStatus


class PaymentModel(Base):
    """
    Таблица платежей.
    """

    __tablename__ = "payments"

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

    subscription_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "subscriptions.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="RUB",
    )

    status: Mapped[PaymentStatus] = mapped_column(
        Enum(
            PaymentStatus,
            name="payment_status",
            native_enum=False,
        ),
        nullable=False,
        default=PaymentStatus.PENDING,
    )

    payment_provider: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )