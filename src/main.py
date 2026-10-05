import asyncio
from decimal import Decimal

from application.payments.payment_service import PaymentService
from application.subscriptions.subscription_service import (
    SubscriptionService,
)
from infrastructure.database.database import (
    AsyncSessionLocal,
    create_tables,
)
from infrastructure.repositories.expert_repository import ExpertRepository
from infrastructure.repositories.payment_repository import PaymentRepository
from infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from infrastructure.repositories.user_repository import UserRepository


async def main():
    await create_tables()

    async with AsyncSessionLocal() as session:
        user_repository = UserRepository(session)
        expert_repository = ExpertRepository(session)
        subscription_repository = SubscriptionRepository(session)
        payment_repository = PaymentRepository(session)

        subscription_service = SubscriptionService(
            subscription_repository
        )

        payment_service = PaymentService(
            payment_repository=payment_repository,
            subscription_repository=subscription_repository,
        )

        print("\n========== 1. ПОИСК ПОЛЬЗОВАТЕЛЯ ==========")

        user = await user_repository.get_by_telegram_id(
            111222333
        )

        if user is None:
            print("Пользователь не найден!")
            return

        print(f"Пользователь: {user.full_name}")
        print(f"UUID: {user.id}")

        print("\n========== 2. ПОИСК ЭКСПЕРТА ==========")

        expert = await expert_repository.get_by_telegram_id(
            444555666
        )

        if expert is None:
            print("Эксперт не найден!")
            return

        print(f"Эксперт: {expert.full_name}")
        print(f"UUID: {expert.id}")

        print("\n========== 3. СОЗДАНИЕ PENDING ПОДПИСКИ ==========")

        subscription = await subscription_service.create_subscription(
            user_id=user.id,
            expert_id=expert.id,
            duration_days=30,
        )

        print(f"Подписка: {subscription.id}")
        print(f"Статус: {subscription.status}")
        print(f"Начало: {subscription.start_date}")
        print(f"Окончание: {subscription.end_date}")

        if subscription.status.value != "pending":
            print(
                "ОШИБКА: новая подписка должна иметь статус PENDING!"
            )
            return

        print("✓ Новая подписка действительно PENDING")

        print("\n========== 4. ПРОВЕРКА ACTIVE ПОДПИСКИ ==========")

        saved_subscription = (
            await subscription_repository.get_by_id(
                subscription.id
            )
        )

        if saved_subscription is None:
            print("ОШИБКА: подписка не найдена!")
            return

        print(
            f"Статус созданной подписки в БД: "
            f"{saved_subscription.status}"
        )

        if saved_subscription.status.value != "pending":
            print(
                "ОШИБКА: созданная подписка должна "
                "иметь статус PENDING!"
            )
            return

        print(
            "✓ Конкретно созданная подписка "
            "не является активной"
        )

        print("\n========== 5. СОЗДАНИЕ PENDING ПЛАТЕЖА ==========")

        payment = await payment_service.create_payment(
            user_id=user.id,
            amount=Decimal("1990.00"),
            currency="RUB",
            subscription_id=subscription.id,
            payment_provider="test",
        )

        print(f"Платёж: {payment.id}")
        print(f"Сумма: {payment.amount} {payment.currency}")
        print(f"Статус: {payment.status}")

        if payment.status.value != "pending":
            print(
                "ОШИБКА: новый платёж должен иметь статус PENDING!"
            )
            return

        print("✓ Платёж создан со статусом PENDING")

        print("\n========== 6. УСПЕШНАЯ ОПЛАТА ==========")

        payment = await payment_service.mark_as_succeeded(
            payment.id
        )

        if payment is None:
            print("ОШИБКА: платёж не найден!")
            return

        print(f"Платёж после оплаты: {payment.status}")

        if payment.status.value != "succeeded":
            print(
                "ОШИБКА: платёж должен иметь статус SUCCEEDED!"
            )
            return

        print("✓ Платёж успешно оплачен")

        print("\n========== 7. ПРОВЕРКА АВТОМАТИЧЕСКОЙ АКТИВАЦИИ ==========")

        subscription = await subscription_service.get_active_subscription(
            user.id
        )

        if subscription is None:
            print(
                "ОШИБКА: после успешной оплаты "
                "подписка не активировалась!"
            )
            return

        print(f"Подписка: {subscription.id}")
        print(f"Статус: {subscription.status}")

        if subscription.status.value != "active":
            print(
                "ОШИБКА: подписка должна быть ACTIVE!"
            )
            return

        print("✓ Подписка автоматически активирована")

        print("\n========== 8. ПОВТОРНОЕ ПОДТВЕРЖДЕНИЕ ПЛАТЕЖА ==========")

        payment_again = await payment_service.mark_as_succeeded(
            payment.id
        )

        if payment_again is None:
            print("ОШИБКА: платёж пропал!")
            return

        print(
            f"Статус после повторного подтверждения: "
            f"{payment_again.status}"
        )

        if payment_again.status.value != "succeeded":
            print(
                "ОШИБКА: повторное подтверждение "
                "изменило статус!"
            )
            return

        print(
            "✓ Повторное подтверждение обработано безопасно"
        )

        print("\n========== 9. ПРОВЕРКА ПЛАТЕЖЕЙ ==========")

        payments = await payment_service.get_subscription_payments(
            subscription.id
        )

        print(
            f"Платежей у этой подписки: {len(payments)}"
        )

        for item in payments:
            print(
                f"- {item.id} | "
                f"{item.amount} {item.currency} | "
                f"{item.status}"
            )

        print("\n==============================================")
        print("ВСЕ ТЕСТЫ БИЗНЕС-ЛОГИКИ ПРОЙДЕНЫ!")
        print("==============================================")


if __name__ == "__main__":
    asyncio.run(main())