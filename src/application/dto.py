from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from src.core.enums import PaymentProvider, Role, TransactionStatus


@dataclass(frozen=True, slots=True)
class CommissionBreakdown:
    gross: Decimal
    service_percent: Decimal
    service_fee: Decimal
    expert_income: Decimal
    # Compatibility for callers from the previous two-commission API. The
    # application itself only uses the single service commission.
    payment_percent: Decimal = Decimal("0")
    payment_fee: Decimal = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class Checkout:
    transaction_id: UUID
    provider_payment_id: str
    url: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class ProviderPayment:
    provider_payment_id: str
    url: str


@dataclass(frozen=True, slots=True)
class WebhookPayment:
    provider: PaymentProvider
    provider_payment_id: str
    status: TransactionStatus
    amount: Decimal
    raw_status: str
    safe_payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class MenuDescriptor:
    role: Role
    title: str
    buttons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IncomingTopicMessage:
    chat_id: int
    thread_id: int
    telegram_message_id: int
    sender_telegram_id: int
    content_type: str
    text: str
    file_id: str | None = None
    reply_to_telegram_message_id: int | None = None


@dataclass(frozen=True, slots=True)
class TopicDelivery:
    telegram_message_id: int


@dataclass(frozen=True, slots=True)
class TopicRelayResult:
    status: str
    conversation_id: int | None = None
    destination_topic_id: int | None = None
