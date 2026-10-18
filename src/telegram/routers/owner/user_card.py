from __future__ import annotations

from decimal import Decimal

from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.validation import safe_html
from src.core.enums import Role, SubscriptionStatus, TransactionStatus
from src.core.exceptions import ValidationError
from src.infrastructure.database.models import (
    ClientExpertSubscription,
    Conversation,
    ConversationTopic,
    ExpertApplication,
    ExpertProfile,
    ExpertTariff,
    PaymentTransaction,
    ReferralReward,
    Tariff,
    User,
)

from .context import OwnerRouterContext


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows
        ]
    )


async def render_owner_user(
    context: OwnerRouterContext,
    event: Message | CallbackQuery,
    session: AsyncSession,
    owner: User,
    target_id: int,
) -> None:
    target = await session.get(User, target_id)
    if target is None:
        raise ValidationError("Пользователь не найден")
    profile = await session.scalar(select(ExpertProfile).where(ExpertProfile.user_id == target.id))
    application = await session.scalar(
        select(ExpertApplication)
        .where(ExpertApplication.user_id == target.id)
        .order_by(ExpertApplication.created_at.desc())
    )
    subscriptions = (
        await session.execute(
            select(ClientExpertSubscription, ExpertProfile, Tariff)
            .join(ExpertProfile, ExpertProfile.id == ClientExpertSubscription.expert_id)
            .join(Tariff, Tariff.id == ClientExpertSubscription.tariff_id)
            .where(ClientExpertSubscription.client_id == target.id)
            .order_by(ClientExpertSubscription.created_at.desc())
            .limit(2)
        )
    ).all()
    paid_count, paid_total = (
        await session.execute(
            select(
                func.count(PaymentTransaction.id),
                func.coalesce(func.sum(PaymentTransaction.amount), 0),
            ).where(
                PaymentTransaction.client_id == target.id,
                PaymentTransaction.status == TransactionStatus.COMPLETED,
            )
        )
    ).one()
    latest_transactions = list(
        await session.scalars(
            select(PaymentTransaction)
            .where(PaymentTransaction.client_id == target.id)
            .order_by(PaymentTransaction.created_at.desc())
            .limit(2)
        )
    )
    conversation_count = int(
        await session.scalar(
            select(func.count(Conversation.id)).where(
                or_(
                    Conversation.client_id == target.id,
                    Conversation.expert_id == (profile.id if profile else -1),
                )
            )
        )
        or 0
    )
    invited = int(
        await session.scalar(select(func.count(User.id)).where(User.referrer_id == target.id)) or 0
    )
    paid_referrals = int(
        await session.scalar(
            select(func.count(func.distinct(PaymentTransaction.client_id)))
            .join(User, User.id == PaymentTransaction.client_id)
            .where(
                User.referrer_id == target.id,
                PaymentTransaction.status == TransactionStatus.COMPLETED,
            )
        )
        or 0
    )
    reward_count, rewards = (
        await session.execute(
            select(
                func.count(ReferralReward.id),
                func.coalesce(func.sum(ReferralReward.amount), 0),
            ).where(
                ReferralReward.referrer_id == target.id,
                ReferralReward.reversed_at.is_(None),
            )
        )
    ).one()
    active_tariffs = 0
    active_clients = 0
    if profile:
        active_tariffs = int(
            await session.scalar(
                select(func.count(ExpertTariff.id)).where(
                    ExpertTariff.expert_id == profile.id,
                    ExpertTariff.is_active.is_(True),
                )
            )
            or 0
        )
        active_clients = int(
            await session.scalar(
                select(func.count(func.distinct(ClientExpertSubscription.client_id))).where(
                    ClientExpertSubscription.expert_id == profile.id,
                    ClientExpertSubscription.status == SubscriptionStatus.ACTIVE,
                    ClientExpertSubscription.expires_at > func.now(),
                )
            )
            or 0
        )
    topic_status_rows = (
        await session.execute(
            select(ConversationTopic.status, func.count(ConversationTopic.id))
            .join(Conversation, Conversation.id == ConversationTopic.conversation_id)
            .where(
                or_(
                    Conversation.client_id == target.id,
                    Conversation.expert_id == (profile.id if profile else -1),
                )
            )
            .group_by(ConversationTopic.status)
        )
    ).all()
    referrer = await session.get(User, target.referrer_id) if target.referrer_id else None
    lines = [
        "<b>📝 Информация о пользователе</b>\n\n",
        "<b>👤 Профиль:</b>\n<blockquote>",
        f"• ID: <code>{target.id}</code> / TG <code>{target.telegram_id}</code>\n",
        f"• Имя: {safe_html(target.telegram_name)} ",
        f"(@{safe_html(target.username)})\n" if target.username else "(без username)\n",
        f"• ФИО: {safe_html(target.full_name or '—')}\n",
        f"• Телефон: {safe_html(target.phone or '—')}\n",
        f"• Роль: {target.role.value}; язык: {safe_html(target.language)}\n",
        f"• Зарегистрирован: {target.created_at:%d.%m.%Y %H:%M}\n",
        f"• Заблокирован: {'да' if target.is_blocked else 'нет'}",
        "</blockquote>",
    ]
    if profile or application:
        specialization = (
            profile.specialization
            if profile
            else application.specialization
            if application
            else "—"
        )
        lines.extend(
            [
                "\n\n<b>👨‍⚕️ Эксперт:</b>\n<blockquote>",
                f"• Заявка: {application.status.value if application else '—'}\n",
                f"• Специализация: {safe_html(specialization)}\n",
                f"• Профиль: #{profile.id if profile else '—'}, "
                f"{'опубликован' if profile and profile.is_published else 'скрыт'}\n",
                f"• Активных тарифов: {active_tariffs}\n",
                f"• Активных клиентов: {active_clients}",
                "</blockquote>",
            ]
        )
    if subscriptions:
        lines.append("\n\n<b>🔗 Текущие и последние подписки:</b>")
        for subscription, expert, tariff in subscriptions:
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
            lines.extend(
                [
                    "\n<blockquote>",
                    f"• {safe_html(expert.display_name)} / {safe_html(tariff.name)}\n",
                    f"• Статус: {subscription.status.value}\n",
                    f"• Период: {started} — {expires}\n",
                    f"• Сообщения: {subscription.messages_used}/"
                    f"{subscription.message_limit or '∞'}",
                    "</blockquote>",
                ]
            )
    referrer_name = safe_html(referrer.full_name or referrer.telegram_name) if referrer else "—"
    transaction_preview = ", ".join(
        f"{str(item.id)[:8]} {item.amount:.0f}₽/{item.status.value}" for item in latest_transactions
    )
    topic_preview = ", ".join(f"{status.value}: {count}" for status, count in topic_status_rows)
    lines.extend(
        [
            f"\n\n<b>💳 Финансы:</b> {paid_count} оплат / {Decimal(paid_total or 0):.2f} ₽",
            f"\nПоследние: {safe_html(transaction_preview or '—')}",
            f"\n<b>💬 Консультации:</b> {conversation_count}; "
            f"темы: {safe_html(topic_preview or '—')}",
            "\n<b>👥 Рефералы:</b> "
            f"пригласил {invited}, оплатили {paid_referrals}, "
            f"начислений {reward_count} на {Decimal(rewards or 0):.2f} ₽, "
            f"баланс {target.bonus_balance:.2f} ₽",
            f"\nПригласил: {referrer_name}",
        ]
    )
    rows: list[list[tuple[str, str]]] = [
        [
            ("📊 Статистика", f"owner:usr_stats:{target.id}"),
            ("🧾 Транзакции", f"owner:usr_tx:{target.id}"),
        ],
        [
            ("💎 Подписки", f"owner:usr_subs:{target.id}"),
            ("💬 Консультации", f"owner:usr_chats:{target.id}"),
        ],
        [("✉️ Отправить сообщение", f"owner:usr_msg:{target.id}")],
    ]
    if target.id != owner.id:
        rows.append([("👮 Роль", f"owner:usr_role:{target.id}")])
        rows.append(
            [
                (
                    "🔓 Разблокировать" if target.is_blocked else "🔒 Заблокировать",
                    f"owner:usr_block:{target.id}",
                )
            ]
        )
    if profile:
        rows.append([("🪪 Профиль эксперта", f"owner:usr_profile:{target.id}")])
        rows.append(
            [
                (
                    "🙈 Скрыть профиль" if profile.is_published else "👁 Опубликовать профиль",
                    f"owner:usr_publish:{target.id}",
                ),
                ("💎 Тарифы эксперта", f"owner:usr_tariffs:{target.id}"),
            ]
        )
    rows.extend(
        [
            [("⬅️ К результатам поиска", "owner:usr_back")],
            [("↩️ В панель управления", "owner:panel")],
            [("🏠 В пользовательское меню", "owner:client_view")],
        ]
    )
    await context.show_screen(event, "admin_user", "".join(lines), keyboard(rows))


