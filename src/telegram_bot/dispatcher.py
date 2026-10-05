from typing import Any

from aiogram import Dispatcher, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from application.experts.expert_service import ExpertService
from application.subscriptions.subscription_service import SubscriptionService
from application.users.user_service import UserService
from infrastructure.database.unit_of_work import UnitOfWork


def create_dispatcher(session_factory: Any) -> Dispatcher:
    router = Router(name="public")

    def make_uow() -> UnitOfWork:
        return UnitOfWork(session_factory)

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer(
            "Здравствуйте! Я помогу выбрать эксперта и управлять подпиской.\n"
            "Для регистрации отправьте: /register +79990000000\n"
            "Список экспертов: /experts\n"
            "Статус подписки: /status"
        )

    @router.message(Command("register"))
    async def register(message: Message, command: CommandObject) -> None:
        if message.from_user is None:
            return
        phone = (command.args or "").strip()
        if not phone:
            await message.answer("Укажите телефон: /register +79990000000")
            return

        user = await UserService(make_uow()).register_user(
            telegram_id=message.from_user.id,
            full_name=message.from_user.full_name,
            phone_number=phone,
        )
        await message.answer(f"Регистрация завершена, {user.full_name}.")

    @router.message(Command("experts"))
    async def experts(message: Message) -> None:
        available = await ExpertService(make_uow()).get_active_experts()
        if not available:
            await message.answer("Сейчас нет доступных экспертов.")
            return
        lines = ["Доступные эксперты:"]
        lines.extend(
            f"• {expert.full_name} — {expert.description or 'без описания'}"
            for expert in available
        )
        await message.answer("\n".join(lines))

    @router.message(Command("status"))
    async def status(message: Message) -> None:
        if message.from_user is None:
            return
        user = await UserService(make_uow()).get_user_by_telegram_id(
            message.from_user.id
        )
        if user is None:
            await message.answer("Сначала зарегистрируйтесь через /register.")
            return
        subscription = await SubscriptionService(make_uow()).get_active_subscription(
            user.id
        )
        if subscription is None:
            await message.answer("Активной подписки нет.")
            return
        await message.answer(
            "Подписка активна до "
            f"{subscription.end_date.astimezone().strftime('%d.%m.%Y %H:%M')}."
        )

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher

