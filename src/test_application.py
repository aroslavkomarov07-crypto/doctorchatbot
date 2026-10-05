import asyncio
from decimal import Decimal

from application.experts.expert_service import ExpertService
from application.messages.message_service import MessageService
from application.payments.payment_service import PaymentService
from application.subscriptions.subscription_service import (
    SubscriptionService,
)
from application.users.user_service import UserService

from core.enums.message_sender_type import MessageSenderType
from core.enums.payment_status import PaymentStatus
from core.enums.subscription_status import SubscriptionStatus

from infrastructure.database.database import AsyncSessionLocal
from infrastructure.database.unit_of_work import UnitOfWork


async def main() -> None:
    print("========================================")
    print("INTEGRATION TEST")
    print("========================================")

    # ========================================================
    # 1. Создаём UnitOfWork
    # ========================================================

    user_service = UserService(
        UnitOfWork(AsyncSessionLocal)
    )

    expert_service = ExpertService(
        UnitOfWork(AsyncSessionLocal)
    )

    subscription_service = SubscriptionService(
        UnitOfWork(AsyncSessionLocal)
    )

    payment_service = PaymentService(
        UnitOfWork(AsyncSessionLocal)
    )

    message_service = MessageService(
        UnitOfWork(AsyncSessionLocal)
    )

    # ========================================================
    # 2. Регистрируем пользователя
    # ========================================================

    user = await user_service.register_user(
        telegram_id=111222333,
        full_name="Иван Иванов",
        phone_number="+79990001122",
    )

    print()
    print("[1] Пользователь")
    print(f"    ID: {user.id}")
    print(f"    Telegram ID: {user.telegram_id}")
    print(f"    Имя: {user.full_name}")

    # ========================================================
    # 3. Регистрируем эксперта
    # ========================================================

    expert = await expert_service.register_expert(
        telegram_id=444555666,
        full_name="Петр Петров",
        description="Тестовый эксперт",
    )

    print()
    print("[2] Эксперт")
    print(f"    ID: {expert.id}")
    print(f"    Telegram ID: {expert.telegram_id}")
    print(f"    Имя: {expert.full_name}")

    # ========================================================
    # 4. Создаём подписку
    # ========================================================

    subscription = (
        await subscription_service.create_subscription(
            user_id=user.id,
            expert_id=expert.id,
            duration_days=30,
        )
    )

    print()
    print("[3] Подписка")
    print(f"    ID: {subscription.id}")
    print(f"    Статус: {subscription.status}")

    if subscription.status != SubscriptionStatus.PENDING:
        raise AssertionError(
            "Новая подписка должна иметь статус PENDING."
        )

    # ========================================================
    # 5. Создаём платёж
    # ========================================================

    payment = await payment_service.create_payment(
        user_id=user.id,
        amount=Decimal("1990.00"),
        currency="RUB",
        subscription_id=subscription.id,
        payment_provider="integration_test",
    )

    print()
    print("[4] Платёж")
    print(f"    ID: {payment.id}")
    print(f"    Сумма: {payment.amount} {payment.currency}")
    print(f"    Статус: {payment.status}")

    if payment.status != PaymentStatus.PENDING:
        raise AssertionError(
            "Новый платёж должен иметь статус PENDING."
        )

    # ========================================================
    # 6. Подтверждаем платёж
    # ========================================================

    succeeded_payment = (
        await payment_service.mark_as_succeeded(
            payment.id
        )
    )

    if succeeded_payment is None:
        raise AssertionError(
            "Платёж не найден после подтверждения."
        )

    print()
    print("[5] Подтверждение платежа")
    print(
        f"    Статус платежа: "
        f"{succeeded_payment.status}"
    )

    if succeeded_payment.status != PaymentStatus.SUCCEEDED:
        raise AssertionError(
            "Платёж должен иметь статус SUCCEEDED."
        )

    # ========================================================
    # 7. Проверяем автоматическую активацию подписки
    # ========================================================

    active_subscription = (
        await subscription_service.get_active_subscription(
            user.id
        )
    )

    print()
    print("[6] Проверка подписки")

    if active_subscription is None:
        raise AssertionError(
            "Активная подписка не найдена."
        )

    print(
        f"    Статус подписки: "
        f"{active_subscription.status}"
    )

    if active_subscription.id != subscription.id:
        raise AssertionError(
            "Активирована не та подписка."
        )

    if active_subscription.status != SubscriptionStatus.ACTIVE:
        raise AssertionError(
            "Подписка должна иметь статус ACTIVE."
        )

    # ========================================================
    # 8. Отправляем сообщение пользователя
    # ========================================================

    user_message = await message_service.send_message(
        user_id=user.id,
        expert_id=expert.id,
        text="Здравствуйте! Это тестовый вопрос.",
        sender_type=MessageSenderType.USER,
    )

    print()
    print("[7] Сообщение пользователя")
    print(f"    ID: {user_message.id}")
    print(f"    Текст: {user_message.text}")
    print(
        f"    Отправитель: "
        f"{user_message.sender_type}"
    )

    # ========================================================
    # 9. Отправляем ответ эксперта
    # ========================================================

    expert_message = await message_service.send_message(
        user_id=user.id,
        expert_id=expert.id,
        text="Здравствуйте! Это тестовый ответ эксперта.",
        sender_type=MessageSenderType.EXPERT,
    )

    print()
    print("[8] Ответ эксперта")
    print(f"    ID: {expert_message.id}")
    print(f"    Текст: {expert_message.text}")
    print(
        f"    Отправитель: "
        f"{expert_message.sender_type}"
    )

    # ========================================================
    # 10. Получаем историю переписки
    # ========================================================

    conversation = (
        await message_service.get_conversation(
            user_id=user.id,
            expert_id=expert.id,
        )
    )

    print()
    print("[9] История переписки")
    print(
        f"    Количество сообщений: "
        f"{len(conversation)}"
    )

    if len(conversation) < 2:
        raise AssertionError(
            "В истории должно быть минимум 2 сообщения."
        )

    for message in conversation:
        print(
            f"    [{message.sender_type}] "
            f"{message.text}"
        )

    # ========================================================
    # 11. Проверяем повторную регистрацию пользователя
    # ========================================================

    same_user = await user_service.register_user(
        telegram_id=111222333,
        full_name="Другое Имя",
        phone_number="+70000000000",
    )

    print()
    print("[10] Повторная регистрация пользователя")
    print(f"    Старый ID: {user.id}")
    print(f"    Полученный ID: {same_user.id}")

    if same_user.id != user.id:
        raise AssertionError(
            "Повторная регистрация создала нового пользователя."
        )

    # ========================================================
    # 12. Проверяем повторную регистрацию эксперта
    # ========================================================

    same_expert = await expert_service.register_expert(
        telegram_id=444555666,
        full_name="Другое Имя",
    )

    print()
    print("[11] Повторная регистрация эксперта")
    print(f"    Старый ID: {expert.id}")
    print(f"    Полученный ID: {same_expert.id}")

    if same_expert.id != expert.id:
        raise AssertionError(
            "Повторная регистрация создала нового эксперта."
        )

    # ========================================================
    # 13. Проверяем платежи пользователя
    # ========================================================

    user_payments = (
        await payment_service.get_user_payments(
            user.id
        )
    )

    print()
    print("[12] Платежи пользователя")
    print(
        f"    Количество платежей: "
        f"{len(user_payments)}"
    )

    if not user_payments:
        raise AssertionError(
            "У пользователя должен быть хотя бы один платёж."
        )

    # ========================================================
    # 14. Проверяем подписки пользователя
    # ========================================================

    user_subscriptions = (
        await subscription_service.get_user_subscriptions(
            user.id
        )
    )

    print()
    print("[13] Подписки пользователя")
    print(
        f"    Количество подписок: "
        f"{len(user_subscriptions)}"
    )

    if not user_subscriptions:
        raise AssertionError(
            "У пользователя должна быть хотя бы одна подписка."
        )

    # ========================================================
    # 15. Финальный результат
    # ========================================================

    print()
    print("========================================")
    print("INTEGRATION TEST PASSED")
    print("========================================")
    print("Пользователь             OK")
    print("Эксперт                  OK")
    print("Подписка                 OK")
    print("Платёж                   OK")
    print("Активация подписки       OK")
    print("Сообщение пользователя   OK")
    print("Ответ эксперта           OK")
    print("История переписки        OK")
    print("Повторная регистрация    OK")
    print("========================================")


if __name__ == "__main__":
    asyncio.run(main())