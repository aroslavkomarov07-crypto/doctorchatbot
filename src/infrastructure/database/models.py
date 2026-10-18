from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.core.enums import (
    BroadcastAudience,
    BroadcastStatus,
    ConversationTopicSide,
    ConversationTopicStatus,
    ExpertApplicationStatus,
    LedgerEntryType,
    MessageStatus,
    PaymentProvider,
    ReferralAccrual,
    ReferralRewardKind,
    Role,
    SubscriptionStatus,
    TransactionStatus,
    WithdrawalStatus,
)

from .base import Base, TimestampMixin

MONEY = Numeric(14, 2)
PERCENT = Numeric(6, 3)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(32), index=True)
    telegram_name: Mapped[str] = mapped_column(String(128))
    full_name: Mapped[str | None] = mapped_column(String(160), index=True)
    phone: Mapped[str | None] = mapped_column(String(20), index=True)
    language: Mapped[str] = mapped_column(String(10), default="ru")
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False), index=True)
    registration_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    referral_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    referrer_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    bonus_balance: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    menu_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    menu_message_id: Mapped[int | None] = mapped_column(BigInteger)
    menu_message_kind: Mapped[str | None] = mapped_column(String(10))


class ExpertApplication(Base, TimestampMixin):
    __tablename__ = "expert_applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    specialization: Mapped[str] = mapped_column(String(160))
    short_bio: Mapped[str] = mapped_column(String(500))
    bio: Mapped[str] = mapped_column(Text)
    photo_file_id: Mapped[str] = mapped_column(String(512))
    status: Mapped[ExpertApplicationStatus] = mapped_column(
        Enum(ExpertApplicationStatus, native_enum=False),
        default=ExpertApplicationStatus.PENDING,
        index=True,
    )
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (Index("ix_expert_applications_user_status", "user_id", "status"),)


class ExpertProfile(Base, TimestampMixin):
    __tablename__ = "expert_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    display_name: Mapped[str] = mapped_column(String(160), index=True)
    specialization: Mapped[str] = mapped_column(String(160), index=True)
    short_bio: Mapped[str] = mapped_column(String(500))
    bio: Mapped[str] = mapped_column(Text)
    photo_file_id: Mapped[str] = mapped_column(String(512))
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)


class Tariff(Base, TimestampMixin):
    __tablename__ = "tariffs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(1000))
    duration_days: Mapped[int] = mapped_column(Integer)
    message_limit: Mapped[int | None] = mapped_column(Integer)
    default_price: Mapped[Decimal] = mapped_column(MONEY)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)

    __table_args__ = (
        CheckConstraint("duration_days > 0", name="duration_positive"),
        CheckConstraint("message_limit IS NULL OR message_limit > 0", name="message_limit"),
        CheckConstraint("default_price >= 0", name="price_non_negative"),
    )


class ExpertTariff(Base, TimestampMixin):
    __tablename__ = "expert_tariffs"

    id: Mapped[int] = mapped_column(primary_key=True)
    expert_id: Mapped[int] = mapped_column(
        ForeignKey("expert_profiles.id", ondelete="CASCADE"), index=True
    )
    tariff_id: Mapped[int] = mapped_column(
        ForeignKey("tariffs.id", ondelete="RESTRICT"), index=True
    )
    custom_price: Mapped[Decimal | None] = mapped_column(MONEY)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    __table_args__ = (
        UniqueConstraint("expert_id", "tariff_id"),
        CheckConstraint("custom_price IS NULL OR custom_price >= 0", name="custom_price"),
    )


class PaymentTransaction(Base, TimestampMixin):
    __tablename__ = "payment_transactions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider_payment_id: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    expert_id: Mapped[int] = mapped_column(
        ForeignKey("expert_profiles.id", ondelete="RESTRICT"), index=True
    )
    tariff_id: Mapped[int] = mapped_column(ForeignKey("tariffs.id", ondelete="RESTRICT"))
    provider: Mapped[PaymentProvider] = mapped_column(
        Enum(PaymentProvider, native_enum=False), index=True
    )
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(TransactionStatus, native_enum=False), default=TransactionStatus.PENDING, index=True
    )
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    service_commission_percent: Mapped[Decimal] = mapped_column(PERCENT)
    service_commission_amount: Mapped[Decimal] = mapped_column(MONEY)
    expert_income_amount: Mapped[Decimal] = mapped_column(MONEY)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    provider_status: Mapped[str | None] = mapped_column(String(64))
    provider_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": version}
    __table_args__ = (
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        CheckConstraint(
            "service_commission_percent BETWEEN 0 AND 100",
            name="service_commission_range",
        ),
    )


class ClientExpertSubscription(Base, TimestampMixin):
    __tablename__ = "client_expert_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expert_id: Mapped[int] = mapped_column(
        ForeignKey("expert_profiles.id", ondelete="RESTRICT"), index=True
    )
    tariff_id: Mapped[int] = mapped_column(ForeignKey("tariffs.id", ondelete="RESTRICT"))
    transaction_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_transactions.id", ondelete="RESTRICT"), unique=True
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, native_enum=False),
        default=SubscriptionStatus.PENDING_PAYMENT,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    message_limit: Mapped[int | None] = mapped_column(Integer)
    messages_used: Mapped[int] = mapped_column(Integer, default=0)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    expiration_notice_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_subscriptions_client_expert_expire", "client_id", "expert_id", "expires_at"),
        CheckConstraint("messages_used >= 0", name="messages_used_non_negative"),
    )


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expert_id: Mapped[int] = mapped_column(
        ForeignKey("expert_profiles.id", ondelete="CASCADE"), index=True
    )

    __table_args__ = (UniqueConstraint("client_id", "expert_id"),)


