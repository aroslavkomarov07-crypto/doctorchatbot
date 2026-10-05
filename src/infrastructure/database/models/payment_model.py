from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.enums.payment_status import PaymentStatus
from infrastructure.database.base import Base


class PaymentModel(Base):
    """
    Таблица платежей.
    """

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
        CheckConstraint(
            "provider_payment_id IS NULL OR payment_provider IS NOT NULL",
            name="ck_payments_provider_for_operation",
        ),
        UniqueConstraint(
            "payment_provider",
            "provider_payment_id",
            name="uq_payments_provider_operation",
        ),
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

    provider_payment_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
