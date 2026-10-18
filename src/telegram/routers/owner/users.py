from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageOriginUser,
)
from sqlalchemy import func, or_, select

from src.application.money import money
from src.application.validation import safe_html, validate_text
from src.core.enums import (
    Role,
    TransactionStatus,
)
from src.core.exceptions import ValidationError
from src.infrastructure.database.models import (
    AuditLog,
    ClientExpertSubscription,
    Conversation,
    ConversationTopic,
    ExpertProfile,
    ExpertTariff,
    PaymentTransaction,
    Tariff,
    User,
)
from src.infrastructure.telegram_notifications import send_notification
from src.telegram.states import (
    OwnerExpertTariffPrice,
    OwnerSearch,
    OwnerUserMessage,
)

from .context import OwnerRouterContext
from .user_card import render_owner_user, render_owner_user_list


def safe_html_limit(value: str, limit: int) -> str:
    result: list[str] = []
    length = 0
    for char in value:
        escaped = safe_html(char)
        if length + len(escaped) > limit - 1:
            result.append("…")
            break
        result.append(escaped)
        length += len(escaped)
    return "".join(result)


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows
        ]
    )


def build_owner_users_router(context: OwnerRouterContext) -> Router:
    router = Router(name="owner-users")

    @router.callback_query(F.data == "owner:users")
    async def owner_users(callback: CallbackQuery, state: FSMContext) -> None:
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            await context.show(
                callback,
                "<b>👥 Пользователи</b>\n\nВыберите раздел или найдите конкретного пользователя.",
                keyboard(
                    [
                        [("🔍 Поиск пользователя", "owner:user_search")],
                        [
                            ("👨‍⚕️ Эксперты", "owner:users_filter:EXPERT:0"),
                            ("👤 Клиенты", "owner:users_filter:CLIENT:0"),
                        ],
                        [("⬅️ В панель управления", "owner:panel")],
                    ]
                ),
            )
        await state.clear()
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:users_filter:"))
    async def owner_users_filter(callback: CallbackQuery) -> None:
        _, _, role_value, page_value = (callback.data or "").split(":", 3)
        role = Role(role_value)
        if role not in {Role.CLIENT, Role.EXPERT}:
            raise ValidationError("Неизвестная роль")
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            await render_owner_user_list(context, callback, session, role, max(int(page_value), 0))
        await callback.answer()

    @router.callback_query(F.data == "owner:user_search")
    async def owner_user_search(callback: CallbackQuery, state: FSMContext) -> None:
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
        await state.clear()
        await state.set_state(OwnerSearch.query)
        await context.show(
            callback,
            "<b>🔍 Поиск пользователя</b>\n\n"
            "Введите ID пользователя, часть имени или перешлите любое его сообщение.",
            keyboard([[("⬅️ Назад", "owner:users")]]),
        )
        await callback.answer()

    @router.message(OwnerSearch.query)
    async def owner_user_search_result(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        async with context.session_factory() as session:
            owner = await context.owner(session, message.from_user.id)
            forwarded_id: int | None = None
            if message.forward_origin is not None:
                if isinstance(message.forward_origin, MessageOriginUser):
                    forwarded_id = message.forward_origin.sender_user.id
                else:
                    await context.show(
                        message,
                        "Telegram скрыл отправителя этого сообщения. "
                        "Введите ID, username, имя, ФИО или телефон вручную.",
                        keyboard([[("⬅️ Назад", "owner:users")]]),
                    )
                    return
            query = (message.text or "").strip().lstrip("@")
            if forwarded_id is None and not query:
                await context.show(
                    message,
                    "Не удалось распознать запрос. Введите текст или перешлите "
                    "сообщение пользователя.",
                    keyboard([[("⬅️ Назад", "owner:users")]]),
                )
                return
            clauses: list[Any] = [
                User.username.ilike(f"%{query}%"),
                User.telegram_name.ilike(f"%{query}%"),
                User.full_name.ilike(f"%{query}%"),
                User.phone.ilike(f"%{query}%"),
            ]
            if query.isdigit():
                clauses.append(User.id == int(query))
                clauses.append(User.telegram_id == int(query))
            if forwarded_id is not None:
                clauses = [User.telegram_id == forwarded_id]
            users = list(
                await session.scalars(
                    select(User).where(or_(*clauses)).order_by(User.created_at.desc()).limit(100)
                )
            )
            if not users:
                await context.show(
                    message,
                    "Ничего не найдено. Попробуйте другой ID, username, имя, ФИО или телефон.",
                    keyboard([[("⬅️ Назад", "owner:users")]]),
                )
                return
            if len(users) == 1:
                await state.set_state(None)
                await state.update_data(owner_search_ids=[users[0].id], owner_search_page=0)
                await render_owner_user(context, message, session, owner, users[0].id)
                return
            await state.set_state(None)
            await state.update_data(
                owner_search_ids=[item.id for item in users],
                owner_search_page=0,
            )
            rows = [
                [(item.full_name or item.telegram_name, f"owner:user:{item.id}")]
                for item in users[:8]
            ]
            if len(users) > 8:
                rows.append([("➡️", "owner:search_results:1")])
            rows.append([("⬅️ Назад", "owner:users")])
            await context.show(message, f"Найдено: {len(users)}", keyboard(rows))

    @router.callback_query(F.data.startswith("owner:search_results:"))
    async def owner_search_results(callback: CallbackQuery, state: FSMContext) -> None:
        page = max(int((callback.data or "").rsplit(":", 1)[1]), 0)
        data = await state.get_data()
        user_ids = [int(value) for value in data.get("owner_search_ids", [])]
        if not user_ids:
            raise ValidationError("Результаты поиска устарели. Выполните поиск заново")
        per_page = 8
        await state.update_data(owner_search_page=page)
        page_ids = user_ids[page * per_page : (page + 1) * per_page]
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            found = list(await session.scalars(select(User).where(User.id.in_(page_ids))))
            found_by_id = {item.id: item for item in found}
            rows = [
                [
                    (
                        found_by_id[user_id].full_name or found_by_id[user_id].telegram_name,
                        f"owner:user:{user_id}",
                    )
                ]
                for user_id in page_ids
                if user_id in found_by_id
            ]
            navigation: list[tuple[str, str]] = []
            if page > 0:
                navigation.append(("⬅️", f"owner:search_results:{page - 1}"))
            if (page + 1) * per_page < len(user_ids):
                navigation.append(("➡️", f"owner:search_results:{page + 1}"))
            if navigation:
                rows.append(navigation)
            rows.append([("🔍 Новый поиск", "owner:user_search")])
            rows.append([("⬅️ К пользователям", "owner:users")])
            await context.show(
                callback, f"<b>Результаты поиска</b>\n\nНайдено: {len(user_ids)}", keyboard(rows)
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:user:"))
    async def owner_user_view(callback: CallbackQuery, state: FSMContext) -> None:
        user_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            await render_owner_user(context, callback, session, owner, user_id)
        await callback.answer()

    @router.callback_query(F.data == "owner:usr_back")
    async def owner_user_back(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        user_ids = data.get("owner_search_ids", [])
        if len(user_ids) > 1:
            page = max(int(data.get("owner_search_page", 0)), 0)
            per_page = 8
            page_ids = [int(value) for value in user_ids[page * per_page : (page + 1) * per_page]]
            async with context.session_factory() as session:
                await context.owner(session, callback.from_user.id)
                found = list(await session.scalars(select(User).where(User.id.in_(page_ids))))
                found_by_id = {item.id: item for item in found}
                rows = [
                    [
                        (
                            found_by_id[user_id].full_name or found_by_id[user_id].telegram_name,
                            f"owner:user:{user_id}",
                        )
                    ]
                    for user_id in page_ids
                    if user_id in found_by_id
                ]
                navigation: list[tuple[str, str]] = []
                if page > 0:
                    navigation.append(("⬅️", f"owner:search_results:{page - 1}"))
                if (page + 1) * per_page < len(user_ids):
                    navigation.append(("➡️", f"owner:search_results:{page + 1}"))
                if navigation:
                    rows.append(navigation)
                rows.append([("🔍 Новый поиск", "owner:user_search")])
                rows.append([("⬅️ К пользователям", "owner:users")])
                await context.show(
                    callback,
                    f"<b>Результаты поиска</b>\n\nНайдено: {len(user_ids)}",
                    keyboard(rows),
                )
            await callback.answer()
            return
        if len(user_ids) == 1:
            await owner_user_search(callback, state)
            return
        await owner_users(callback, state)

    @router.callback_query(F.data.startswith("owner:usr_block:"))
    async def owner_user_block_confirm(callback: CallbackQuery) -> None:
        user_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            user = await session.get(User, user_id)
            if user is None or user.id == owner.id:
                raise ValidationError("Нельзя изменить этого пользователя")
            action = "разблокировать" if user.is_blocked else "заблокировать"
            await context.show(
                callback,
                f"Подтвердите действие: <b>{action}</b> пользователя "
                f"{safe_html(user.full_name or user.telegram_name)}?",
                keyboard(
                    [
                        [("✅ Подтвердить", f"owner:usr_block_apply:{user.id}")],
                        [("⬅️ Назад", f"owner:user:{user.id}")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:usr_block_apply:"))
    async def owner_user_block_apply(callback: CallbackQuery) -> None:
        user_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            user = await session.get(User, user_id, with_for_update=True)
            if user is None or user.id == owner.id or user.role is Role.OWNER:
                raise ValidationError("Нельзя изменить этого пользователя")
            user.is_blocked = not user.is_blocked
            session.add(
                AuditLog(
                    actor_id=owner.id,
                    action="user.block" if user.is_blocked else "user.unblock",
                    entity_type="user",
                    entity_id=str(user.id),
                    data={"telegram_id": user.telegram_id},
                )
            )
            await session.commit()
            await render_owner_user(context, callback, session, owner, user.id)
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:usr_role:"))
    async def owner_user_role(callback: CallbackQuery) -> None:
        user_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            target = await session.get(User, user_id)
            if target is None or target.id == owner.id or target.role is Role.OWNER:
                raise ValidationError("Нельзя изменить роль этого пользователя")
            await context.show(
                callback,
                f"Выберите новую роль для {safe_html(target.full_name or target.telegram_name)}:",
                keyboard(
                    [
                        [("👤 Клиент", f"owner:usr_role_confirm:{user_id}:CLIENT")],
                        [("👨‍⚕️ Эксперт", f"owner:usr_role_confirm:{user_id}:EXPERT")],
                        [("⬅️ Назад", f"owner:user:{user_id}")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:usr_role_confirm:"))
    async def owner_user_role_confirm(callback: CallbackQuery) -> None:
        _, _, user_id_value, role_value = (callback.data or "").split(":", 3)
        role = Role(role_value)
        if role not in {Role.CLIENT, Role.EXPERT}:
            raise ValidationError("Недопустимая роль")
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            target = await session.get(User, int(user_id_value))
            if target is None or target.id == owner.id or target.role is Role.OWNER:
                raise ValidationError("Нельзя изменить роль этого пользователя")
        await context.show(
            callback,
            f"Подтвердите изменение роли на <b>{role.value}</b>.",
            keyboard(
                [
                    [("✅ Подтвердить", f"owner:usr_role_apply:{user_id_value}:{role.value}")],
                    [("⬅️ Назад", f"owner:usr_role:{user_id_value}")],
                ]
            ),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:usr_role_apply:"))
    async def owner_user_role_apply(callback: CallbackQuery) -> None:
        _, _, user_id_value, role_value = (callback.data or "").split(":", 3)
        user_id = int(user_id_value)
        new_role = Role(role_value)
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            target = await session.get(User, user_id, with_for_update=True)
            if target is None or target.id == owner.id or target.role is Role.OWNER:
                raise ValidationError("Нельзя изменить роль этого пользователя")
            if new_role is Role.EXPERT:
                profile_id = await session.scalar(
                    select(ExpertProfile.id).where(ExpertProfile.user_id == target.id)
                )
                if profile_id is None:
                    raise ValidationError("Сначала одобрите заявку и создайте профиль эксперта")
            previous = target.role
            target.role = new_role
            session.add(
                AuditLog(
                    actor_id=owner.id,
                    action="user.role_change",
                    entity_type="user",
                    entity_id=str(target.id),
                    data={"from": previous.value, "to": new_role.value},
                )
            )
            await session.commit()
            await render_owner_user(context, callback, session, owner, target.id)
        await callback.answer("Роль изменена")

    @router.callback_query(F.data.startswith("owner:usr_publish:"))
    async def owner_user_publish_confirm(callback: CallbackQuery) -> None:
        user_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            target = await session.get(User, user_id)
            if target is None or target.id == owner.id or target.role is Role.OWNER:
                raise ValidationError("Нельзя изменить профиль владельца")
            profile = await session.scalar(
                select(ExpertProfile).where(ExpertProfile.user_id == user_id)
            )
            if profile is None:
                raise ValidationError("Профиль эксперта не найден")
            action = "скрыть" if profile.is_published else "опубликовать"
            await context.show(
                callback,
                f"Подтвердите действие: <b>{action}</b> профиль эксперта?",
                keyboard(
                    [
                        [("✅ Подтвердить", f"owner:usr_publish_apply:{user_id}")],
                        [("⬅️ Назад", f"owner:user:{user_id}")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:usr_publish_apply:"))
    async def owner_user_publish_apply(callback: CallbackQuery) -> None:
        user_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            target = await session.get(User, user_id)
            if target is None or target.id == owner.id or target.role is Role.OWNER:
                raise ValidationError("Нельзя изменить профиль владельца")
            profile = await session.scalar(
                select(ExpertProfile).where(ExpertProfile.user_id == user_id).with_for_update()
            )
            if profile is None:
                raise ValidationError("Профиль эксперта не найден")
            profile.is_published = not profile.is_published
            session.add(
                AuditLog(
                    actor_id=owner.id,
                    action="expert.publish" if profile.is_published else "expert.unpublish",
                    entity_type="expert_profile",
                    entity_id=str(profile.id),
                    data={"user_id": user_id},
                )
            )
            await session.commit()
            await render_owner_user(context, callback, session, owner, user_id)
        await callback.answer("Статус публикации изменён")

    @router.callback_query(F.data.startswith("owner:usr_stats:"))
    async def owner_user_stats(callback: CallbackQuery) -> None:
        user_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            target = await session.get(User, user_id)
            if target is None:
                raise ValidationError("Пользователь не найден")
            payment_count, payment_sum = (
                await session.execute(
                    select(
                        func.count(PaymentTransaction.id),
                        func.coalesce(func.sum(PaymentTransaction.amount), 0),
                    ).where(
                        PaymentTransaction.client_id == user_id,
                        PaymentTransaction.status == TransactionStatus.COMPLETED,
                    )
                )
            ).one()
            subscription_count = int(
                await session.scalar(
                    select(func.count(ClientExpertSubscription.id)).where(
                        ClientExpertSubscription.client_id == user_id
                    )
                )
                or 0
            )
            referrals_count = int(
                await session.scalar(select(func.count(User.id)).where(User.referrer_id == user_id))
                or 0
            )
            await context.show(
                callback,
                f"<b>📊 Статистика пользователя</b>\n\n"
                f"• Оплат: {payment_count}\n"
                f"• Сумма оплат: {Decimal(payment_sum or 0):.2f} ₽\n"
                f"• Подписок: {subscription_count}\n"
                f"• Приглашено: {referrals_count}\n"
                f"• Реферальный баланс: {target.bonus_balance:.2f} ₽",
                keyboard([[("⬅️ Назад", f"owner:user:{user_id}")]]),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:usr_tx:"))
    async def owner_user_transactions(callback: CallbackQuery) -> None:
        user_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            items = list(
                await session.scalars(
                    select(PaymentTransaction)
                    .where(PaymentTransaction.client_id == user_id)
                    .order_by(PaymentTransaction.created_at.desc())
                    .limit(15)
                )
            )
            lines = ["<b>🧾 Транзакции пользователя</b>"]
            lines.extend(
                f"\n• <code>{str(item.id)[:8]}</code> — {item.amount:.2f} ₽ — {item.status.value}"
                for item in items
            )
            if not items:
                lines.append("\n\nТранзакций пока нет.")
            await context.show(
                callback,
                "".join(lines),
                keyboard([[("⬅️ Назад", f"owner:user:{user_id}")]]),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:usr_subs:"))
    async def owner_user_subscriptions(callback: CallbackQuery) -> None:
        user_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            items = (
                await session.execute(
                    select(ClientExpertSubscription, ExpertProfile, Tariff)
                    .join(ExpertProfile, ExpertProfile.id == ClientExpertSubscription.expert_id)
                    .join(Tariff, Tariff.id == ClientExpertSubscription.tariff_id)
                    .where(ClientExpertSubscription.client_id == user_id)
                    .order_by(ClientExpertSubscription.created_at.desc())
                    .limit(15)
                )
            ).all()
            lines = ["<b>💎 Подписки пользователя</b>"]
            for subscription, expert, tariff in items:
                started = (
                    subscription.started_at.strftime("%d.%m.%Y %H:%M")
                    if subscription.started_at
                    else "—"
                )
                expires = (
                    subscription.expires_at.strftime("%d.%m.%Y %H:%M")
                    if subscription.expires_at
                    else "—"
                )
                lines.append(
                    "\n<blockquote>"
                    f"• {safe_html(expert.display_name)} / {safe_html(tariff.name)}\n"
                    f"• Статус: {subscription.status.value}\n"
                    f"• Период: {started} — {expires}\n"
                    f"• Сообщения: {subscription.messages_used}/"
                    f"{subscription.message_limit or '∞'}"
                    "</blockquote>"
                )
            if not items:
                lines.append("\n\nПодписок пока нет.")
            await context.show(
                callback, "".join(lines), keyboard([[("⬅️ Назад", f"owner:user:{user_id}")]])
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:usr_chats:"))
    async def owner_user_conversations(callback: CallbackQuery) -> None:
        user_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            profile_id = await session.scalar(
                select(ExpertProfile.id).where(ExpertProfile.user_id == user_id)
            )
            conversations = list(
                await session.scalars(
                    select(Conversation)
                    .where(
                        or_(
                            Conversation.client_id == user_id,
                            Conversation.expert_id == (profile_id if profile_id else -1),
                        )
                    )
                    .order_by(Conversation.created_at.desc())
                    .limit(20)
                )
            )
            lines = ["<b>💬 Консультации пользователя</b>"]
            for conversation in conversations:
                topic_rows = list(
                    await session.scalars(
                        select(ConversationTopic).where(
                            ConversationTopic.conversation_id == conversation.id
                        )
                    )
                )
                statuses = ", ".join(
                    f"{item.side.value}: {item.status.value}" for item in topic_rows
                )
                lines.append(f"\n• Диалог #{conversation.id} — {statuses or 'темы не созданы'}")
            if not conversations:
                lines.append("\n\nКонсультаций пока нет.")
            await context.show(
                callback, "".join(lines), keyboard([[("⬅️ Назад", f"owner:user:{user_id}")]])
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:usr_tariffs:"))
    async def owner_user_tariffs(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        user_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            profile = await session.scalar(
                select(ExpertProfile).where(ExpertProfile.user_id == user_id)
            )
            if profile is None:
                raise ValidationError("Профиль эксперта не найден")
            items = (
                await session.execute(
                    select(ExpertTariff, Tariff)
                    .join(Tariff, Tariff.id == ExpertTariff.tariff_id)
                    .where(ExpertTariff.expert_id == profile.id)
                    .order_by(Tariff.sort_order)
                )
            ).all()
            lines = [f"<b>💎 Тарифы {safe_html(profile.display_name)}</b>"]
            buttons: list[list[tuple[str, str]]] = []
            for item, tariff in items:
                price = item.custom_price if item.custom_price is not None else tariff.default_price
                status_icon = "🟢" if item.is_active else "🔴"
                lines.append(f"\n• {status_icon} {safe_html(tariff.name)} — {price:.2f} ₽")
                buttons.append(
                    [
                        (
                            f"{status_icon} {tariff.name} — {price:.0f} ₽",
                            f"owner:usr_tariff:{user_id}:{item.id}",
                        )
                    ]
                )
            buttons.append([("⬅️ Назад", f"owner:user:{user_id}")])
            await context.show(callback, "".join(lines), keyboard(buttons))
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:usr_profile:"))
    async def owner_user_expert_profile(callback: CallbackQuery) -> None:
        user_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            profile = await session.scalar(
                select(ExpertProfile).where(ExpertProfile.user_id == user_id)
            )
            if profile is None:
                raise ValidationError("Профиль эксперта не найден")
            await context.show_photo(
                callback,
                profile.photo_file_id,
                f"<b>{safe_html_limit(profile.display_name, 160)}</b>\n"
                f"<i>{safe_html_limit(profile.specialization, 160)}</i>\n\n"
                f"{safe_html_limit(profile.short_bio, 300)}\n\n"
                f"{safe_html_limit(profile.bio, 400)}",
                keyboard([[("⬅️ Назад", f"owner:user:{user_id}")]]),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:usr_tariff:"))
    async def owner_user_tariff_view(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        _, _, user_id_value, item_id_value = (callback.data or "").split(":", 3)
        user_id = int(user_id_value)
        item_id = int(item_id_value)
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            row = await session.execute(
                select(ExpertTariff, Tariff, ExpertProfile)
                .join(Tariff, Tariff.id == ExpertTariff.tariff_id)
                .join(ExpertProfile, ExpertProfile.id == ExpertTariff.expert_id)
                .where(
                    ExpertTariff.id == item_id,
                    ExpertProfile.user_id == user_id,
                )
            )
            result = row.one_or_none()
            if result is None:
                raise ValidationError("Тариф эксперта не найден")
            item, tariff, profile = result
            price = item.custom_price if item.custom_price is not None else tariff.default_price
            own_price = item.custom_price if item.custom_price is not None else "не задана"
            await context.show(
                callback,
                f"<b>{safe_html(profile.display_name)} — {safe_html(tariff.name)}</b>\n\n"
                f"• Итоговая цена: {price:.2f} ₽\n"
                f"• Цена эксперта: {own_price}\n"
                f"• Цена владельца: {tariff.default_price:.2f} ₽\n"
                f"• Статус: {'активен' if item.is_active else 'выключен'}",
                keyboard(
                    [
                        [("💰 Изменить цену", f"owner:usr_tariff_price:{user_id}:{item_id}")],
                        [
                            (
                                "🔴 Выключить" if item.is_active else "🟢 Включить",
                                f"owner:usr_tariff_toggle:{user_id}:{item_id}",
                            )
                        ],
                        [("⬅️ Назад", f"owner:usr_tariffs:{user_id}")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:usr_tariff_toggle:"))
    async def owner_user_tariff_toggle_confirm(callback: CallbackQuery) -> None:
        _, _, user_id_value, item_id_value = (callback.data or "").split(":", 3)
        user_id = int(user_id_value)
        item_id = int(item_id_value)
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            target = await session.get(User, user_id)
            if target is None or target.id == owner.id or target.role is Role.OWNER:
                raise ValidationError("Нельзя изменить тариф владельца")
            item = await session.scalar(
                select(ExpertTariff)
                .join(ExpertProfile, ExpertProfile.id == ExpertTariff.expert_id)
                .where(ExpertTariff.id == item_id, ExpertProfile.user_id == user_id)
            )
            if item is None:
                raise ValidationError("Тариф эксперта не найден")
            action = "выключить" if item.is_active else "включить"
            await context.show(
                callback,
                f"Подтвердите: <b>{action}</b> тариф эксперта?",
                keyboard(
                    [
                        [
                            (
                                "✅ Подтвердить",
                                f"owner:usr_tariff_toggle_apply:{user_id}:{item_id}",
                            )
                        ],
                        [("⬅️ Назад", f"owner:usr_tariff:{user_id}:{item_id}")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:usr_tariff_toggle_apply:"))
    async def owner_user_tariff_toggle_apply(callback: CallbackQuery) -> None:
        _, _, user_id_value, item_id_value = (callback.data or "").split(":", 3)
        user_id = int(user_id_value)
        item_id = int(item_id_value)
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            target = await session.get(User, user_id)
            if target is None or target.id == owner.id or target.role is Role.OWNER:
                raise ValidationError("Нельзя изменить тариф владельца")
            item = await session.scalar(
                select(ExpertTariff)
                .join(ExpertProfile, ExpertProfile.id == ExpertTariff.expert_id)
                .where(ExpertTariff.id == item_id, ExpertProfile.user_id == user_id)
                .with_for_update()
            )
            if item is None:
                raise ValidationError("Тариф эксперта не найден")
            item.is_active = not item.is_active
            session.add(
                AuditLog(
                    actor_id=owner.id,
                    action="expert_tariff.enable" if item.is_active else "expert_tariff.disable",
                    entity_type="expert_tariff",
                    entity_id=str(item.id),
                    data={"user_id": user_id},
                )
            )
            await session.commit()
            await context.show(
                callback,
                "Статус тарифа эксперта изменён.",
                keyboard([[("⬅️ К тарифу", f"owner:usr_tariff:{user_id}:{item_id}")]]),
            )
        await callback.answer("Готово")

    @router.callback_query(F.data.startswith("owner:usr_tariff_price:"))
    async def owner_user_tariff_price_start(callback: CallbackQuery, state: FSMContext) -> None:
        _, _, user_id_value, item_id_value = (callback.data or "").split(":", 3)
        user_id = int(user_id_value)
        item_id = int(item_id_value)
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            target = await session.get(User, user_id)
            if target is None or target.id == owner.id or target.role is Role.OWNER:
                raise ValidationError("Нельзя изменить тариф владельца")
            item = await session.scalar(
                select(ExpertTariff)
                .join(ExpertProfile, ExpertProfile.id == ExpertTariff.expert_id)
                .where(ExpertTariff.id == item_id, ExpertProfile.user_id == user_id)
            )
            if item is None:
                raise ValidationError("Тариф эксперта не найден")
        await state.clear()
        await state.update_data(target_user_id=user_id, expert_tariff_id=item_id)
        await state.set_state(OwnerExpertTariffPrice.price)
        await context.show(
            callback,
            "Введите новую цену в рублях или 0, чтобы использовать цену владельца:",
            keyboard([[("⬅️ Назад", f"owner:usr_tariff:{user_id}:{item_id}")]]),
        )
        await callback.answer()

    @router.message(OwnerExpertTariffPrice.price, F.text)
    async def owner_user_tariff_price_preview(message: Message, state: FSMContext) -> None:
        try:
            value = money(Decimal((message.text or "").replace(",", ".")))
        except InvalidOperation as error:
            raise ValidationError("Введите корректную цену") from error
        if value < 0:
            raise ValidationError("Цена не может быть отрицательной")
        await state.update_data(expert_tariff_price=str(value))
        data = await state.get_data()
        user_id = int(data["target_user_id"])
        item_id = int(data["expert_tariff_id"])
        await state.set_state(OwnerExpertTariffPrice.confirm)
        price_text = "цена владельца" if value == 0 else f"{value:.2f} ₽"
        await context.show(
            message,
            f"Подтвердите новую цену: <b>{price_text}</b>.",
            keyboard(
                [
                    [("✅ Сохранить", "owner:usr_tariff_price_apply")],
                    [("⬅️ Назад", f"owner:usr_tariff_price:{user_id}:{item_id}")],
                    [("❌ Отменить", f"owner:usr_tariff:{user_id}:{item_id}")],
                ]
            ),
        )

    @router.callback_query(
        OwnerExpertTariffPrice.confirm,
        F.data == "owner:usr_tariff_price_apply",
    )
    async def owner_user_tariff_price_apply(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        user_id = int(data["target_user_id"])
        item_id = int(data["expert_tariff_id"])
        value = money(Decimal(str(data["expert_tariff_price"])))
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            target = await session.get(User, user_id)
            if target is None or target.id == owner.id or target.role is Role.OWNER:
                raise ValidationError("Нельзя изменить тариф владельца")
            item = await session.scalar(
                select(ExpertTariff)
                .join(ExpertProfile, ExpertProfile.id == ExpertTariff.expert_id)
                .where(ExpertTariff.id == item_id, ExpertProfile.user_id == user_id)
                .with_for_update()
            )
            if item is None:
                raise ValidationError("Тариф эксперта не найден")
            previous = item.custom_price
            item.custom_price = None if value == 0 else value
            session.add(
                AuditLog(
                    actor_id=owner.id,
                    action="expert_tariff.price_change",
                    entity_type="expert_tariff",
                    entity_id=str(item.id),
                    data={
                        "user_id": user_id,
                        "before": str(previous) if previous is not None else None,
                        "after": str(item.custom_price) if item.custom_price is not None else None,
                    },
                )
            )
            await session.commit()
        await state.clear()
        await context.show(
            callback,
            "Цена тарифа эксперта изменена.",
            keyboard([[("⬅️ К тарифу", f"owner:usr_tariff:{user_id}:{item_id}")]]),
        )
        await callback.answer("Готово")

    @router.callback_query(F.data.startswith("owner:usr_msg:"))
    async def owner_user_message_start(callback: CallbackQuery, state: FSMContext) -> None:
        user_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            if await session.get(User, user_id) is None:
                raise ValidationError("Пользователь не найден")
        await state.clear()
        await state.update_data(target_user_id=user_id)
        await state.set_state(OwnerUserMessage.text)
        await context.show(
            callback,
            "Введите сообщение пользователю:",
            keyboard([[("⬅️ Назад", f"owner:user:{user_id}")]]),
        )
        await callback.answer()

    @router.message(OwnerUserMessage.text, F.text)
    async def owner_user_message_preview(message: Message, state: FSMContext) -> None:
        text = validate_text(message.text or "", maximum=4000)
        await state.update_data(text=text)
        data = await state.get_data()
        target_user_id = int(data["target_user_id"])
        await state.set_state(OwnerUserMessage.confirm)
        await context.show(
            message,
            f"<b>Подтвердите отправку</b>\n\n{safe_html(text)}",
            keyboard(
                [
                    [("✅ Отправить", "owner:usr_msg_send")],
                    [("⬅️ Назад", f"owner:usr_msg:{target_user_id}")],
                    [("❌ Отменить", f"owner:user:{target_user_id}")],
                ]
            ),
        )

    @router.callback_query(OwnerUserMessage.confirm, F.data == "owner:usr_msg_send")
    async def owner_user_message_send(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        data = await state.get_data()
        user_id = int(data["target_user_id"])
        text = str(data["text"])
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            target = await session.get(User, user_id)
            if target is None:
                raise ValidationError("Пользователь не найден")
            await send_notification(
                bot, target.telegram_id, f"<b>Сообщение от администрации</b>\n\n{safe_html(text)}"
            )
            session.add(
                AuditLog(
                    actor_id=owner.id,
                    action="user.message",
                    entity_type="user",
                    entity_id=str(target.id),
                    data={"length": len(text)},
                )
            )
            await session.commit()
            await state.clear()
            await render_owner_user(context, callback, session, owner, target.id)
        await callback.answer("Сообщение отправлено")

    return router
