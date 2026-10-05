from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from core.enums.payment_status import PaymentStatus


@dataclass
class Payment:
    """
    Платёж пользователя за подписку.
    """

    id: UUID = field(default_factory=uuid4)

    # Кто совершил платёж
    user_id: UUID = field(default_factory=uuid4)

    # За какую подписку произведён платёж
    subscription_id: UUID | None = None

    # Сумма
    amount: Decimal = Decimal("0.00")

    # Валюта
    currency: str = "RUB"

    # Статус платежа
    status: PaymentStatus = PaymentStatus.PENDING

    # Платёжная система
    payment_provider: str | None = None

    # Дата создания платежа
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ValueError(
                "Сумма платежа должна быть больше нуля."
            )

        if not self.currency.strip():
            raise ValueError(
                "Валюта платежа не может быть пустой."
            )