from dataclasses import dataclass, field
from datetime import UTC, datetime
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

    # Уникальный ID операции у провайдера
    provider_payment_id: str | None = None

    # Дата создания платежа
    created_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
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

        self.currency = self.currency.strip().upper()

        if len(self.currency) != 3:
            raise ValueError(
                "Валюта должна быть трёхбуквенным ISO-кодом."
            )

        if self.provider_payment_id is not None:
            self.provider_payment_id = self.provider_payment_id.strip()
            if not self.provider_payment_id:
                raise ValueError("ID платежа провайдера не может быть пустым.")