async def render_owner_user_list(
    context: OwnerRouterContext,
    callback: CallbackQuery,
    session: AsyncSession,
    role: Role,
    page: int,
) -> None:
    per_page = 8
    total = int(await session.scalar(select(func.count(User.id)).where(User.role == role)) or 0)
    users = list(
        await session.scalars(
            select(User)
            .where(User.role == role)
            .order_by(User.created_at.desc())
            .offset(page * per_page)
            .limit(per_page)
        )
    )
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text=item.full_name or item.telegram_name,
                callback_data=f"owner:user:{item.id}",
            )
        ]
        for item in users
    ]
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="⬅️", callback_data=f"owner:users_filter:{role.value}:{page - 1}"
            )
        )
    if (page + 1) * per_page < total:
        navigation.append(
            InlineKeyboardButton(
                text="➡️", callback_data=f"owner:users_filter:{role.value}:{page + 1}"
            )
        )
    if navigation:
        buttons.append(navigation)
    buttons.append([InlineKeyboardButton(text="⬅️ К пользователям", callback_data="owner:users")])
    await context.show(
        callback,
        f"<b>{'👨‍⚕️ Эксперты' if role is Role.EXPERT else '👤 Клиенты'}</b>\n\nВсего: {total}",
        InlineKeyboardMarkup(inline_keyboard=buttons),
    )
