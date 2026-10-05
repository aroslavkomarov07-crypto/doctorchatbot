from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.entities.payment import Payment
from core.enums.payment_status import PaymentStatus
from infrastructure.database.models.payment_model import PaymentModel


class PaymentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        payment: Payment,
    ) -> Payment:
        payment_model = PaymentModel(
            id=payment.id,
            user_id=payment.user_id,
            subscription_id=payment.subscription_id,
            amount=payment.amount,
            currency=payment.currency,
            status=payment.status,
            payment_provider=payment.payment_provider,
            provider_payment_id=payment.provider_payment_id,
            created_at=payment.created_at,
        )

        self.session.add(payment_model)

        await self.session.flush()

        return payment

    async def get_by_id(
        self,
        payment_id: UUID,
        *,
        for_update: bool = False,
    ) -> Payment | None:
        statement = select(PaymentModel).where(PaymentModel.id == payment_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)

        payment_model = result.scalar_one_or_none()

        if payment_model is None:
            return None

        return self._to_entity(payment_model)

    async def get_by_provider_operation(
        self,
        payment_provider: str,
        provider_payment_id: str,
    ) -> Payment | None:
        result = await self.session.execute(
            select(PaymentModel).where(
                PaymentModel.payment_provider == payment_provider,
                PaymentModel.provider_payment_id == provider_payment_id,
            )
        )
        payment_model = result.scalar_one_or_none()
        return None if payment_model is None else self._to_entity(payment_model)

    async def get_for_user(
        self,
        user_id: UUID,
    ) -> list[Payment]:
        result = await self.session.execute(
            select(PaymentModel)
            .where(
                PaymentModel.user_id == user_id
            )
            .order_by(
                PaymentModel.created_at.desc()
            )
        )

        payment_models = result.scalars().all()

        return [
            self._to_entity(payment)
            for payment in payment_models
        ]

    async def get_by_subscription(
        self,
        subscription_id: UUID,
    ) -> list[Payment]:
        result = await self.session.execute(
            select(PaymentModel)
            .where(
                PaymentModel.subscription_id == subscription_id
            )
            .order_by(
                PaymentModel.created_at.desc()
            )
        )

        payment_models = result.scalars().all()

        return [
            self._to_entity(payment)
            for payment in payment_models
        ]

    async def update_status(
        self,
        payment_id: UUID,
        status: PaymentStatus,
    ) -> Payment | None:
        result = await self.session.execute(
            select(PaymentModel).where(
                PaymentModel.id == payment_id
            )
        )

        payment_model = result.scalar_one_or_none()

        if payment_model is None:
            return None

        payment_model.status = status

        await self.session.flush()

        return self._to_entity(payment_model)

    @staticmethod
    def _to_entity(
        payment_model: PaymentModel,
    ) -> Payment:
        return Payment(
            id=payment_model.id,
            user_id=payment_model.user_id,
            subscription_id=payment_model.subscription_id,
            amount=payment_model.amount,
            currency=payment_model.currency,
            status=payment_model.status,
            payment_provider=payment_model.payment_provider,
            provider_payment_id=payment_model.provider_payment_id,
            created_at=payment_model.created_at,
        )
