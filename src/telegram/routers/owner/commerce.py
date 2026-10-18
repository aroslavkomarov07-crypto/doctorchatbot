from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from src.application.money import money
from src.application.services import FinanceService
from src.application.validation import safe_html, validate_text
from src.core.enums import WithdrawalStatus
from src.core.exceptions import ValidationError
from src.infrastructure.database.models import (
    AuditLog,
    ClientExpertSubscription,
    CommissionSettings,
    ExpertProfile,
    ExpertTariff,
    PaymentTransaction,
    Tariff,
    User,
    WithdrawalRequest,
)
from src.infrastructure.telegram_notifications import send_notification
from src.telegram import events
from src.telegram.states import OwnerCommissionForm, OwnerTariffEdit, OwnerTariffForm

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


def build_owner_commerce_router(context: OwnerRouterContext) -> Router:
    router = Router(name="owner-commerce")

    @router.callback_query(F.data == "owner:tariffs")
    async def owner_tariffs(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            tariffs = list(await session.scalars(select(Tariff).order_by(Tariff.sort_order)))
            rows = [
                [
                    (
                        f"{'🟢' if tariff.is_active else '🔴'} {tariff.name} — "
                        f"{tariff.default_price:.0f} ₽",
                        f"owner:tariff:{tariff.id}",
                    )
                ]
                for tariff in tariffs
            ]
            rows.extend(
                [
                    [("➕ Добавить тариф", "owner:tariff_add")],
                    [("⬅️ В панель управления", "owner:panel")],
                ]
            )
            await context.show(
                callback,
                "<b>💎 Тарифы</b>\n\nВыберите тариф для редактирования.",
                keyboard(rows),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:tariff:"))
    async def owner_tariff_view(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        tariff_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            tariff = await session.get(Tariff, tariff_id)
            if tariff is None:
                raise ValidationError("Тариф не найден")
            await context.show(
                callback,
                f"<b>{safe_html(tariff.name)}</b>\n\n<blockquote>"
                f"• Длительность: {tariff.duration_days} дней\n"
                f"• Сообщений: {tariff.message_limit or '∞'}\n"
                f"• Цена: {tariff.default_price:.2f} ₽\n"
                f"• Статус: {'активен' if tariff.is_active else 'выключен'}"
                "</blockquote>",
                keyboard(
                    [
                        [("🏷 Изменить название", f"owner:tariff_name:{tariff.id}")],
                        [("💰 Изменить цену", f"owner:tariff_price:{tariff.id}")],
                        [("⏳ Изменить длительность", f"owner:tariff_duration:{tariff.id}")],
                        [("🔁 Включить/выключить", f"owner:tariff_toggle:{tariff.id}")],
                        [("⬅️ Назад", "owner:tariffs")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:tariff_toggle:"))
    async def owner_tariff_toggle(callback: CallbackQuery) -> None:
        tariff_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            tariff = await session.get(Tariff, tariff_id)
            if tariff is None:
                raise ValidationError("Тариф не найден")
            action = "выключить" if tariff.is_active else "включить"
            await context.show(
                callback,
                f"Подтвердите: <b>{action}</b> тариф {safe_html(tariff.name)}?",
                keyboard(
                    [
                        [("✅ Подтвердить", f"owner:tariff_toggle_apply:{tariff_id}")],
                        [("⬅️ Назад", f"owner:tariff:{tariff_id}")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:tariff_toggle_apply:"))
    async def owner_tariff_toggle_apply(callback: CallbackQuery) -> None:
        tariff_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            tariff = await session.get(Tariff, tariff_id, with_for_update=True)
            if tariff is None:
                raise ValidationError("Тариф не найден")
            tariff.is_active = not tariff.is_active
            session.add(
                AuditLog(
                    actor_id=owner.id,
                    action="tariff.enable" if tariff.is_active else "tariff.disable",
                    entity_type="tariff",
                    entity_id=str(tariff.id),
                    data={},
                )
            )
            await session.commit()
        await context.show(
            callback,
            "Статус тарифа изменен.",
            keyboard([[("⬅️ К тарифу", f"owner:tariff:{tariff_id}")]]),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:tariff_name:"))
    async def owner_tariff_name_start(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_data({"tariff_id": int((callback.data or "").rsplit(":", 1)[1])})
        await state.set_state(OwnerTariffEdit.name)
        tariff_id = int((callback.data or "").rsplit(":", 1)[1])
        await context.show(
            callback,
            "Введите новое название тарифа:",
            keyboard([[("⬅️ Назад", f"owner:tariff:{tariff_id}")]]),
        )
        await callback.answer()

    @router.message(OwnerTariffEdit.name, F.text)
    async def owner_tariff_name_preview(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        data = await state.get_data()
        value = validate_text(message.text or "", maximum=80)
        await state.update_data(edit_field="name", edit_value=value)
        await state.set_state(OwnerTariffEdit.confirm)
        await context.show(
            message,
            f"Сохранить новое название <b>{safe_html(value)}</b>?",
            keyboard(
                [
                    [("✅ Сохранить", "owner:tariff_edit_confirm")],
                    [("⬅️ Назад", f"owner:tariff_name:{data['tariff_id']}")],
                    [("❌ Отменить", f"owner:tariff:{data['tariff_id']}")],
                ]
            ),
        )

    @router.callback_query(F.data.startswith("owner:tariff_price:"))
    async def owner_tariff_price_start(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_data({"tariff_id": int((callback.data or "").rsplit(":", 1)[1])})
        await state.set_state(OwnerTariffEdit.price)
        tariff_id = int((callback.data or "").rsplit(":", 1)[1])
        await context.show(
            callback,
            "Введите новую цену в рублях:",
            keyboard([[("⬅️ Назад", f"owner:tariff:{tariff_id}")]]),
        )
        await callback.answer()

    @router.message(OwnerTariffEdit.price, F.text)
    async def owner_tariff_price_preview(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        try:
            price = money(Decimal((message.text or "").replace(",", ".")))
        except InvalidOperation as error:
            raise ValidationError("Введите корректную цену") from error
        if price < 0:
            raise ValidationError("Цена не может быть отрицательной")
        data = await state.get_data()
        await state.update_data(edit_field="price", edit_value=str(price))
        await state.set_state(OwnerTariffEdit.confirm)
        await context.show(
            message,
            f"Сохранить новую цену <b>{price:.2f} ₽</b>?",
            keyboard(
                [
                    [("✅ Сохранить", "owner:tariff_edit_confirm")],
                    [("⬅️ Назад", f"owner:tariff_price:{data['tariff_id']}")],
                    [("❌ Отменить", f"owner:tariff:{data['tariff_id']}")],
                ]
            ),
        )

    @router.callback_query(F.data.startswith("owner:tariff_duration:"))
    async def owner_tariff_duration_start(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_data({"tariff_id": int((callback.data or "").rsplit(":", 1)[1])})
        await state.set_state(OwnerTariffEdit.duration)
        tariff_id = int((callback.data or "").rsplit(":", 1)[1])
        await context.show(
            callback,
            "Введите новую длительность в днях:",
            keyboard([[("⬅️ Назад", f"owner:tariff:{tariff_id}")]]),
        )
        await callback.answer()

    @router.message(OwnerTariffEdit.duration, F.text)
    async def owner_tariff_duration_preview(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        try:
            duration = int(message.text or "")
        except ValueError as error:
            raise ValidationError("Введите целое число") from error
        if not 1 <= duration <= 3650:
            raise ValidationError("Длительность должна быть от 1 до 3650 дней")
        data = await state.get_data()
        await state.update_data(edit_field="duration", edit_value=str(duration))
        await state.set_state(OwnerTariffEdit.confirm)
        await context.show(
            message,
            f"Сохранить новую длительность <b>{duration} дней</b>?",
            keyboard(
                [
                    [("✅ Сохранить", "owner:tariff_edit_confirm")],
                    [("⬅️ Назад", f"owner:tariff_duration:{data['tariff_id']}")],
                    [("❌ Отменить", f"owner:tariff:{data['tariff_id']}")],
                ]
            ),
        )

    @router.callback_query(OwnerTariffEdit.confirm, F.data == "owner:tariff_edit_confirm")
    async def owner_tariff_edit_confirm(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        tariff_id = int(data["tariff_id"])
        field = str(data["edit_field"])
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            tariff = await session.get(Tariff, tariff_id, with_for_update=True)
            if tariff is None:
                raise ValidationError("Тариф не найден")
            before: str
            if field == "name":
                before = tariff.name
                tariff.name = str(data["edit_value"])
            elif field == "price":
                before = str(tariff.default_price)
                tariff.default_price = money(Decimal(str(data["edit_value"])))
            elif field == "duration":
                before = str(tariff.duration_days)
                tariff.duration_days = int(data["edit_value"])
            else:
                raise ValidationError("Неизвестное поле тарифа")
            session.add(
                AuditLog(
                    actor_id=owner.id,
                    action="tariff.edit",
                    entity_type="tariff",
                    entity_id=str(tariff.id),
                    data={"field": field, "before": before, "after": str(data["edit_value"])},
                )
            )
            await session.commit()
            await state.clear()
            await context.show(
                callback,
                "Тариф изменён.",
                keyboard([[("⬅️ К тарифу", f"owner:tariff:{tariff.id}")]]),
            )
        await callback.answer("Готово")

    @router.callback_query(F.data == "owner:tariff_add")
    async def owner_tariff_add(callback: CallbackQuery, state: FSMContext) -> None:
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
        await state.clear()
        await state.set_state(OwnerTariffForm.name)
        await context.show(
            callback,
            "Введите название тарифа:",
            keyboard([[("⬅️ Назад", "owner:tariffs")]]),
        )
        await callback.answer()

    @router.message(OwnerTariffForm.name, F.text)
    async def owner_tariff_name(message: Message, state: FSMContext) -> None:
        await state.update_data(name=validate_text(message.text or "", maximum=80))
        await state.set_state(OwnerTariffForm.duration)
        await context.show(
            message,
            "Введите длительность в днях:",
            keyboard([[("⬅️ Назад", "owner:tariff_back_name")]]),
        )

    @router.message(OwnerTariffForm.duration, F.text)
    async def owner_tariff_duration(message: Message, state: FSMContext) -> None:
        try:
            duration = int(message.text or "")
        except ValueError as error:
            raise ValidationError("Введите целое число дней") from error
        if not 1 <= duration <= 3650:
            raise ValidationError("Длительность должна быть от 1 до 3650 дней")
        await state.update_data(duration=duration)
        await state.set_state(OwnerTariffForm.limit)
        await context.show(
            message,
            "Введите лимит сообщений или `0` для безлимита:",
            keyboard([[("⬅️ Назад", "owner:tariff_back_duration")]]),
        )

    @router.message(OwnerTariffForm.limit, F.text)
    async def owner_tariff_limit(message: Message, state: FSMContext) -> None:
        try:
            limit = int(message.text or "")
        except ValueError as error:
            raise ValidationError("Введите целое число") from error
        if limit < 0:
            raise ValidationError("Лимит не может быть отрицательным")
        await state.update_data(limit=limit or None)
        await state.set_state(OwnerTariffForm.price)
        await context.show(
            message,
            "Введите цену в рублях:",
            keyboard([[("⬅️ Назад", "owner:tariff_back_limit")]]),
        )

    @router.message(OwnerTariffForm.price, F.text)
    async def owner_tariff_price(message: Message, state: FSMContext) -> None:
        try:
            price = money(Decimal((message.text or "").replace(",", ".")))
        except InvalidOperation as error:
            raise ValidationError("Введите корректную цену") from error
        if price < 0:
            raise ValidationError("Цена не может быть отрицательной")
        await state.update_data(price=str(price))
        data = await state.get_data()
        await state.set_state(OwnerTariffForm.confirm)
        await context.show(
            message,
            "<b>Создать тариф?</b>\n\n"
            f"• {safe_html(data['name'])}\n"
            f"• {data['duration']} дней\n"
            f"• сообщений: {data['limit'] or '∞'}\n"
            f"• {price:.2f} ₽",
            keyboard(
                [
                    [("✅ Создать", "owner:tariff_confirm")],
                    [("⬅️ Назад", "owner:tariff_back_price")],
                    [("❌ Отменить", "owner:tariffs")],
                ]
            ),
        )

    @router.callback_query(F.data == "owner:tariff_back_name")
    async def owner_tariff_back_name(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(OwnerTariffForm.name)
        await context.show(
            callback,
            "Введите название тарифа:",
            keyboard([[("⬅️ Назад", "owner:tariffs")]]),
        )
        await callback.answer()

    @router.callback_query(F.data == "owner:tariff_back_duration")
    async def owner_tariff_back_duration(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(OwnerTariffForm.duration)
        await context.show(
            callback,
            "Введите длительность в днях:",
            keyboard([[("⬅️ Назад", "owner:tariff_back_name")]]),
        )
        await callback.answer()

    @router.callback_query(F.data == "owner:tariff_back_limit")
    async def owner_tariff_back_limit(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(OwnerTariffForm.limit)
        await context.show(
            callback,
            "Введите лимит сообщений или `0` для безлимита:",
            keyboard([[("⬅️ Назад", "owner:tariff_back_duration")]]),
        )
        await callback.answer()

    @router.callback_query(F.data == "owner:tariff_back_price")
    async def owner_tariff_back_price(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(OwnerTariffForm.price)
        await context.show(
            callback,
            "Введите цену в рублях:",
            keyboard([[("⬅️ Назад", "owner:tariff_back_limit")]]),
        )
        await callback.answer()

    @router.callback_query(OwnerTariffForm.confirm, F.data == "owner:tariff_confirm")
    async def owner_tariff_confirm(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            tariff = Tariff(
                name=str(data["name"]),
                duration_days=int(data["duration"]),
                message_limit=int(data["limit"]) if data["limit"] else None,
                default_price=Decimal(str(data["price"])),
            )
            session.add(tariff)
            await session.flush()
            experts = list(await session.scalars(select(ExpertProfile.id)))
            session.add_all(
                [ExpertTariff(expert_id=expert_id, tariff_id=tariff.id) for expert_id in experts]
            )
            await session.commit()
        await state.clear()
        await context.show(callback, "Тариф создан и добавлен всем экспертам.", admin_back())
        await callback.answer()

    @router.callback_query(F.data == "owner:commissions")
    async def owner_commissions(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            settings = await session.get(CommissionSettings, 1)
            if settings is None:
                raise ValidationError("Настройки комиссий не найдены")
            await context.show(
                callback,
                "<b>📐 Комиссия сервиса</b>\n\n<blockquote>"
                f"• Комиссия: <b>{settings.service_percent}%</b>"
                "</blockquote>\n\n"
                "Формула: сумма платежа − комиссия сервиса.",
                keyboard(
                    [
                        [("✏️ Изменить", "owner:commissions_edit")],
                        [("⬅️ В панель управления", "owner:panel")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data == "owner:commissions_edit")
    async def owner_commissions_edit(callback: CallbackQuery, state: FSMContext) -> None:
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
        await state.clear()
        await state.set_state(OwnerCommissionForm.service)
        await context.show(
            callback,
            "Введите комиссию сервиса в процентах:",
            keyboard([[("⬅️ Назад", "owner:commissions")]]),
        )
        await callback.answer()

    @router.message(OwnerCommissionForm.service, F.text)
    async def owner_commission_preview(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        try:
            service = Decimal((message.text or "").replace(",", "."))
        except InvalidOperation as error:
            raise ValidationError("Введите число") from error
        if service < 0 or service > 100:
            raise ValidationError("Комиссия должна быть от 0 до 100%")
        await state.update_data(service_percent=str(service))
        await state.set_state(OwnerCommissionForm.confirm)
        await context.show(
            message,
            f"Сохранить комиссию сервиса <b>{service}%</b>?",
            keyboard(
                [
                    [("✅ Сохранить", "owner:commission_confirm")],
                    [("⬅️ Назад", "owner:commissions_edit")],
                    [("❌ Отменить", "owner:commissions")],
                ]
            ),
        )

    @router.callback_query(OwnerCommissionForm.confirm, F.data == "owner:commission_confirm")
    async def owner_commission_service(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        service = Decimal(str(data["service_percent"]))
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            settings = await session.get(CommissionSettings, 1, with_for_update=True)
            if settings is None:
                raise ValidationError("Настройки комиссий не найдены")
            previous = settings.service_percent
            settings.service_percent = service
            session.add(
                AuditLog(
                    actor_id=owner.id,
                    action="commission.change",
                    entity_type="commission_settings",
                    entity_id=str(settings.id),
                    data={"before": str(previous), "after": str(service)},
                )
            )
            await session.commit()
            await state.clear()
            await context.show(
                callback,
                "Комиссия сервиса изменена.",
                keyboard([[("⬅️ К комиссиям", "owner:commissions")]]),
            )
        await callback.answer("Готово")

    @router.callback_query(F.data == "owner:withdrawals")
    async def owner_withdrawals(callback: CallbackQuery) -> None:
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            requests = list(
                await session.scalars(
                    select(WithdrawalRequest)
                    .where(
                        WithdrawalRequest.status.in_(
                            [WithdrawalStatus.PENDING, WithdrawalStatus.APPROVED]
                        )
                    )
                    .order_by(WithdrawalRequest.created_at)
                )
            )
            rows = [
                [(f"#{item.id} — {item.amount:.2f} ₽", f"owner:withdraw_view:{item.id}")]
                for item in requests
            ]
            rows.append([("⬅️ В панель управления", "owner:panel")])
            await context.show(
                callback,
                f"<b>Заявки на вывод</b>\n\nАктивных: {len(requests)}",
                keyboard(rows),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:withdraw_view:"))
    async def owner_withdraw_view(callback: CallbackQuery) -> None:
        request_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            request = await session.get(WithdrawalRequest, request_id)
            if request is None:
                raise ValidationError("Заявка не найдена")
            expert = await session.get(ExpertProfile, request.expert_id)
            user = await session.get(User, expert.user_id if expert else 0)
            if not expert or not user:
                raise ValidationError("Эксперт заявки не найден")
            await context.show(
                callback,
                events.withdrawal_requested(request, expert, user),
                keyboard(
                    [
                        [("✅ Выплачено", f"owner:withdraw_ok:{request.id}")],
                        [("❌ Отклонить", f"owner:withdraw_no:{request.id}")],
                        [("⬅️ Назад", "owner:withdrawals")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:withdraw_ok:"))
    async def owner_withdraw_ok_confirm(callback: CallbackQuery) -> None:
        request_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            if await session.get(WithdrawalRequest, request_id) is None:
                raise ValidationError("Заявка не найдена")
            await context.show(
                callback,
                "Подтвердите, что выплата действительно выполнена.",
                keyboard(
                    [
                        [("✅ Подтвердить", f"owner:withdraw_ok_apply:{request_id}")],
                        [("⬅️ Назад", f"owner:withdraw_view:{request_id}")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:withdraw_ok_apply:"))
    async def owner_withdraw_ok(callback: CallbackQuery, bot: Bot) -> None:
        request_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            request = await FinanceService(session).process_withdrawal(owner, request_id, paid=True)
            expert = await session.get(ExpertProfile, request.expert_id)
            user = await session.get(User, expert.user_id if expert else 0)
            if user:
                await send_notification(
                    bot, user.telegram_id, f"✅ Выплата {request.amount:.2f} ₽ подтверждена."
                )
            await context.show(callback, "Выплата отмечена как выполненная.", admin_back())
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:withdraw_no:"))
    async def owner_withdraw_no_confirm(callback: CallbackQuery) -> None:
        request_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            if await session.get(WithdrawalRequest, request_id) is None:
                raise ValidationError("Заявка не найдена")
            await context.show(
                callback,
                "Отклонить заявку и вернуть средства на баланс эксперта?",
                keyboard(
                    [
                        [("✅ Подтвердить", f"owner:withdraw_no_apply:{request_id}")],
                        [("⬅️ Назад", f"owner:withdraw_view:{request_id}")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:withdraw_no_apply:"))
    async def owner_withdraw_no(callback: CallbackQuery, bot: Bot) -> None:
        request_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            request = await FinanceService(session).process_withdrawal(
                owner, request_id, paid=False
            )
            expert = await session.get(ExpertProfile, request.expert_id)
            user = await session.get(User, expert.user_id if expert else 0)
            if user:
                await send_notification(
                    bot,
                    user.telegram_id,
                    f"❌ Заявка на вывод {request.amount:.2f} ₽ отклонена. Средства возвращены.",
                )
            await context.show(callback, "Заявка отклонена, резерв освобожден.", admin_back())
        await callback.answer()

    @router.callback_query(F.data == "owner:subscriptions")
    async def owner_subscriptions(callback: CallbackQuery) -> None:
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            rows = (
                await session.execute(
                    select(ClientExpertSubscription, User, ExpertProfile)
                    .join(User, User.id == ClientExpertSubscription.client_id)
                    .join(ExpertProfile, ExpertProfile.id == ClientExpertSubscription.expert_id)
                    .order_by(ClientExpertSubscription.created_at.desc())
                    .limit(20)
                )
            ).all()
            text = ["<b>Последние подписки</b>"]
            for subscription, client, expert in rows:
                text.append(
                    f"\n• {safe_html(client.full_name or client.telegram_name)} → "
                    f"{safe_html(expert.display_name)}: {subscription.status.value}"
                )
            await context.show(callback, "".join(text), admin_back())
        await callback.answer()

    @router.callback_query(F.data == "owner:transactions")
    async def owner_transactions(callback: CallbackQuery) -> None:
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            transactions = list(
                await session.scalars(
                    select(PaymentTransaction)
                    .order_by(PaymentTransaction.created_at.desc())
                    .limit(20)
                )
            )
            text = ["<b>Последние транзакции</b>"]
            for item in transactions:
                text.append(
                    f"\n• <code>{str(item.id)[:8]}</code> — {item.amount:.2f} ₽ — "
                    f"{item.status.value}"
                )
            await context.show(callback, "".join(text), admin_back())
        await callback.answer()

    return router
