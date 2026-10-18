from datetime import datetime
from decimal import Decimal

from src.application.money import money
from src.core.enums import (
    ReferralRewardKind,
    Role,
    SubscriptionStatus,
    TransactionStatus,
)
from src.core.exceptions import (
    AccessDenied,
    InsufficientBalance,
    MessageLimitReached,
    PaymentConflict,
    SubscriptionRequired,
)


def role_for_user(telegram_id: int, owner_id: int) -> Role:
    return Role.OWNER if telegram_id == owner_id else Role.CLIENT


def effective_price(default: Decimal, custom: Decimal | None) -> Decimal:
    value = default if custom is None else custom
    if value < 0:
        raise ValueError("Price cannot be negative")
    return money(value)


def ensure_payment_transition(current: TransactionStatus, new: TransactionStatus) -> None:
    allowed = {
        TransactionStatus.PENDING: {
            TransactionStatus.COMPLETED,
            TransactionStatus.CANCELED,
            TransactionStatus.FAILED,
        },
        TransactionStatus.COMPLETED: {TransactionStatus.REFUNDED},
    }
    if new == current:
        return
    if new not in allowed.get(current, set()):
        raise PaymentConflict(f"Invalid payment transition: {current} -> {new}")


def ensure_webhook_amount(expected: Decimal, received: Decimal) -> None:
    if money(expected) != money(received):
        raise PaymentConflict("Webhook amount mismatch")


def subscription_is_active(
    status: SubscriptionStatus,
    expires_at: datetime | None,
    now: datetime,
) -> bool:
    return status is SubscriptionStatus.ACTIVE and expires_at is not None and expires_at > now


def ensure_message_allowed(
    *,
    active: bool,
    message_limit: int | None,
    messages_used: int,
) -> None:
    if not active:
        raise SubscriptionRequired("Active subscription required")
    if message_limit is not None and messages_used >= message_limit:
        raise MessageLimitReached("Message limit reached")


def referral_reward(
    gross: Decimal,
    kind: ReferralRewardKind,
    value: Decimal,
) -> Decimal:
    result = value if kind is ReferralRewardKind.FIXED else gross * value / Decimal("100")
    return money(result)


def ledger_balance(entries: list[tuple[Decimal, bool]]) -> Decimal:
    return money(sum((amount for amount, affects in entries if affects), Decimal("0")))


def ensure_can_withdraw(balance: Decimal, has_open_request: bool) -> None:
    if has_open_request:
        raise InsufficientBalance("Open withdrawal exists")
    if money(balance) <= 0:
        raise InsufficientBalance("No available balance")


def ensure_conversation_access(
    *,
    actor_id: int,
    actor_role: Role,
    client_id: int,
    expert_user_id: int,
) -> None:
    if actor_role is Role.OWNER:
        return
    if actor_role is Role.CLIENT and actor_id == client_id:
        return
    if actor_role is Role.EXPERT and actor_id == expert_user_id:
        return
    raise AccessDenied("Conversation does not belong to actor")
