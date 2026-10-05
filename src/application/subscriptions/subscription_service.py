from datetime import datetime, timedelta, timezone
from uuid import UUID

from core.entities.subscription import Subscription
from core.enums.subscription_status import SubscriptionStatus
from infrastructure.database.unit_of_work import UnitOfWork


class SubscriptionService:
    def __init__(
        self,
        unit_of_work: UnitOfWork,
    ):
        self.unit_of_work = unit_of_work

    async def create_subscription(
        self,
        user_id: UUID,
        expert_id: UUID,
        duration_days: int = 30,
    ) -> Subscription:
        if duration_days <= 0:
            raise ValueError(
                "Продолжительность подписки должна быть больше нуля."
            )

        start_date = datetime.now(timezone.utc)
        end_date = start_date + timedelta(days=duration_days)

        subscription = Subscription.create(
            user_id=user_id,
            expert_id=expert_id,
            start_date=start_date,
            end_date=end_date,
        )

        async with self.unit_of_work as uow:
            if uow.subscriptions is None:
                raise RuntimeError(
                    "SubscriptionRepository не инициализирован."
                )

            return await uow.subscriptions.create(
                subscription
            )

    async def get_active_subscription(
        self,
        user_id: UUID,
    ) -> Subscription | None:
        now = datetime.now(timezone.utc)

        async with self.unit_of_work as uow:
            if uow.subscriptions is None:
                raise RuntimeError(
                    "SubscriptionRepository не инициализирован."
                )

            return await uow.subscriptions.get_active_for_user(
                user_id=user_id,
                now=now,
            )

    async def get_user_subscriptions(
        self,
        user_id: UUID,
    ) -> list[Subscription]:
        async with self.unit_of_work as uow:
            if uow.subscriptions is None:
                raise RuntimeError(
                    "SubscriptionRepository не инициализирован."
                )

            return await uow.subscriptions.get_for_user(
                user_id
            )

    async def activate_subscription(
        self,
        subscription_id: UUID,
    ) -> Subscription | None:
        async with self.unit_of_work as uow:
            if uow.subscriptions is None:
                raise RuntimeError(
                    "SubscriptionRepository не инициализирован."
                )

            return await uow.subscriptions.update_status(
                subscription_id,
                SubscriptionStatus.ACTIVE,
            )

    async def cancel_subscription(
        self,
        subscription_id: UUID,
    ) -> Subscription | None:
        async with self.unit_of_work as uow:
            if uow.subscriptions is None:
                raise RuntimeError(
                    "SubscriptionRepository не инициализирован."
                )

            return await uow.subscriptions.update_status(
                subscription_id,
                SubscriptionStatus.CANCELLED,
            )