from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.money import money
from src.application.services import ExpertService, FinanceService, notification_enabled, utcnow
from src.application.validation import safe_html, validate_full_name, validate_text
from src.core.enums import (
    ConversationTopicSide,
    ConversationTopicStatus,
    Role,
    SubscriptionStatus,
    TransactionStatus,
    WithdrawalStatus,
)
from src.core.exceptions import ValidationError
from src.infrastructure.database.models import (
    ClientExpertSubscription,
    Conversation,
    ConversationTopic,
    ExpertProfile,
    ExpertTariff,
    PaymentTransaction,
    Tariff,
    User,
    WithdrawalRequest,
)
from src.infrastructure.telegram_notifications import send_notification
from src.telegram import events
from src.telegram.routers.context import FeatureRouterContext
from src.telegram.routers.helpers import back_menu, keyboard, safe_html_limit
from src.telegram.states import ExpertPriceForm, ExpertProfileEdit, WithdrawalForm


def build_expert_router(context: FeatureRouterContext) -> Router:
    router = Router(name="expert")

    async def render_expert_profile(
        event: Message | CallbackQuery,
        session: AsyncSession,
        actor: User,
    ) -> None:
        profile = await session.scalar(
            select(ExpertProfile).where(ExpertProfile.user_id == actor.id)
        )
        if profile is None:
            raise ValidationError("Профиль эксперта не найден")
        caption = (
            f"<b>{safe_html_limit(profile.display_name, 160)}</b>\n"
            f"<i>{safe_html_limit(profile.specialization, 160)}</i>\n\n"
            f"<b>Кратко:</b> {safe_html_limit(profile.short_bio, 220)}\n\n"
            f"{safe_html_limit(profile.bio, 300)}\n\n"
            f"Публикация: "
            f"{'включена' if profile.is_published else 'выключена'}"
        )
        await context.show_photo(
            event,
            profile.photo_file_id,
            caption,
            keyboard(
                [
                    [("✏️ Редактировать", "expert:profile_edit")],
                    [("⬅️ Главное меню", "menu")],
                ]
            ),
        )

    @router.callback_query(F.data == "expert:dialogs")
    async def expert_dialogs(callback: CallbackQuery, bot: Bot) -> None:
        bot_user = await bot.get_me()
        async with context.session_factory() as session:
            actor = await context.user(session, callback.from_user.id)
            profile_id = await session.scalar(
                select(ExpertProfile.id).where(ExpertProfile.user_id == actor.id)
            )
            rows_data = (
                await session.execute(
                    select(Conversation, User, ConversationTopic)
                    .join(User, User.id == Conversation.client_id)
                    .outerjoin(
                        ConversationTopic,
                        (ConversationTopic.conversation_id == Conversation.id)
                        & (ConversationTopic.side == ConversationTopicSide.EXPERT),
                    )
                    .where(Conversation.expert_id == profile_id)
                    .order_by(Conversation.updated_at.desc())
                    .limit(30)
                )
            ).all()
            rows: list[list[InlineKeyboardButton]] = []
            for conversation, client, topic in rows_data:
                name = client.full_name or client.telegram_name
                if (
                    topic is not None
                    and topic.status is ConversationTopicStatus.READY
                    and topic.message_thread_id is not None
                ):
                    rows.append(
                        [
                            InlineKeyboardButton(
                                text=f"💬 {name}",
                                url=(f"https://t.me/{bot_user.username}/{topic.message_thread_id}"),
                            )
                        ]
                    )
                else:
                    rows.append(
                        [
                            InlineKeyboardButton(
                                text=f"🔄 Создать тему: {name}",
                                callback_data=f"expert:conversation:{conversation.id}",
                            )
                        ]
                    )
            if not rows:
                text = "<b>Диалоги</b>\n\nДиалогов пока нет."
            else:
                text = "<b>Диалоги</b>\n\nВыберите клиента:"
            rows.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu")])
            await context.show_screen(
                callback, "consultations", text, InlineKeyboardMarkup(inline_keyboard=rows)
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("expert:conversation:"))
    async def expert_show_topic(callback: CallbackQuery, bot: Bot) -> None:
        conversation_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            actor = await context.user(session, callback.from_user.id)
            profile_id = await session.scalar(
                select(ExpertProfile.id).where(ExpertProfile.user_id == actor.id)
            )
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None or conversation.expert_id != profile_id:
                raise ValidationError("Диалог не найден")
            topic = await session.scalar(
                select(ConversationTopic).where(
                    ConversationTopic.conversation_id == conversation.id,
                    ConversationTopic.side == ConversationTopicSide.EXPERT,
                )
            )
            if (
                topic is not None
                and topic.status is ConversationTopicStatus.READY
                and topic.message_thread_id is not None
            ):
                await send_notification(
                    bot,
                    chat_id=topic.telegram_chat_id,
                    message_thread_id=topic.message_thread_id,
                    text="📌 Продолжайте диалог в этой теме.",
                )
                await callback.answer("Тема поднята в списке", show_alert=True)
                return
        from src.infrastructure.taskiq.tasks import (  # noqa: PLC0415
            provision_conversation_topics_task,
        )

        await provision_conversation_topics_task.kiq(conversation_id)
        await callback.answer("Создание темы поставлено в очередь", show_alert=True)

    @router.callback_query(F.data == "expert:tariffs")
    async def expert_own_tariffs(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        async with context.session_factory() as session:
            actor = await context.user(session, callback.from_user.id)
            result = await session.execute(
                select(ExpertTariff, Tariff)
                .join(ExpertProfile, ExpertProfile.id == ExpertTariff.expert_id)
                .join(Tariff, Tariff.id == ExpertTariff.tariff_id)
                .where(ExpertProfile.user_id == actor.id, Tariff.is_archived.is_(False))
            )
            rows_data = result.all()
            rows = [
                [
                    (
                        f"{'🟢' if item.is_active else '🔴'} {tariff.name}: "
                        f"{(item.custom_price or tariff.default_price):.0f} ₽",
                        f"expert:tariff:{item.id}",
                    )
                ]
                for item, tariff in rows_data
            ]
            rows.append([("⬅️ Главное меню", "menu")])
            await context.show_screen(
                callback,
                "tariffs",
                "<b>Мои тарифы</b>\n\nВыберите тариф, чтобы установить свою цену.",
                keyboard(rows),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("expert:tariff:"))
    async def expert_tariff_view(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        item_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            actor = await context.user(session, callback.from_user.id)
            row = await session.execute(
                select(ExpertTariff, Tariff)
                .join(ExpertProfile, ExpertProfile.id == ExpertTariff.expert_id)
                .join(Tariff, Tariff.id == ExpertTariff.tariff_id)
                .where(ExpertTariff.id == item_id, ExpertProfile.user_id == actor.id)
            )
            data = row.one_or_none()
            if data is None:
                raise ValidationError("Тариф не найден")
            expert_tariff, tariff = data
            price = expert_tariff.custom_price or tariff.default_price
            await context.show(
                callback,
                f"<b>{safe_html(tariff.name)}</b>\n\n"
                f"Цена для клиентов: <b>{price:.2f} ₽</b>\n"
                f"Собственная цена: {expert_tariff.custom_price or 'не задана'}\n"
                f"Статус: {'включен' if expert_tariff.is_active else 'выключен'}",
                keyboard(
                    [
                        [("💰 Изменить цену", f"expert:price:{item_id}")],
                        [("🔁 Включить/выключить", f"expert:tariff_toggle:{item_id}")],
                        [("⬅️ Назад", "expert:tariffs")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("expert:tariff_toggle:"))
    async def expert_tariff_toggle(callback: CallbackQuery) -> None:
        item_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            actor = await context.user(session, callback.from_user.id)
            row = await session.scalar(
                select(ExpertTariff)
                .join(ExpertProfile, ExpertProfile.id == ExpertTariff.expert_id)
                .where(ExpertTariff.id == item_id, ExpertProfile.user_id == actor.id)
                .with_for_update()
            )
            if row is None:
                raise ValidationError("Тариф не найден")
            row.is_active = not row.is_active
            await session.commit()
        await context.show(
            callback,
            "Статус тарифа изменен.",
            keyboard([[("⬅️ К тарифу", f"expert:tariff:{item_id}")]]),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("expert:price:"))
    async def expert_price_start(callback: CallbackQuery, state: FSMContext) -> None:
        item_id = int((callback.data or "").rsplit(":", 1)[1])
        await state.clear()
        await state.update_data(expert_tariff_id=item_id)
        await state.set_state(ExpertPriceForm.price)
        await context.show(
            callback,
            "Введите новую цену в рублях или `0`, чтобы использовать цену владельца:",
            keyboard([[("⬅️ Назад", f"expert:tariff:{item_id}")]]),
        )
        await callback.answer()

    @router.message(ExpertPriceForm.price, F.text)
    async def expert_price_preview(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        try:
            value = money(Decimal((message.text or "").replace(",", ".")))
        except InvalidOperation as error:
            raise ValidationError("Введите корректную цену") from error
        if value < 0:
            raise ValidationError("Цена не может быть отрицательной")
        await state.update_data(expert_price=str(value))
        data = await state.get_data()
        item_id = int(data["expert_tariff_id"])
        await state.set_state(ExpertPriceForm.confirm)
        price_text = "цена владельца" if value == 0 else f"{value:.2f} ₽"
        await context.show(
            message,
            f"Подтвердите новую цену: <b>{price_text}</b>.",
            keyboard(
                [
                    [("✅ Сохранить", "expert:price_confirm")],
                    [("⬅️ Назад", f"expert:price:{item_id}")],
                    [("❌ Отменить", f"expert:tariff:{item_id}")],
                ]
            ),
        )

    @router.callback_query(ExpertPriceForm.confirm, F.data == "expert:price_confirm")
    async def expert_price_save(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        value = money(Decimal(str(data["expert_price"])))
        item_id = int(data["expert_tariff_id"])
        async with context.session_factory() as session:
            actor = await context.user(session, callback.from_user.id)
            await ExpertService(session).set_custom_price(
                actor,
                item_id,
                None if value == 0 else value,
            )
            await state.clear()
            await context.show(
                callback,
                "Цена сохранена.",
                keyboard([[("⬅️ К тарифу", f"expert:tariff:{item_id}")]]),
            )
        await callback.answer("Готово")

    @router.callback_query(F.data == "expert:stats")
    async def expert_stats(callback: CallbackQuery) -> None:
        async with context.session_factory() as session:
            actor = await context.user(session, callback.from_user.id)
            profile = await session.scalar(
                select(ExpertProfile).where(ExpertProfile.user_id == actor.id)
            )
            if profile is None:
                raise ValidationError("Профиль эксперта не найден")
            active = await session.scalar(
                select(func.count(ClientExpertSubscription.id)).where(
                    ClientExpertSubscription.expert_id == profile.id,
                    ClientExpertSubscription.status == SubscriptionStatus.ACTIVE,
                    ClientExpertSubscription.expires_at > func.now(),
                )
            )
            income = await session.scalar(
                select(func.coalesce(func.sum(PaymentTransaction.expert_income_amount), 0)).where(
                    PaymentTransaction.expert_id == profile.id,
                    PaymentTransaction.status == TransactionStatus.COMPLETED,
                )
            )
            balance = await FinanceService(session).available_balance(profile.id)
            service_fees = await session.scalar(
                select(
                    func.coalesce(func.sum(PaymentTransaction.service_commission_amount), 0)
                ).where(
                    PaymentTransaction.expert_id == profile.id,
                    PaymentTransaction.status == TransactionStatus.COMPLETED,
                )
            )
            period_income: dict[str, Decimal] = {}
            for label, delta in {
                "день": timedelta(days=1),
                "неделя": timedelta(days=7),
                "месяц": timedelta(days=30),
            }.items():
                amount = await session.scalar(
                    select(
                        func.coalesce(func.sum(PaymentTransaction.expert_income_amount), 0)
                    ).where(
                        PaymentTransaction.expert_id == profile.id,
                        PaymentTransaction.status == TransactionStatus.COMPLETED,
                        PaymentTransaction.completed_at >= utcnow() - delta,
                    )
                )
                period_income[label] = Decimal(amount or 0)
            paid = await session.scalar(
                select(func.coalesce(func.sum(WithdrawalRequest.amount), 0)).where(
                    WithdrawalRequest.expert_id == profile.id,
                    WithdrawalRequest.status == WithdrawalStatus.PAID,
                )
            )
            pending_withdrawals = await session.scalar(
                select(func.coalesce(func.sum(WithdrawalRequest.amount), 0)).where(
                    WithdrawalRequest.expert_id == profile.id,
                    WithdrawalRequest.status.in_(
                        [WithdrawalStatus.PENDING, WithdrawalStatus.APPROVED]
                    ),
                )
            )
            await context.show_screen(
                callback,
                "finance",
                "<b>📊 Статистика эксперта</b>\n\n<blockquote>"
                f"• <b>Активные подписки</b>: {active or 0}\n"
                f"• <b>Комиссия сервиса</b>: {Decimal(service_fees or 0):.2f} ₽\n"
                f"• <b>Заработано</b>: {Decimal(income or 0):.2f} ₽\n"
                f"• <b>За день</b>: {period_income['день']:.2f} ₽\n"
                f"• <b>За неделю</b>: {period_income['неделя']:.2f} ₽\n"
                f"• <b>За месяц</b>: {period_income['месяц']:.2f} ₽\n"
                f"• <b>Выплачено</b>: {Decimal(paid or 0):.2f} ₽\n"
                f"• <b>В заявках</b>: {Decimal(pending_withdrawals or 0):.2f} ₽\n"
                f"• <b>Доступно к выводу</b>: {balance:.2f} ₽"
                "</blockquote>",
                back_menu(),
            )
        await callback.answer()

    @router.callback_query(F.data == "expert:balance")
    async def expert_balance(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        async with context.session_factory() as session:
            actor = await context.user(session, callback.from_user.id)
            profile_id = await session.scalar(
                select(ExpertProfile.id).where(ExpertProfile.user_id == actor.id)
            )
            if profile_id is None:
                raise ValidationError("Профиль эксперта не найден")
            balance = await FinanceService(session).available_balance(profile_id)
            await context.show_screen(
                callback,
                "finance",
                f"<b>💰 Баланс</b>\n\nДоступно к выводу: <b>{balance:.2f} ₽</b>",
                keyboard(
                    [
                        [("🏦 Вывести средства", "expert:withdraw")],
                        [("⬅️ Главное меню", "menu")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data == "expert:profile")
    async def expert_profile(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        async with context.session_factory() as session:
            actor = await context.user(session, callback.from_user.id)
            await render_expert_profile(callback, session, actor)
        await callback.answer()

    @router.callback_query(F.data == "expert:profile_edit")
    async def expert_profile_edit(callback: CallbackQuery) -> None:
        await context.show(
            callback,
            "<b>Редактирование профиля</b>\n\nЧто изменить?",
            keyboard(
                [
                    [("👤 Имя и фамилия", "expert:profile_field:display_name")],
                    [("🩺 Специализация", "expert:profile_field:specialization")],
                    [("📝 Краткое описание", "expert:profile_field:short_bio")],
                    [("📄 Полное описание", "expert:profile_field:bio")],
                    [("📷 Фото", "expert:profile_field:photo")],
                    [("👁 Включить/выключить публикацию", "expert:profile_publish")],
                    [("⬅️ К профилю", "expert:profile")],
                ]
            ),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("expert:profile_field:"))
    async def expert_profile_field(callback: CallbackQuery, state: FSMContext) -> None:
        field = (callback.data or "").rsplit(":", 1)[1]
        prompts = {
            "display_name": "Введите имя и фамилию:",
            "specialization": "Введите специализацию:",
            "short_bio": "Введите краткое описание (до 500 символов):",
            "bio": "Введите полное описание:",
            "photo": "Отправьте новое фото:",
        }
        if field not in prompts:
            raise ValidationError("Неизвестное поле")
        await state.clear()
        await state.update_data(profile_field=field)
        await state.set_state(ExpertProfileEdit.value)
        await context.show(
            callback,
            prompts[field],
            keyboard([[("⬅️ Назад", "expert:profile_edit")]]),
        )
        await callback.answer()

    @router.message(ExpertProfileEdit.value, F.photo)
    async def expert_profile_photo(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        photos = message.photo
        if not photos:
            raise ValidationError("Отправьте фото")
        data = await state.get_data()
        if data.get("profile_field") != "photo":
            raise ValidationError("Ожидается текстовое значение")
        file_id = photos[-1].file_id[:512]
        await state.update_data(profile_value=file_id)
        await state.set_state(ExpertProfileEdit.confirm)
        await context.show_photo(
            message,
            file_id,
            "Сохранить это фото в профиле?",
            keyboard(
                [
                    [("✅ Сохранить", "expert:profile_save")],
                    [("⬅️ Назад", "expert:profile_field:photo")],
                    [("❌ Отменить", "expert:profile")],
                ]
            ),
        )

    @router.message(ExpertProfileEdit.value, F.text)
    async def expert_profile_value(message: Message, state: FSMContext) -> None:
        if message.from_user is None:
            return
        data = await state.get_data()
        field = str(data.get("profile_field", ""))
        if field == "photo":
            raise ValidationError("Отправьте фото, а не текст")
        validators = {
            "display_name": lambda value: validate_full_name(value),
            "specialization": lambda value: validate_text(value, maximum=160),
            "short_bio": lambda value: validate_text(value, maximum=500),
            "bio": lambda value: validate_text(value, maximum=4000),
        }
        validator = validators.get(field)
        if validator is None:
            raise ValidationError("Неизвестное поле")
        value = validator(message.text or "")
        await state.update_data(profile_value=value)
        await state.set_state(ExpertProfileEdit.confirm)
        await context.show(
            message,
            f"<b>Сохранить изменение?</b>\n\n{safe_html(value)}",
            keyboard(
                [
                    [("✅ Сохранить", "expert:profile_save")],
                    [("⬅️ Назад", f"expert:profile_field:{field}")],
                    [("❌ Отменить", "expert:profile")],
                ]
            ),
        )

    @router.callback_query(ExpertProfileEdit.confirm, F.data == "expert:profile_save")
    async def expert_profile_save(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        field = str(data.get("profile_field", ""))
        if field not in {"display_name", "specialization", "short_bio", "bio", "photo"}:
            raise ValidationError("Неизвестное поле")
        value = str(data.get("profile_value", ""))
        async with context.session_factory() as session:
            actor = await context.user(session, callback.from_user.id)
            profile = await session.scalar(
                select(ExpertProfile).where(ExpertProfile.user_id == actor.id)
            )
            if profile is None:
                raise ValidationError("Профиль эксперта не найден")
            target_field = "photo_file_id" if field == "photo" else field
            setattr(profile, target_field, value)
            if field == "display_name":
                actor.full_name = value
            await session.commit()
            await state.clear()
            await render_expert_profile(callback, session, actor)
        await callback.answer("Профиль обновлён")

    @router.callback_query(F.data == "expert:profile_publish")
    async def expert_profile_publish(callback: CallbackQuery) -> None:
        async with context.session_factory() as session:
            actor = await context.user(session, callback.from_user.id)
            profile = await session.scalar(
                select(ExpertProfile).where(ExpertProfile.user_id == actor.id)
            )
            if profile is None:
                raise ValidationError("Профиль эксперта не найден")
            action = "выключить публикацию" if profile.is_published else "включить публикацию"
            await context.show(
                callback,
                f"Подтвердите: <b>{action}</b> профиля?",
                keyboard(
                    [
                        [("✅ Подтвердить", "expert:profile_publish_apply")],
                        [("⬅️ Назад", "expert:profile_edit")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data == "expert:profile_publish_apply")
    async def expert_profile_publish_apply(callback: CallbackQuery) -> None:
        async with context.session_factory() as session:
            actor = await context.user(session, callback.from_user.id)
            profile = await session.scalar(
                select(ExpertProfile).where(ExpertProfile.user_id == actor.id).with_for_update()
            )
            if profile is None:
                raise ValidationError("Профиль эксперта не найден")
            profile.is_published = not profile.is_published
            await session.commit()
            await render_expert_profile(callback, session, actor)
        await callback.answer("Статус публикации изменён")

    @router.callback_query(F.data == "expert:withdraw")
    async def withdrawal_start(callback: CallbackQuery, state: FSMContext) -> None:
        async with context.session_factory() as session:
            actor = await context.user(session, callback.from_user.id)
            profile_id = await session.scalar(
                select(ExpertProfile.id).where(ExpertProfile.user_id == actor.id)
            )
            if profile_id is None:
                raise ValidationError("Профиль эксперта не найден")
            balance = await FinanceService(session).available_balance(profile_id)
        await state.clear()
        await state.update_data(amount=str(balance))
        await state.set_state(WithdrawalForm.bank)
        await context.show(
            callback,
            f"Доступно к выводу: <b>{balance:.2f} ₽</b>\n\nВведите название банка:",
            keyboard([[("⬅️ Назад", "expert:balance")]]),
        )
        await callback.answer()

    @router.message(WithdrawalForm.bank, F.text)
    async def withdrawal_bank(message: Message, state: FSMContext) -> None:
        bank = validate_text(message.text or "", maximum=120)
        await state.update_data(bank=bank)
        data = await state.get_data()
        await state.set_state(WithdrawalForm.confirm)
        await context.show(
            message,
            "<b>Подтвердите вывод</b>\n\n"
            f"• Сумма: <b>{Decimal(data['amount']):.2f} ₽</b>\n"
            f"• Банк: <b>{safe_html(bank)}</b>",
            keyboard(
                [
                    [("✅ Создать заявку", "withdraw:confirm")],
                    [("⬅️ Назад", "expert:withdraw")],
                    [("❌ Отменить", "menu")],
                ]
            ),
        )

    @router.callback_query(WithdrawalForm.confirm, F.data == "withdraw:confirm")
    async def withdrawal_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        data = await state.get_data()
        async with context.session_factory() as session:
            actor = await context.user(session, callback.from_user.id)
            request = await FinanceService(session).create_withdrawal(actor, str(data["bank"]))
            expert = await session.get(ExpertProfile, request.expert_id)
            if expert and await notification_enabled(session, "withdrawal"):
                await send_notification(
                    bot,
                    context.config.bot_owner_id,
                    events.withdrawal_requested(request, expert, actor),
                    reply_markup=keyboard(
                        [
                            [("✅ Подтвердить выплату", f"owner:withdraw_ok:{request.id}")],
                            [("❌ Отклонить", f"owner:withdraw_no:{request.id}")],
                        ]
                    ),
                )
        await state.clear()
        await context.show(callback, "Заявка создана. Средства зарезервированы.", back_menu())
        await callback.answer()

    async def owner_user(session: AsyncSession, telegram_id: int) -> User:
        actor = await context.user(session, telegram_id)
        if actor.role is not Role.OWNER:
            raise ValidationError("Доступно только владельцу")
        return actor

    return router
