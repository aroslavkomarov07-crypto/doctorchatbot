import asyncio
from decimal import Decimal

from infrastructure.database.database import AsyncSessionLocal
from infrastructure.database.unit_of_work import UnitOfWork

from application.payments.payment_service import PaymentService
from application.subscriptions.subscription_service import (
    SubscriptionService,
)
from application.users.user_service import UserService
from application.experts.expert_service import ExpertService

from infrastructure.repositories.user_repository import UserRepository
from infrastructure.repositories.expert_repository import ExpertRepository
from infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)


async def main() -> None:
    print("=== TEST UNIT OF WORK ===")

    # ---------------------------------------------------------
    # 1. Получаем существующего пользователя
    # ---------------------------------------------------------

    async with AsyncSessionLocal() as session:
        user_repository = UserRepository(session)

        user = await user_repository.get_by_telegram_id(
            111222333
        )

        if user is None:
            print("Пользователь не найден.")
            return

        print(
            f"Пользователь: {user.full_name} "
            f"({user.telegram_id})"
        )

    # ---------------------------------------------------------
    # 2. Получаем существующего эксперта
    # ---------------------------------------------------------

    async with AsyncSessionLocal() as session:
        expert_repository = ExpertRepository(session)

        expert = await expert_repository.get_by_telegram_id(
            444555666
        )

        if expert is None:
            print("Эксперт не найден.")
            return

        print(
            f"Эксперт: {expert.full_name} "
            f"({expert.telegram_id})"
        )

    # ---------------------------------------------------------
    # 3. Создаём новую подписку
    # ---------------------------------------------------------

    async with AsyncSessionLocal() as session:
        subscription_repository = SubscriptionRepository(
            session
        )

        subscription_service = SubscriptionService(
            subscription_repository
        )

        subscription = (
            await subscription_service.create_subscription(
                user_id=user.id,
                expert_id=expert.id,
                duration_days=30,
            )
        )

        # SubscriptionService теперь работает с обычной
        # сессией, поэтому здесь явно сохраняем подписку.
        await session.commit()

        print(
            f"Создана подписка: {subscription.id}"
        )

        print(
            f"Статус подписки: "
            f"{subscription.status}"
        )

    # ---------------------------------------------------------
    # 4. Создаём PaymentService через UnitOfWork
    # ---------------------------------------------------------

    unit_of_work = UnitOfWork(AsyncSessionLocal)

    payment_service = PaymentService(
        unit_of_work
    )

    # ---------------------------------------------------------
    # 5. Создаём платёж
    # ---------------------------------------------------------

    payment = await payment_service.create_payment(
        user_id=user.id,
        amount=Decimal("1990.00"),
        currency="RUB",
        subscription_id=subscription.id,
        payment_provider="test_uow",
    )

    print(
        f"Создан платёж: {payment.id}"
    )

    print(
        f"Статус платежа: {payment.status}"
    )

    # ---------------------------------------------------------
    # 6. Подтверждаем платёж
    # ---------------------------------------------------------

    succeeded_payment = (
        await payment_service.mark_as_succeeded(
            payment.id
        )
    )

    if succeeded_payment is None:
        print("Платёж не найден после подтверждения.")
        return

    print(
        f"Платёж после подтверждения: "
        f"{succeeded_payment.status}"
    )

    # ---------------------------------------------------------
    # 7. Проверяем подписку отдельной сессией
    # ---------------------------------------------------------

    async with AsyncSessionLocal() as session:
        subscription_repository = SubscriptionRepository(
            session
        )

        saved_subscription = (
            await subscription_repository.get_by_id(
                subscription.id
            )
        )

        if saved_subscription is None:
            print("Подписка не найдена в БД.")
            return

        print(
            f"Подписка в БД после оплаты: "
            f"{saved_subscription.status}"
        )

    # ---------------------------------------------------------
    # 8. Проверяем результат
    # ---------------------------------------------------------

    if succeeded_payment.status.value != "succeeded":
        raise AssertionError(
            "Платёж не перешёл в SUCCEEDED."
        )

    if saved_subscription.status.value != "active":
        raise AssertionError(
            "Подписка не перешла в ACTIVE."
        )

    print()
    print("================================")
    print("TEST PASSED")
    print("Payment: PENDING -> SUCCEEDED")
    print("Subscription: PENDING -> ACTIVE")
    print("Транзакция UnitOfWork работает.")
    print("================================")


if __name__ == "__main__":
    asyncio.run(main())