from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from src.application.validation import validate_text
from src.core.enums import BroadcastAudience, BroadcastStatus, PaymentProvider
from src.core.exceptions import ValidationError
from src.infrastructure.database.models import Broadcast
from src.infrastructure.database.models import PaymentGateway as PaymentGatewayModel
from src.infrastructure.database.models import Settings as DatabaseSettings
from src.telegram.states import OwnerBroadcastForm

from .context import OwnerRouterContext


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows
        ]
    )


def admin_back() -> InlineKeyboardMarkup:
    return keyboard([[("⬅️ В панель управления", "owner:panel")]])


def build_owner_settings_router(context: OwnerRouterContext) -> Router:
    router = Router(name="owner-settings")

    @router.callback_query(F.data == "owner:broadcasts")
    async def owner_broadcasts(callback: CallbackQuery, state: FSMContext) -> None:
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            broadcasts = list(
                await session.scalars(
                    select(Broadcast).order_by(Broadcast.created_at.desc()).limit(10)
                )
            )
            lines = ["<b>📣 Рассылки</b>"]
            for item in broadcasts:
                lines.append(
                    f"\n• #{item.id} {item.audience.value}: {item.status.value} — "
                    f"{item.success_count}/{item.total_count}"
                )
            await context.show(
                callback,
                "".join(lines),
                keyboard(
                    [
                        [("➕ Новая рассылка", "owner:broadcast_add")],
                        [("⬅️ В панель управления", "owner:panel")],
                    ]
                ),
            )
        await state.clear()
        await callback.answer()

    @router.callback_query(F.data == "owner:broadcast_add")
    async def owner_broadcast_add(callback: CallbackQuery, state: FSMContext) -> None:
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
        await state.set_state(OwnerBroadcastForm.text)
        await context.show(
            callback,
            "Введите текст рассылки с поддержкой Telegram HTML:",
            keyboard([[("⬅️ Назад", "owner:broadcasts")]]),
        )
        await callback.answer()

    @router.message(OwnerBroadcastForm.text, F.text)
    async def owner_broadcast_text(message: Message, state: FSMContext) -> None:
        text = validate_text(message.text or "", maximum=4000)
        await state.update_data(text=text)
        await state.set_state(OwnerBroadcastForm.audience)
        await context.show(
            message,
            "Выберите аудиторию:",
            keyboard(
                [
                    [("👥 Все", "owner:broadcast_aud:ALL")],
                    [("👤 Клиенты", "owner:broadcast_aud:CLIENTS")],
                    [("👨‍⚕️ Эксперты", "owner:broadcast_aud:EXPERTS")],
                    [("✅ Активные подписчики", "owner:broadcast_aud:ACTIVE_SUBSCRIBERS")],
                    [("❌ Без активной подписки", "owner:broadcast_aud:INACTIVE_CLIENTS")],
                    [("⬅️ Назад", "owner:broadcast_add")],
                ]
            ),
        )

    @router.callback_query(OwnerBroadcastForm.audience, F.data.startswith("owner:broadcast_aud:"))
    async def owner_broadcast_audience(callback: CallbackQuery, state: FSMContext) -> None:
        audience = BroadcastAudience((callback.data or "").rsplit(":", 1)[1])
        await state.update_data(audience=audience.value)
        data = await state.get_data()
        await state.set_state(OwnerBroadcastForm.confirm)
        await context.show(
            callback,
            f"<b>Предпросмотр рассылки</b>\n\nАудитория: <b>{audience.value}</b>\n\n{data['text']}",
            keyboard(
                [
                    [("✅ Запустить", "owner:broadcast_confirm")],
                    [("⬅️ Назад", "owner:broadcast_back_audience")],
                    [("❌ Отменить", "owner:broadcasts")],
                ]
            ),
        )
        await callback.answer()

    @router.callback_query(
        OwnerBroadcastForm.confirm,
        F.data == "owner:broadcast_back_audience",
    )
    async def owner_broadcast_back_audience(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(OwnerBroadcastForm.audience)
        await context.show(
            callback,
            "Выберите аудиторию:",
            keyboard(
                [
                    [("👥 Все", "owner:broadcast_aud:ALL")],
                    [("👤 Клиенты", "owner:broadcast_aud:CLIENTS")],
                    [("👨‍⚕️ Эксперты", "owner:broadcast_aud:EXPERTS")],
                    [("✅ Активные подписчики", "owner:broadcast_aud:ACTIVE_SUBSCRIBERS")],
                    [("❌ Без активной подписки", "owner:broadcast_aud:INACTIVE_CLIENTS")],
                    [("⬅️ Назад", "owner:broadcast_add")],
                ]
            ),
        )
        await callback.answer()

    @router.callback_query(OwnerBroadcastForm.confirm, F.data == "owner:broadcast_confirm")
    async def owner_broadcast_confirm(callback: CallbackQuery, state: FSMContext) -> None:
        from src.infrastructure.taskiq.tasks import send_broadcast_task  # noqa: PLC0415

        data = await state.get_data()
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            broadcast = Broadcast(
                created_by_id=owner.id,
                audience=BroadcastAudience(str(data["audience"])),
                text=str(data["text"]),
                status=BroadcastStatus.DRAFT,
            )
            session.add(broadcast)
            await session.commit()
            await session.refresh(broadcast)
            await send_broadcast_task.kiq(broadcast.id)
        await state.clear()
        await context.show(
            callback,
            f"Рассылка #{broadcast.id} поставлена в очередь.",
            admin_back(),
        )
        await callback.answer()

    @router.callback_query(
        F.data.in_(
            {
                "owner:gateways",
                "owner:notifications",
                "owner:settings",
            }
        )
    )
    async def owner_settings_pages(callback: CallbackQuery) -> None:
        page = (callback.data or "").split(":", 1)[1]
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            settings = await session.get(DatabaseSettings, 1)
            platega_status = (
                "настроена" if context.config.platega_merchant_id else "нужны реквизиты в .env"
            )
            registration_status = (
                "включена" if settings and settings.registration_enabled else "выключена"
            )
            payments_status = "включены" if settings and settings.payments_enabled else "выключены"
            texts = {
                "gateways": (
                    "<b>🌐 Платежные системы</b>\n\n"
                    f"Platega/СБП: {platega_status}\n"
                    "Webhook: <code>/api/v1/payments/platega</code>"
                ),
                "notifications": (
                    "<b>🔔 Уведомления</b>\n\n"
                    "Активны события регистрации, заявок экспертов, сообщений, ответов, "
                    "платежей и выводов. Настройки хранятся централизованно "
                    "в Settings.notifications."
                ),
                "settings": (
                    "<b>⚙️ Настройки</b>\n\n"
                    f"Регистрация: {registration_status}\n"
                    f"Платежи: {payments_status}"
                ),
            }
            markup = admin_back()
            if page == "notifications":
                labels = {
                    "user_registered": "Новый пользователь",
                    "expert_application": "Заявка эксперта",
                    "new_message": "Новое сообщение",
                    "answer": "Ответ эксперта",
                    "payment": "Платеж",
                    "withdrawal": "Вывод средств",
                }
                rows = []
                for key, label in labels.items():
                    enabled = True if settings is None else settings.notifications.get(key, True)
                    rows.append([(f"{'🔘' if enabled else '⚪'} {label}", f"owner:ntf:{key}")])
                rows.append([("⬅️ В панель управления", "owner:panel")])
                markup = keyboard(rows)
            elif page == "settings":
                markup = keyboard(
                    [
                        [("🔁 Регистрация", "owner:setting:registration")],
                        [("🔁 Платежи", "owner:setting:payments")],
                        [("⬅️ В панель управления", "owner:panel")],
                    ]
                )
            elif page == "gateways":
                gateway = await session.scalar(
                    select(PaymentGatewayModel).where(
                        PaymentGatewayModel.provider == PaymentProvider.PLATEGA
                    )
                )
                active = bool(gateway and gateway.is_active)
                markup = keyboard(
                    [
                        [
                            (
                                f"{'🟢' if active else '🔴'} Platega/СБП",
                                "owner:gateway_toggle:PLATEGA",
                            )
                        ],
                        [("⬅️ В панель управления", "owner:panel")],
                    ]
                )
            await context.show(callback, texts[page], markup)
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:ntf:"))
    async def owner_notification_toggle(callback: CallbackQuery) -> None:
        event_key = (callback.data or "").rsplit(":", 1)[1]
        allowed = {
            "user_registered",
            "expert_application",
            "new_message",
            "answer",
            "payment",
            "withdrawal",
        }
        if event_key not in allowed:
            raise ValidationError("Неизвестное уведомление")
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            settings = await session.get(DatabaseSettings, 1, with_for_update=True)
            if settings is None:
                raise ValidationError("Настройки не найдены")
            notifications = dict(settings.notifications)
            notifications[event_key] = not notifications.get(event_key, True)
            settings.notifications = notifications
            await session.commit()
        await context.show(
            callback,
            "Настройка уведомления изменена.",
            keyboard([[("⬅️ Назад", "owner:notifications")]]),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:setting:"))
    async def owner_setting_toggle(callback: CallbackQuery) -> None:
        key = (callback.data or "").rsplit(":", 1)[1]
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            settings = await session.get(DatabaseSettings, 1, with_for_update=True)
            if settings is None:
                raise ValidationError("Настройки не найдены")
            if key == "registration":
                settings.registration_enabled = not settings.registration_enabled
                return_page = "owner:settings"
            elif key == "payments":
                settings.payments_enabled = not settings.payments_enabled
                return_page = "owner:settings"
            elif key == "referral":
                settings.referral_enabled = not settings.referral_enabled
                return_page = "owner:referrals"
            else:
                raise ValidationError("Неизвестная настройка")
            await session.commit()
        await context.show(
            callback,
            "Настройка изменена.",
            keyboard([[("⬅️ Назад", return_page)]]),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:gateway_toggle:"))
    async def owner_gateway_toggle(callback: CallbackQuery) -> None:
        provider = PaymentProvider((callback.data or "").rsplit(":", 1)[1])
        if provider is PaymentProvider.PLATEGA and not (
            context.config.platega_merchant_id
            and context.config.platega_api_key
            and context.config.platega_sbp_payment_method is not None
        ):
            raise ValidationError("Сначала заполните реквизиты Platega в .env")
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            gateway = await session.scalar(
                select(PaymentGatewayModel)
                .where(PaymentGatewayModel.provider == provider)
                .with_for_update()
            )
            if gateway is None:
                raise ValidationError("Платежная система не найдена")
            gateway.is_active = not gateway.is_active
            await session.commit()
        await context.show(
            callback,
            "Статус платежной системы изменен.",
            keyboard([[("⬅️ Назад", "owner:gateways")]]),
        )
        await callback.answer()

    return router
