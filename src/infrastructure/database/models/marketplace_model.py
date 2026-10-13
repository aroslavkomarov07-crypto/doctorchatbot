from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database.base import Base


class TariffModel(Base):
    __tablename__ = "tariffs"
    __table_args__ = (
        CheckConstraint("duration_days > 0", name="ck_tariff_duration"),
        CheckConstraint("base_price > 0", name="ck_tariff_price"),
        CheckConstraint("message_limit IS NULL OR message_limit > 0", name="ck_tariff_limit"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    message_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ExpertPriceModel(Base):
    __tablename__ = "expert_prices"
    __table_args__ = (
        UniqueConstraint("expert_id", "tariff_id", name="uq_expert_tariff_price"),
        CheckConstraint("price > 0", name="ck_expert_price_positive"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    expert_id: Mapped[UUID] = mapped_column(ForeignKey("experts.id", ondelete="CASCADE"), index=True)
    tariff_id: Mapped[UUID] = mapped_column(ForeignKey("tariffs.id", ondelete="CASCADE"))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)


class ExpertApplicationModel(Base):
    __tablename__ = "expert_applications"
    __table_args__ = (
        Index(
            "uq_expert_applications_pending_user",
            "user_id",
            unique=True,
            postgresql_where="status = 'pending'",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    specialization: Mapped[str] = mapped_column(String(255), nullable=False)
    education: Mapped[str] = mapped_column(String(1000), nullable=False)
    experience: Mapped[str] = mapped_column(String(1000), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    photo_file_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FeeSettingsModel(Base):
    __tablename__ = "fee_settings"
    __table_args__ = (
        CheckConstraint(
            "payment_percent >= 0 AND service_percent >= 0 AND "
            "payment_percent + service_percent <= 100",
            name="ck_fee_percentages",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    service_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)


class WithdrawalModel(Base):
    __tablename__ = "withdrawals"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_withdrawal_amount"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    expert_id: Mapped[UUID] = mapped_column(ForeignKey("experts.id", ondelete="CASCADE"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
