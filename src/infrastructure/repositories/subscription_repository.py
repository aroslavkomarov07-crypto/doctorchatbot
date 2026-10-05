from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.entities.subscription import Subscription
from core.enums.subscription_status import SubscriptionStatus
from infrastructure.database.models.subscription_model import SubscriptionModel


class SubscriptionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        subscription: Subscription,
    ) -> Subscription:
        subscription_model = SubscriptionModel(
            id=subscription.id,
            user_id=subscription.user_id,
            expert_id=subscription.expert_id,
            start_date=subscription.start_date,
            end_date=subscription.end_date,
            status=subscription.status,
        )

        self.session.add(subscription_model)

        await self.session.flush()

        return subscription

    async def get_by_id(
        self,
        subscription_id: UUID,
    ) -> Subscription | None:
        result = await self.session.execute(
            select(SubscriptionModel).where(
                SubscriptionModel.id == subscription_id
            )
        )

        subscription_model = result.scalar_one_or_none()

        if subscription_model is None:
            return None

        return self._to_entity(subscription_model)

    async def get_active_for_user(
        self,
        user_id: UUID,
        now: datetime,
    ) -> Subscription | None:
        result = await self.session.execute(
            select(SubscriptionModel)
            .where(
                SubscriptionModel.user_id == user_id,
                SubscriptionModel.status == SubscriptionStatus.ACTIVE,
                SubscriptionModel.start_date <= now,
                SubscriptionModel.end_date > now,
            )
            .order_by(
                SubscriptionModel.end_date.desc()
            )
        )

        subscription_model = result.scalars().first()

        if subscription_model is None:
            return None

        return self._to_entity(subscription_model)

    async def get_for_user(
        self,
        user_id: UUID,
    ) -> list[Subscription]:
        result = await self.session.execute(
            select(SubscriptionModel)
            .where(
                SubscriptionModel.user_id == user_id
            )
            .order_by(
                SubscriptionModel.start_date.desc()
            )
        )

        subscription_models = result.scalars().all()

        return [
            self._to_entity(subscription)
            for subscription in subscription_models
        ]

    async def get_for_expert(
        self,
        expert_id: UUID,
    ) -> list[Subscription]:
        result = await self.session.execute(
            select(SubscriptionModel)
            .where(
                SubscriptionModel.expert_id == expert_id
            )
            .order_by(
                SubscriptionModel.start_date.desc()
            )
        )

        subscription_models = result.scalars().all()

        return [
            self._to_entity(subscription)
            for subscription in subscription_models
        ]

    async def update_status(
        self,
        subscription_id: UUID,
        status: SubscriptionStatus,
    ) -> Subscription | None:
        result = await self.session.execute(
            select(SubscriptionModel).where(
                SubscriptionModel.id == subscription_id
            )
        )

        subscription_model = result.scalar_one_or_none()

        if subscription_model is None:
            return None

        subscription_model.status = status

        await self.session.flush()

        return self._to_entity(subscription_model)

    @staticmethod
    def _to_entity(
        subscription_model: SubscriptionModel,
    ) -> Subscription:
        return Subscription(
            id=subscription_model.id,
            user_id=subscription_model.user_id,
            expert_id=subscription_model.expert_id,
            start_date=subscription_model.start_date,
            end_date=subscription_model.end_date,
            status=subscription_model.status,
        )