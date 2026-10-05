from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from core.enums.subscription_status import SubscriptionStatus


@dataclass
class Subscription:
    """
    Подписка пользователя на конкретного эксперта.
    """

    id: UUID

    # Кто купил подписку
    user_id: UUID

    # С кем пользователь покупает общение
    expert_id: UUID

    # Начало действия подписки
    start_date: datetime

    # Окончание действия подписки
    end_date: datetime

    # Текущий статус
    status: SubscriptionStatus = SubscriptionStatus.PENDING

    @classmethod
    def create(
        cls,
        user_id: UUID,
        expert_id: UUID,
        start_date: datetime,
        end_date: datetime,
    ) -> "Subscription":
        """
        Создаёт новую подписку.
        """

        if end_date <= start_date:
            raise ValueError(
                "Дата окончания подписки должна быть позже даты начала."
            )

        return cls(
            id=uuid4(),
            user_id=user_id,
            expert_id=expert_id,
            start_date=start_date,
            end_date=end_date,
        )

    def is_active(self, now: datetime) -> bool:
        """
        Проверяет, действует ли подписка в данный момент.
        """

        return (
            self.status == SubscriptionStatus.ACTIVE
            and self.start_date <= now < self.end_date
        )