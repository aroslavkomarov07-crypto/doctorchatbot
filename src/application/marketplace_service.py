from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from core.entities.payment import Payment
from core.entities.subscription import Subscription
from core.enums.payment_status import PaymentStatus
from infrastructure.database.models.expert_model import ExpertModel
from infrastructure.database.models.marketplace_model import (
    ExpertApplicationModel,
    TariffModel,
    WithdrawalModel,
)
from infrastructure.database.models.payment_model import PaymentModel
from infrastructure.database.models.user_model import UserModel
from infrastructure.database.unit_of_work import UnitOfWork

CENT = Decimal("0.01")
MAX_PAYMENT = Decimal("99999999.99")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Earnings:
    turnover: Decimal
    payment_fees: Decimal
    service_fees: Decimal
    income: Decimal
    paid: Decimal
    pending: Decimal
    available: Decimal
    subscriptions: int


class MarketplaceService:
    def __init__(self, unit_of_work: UnitOfWork) -> None:
        self.uow = unit_of_work

    async def tariffs(self, active_only: bool = False) -> list[TariffModel]:
        async with self.uow as uow:
            assert uow.marketplace is not None
            return await uow.marketplace.tariffs(active_only)

    async def users(self, page: int) -> list[UserModel]:
        async with self.uow as uow:
            assert uow.marketplace is not None
            return await uow.marketplace.users(max(0, page) * 10)

    async def experts(self, page: int) -> list[ExpertModel]:
        async with self.uow as uow:
            assert uow.marketplace is not None
            return await uow.marketplace.experts(max(0, page) * 10)

    async def payments(self, page: int) -> list[PaymentModel]:
        async with self.uow as uow:
            assert uow.marketplace is not None
            return await uow.marketplace.payments(max(0, page) * 10)

    async def application(self, application_id: UUID) -> ExpertApplicationModel | None:
        async with self.uow as uow:
            assert uow.marketplace is not None
            return await uow.marketplace.application(application_id)

    async def withdrawal(self, withdrawal_id: UUID) -> WithdrawalModel | None:
        async with self.uow as uow:
            assert uow.marketplace is not None
            return await uow.marketplace.withdrawal(withdrawal_id)

    async def deactivate_expert(self, expert_id: UUID) -> int:
        async with self.uow as uow:
            assert uow.marketplace is not None
            expert = await uow.marketplace.expert_by_id(expert_id)
            if expert is None:
                raise ValueError("Эксперт не найден.")
            expert.is_active = not expert.is_active
            return expert.telegram_id

    async def tariff(self, tariff_id: UUID) -> TariffModel | None:
        async with self.uow as uow:
            assert uow.marketplace is not None
            return await uow.marketplace.tariff(tariff_id)

    async def effective_price(self, expert_id: UUID, tariff_id: UUID) -> Decimal:
        async with self.uow as uow:
            assert uow.marketplace is not None
            tariff = await uow.marketplace.tariff(tariff_id)
            expert = await uow.marketplace.expert_by_id(expert_id)
            if tariff is None or not tariff.is_active or expert is None or not expert.is_active:
                raise ValueError("Тариф или эксперт недоступен.")
            return await uow.marketplace.price(expert_id, tariff_id) or tariff.base_price

    async def create_tariff(
        self, name: str, description: str, days: int, limit: int | None, price: Decimal
    ) -> TariffModel:
        if (
            not name.strip() or days <= 0 or price <= 0 or price > MAX_PAYMENT
            or (limit is not None and limit <= 0)
        ):
            raise ValueError("Некорректные параметры тарифа.")
        async with self.uow as uow:
            assert uow.marketplace is not None
            return await uow.marketplace.create_tariff(
                name.strip()[:255], description.strip(), days, limit, money(price)
            )

    async def update_tariff(self, tariff_id: UUID, field: str, value: str) -> None:
        allowed = {"name", "description", "duration_days", "message_limit", "base_price", "is_active"}
        if field not in allowed:
            raise ValueError("Недопустимое поле тарифа.")
        async with self.uow as uow:
            assert uow.marketplace is not None
            tariff = await uow.marketplace.tariff(tariff_id)
            if tariff is None:
                raise ValueError("Тариф не найден.")
            if field == "name":
                if not value.strip() or len(value) > 255:
                    raise ValueError("Название тарифа должно быть от 1 до 255 символов.")
                tariff.name = value.strip()
            elif field == "description":
                tariff.description = value.strip()
            elif field == "duration_days":
                days = int(value)
                if days <= 0:
                    raise ValueError("Длительность должна быть положительной.")
                tariff.duration_days = days
            elif field == "message_limit":
                limit = None if value == "∞" else int(value)
                if limit is not None and limit <= 0:
                    raise ValueError("Лимит должен быть положительным.")
                tariff.message_limit = limit
            elif field == "base_price":
                price = money(Decimal(value))
                if price <= 0 or price > MAX_PAYMENT:
                    raise ValueError("Цена должна быть от 0,01 до 99 999 999,99 ₽.")
                tariff.base_price = price
            elif field == "is_active":
                tariff.is_active = value == "true"

    async def set_expert_price(self, telegram_id: int, tariff_id: UUID, price: Decimal) -> None:
        price = money(price)
        if price <= 0 or price > MAX_PAYMENT:
            raise ValueError("Цена должна быть от 0,01 до 99 999 999,99 ₽.")
        async with self.uow as uow:
            assert uow.marketplace is not None
            expert = await uow.marketplace.expert_by_telegram_id(telegram_id)
            tariff = await uow.marketplace.tariff(tariff_id)
            if expert is None or not expert.is_active or tariff is None or not tariff.is_active:
                raise PermissionError("Нет доступа к тарифу.")
            await uow.marketplace.set_price(expert.id, tariff_id, price)

    async def apply_for_expert(
        self, user_id: UUID, full_name: str, specialization: str,
        education: str, experience: str, description: str, photo_file_id: str,
    ) -> ExpertApplicationModel:
        fields = (full_name, specialization, education, experience, description, photo_file_id)
        if any(not field.strip() for field in fields):
            raise ValueError("Заполните все поля анкеты.")
        async with self.uow as uow:
            assert uow.marketplace is not None
            if await uow.marketplace.user_by_id(user_id) is None:
                raise ValueError("Пользователь не найден.")
            if await uow.marketplace.pending_application_for_user(user_id) is not None:
                raise ValueError("Ваша предыдущая анкета ещё рассматривается.")
            return await uow.marketplace.create_application(
                user_id, full_name.strip(), specialization.strip(), education.strip(),
                experience.strip(), description.strip(), photo_file_id,
            )

    async def applications(self) -> list[ExpertApplicationModel]:
        async with self.uow as uow:
            assert uow.marketplace is not None
            return await uow.marketplace.applications()

    async def decide_application(self, application_id: UUID, decision: str) -> int:
        if decision not in {"approved", "rejected", "revision"}:
            raise ValueError("Недопустимое решение.")
        async with self.uow as uow:
            assert uow.marketplace is not None
            application = await uow.marketplace.application(application_id, lock=True)
            if application is None or application.status != "pending":
                raise ValueError("Заявка уже обработана.")
            user = await uow.marketplace.user_by_id(application.user_id)
            if user is None:
                raise ValueError("Пользователь не найден.")
            if decision == "approved":
                await uow.marketplace.create_or_update_expert(application, user.telegram_id)
            else:
                application.status = decision
            return user.telegram_id

    async def fee_settings(self) -> tuple[Decimal, Decimal]:
        async with self.uow as uow:
            assert uow.marketplace is not None
            settings = await uow.marketplace.fee_settings()
            return settings.payment_percent, settings.service_percent

    async def set_fees(self, payment_percent: Decimal, service_percent: Decimal) -> None:
        payment_percent = money(payment_percent)
        service_percent = money(service_percent)
        if (
            payment_percent < 0 or service_percent < 0
            or payment_percent + service_percent > 100
            or payment_percent > 100 or service_percent > 100
        ):
            raise ValueError("Сумма комиссий должна быть от 0 до 100%.")
        async with self.uow as uow:
            assert uow.marketplace is not None
            settings = await uow.marketplace.fee_settings(lock=True)
            settings.payment_percent = money(payment_percent)
            settings.service_percent = money(service_percent)

    async def create_purchase(
        self, user_id: UUID, expert_id: UUID, tariff_id: UUID,
        provider: str, external_id: str,
    ) -> Payment:
        async with self.uow as uow:
            assert uow.marketplace and uow.subscriptions and uow.payments
            user = await uow.marketplace.user_by_id(user_id)
            expert = await uow.marketplace.expert_by_id(expert_id)
            tariff = await uow.marketplace.tariff(tariff_id)
            if user is None or expert is None or not expert.is_active or tariff is None or not tariff.is_active:
                raise ValueError("Выбор больше недоступен.")
            price = await uow.marketplace.price(expert_id, tariff_id) or tariff.base_price
            now = datetime.now(UTC)
            subscription = Subscription.create(user_id, expert_id, now, now + timedelta(days=tariff.duration_days))
            subscription.tariff_id = tariff.id
            subscription.message_limit = tariff.message_limit
            await uow.subscriptions.create(subscription)
            payment = Payment(
                id=UUID(external_id),
                user_id=user_id, subscription_id=subscription.id, expert_id=expert_id,
                tariff_id=tariff.id, tariff_name=tariff.name, duration_days=tariff.duration_days,
                message_limit=tariff.message_limit, amount=price, currency="RUB",
                status=PaymentStatus.PENDING, payment_provider=provider,
                provider_payment_id=external_id if provider == "mock" else None,
            )
            return await uow.payments.create(payment)

    async def set_payment_url(self, payment_id: UUID, url: str) -> None:
        async with self.uow as uow:
            assert uow.payments is not None
            await uow.payments.set_payment_url(payment_id, url)

    async def attach_provider_payment(
        self, payment_id: UUID, provider_payment_id: UUID, url: str,
    ) -> None:
        async with self.uow as uow:
            assert uow.payments is not None
            await uow.payments.set_provider_details(payment_id, str(provider_payment_id), url)

    async def earnings(self, expert_id: UUID) -> Earnings:
        async with self.uow as uow:
            assert uow.marketplace is not None
            turnover, payment_fees, service_fees, income, count = (
                await uow.marketplace.financial_totals(expert_id)
            )
            paid, pending = await uow.marketplace.withdrawal_totals(expert_id)
            return Earnings(
                turnover, payment_fees, service_fees, income, paid, pending,
                max(Decimal(0), income - paid - pending), count,
            )

    async def request_withdrawal(self, expert_id: UUID, amount: Decimal, bank: str) -> WithdrawalModel:
        amount = money(amount)
        if amount <= 0 or not bank.strip() or len(bank) > 255:
            raise ValueError("Укажите сумму и банк.")
        async with self.uow as uow:
            assert uow.marketplace and uow.session
            # Serialize balance changes for this expert, including competing requests.
            expert = await uow.marketplace.expert_by_id(expert_id)
            if expert is None or not expert.is_active:
                raise PermissionError("Профиль эксперта неактивен.")
            await uow.session.refresh(expert, with_for_update=True)
            _, _, _, income, _ = await uow.marketplace.financial_totals(expert_id)
            paid, pending = await uow.marketplace.withdrawal_totals(expert_id)
            if amount > income - paid - pending:
                raise ValueError("Недостаточно средств для вывода.")
            return await uow.marketplace.create_withdrawal(expert_id, amount, bank.strip())

    async def withdrawals(self) -> list[WithdrawalModel]:
        async with self.uow as uow:
            assert uow.marketplace is not None
            return await uow.marketplace.withdrawals()

    async def decide_withdrawal(
        self, withdrawal_id: UUID, approve: bool, owner_id: int, reason: str | None = None
    ) -> int:
        async with self.uow as uow:
            assert uow.marketplace is not None
            withdrawal = await uow.marketplace.withdrawal(withdrawal_id, lock=True)
            if withdrawal is None or withdrawal.status != "pending":
                raise ValueError("Заявка уже обработана.")
            expert = await uow.marketplace.expert_by_id(withdrawal.expert_id)
            if expert is None:
                raise ValueError("Эксперт не найден.")
            withdrawal.status = "paid" if approve else "rejected"
            withdrawal.reason = reason
            withdrawal.processed_by = owner_id
            withdrawal.processed_at = datetime.now(UTC)
            return expert.telegram_id
