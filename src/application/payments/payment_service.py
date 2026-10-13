from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from application.marketplace_service import money
from core.entities.payment import Payment
from core.enums.payment_status import PaymentStatus
from core.enums.subscription_status import SubscriptionStatus
from infrastructure.database.unit_of_work import UnitOfWork


class PaymentService:
    def __init__(
        self,
        unit_of_work: UnitOfWork,
    ):
        self.unit_of_work = unit_of_work

    async def create_payment(
        self,
        user_id: UUID,
        amount: Decimal,
        currency: str = "RUB",
        subscription_id: UUID | None = None,
        payment_provider: str | None = None,
        provider_payment_id: str | None = None,
    ) -> Payment:
        if amount <= 0:
            raise ValueError(
                "Сумма платежа должна быть больше нуля."
            )

        async with self.unit_of_work as uow:
            if uow.payments is None or uow.subscriptions is None:
                raise RuntimeError(
                    "PaymentRepository не инициализирован."
                )

            if provider_payment_id is not None:
                if payment_provider is None:
                    raise ValueError(
                        "Для ID операции необходимо указать платёжного провайдера."
                    )
                existing = await uow.payments.get_by_provider_operation(
                    payment_provider,
                    provider_payment_id,
                )
                if existing is not None:
                    if (
                        existing.user_id != user_id
                        or existing.amount != amount
                        or existing.currency != currency.strip().upper()
                    ):
                        raise ValueError(
                            "ID операции уже связан с другим платежом."
                        )
                    return existing

            if subscription_id is not None:
                subscription = await uow.subscriptions.get_by_id(subscription_id)
                if subscription is None:
                    raise ValueError("Подписка для платежа не найдена.")
                if subscription.user_id != user_id:
                    raise PermissionError("Нельзя оплатить чужую подписку.")
                if subscription.status != SubscriptionStatus.PENDING:
                    raise ValueError("Оплатить можно только ожидающую оплаты подписку.")

            payment = Payment(
                user_id=user_id,
                subscription_id=subscription_id,
                amount=amount,
                currency=currency,
                status=PaymentStatus.PENDING,
                payment_provider=payment_provider,
                provider_payment_id=provider_payment_id,
            )
            return await uow.payments.create(payment)

    async def get_payment(
        self,
        payment_id: UUID,
    ) -> Payment | None:
        async with self.unit_of_work as uow:
            if uow.payments is None:
                raise RuntimeError(
                    "PaymentRepository не инициализирован."
                )

            return await uow.payments.get_by_id(
                payment_id
            )

    async def get_user_payments(
        self,
        user_id: UUID,
    ) -> list[Payment]:
        async with self.unit_of_work as uow:
            if uow.payments is None:
                raise RuntimeError(
                    "PaymentRepository не инициализирован."
                )

            return await uow.payments.get_for_user(
                user_id
            )

    async def get_subscription_payments(
        self,
        subscription_id: UUID,
    ) -> list[Payment]:
        async with self.unit_of_work as uow:
            if uow.payments is None:
                raise RuntimeError(
                    "PaymentRepository не инициализирован."
                )

            return await uow.payments.get_by_subscription(
                subscription_id
            )

    async def mark_as_succeeded(
        self,
        payment_id: UUID,
    ) -> Payment | None:
        async with self.unit_of_work as uow:
            if (
                uow.payments is None
                or uow.subscriptions is None
                or uow.marketplace is None
            ):
                raise RuntimeError(
                    "Репозитории Payment/Subscription "
                    "не инициализированы."
                )

            payment = await uow.payments.get_by_id(payment_id, for_update=True)

            if payment is None:
                return None

            if payment.status == PaymentStatus.SUCCEEDED:
                return payment

            if payment.status in {
                PaymentStatus.REFUNDED,
                PaymentStatus.FAILED,
            }:
                raise ValueError(
                    "Нельзя подтвердить этот платёж."
                )

            if payment.expert_id is not None:
                settings = await uow.marketplace.fee_settings(lock=True)
                payment_fee = money(payment.amount * settings.payment_percent / 100)
                service_fee = money(payment.amount * settings.service_percent / 100)
                income = payment.amount - payment_fee - service_fee
                await uow.payments.set_financial_snapshot(
                    payment.id, settings.payment_percent, settings.service_percent,
                    payment_fee, service_fee, income,
                )

            payment = await uow.payments.update_status(
                payment_id,
                PaymentStatus.SUCCEEDED,
            )

            if payment is None:
                return None

            if payment.subscription_id is not None:
                subscription = (
                    await uow.subscriptions.get_by_id(
                        payment.subscription_id
                    )
                )

                if subscription is not None:
                    if (
                        subscription.status
                        == SubscriptionStatus.PENDING
                    ):
                        await uow.subscriptions.activate(
                            subscription.id,
                            datetime.now(UTC),
                        )

            return payment

    async def complete_provider_payment(
        self, provider: str, external_id: str, amount: Decimal,
    ) -> Payment:
        payment, _ = await self.apply_provider_event(
            provider, external_id, amount, PaymentStatus.SUCCEEDED,
        )
        return payment

    async def apply_provider_event(
        self, provider: str, external_id: str, amount: Decimal,
        target_status: PaymentStatus,
    ) -> tuple[Payment, bool]:
        async with self.unit_of_work as uow:
            assert uow.payments and uow.subscriptions and uow.marketplace
            payment = await uow.payments.get_by_provider_operation(
                provider, external_id, for_update=True
            )
            if payment is None or payment.amount != amount or payment.currency != "RUB":
                raise ValueError("Платёж, сумма или валюта не совпадают.")
            if payment.status == target_status:
                return payment, False
            if target_status == PaymentStatus.SUCCEEDED:
                if payment.status != PaymentStatus.PENDING:
                    raise ValueError("Платёж уже закрыт.")
                if payment.subscription_id is None:
                    raise ValueError("Подписка не привязана к платежу.")
                subscription = await uow.subscriptions.get_by_id(payment.subscription_id)
                if subscription is None or subscription.user_id != payment.user_id:
                    raise ValueError("Подписка платежа не найдена.")
                if subscription.status != SubscriptionStatus.PENDING:
                    raise ValueError("Подписка не ожидает оплаты.")
                settings = await uow.marketplace.fee_settings(lock=True)
                payment_fee = money(payment.amount * settings.payment_percent / 100)
                service_fee = money(payment.amount * settings.service_percent / 100)
                income = payment.amount - payment_fee - service_fee
                await uow.payments.set_financial_snapshot(
                    payment.id, settings.payment_percent, settings.service_percent,
                    payment_fee, service_fee, income,
                )
                await uow.subscriptions.activate(subscription.id, datetime.now(UTC))
            elif target_status == PaymentStatus.FAILED:
                if payment.status != PaymentStatus.PENDING:
                    return payment, False  # Ignore stale cancellation after confirmation.
            elif target_status == PaymentStatus.REFUNDED:
                if payment.status not in {PaymentStatus.PENDING, PaymentStatus.SUCCEEDED}:
                    return payment, False
                if payment.subscription_id is not None:
                    subscription = await uow.subscriptions.get_by_id(payment.subscription_id)
                    if subscription is not None and subscription.status in {
                        SubscriptionStatus.PENDING, SubscriptionStatus.ACTIVE,
                    }:
                        await uow.subscriptions.cancel(subscription.id)
            else:
                raise ValueError("Недопустимый статус провайдера.")
            completed = await uow.payments.update_status(payment.id, target_status)
            assert completed is not None
            return completed, True

    async def mark_as_failed(
        self,
        payment_id: UUID,
    ) -> Payment | None:
        async with self.unit_of_work as uow:
            if uow.payments is None:
                raise RuntimeError(
                    "PaymentRepository не инициализирован."
                )

            payment = await uow.payments.get_by_id(payment_id, for_update=True)

            if payment is None:
                return None

            if payment.status == PaymentStatus.SUCCEEDED:
                raise ValueError(
                    "Нельзя сделать успешный платёж неуспешным."
                )

            if payment.status == PaymentStatus.REFUNDED:
                raise ValueError(
                    "Нельзя изменить возвращённый платёж."
                )

            if payment.status == PaymentStatus.FAILED:
                return payment

            return await uow.payments.update_status(
                payment_id,
                PaymentStatus.FAILED,
            )

    async def refund(
        self,
        payment_id: UUID,
    ) -> Payment | None:
        async with self.unit_of_work as uow:
            if uow.payments is None or uow.subscriptions is None:
                raise RuntimeError(
                    "PaymentRepository не инициализирован."
                )

            payment = await uow.payments.get_by_id(payment_id, for_update=True)

            if payment is None:
                return None

            if payment.status == PaymentStatus.REFUNDED:
                return payment

            if payment.status != PaymentStatus.SUCCEEDED:
                raise ValueError(
                    "Вернуть можно только успешно оплаченный платёж."
                )

            refunded = await uow.payments.update_status(
                payment_id,
                PaymentStatus.REFUNDED,
            )
            if payment.subscription_id is not None:
                subscription = await uow.subscriptions.get_by_id(
                    payment.subscription_id
                )
                if subscription is not None and subscription.status in {
                    SubscriptionStatus.PENDING,
                    SubscriptionStatus.ACTIVE,
                }:
                    await uow.subscriptions.cancel(subscription.id)
            return refunded
