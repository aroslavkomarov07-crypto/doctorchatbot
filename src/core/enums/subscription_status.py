from enum import StrEnum


class SubscriptionStatus(StrEnum):
    """
    Состояние подписки.
    """

    # Подписка создана, но ещё не оплачена
    PENDING = "pending"

    # Подписка активна
    ACTIVE = "active"

    # Срок подписки закончился
    EXPIRED = "expired"

    # Подписка отменена
    CANCELLED = "cancelled"