class ConversationTopic(Base, TimestampMixin):
    __tablename__ = "conversation_topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    participant_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    side: Mapped[ConversationTopicSide] = mapped_column(
        Enum(ConversationTopicSide, native_enum=False), index=True
    )
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_thread_id: Mapped[int | None] = mapped_column(BigInteger)
    topic_name_snapshot: Mapped[str] = mapped_column(String(128))
    status: Mapped[ConversationTopicStatus] = mapped_column(
        Enum(ConversationTopicStatus, native_enum=False),
        default=ConversationTopicStatus.PENDING,
        index=True,
    )
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_announced_subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("client_expert_subscriptions.id", ondelete="SET NULL")
    )

    __table_args__ = (
        UniqueConstraint("conversation_id", "side"),
        UniqueConstraint("telegram_chat_id", "message_thread_id"),
        Index("ix_conversation_topics_chat_thread", "telegram_chat_id", "message_thread_id"),
        CheckConstraint("retry_count >= 0", name="topic_retry_count_non_negative"),
    )


class ConversationMessage(Base, TimestampMixin):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    sender_role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False))
    text: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str | None] = mapped_column(String(20))
    media_file_id: Mapped[str | None] = mapped_column(String(512))
    source_topic_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "conversation_topics.id",
            ondelete="SET NULL",
            name="fk_conv_messages_source_topic",
        ),
        index=True,
    )
    destination_topic_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "conversation_topics.id",
            ondelete="SET NULL",
            name="fk_conv_messages_destination_topic",
        ),
        index=True,
    )
    source_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    source_telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    destination_telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    reply_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="SET NULL")
    )
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus, native_enum=False), default=MessageStatus.CREATED, index=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(120))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("source_chat_id", "source_telegram_message_id"),
        Index(
            "ix_conversation_messages_source_telegram",
            "source_chat_id",
            "source_telegram_message_id",
        ),
    )


class CommissionSettings(Base, TimestampMixin):
    __tablename__ = "commission_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    service_percent: Mapped[Decimal] = mapped_column(PERCENT, default=Decimal("13.000"))

    __table_args__ = (
        CheckConstraint(
            "service_percent BETWEEN 0 AND 100",
            name="service_percent_range",
        ),
    )


class WithdrawalRequest(Base, TimestampMixin):
    __tablename__ = "withdrawal_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    expert_id: Mapped[int] = mapped_column(
        ForeignKey("expert_profiles.id", ondelete="RESTRICT"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(MONEY)
    bank_name: Mapped[str] = mapped_column(String(120))
    phone_snapshot: Mapped[str] = mapped_column(String(20))
    status: Mapped[WithdrawalStatus] = mapped_column(
        Enum(WithdrawalStatus, native_enum=False), default=WithdrawalStatus.PENDING, index=True
    )
    processed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint("amount > 0", name="amount_positive"),)


class FinancialLedgerEntry(Base, TimestampMixin):
    __tablename__ = "financial_ledger_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    expert_id: Mapped[int | None] = mapped_column(
        ForeignKey("expert_profiles.id", ondelete="RESTRICT"), index=True
    )
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    transaction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("payment_transactions.id", ondelete="RESTRICT"), index=True
    )
    withdrawal_id: Mapped[int | None] = mapped_column(
        ForeignKey("withdrawal_requests.id", ondelete="RESTRICT"), index=True
    )
    type: Mapped[LedgerEntryType] = mapped_column(
        Enum(LedgerEntryType, native_enum=False), index=True
    )
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    affects_expert_balance: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    description: Mapped[str | None] = mapped_column(String(500))
    reversal_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("financial_ledger_entries.id", ondelete="RESTRICT")
    )


class ReferralReward(Base, TimestampMixin):
    __tablename__ = "referral_rewards"

    id: Mapped[int] = mapped_column(primary_key=True)
    referrer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    referred_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    transaction_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_transactions.id", ondelete="RESTRICT"), index=True
    )
    level: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("referrer_id", "transaction_id", "level"),)


class PaymentGateway(Base, TimestampMixin):
    __tablename__ = "payment_gateways"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[PaymentProvider] = mapped_column(
        Enum(PaymentProvider, native_enum=False), unique=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Settings(Base, TimestampMixin):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    registration_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    payments_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    referral_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    referral_levels: Mapped[int] = mapped_column(Integer, default=1)
    referral_accrual: Mapped[ReferralAccrual] = mapped_column(
        Enum(ReferralAccrual, native_enum=False), default=ReferralAccrual.FIRST_PAYMENT
    )
    referral_reward_kind: Mapped[ReferralRewardKind] = mapped_column(
        Enum(ReferralRewardKind, native_enum=False), default=ReferralRewardKind.PERCENT
    )
    referral_level_1: Mapped[Decimal] = mapped_column(PERCENT, default=Decimal("5.000"))
    referral_level_2: Mapped[Decimal] = mapped_column(PERCENT, default=Decimal("2.000"))
    notifications: Mapped[dict[str, bool]] = mapped_column(JSON, default=dict)
    menu: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    __table_args__ = (CheckConstraint("referral_levels BETWEEN 1 AND 2", name="referral_levels"),)


class Broadcast(Base, TimestampMixin):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    audience: Mapped[BroadcastAudience] = mapped_column(Enum(BroadcastAudience, native_enum=False))
    audience_filter: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[BroadcastStatus] = mapped_column(
        Enum(BroadcastStatus, native_enum=False), default=BroadcastStatus.DRAFT, index=True
    )
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)


class BroadcastMessage(Base, TimestampMixin):
    __tablename__ = "broadcast_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    broadcast_id: Mapped[int] = mapped_column(
        ForeignKey("broadcasts.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    error: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (UniqueConstraint("broadcast_id", "user_id"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(120), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str | None] = mapped_column(String(80))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default="now()", index=True)
