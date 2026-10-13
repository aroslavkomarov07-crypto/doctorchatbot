from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.database.models.expert_model import ExpertModel
from infrastructure.database.models.marketplace_model import (
    ExpertApplicationModel,
    ExpertPriceModel,
    FeeSettingsModel,
    TariffModel,
    WithdrawalModel,
)
from infrastructure.database.models.payment_model import PaymentModel
from infrastructure.database.models.user_model import UserModel


class MarketplaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def tariffs(self, active_only: bool = False) -> list[TariffModel]:
        query = select(TariffModel).order_by(TariffModel.name)
        if active_only:
            query = query.where(TariffModel.is_active.is_(True))
        return list((await self.session.scalars(query)).all())

    async def tariff(self, tariff_id: UUID) -> TariffModel | None:
        return await self.session.get(TariffModel, tariff_id)

    async def create_tariff(
        self, name: str, description: str, days: int, limit: int | None, price: Decimal
    ) -> TariffModel:
        tariff = TariffModel(
            id=uuid4(), name=name, description=description, duration_days=days,
            message_limit=limit, base_price=price, is_active=True,
        )
        self.session.add(tariff)
        await self.session.flush()
        return tariff

    async def price(self, expert_id: UUID, tariff_id: UUID) -> Decimal | None:
        return cast(Decimal | None, await self.session.scalar(
            select(ExpertPriceModel.price).where(
                ExpertPriceModel.expert_id == expert_id,
                ExpertPriceModel.tariff_id == tariff_id,
            )
        ))

    async def set_price(self, expert_id: UUID, tariff_id: UUID, price: Decimal) -> None:
        model = await self.session.scalar(
            select(ExpertPriceModel).where(
                ExpertPriceModel.expert_id == expert_id,
                ExpertPriceModel.tariff_id == tariff_id,
            )
        )
        if model is None:
            self.session.add(ExpertPriceModel(
                id=uuid4(), expert_id=expert_id, tariff_id=tariff_id, price=price
            ))
        else:
            model.price = price
        await self.session.flush()

    async def applications(self, status: str = "pending") -> list[ExpertApplicationModel]:
        return list((await self.session.scalars(
            select(ExpertApplicationModel).where(ExpertApplicationModel.status == status)
            .order_by(ExpertApplicationModel.created_at.desc()).limit(50)
        )).all())

    async def application(self, application_id: UUID, lock: bool = False) -> ExpertApplicationModel | None:
        query = select(ExpertApplicationModel).where(ExpertApplicationModel.id == application_id)
        if lock:
            query = query.with_for_update()
        return cast(ExpertApplicationModel | None, await self.session.scalar(query))

    async def pending_application_for_user(self, user_id: UUID) -> ExpertApplicationModel | None:
        return cast(ExpertApplicationModel | None, await self.session.scalar(
            select(ExpertApplicationModel).where(
                ExpertApplicationModel.user_id == user_id,
                ExpertApplicationModel.status == "pending",
            ).limit(1)
        ))

    async def create_application(
        self, user_id: UUID, full_name: str, specialization: str,
        education: str, experience: str, description: str, photo_file_id: str,
    ) -> ExpertApplicationModel:
        model = ExpertApplicationModel(
            id=uuid4(), user_id=user_id, full_name=full_name,
            specialization=specialization, education=education,
            experience=experience, description=description,
            photo_file_id=photo_file_id, status="pending", created_at=datetime.now(UTC),
        )
        self.session.add(model)
        await self.session.flush()
        return model

    async def fee_settings(self, lock: bool = False) -> FeeSettingsModel:
        query = select(FeeSettingsModel).where(FeeSettingsModel.id == 1)
        if lock:
            query = query.with_for_update()
        model = await self.session.scalar(query)
        if model is None:
            model = FeeSettingsModel(id=1, payment_percent=Decimal(0), service_percent=Decimal(0))
            self.session.add(model)
            await self.session.flush()
        return model

    async def user_by_id(self, user_id: UUID) -> UserModel | None:
        return await self.session.get(UserModel, user_id)

    async def users(self, offset: int = 0) -> list[UserModel]:
        return list((await self.session.scalars(
            select(UserModel).order_by(UserModel.created_at.desc()).offset(offset).limit(10)
        )).all())

    async def experts(self, offset: int = 0) -> list[ExpertModel]:
        return list((await self.session.scalars(
            select(ExpertModel).order_by(ExpertModel.created_at.desc()).offset(offset).limit(10)
        )).all())

    async def payments(self, offset: int = 0) -> list[PaymentModel]:
        return list((await self.session.scalars(
            select(PaymentModel).order_by(PaymentModel.created_at.desc()).offset(offset).limit(10)
        )).all())

    async def expert_by_id(self, expert_id: UUID) -> ExpertModel | None:
        return await self.session.get(ExpertModel, expert_id)

    async def expert_by_telegram_id(self, telegram_id: int) -> ExpertModel | None:
        return cast(ExpertModel | None, await self.session.scalar(
            select(ExpertModel).where(ExpertModel.telegram_id == telegram_id)
        ))

    async def create_or_update_expert(self, application: ExpertApplicationModel, telegram_id: int) -> ExpertModel:
        expert = await self.expert_by_telegram_id(telegram_id)
        if expert is None:
            expert = ExpertModel(id=uuid4(), telegram_id=telegram_id, created_at=datetime.now(UTC))
            self.session.add(expert)
        expert.full_name = application.full_name
        expert.specialization = application.specialization
        expert.education = application.education
        expert.experience = application.experience
        expert.description = application.description
        expert.photo_file_id = application.photo_file_id
        expert.is_active = True
        application.status = "approved"
        await self.session.flush()
        return expert

    async def withdrawals(self, status: str = "pending") -> list[WithdrawalModel]:
        return list((await self.session.scalars(
            select(WithdrawalModel).where(WithdrawalModel.status == status)
            .order_by(WithdrawalModel.created_at.desc()).limit(50)
        )).all())

    async def withdrawal(self, withdrawal_id: UUID, lock: bool = False) -> WithdrawalModel | None:
        query = select(WithdrawalModel).where(WithdrawalModel.id == withdrawal_id)
        if lock:
            query = query.with_for_update()
        return cast(WithdrawalModel | None, await self.session.scalar(query))

    async def financial_totals(self, expert_id: UUID) -> tuple[Decimal, Decimal, Decimal, Decimal, int]:
        from core.enums.payment_status import PaymentStatus

        payment_row = (await self.session.execute(
            select(
                func.coalesce(func.sum(PaymentModel.amount), 0),
                func.coalesce(func.sum(PaymentModel.payment_fee), 0),
                func.coalesce(func.sum(PaymentModel.service_fee), 0),
                func.coalesce(func.sum(PaymentModel.expert_income), 0),
                func.count(PaymentModel.id),
            ).where(
                PaymentModel.expert_id == expert_id,
                PaymentModel.status == PaymentStatus.SUCCEEDED,
            )
        )).one()
        return (
            Decimal(payment_row[0]), Decimal(payment_row[1]),
            Decimal(payment_row[2]), Decimal(payment_row[3]), int(payment_row[4]),
        )

    async def withdrawal_totals(self, expert_id: UUID) -> tuple[Decimal, Decimal]:
        paid = await self.session.scalar(select(func.coalesce(func.sum(WithdrawalModel.amount), 0)).where(
            WithdrawalModel.expert_id == expert_id, WithdrawalModel.status == "paid"
        ))
        pending = await self.session.scalar(select(func.coalesce(func.sum(WithdrawalModel.amount), 0)).where(
            WithdrawalModel.expert_id == expert_id, WithdrawalModel.status == "pending"
        ))
        return Decimal(paid or 0), Decimal(pending or 0)

    async def create_withdrawal(self, expert_id: UUID, amount: Decimal, bank: str) -> WithdrawalModel:
        model = WithdrawalModel(
            id=uuid4(), expert_id=expert_id, amount=amount, bank_name=bank,
            status="pending", created_at=datetime.now(UTC),
        )
        self.session.add(model)
        await self.session.flush()
        return model
