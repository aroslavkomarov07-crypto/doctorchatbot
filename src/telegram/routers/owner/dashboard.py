from __future__ import annotations

from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from src.application.services import ExpertService
from src.application.validation import safe_html
from src.core.enums import (
    ConversationTopicStatus,
    ExpertApplicationStatus,
    SubscriptionStatus,
    TransactionStatus,
)
from src.core.exceptions import ValidationError
from src.infrastructure.database.models import (
    AuditLog,
    ClientExpertSubscription,
    Conversation,
    ConversationTopic,
    ExpertApplication,
    ExpertProfile,
    PaymentTransaction,
    User,
)
from src.infrastructure.telegram_notifications import send_notification

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


def build_owner_dashboard_router(context: OwnerRouterContext) -> Router:
    router = Router(name="owner-dashboard")

    @router.callback_query(F.data == "owner:stats")
    async def owner_stats(callback: CallbackQuery) -> None:
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            users_count = await session.scalar(select(func.count(User.id)))
            experts_count = await session.scalar(select(func.count(ExpertProfile.id)))
            active_count = await session.scalar(
                select(func.count(ClientExpertSubscription.id)).where(
                    ClientExpertSubscription.status == SubscriptionStatus.ACTIVE,
                    ClientExpertSubscription.expires_at > func.now(),
                )
            )
            transactions_count = await session.scalar(
                select(func.count(PaymentTransaction.id)).where(
                    PaymentTransaction.status == TransactionStatus.COMPLETED
                )
            )
            gross = await session.scalar(
                select(func.coalesce(func.sum(PaymentTransaction.amount), 0)).where(
                    PaymentTransaction.status == TransactionStatus.COMPLETED
                )
            )
            service_income = await session.scalar(
                select(
                    func.coalesce(func.sum(PaymentTransaction.service_commission_amount), 0)
                ).where(PaymentTransaction.status == TransactionStatus.COMPLETED)
            )
            await context.show(
                callback,
                "<b>📊 Общая статистика</b>\n\n<blockquote>"
                f"• <b>Пользователи</b>: {users_count or 0}\n"
                f"• <b>Эксперты</b>: {experts_count or 0}\n"
                f"• <b>Активные подписки</b>: {active_count or 0}\n"
                f"• <b>Успешные платежи</b>: {transactions_count or 0}\n"
                f"• <b>Оборот</b>: {Decimal(gross or 0):.2f} ₽\n"
                f"• <b>Доход сервиса</b>: {Decimal(service_income or 0):.2f} ₽"
                "</blockquote>",
                admin_back(),
            )
        await callback.answer()

    @router.callback_query(F.data == "owner:topics")
    async def owner_topics(callback: CallbackQuery) -> None:
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            count_rows = (
                await session.execute(
                    select(ConversationTopic.status, func.count(ConversationTopic.id)).group_by(
                        ConversationTopic.status
                    )
                )
            ).all()
            counts: dict[ConversationTopicStatus, int] = {
                topic_status: int(count) for topic_status, count in count_rows
            }
            conversations = await session.scalar(select(func.count(Conversation.id)))
            lines = [
                "<b>🧵 Темы консультаций</b>\n\n",
                f"Диалогов: <b>{conversations or 0}</b>\n",
            ]
            for status in ConversationTopicStatus:
                lines.append(f"{status.value}: <b>{counts.get(status, 0)}</b>\n")
            failed = list(
                await session.scalars(
                    select(ConversationTopic)
                    .where(
                        ConversationTopic.status.in_(
                            [
                                ConversationTopicStatus.FAILED,
                                ConversationTopicStatus.UNAVAILABLE,
                            ]
                        )
                    )
                    .order_by(ConversationTopic.updated_at.desc())
                    .limit(5)
                )
            )
            if failed:
                lines.append("\n<b>Последние ошибки:</b>")
                for topic in failed:
                    lines.append(
                        f"\n• Диалог #{topic.conversation_id}, {topic.side.value}: "
                        f"<code>{safe_html(topic.last_error_code or 'UNKNOWN')}</code>"
                    )
            await context.show(
                callback,
                "".join(lines),
                keyboard(
                    [
                        [("🔄 Повторить ошибки", "owner:topics_retry")],
                        [("➕ Создать недостающие", "owner:topics_backfill")],
                        [("⬅️ В панель управления", "owner:panel")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data == "owner:topics_retry")
    async def owner_topics_retry(callback: CallbackQuery) -> None:
        from src.infrastructure.taskiq.tasks import retry_failed_topics_task  # noqa: PLC0415

        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            session.add(
                AuditLog(
                    actor_id=owner.id,
                    action="conversation_topics.retry",
                    entity_type="conversation_topic",
                    data={},
                )
            )
            await session.commit()
        await retry_failed_topics_task.kiq()
        await callback.answer("Повторное создание поставлено в очередь", show_alert=True)

    @router.callback_query(F.data == "owner:topics_backfill")
    async def owner_topics_backfill(callback: CallbackQuery) -> None:
        from src.infrastructure.taskiq.tasks import (  # noqa: PLC0415
            backfill_conversation_topics_task,
        )

        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            session.add(
                AuditLog(
                    actor_id=owner.id,
                    action="conversation_topics.backfill",
                    entity_type="conversation_topic",
                    data={},
                )
            )
            await session.commit()
        await backfill_conversation_topics_task.kiq()
        await callback.answer("Создание недостающих тем запущено", show_alert=True)

    @router.callback_query(F.data == "owner:applications")
    async def owner_applications(callback: CallbackQuery) -> None:
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            applications = list(
                await session.scalars(
                    select(ExpertApplication)
                    .where(ExpertApplication.status == ExpertApplicationStatus.PENDING)
                    .order_by(ExpertApplication.created_at)
                )
            )
            rows = [
                [(f"{item.full_name} — {item.specialization}", f"owner:app_view:{item.id}")]
                for item in applications
            ]
            rows.append([("⬅️ В панель управления", "owner:panel")])
            await context.show(
                callback,
                f"<b>Заявки экспертов</b>\n\nОжидают решения: {len(applications)}",
                keyboard(rows),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:app_view:"))
    async def owner_application_view(callback: CallbackQuery) -> None:
        application_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            application = await session.get(ExpertApplication, application_id)
            if application is None:
                raise ValidationError("Заявка не найдена")
            await context.show(
                callback,
                f"<b>{safe_html(application.full_name)}</b>\n"
                f"{safe_html(application.specialization)}\n\n"
                f"{safe_html(application.short_bio)}\n\n"
                f"{safe_html(application.bio)}",
                keyboard(
                    [
                        [("✅ Одобрить", f"owner:app_ok:{application.id}")],
                        [("❌ Отклонить", f"owner:app_no:{application.id}")],
                        [("⬅️ Назад", "owner:applications")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:app_ok:"))
    async def owner_application_approve_confirm(callback: CallbackQuery) -> None:
        application_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            if await session.get(ExpertApplication, application_id) is None:
                raise ValidationError("Заявка не найдена")
            await context.show(
                callback,
                "Одобрить заявку и создать опубликованный профиль эксперта?",
                keyboard(
                    [
                        [("✅ Подтвердить", f"owner:app_ok_apply:{application_id}")],
                        [("⬅️ Назад", f"owner:app_view:{application_id}")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:app_ok_apply:"))
    async def owner_application_approve(callback: CallbackQuery, bot: Bot) -> None:
        application_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            profile = await ExpertService(session).approve(owner, application_id)
            expert = await session.get(User, profile.user_id)
            if expert:
                await send_notification(
                    bot,
                    expert.telegram_id,
                    "✅ Ваша заявка одобрена. Вам открыт кабинет эксперта.",
                    reply_markup=keyboard([[("🩺 Открыть кабинет", "menu")]]),
                )
            await context.show(
                callback, "Заявка одобрена, профиль эксперта опубликован.", admin_back()
            )
        await callback.answer("Готово")

    @router.callback_query(F.data.startswith("owner:app_no:"))
    async def owner_application_reject_confirm(callback: CallbackQuery) -> None:
        application_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            await context.owner(session, callback.from_user.id)
            if await session.get(ExpertApplication, application_id) is None:
                raise ValidationError("Заявка не найдена")
            await context.show(
                callback,
                "Отклонить заявку эксперта?",
                keyboard(
                    [
                        [("✅ Подтвердить", f"owner:app_no_apply:{application_id}")],
                        [("⬅️ Назад", f"owner:app_view:{application_id}")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("owner:app_no_apply:"))
    async def owner_application_reject(callback: CallbackQuery, bot: Bot) -> None:
        application_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            owner = await context.owner(session, callback.from_user.id)
            application = await session.get(ExpertApplication, application_id)
            user = await session.get(User, application.user_id if application else 0)
            await ExpertService(session).reject(owner, application_id)
            if user:
                await send_notification(bot, user.telegram_id, "Ваша заявка эксперта отклонена.")
            await context.show(callback, "Заявка отклонена.", admin_back())
        await callback.answer("Готово")

    return router
