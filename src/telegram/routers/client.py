from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from urllib.parse import quote

import qrcode  # type: ignore[import-untyped]
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select

from src.application.services import ExpertService, PaymentService, notification_enabled
from src.application.validation import safe_html, validate_text
from src.core.enums import (
    PaymentProvider,
    ReferralAccrual,
    ReferralRewardKind,
    Role,
    TransactionStatus,
)
from src.core.exceptions import DomainError, ValidationError
from src.infrastructure.database.models import (
    ClientExpertSubscription,
    ExpertProfile,
    ExpertTariff,
    PaymentTransaction,
    ReferralReward,
    Tariff,
    User,
)
from src.infrastructure.database.models import Settings as DatabaseSettings
from src.infrastructure.telegram_notifications import notification_keyboard
from src.telegram import events
from src.telegram.routers.context import FeatureRouterContext
from src.telegram.routers.helpers import back_menu, keyboard, safe_html_limit
from src.telegram.states import ExpertApplicationForm


def build_client_router(context: FeatureRouterContext) -> Router:
    router = Router(name="build_client_router")

    @router.callback_query(F.data == "client:referral")
    async def referral(callback: CallbackQuery, bot: Bot) -> None:
        async with context.session_factory() as session:
            user = await context.user(session, callback.from_user.id)
            me = await bot.get_me()
            link = f"https://t.me/{me.username}?start=ref_{user.referral_code}"
            count = await session.scalar(
                select(func.count(User.id)).where(User.referrer_id == user.id)
            )
            paid = await session.scalar(
                select(func.count(func.distinct(PaymentTransaction.client_id)))
                .join(User, User.id == PaymentTransaction.client_id)
                .where(
                    User.referrer_id == user.id,
                    PaymentTransaction.status == TransactionStatus.COMPLETED,
                )
            )
            earned = await session.scalar(
                select(func.coalesce(func.sum(ReferralReward.amount), 0)).where(
                    ReferralReward.referrer_id == user.id,
                    ReferralReward.reversed_at.is_(None),
                )
            )
            settings = await session.get(DatabaseSettings, 1)
            accrual = (
                "за первую оплату"
                if settings is None or settings.referral_accrual is ReferralAccrual.FIRST_PAYMENT
                else "за каждую оплату"
            )
            kind = (
                "%"
                if settings is None or settings.referral_reward_kind is ReferralRewardKind.PERCENT
                else " ₽"
            )
            level_1 = settings.referral_level_1 if settings else Decimal("5")
            terms = f"{level_1:g}{kind} {accrual} приглашённого"
            if settings and settings.referral_levels == 2:
                terms += f"; 2 уровень: {settings.referral_level_2:g}{kind}"
            share_url = (
                "https://t.me/share/url?url="
                f"{quote(link, safe='')}&text={quote('Присоединяйтесь к DoctorShop', safe='')}"
            )
            await context.show_screen(
                callback,
                "referral",
                "<b>👥 Реферальная программа</b>\n\n"
                f"Ваша ссылка: <code>{safe_html(link)}</code>\n"
                f"Приглашено: <b>{count or 0}</b>\n"
                f"Совершили оплату: <b>{paid or 0}</b>\n"
                f"Заработано: <b>{Decimal(earned or 0):.2f} ₽</b>\n"
                f"Бонусный баланс: <b>{user.bonus_balance:.2f} ₽</b>\n\n"
                f"<i>{safe_html(terms)}</i>",
                InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="📋 Скопировать ссылку",
                                copy_text=CopyTextButton(text=link),
                            )
                        ],
                        [InlineKeyboardButton(text="📤 Поделиться", url=share_url)],
                        [
                            InlineKeyboardButton(
                                text="🔳 QR-код", callback_data="client:referral_qr"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="👥 Мои рефералы",
                                callback_data="client:referrals:0",
                            )
                        ],
                        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data == "client:referral_qr")
    async def referral_qr(callback: CallbackQuery, bot: Bot) -> None:
        async with context.session_factory() as session:
            user = await context.user(session, callback.from_user.id)
            me = await bot.get_me()
            link = f"https://t.me/{me.username}?start=ref_{user.referral_code}"
            buffer = BytesIO()
            qrcode.make(link).save(buffer, format="PNG")
            await context.show_photo(
                callback,
                BufferedInputFile(buffer.getvalue(), filename="referral-qr.png"),
                f"<b>Ваша реферальная ссылка</b>\n\n<code>{safe_html(link)}</code>",
                keyboard([[("⬅️ Назад", "client:referral")]]),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("client:referrals:"))
    async def referral_list(callback: CallbackQuery) -> None:
        page = max(0, int((callback.data or "").rsplit(":", 1)[1]))
        per_page = 8
        async with context.session_factory() as session:
            user = await context.user(session, callback.from_user.id)
            total = int(
                await session.scalar(select(func.count(User.id)).where(User.referrer_id == user.id))
                or 0
            )
            referrals = list(
                await session.scalars(
                    select(User)
                    .where(User.referrer_id == user.id)
                    .order_by(User.created_at.desc())
                    .offset(page * per_page)
                    .limit(per_page)
                )
            )
            lines = [f"<b>👥 Мои рефералы</b>\n\nВсего: <b>{total}</b>"]
            for item in referrals:
                paid = await session.scalar(
                    select(PaymentTransaction.id).where(
                        PaymentTransaction.client_id == item.id,
                        PaymentTransaction.status == TransactionStatus.COMPLETED,
                    )
                )
                lines.append(
                    f"\n• {safe_html(item.full_name or item.telegram_name)} "
                    f"— {'✅ оплатил' if paid else '⏳ без оплаты'}"
                )
            buttons: list[list[InlineKeyboardButton]] = []
            navigation: list[InlineKeyboardButton] = []
            if page > 0:
                navigation.append(
                    InlineKeyboardButton(text="⬅️", callback_data=f"client:referrals:{page - 1}")
                )
            if (page + 1) * per_page < total:
                navigation.append(
                    InlineKeyboardButton(text="➡️", callback_data=f"client:referrals:{page + 1}")
                )
            if navigation:
                buttons.append(navigation)
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="⬅️ К реферальной программе", callback_data="client:referral"
                    )
                ]
            )
            await context.show(
                callback,
                "".join(lines)
                if referrals
                else "<b>👥 Мои рефералы</b>\n\nВы пока никого не пригласили.",
                InlineKeyboardMarkup(inline_keyboard=buttons),
            )
        await callback.answer()

    @router.callback_query(F.data == "client:application")
    async def application_start(callback: CallbackQuery, state: FSMContext) -> None:
        async with context.session_factory() as session:
            user = await context.user(session, callback.from_user.id)
            if user.role is not Role.CLIENT:
                raise ValidationError("Заявка доступна только клиенту")
            if not user.full_name:
                raise ValidationError("Сначала заполните ФИО при регистрации")
        await state.clear()
        await state.update_data(full_name=user.full_name)
        await state.set_state(ExpertApplicationForm.specialization)
        await context.show(callback, "Введите специализацию:", keyboard([[("⬅️ Назад", "menu")]]))
        await callback.answer()

    @router.message(ExpertApplicationForm.specialization, F.text)
    async def application_specialization(message: Message, state: FSMContext) -> None:
        await state.update_data(specialization=validate_text(message.text or "", maximum=160))
        await state.set_state(ExpertApplicationForm.short_bio)
        await context.show(
            message,
            "Кратко опишите опыт, до 500 символов:",
            keyboard([[("⬅️ Назад", "application:back_specialization")]]),
        )

    @router.message(ExpertApplicationForm.short_bio, F.text)
    async def application_short_bio(message: Message, state: FSMContext) -> None:
        await state.update_data(short_bio=validate_text(message.text or "", maximum=500))
        await state.set_state(ExpertApplicationForm.bio)
        await context.show(
            message,
            "Отправьте полное резюме, до 4000 символов:",
            keyboard([[("⬅️ Назад", "application:back_short_bio")]]),
        )

    @router.message(ExpertApplicationForm.bio, F.text)
    async def application_bio(message: Message, state: FSMContext) -> None:
        await state.update_data(bio=validate_text(message.text or "", maximum=4000))
        await state.set_state(ExpertApplicationForm.photo)
        await context.show(
            message,
            "Отправьте фотографию для карточки эксперта:",
            keyboard([[("⬅️ Назад", "application:back_bio")]]),
        )

    @router.callback_query(F.data == "application:back_specialization")
    async def application_back_specialization(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(ExpertApplicationForm.specialization)
        await context.show(callback, "Введите специализацию:", keyboard([[("⬅️ Назад", "menu")]]))
        await callback.answer()

    @router.callback_query(F.data == "application:back_short_bio")
    async def application_back_short_bio(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(ExpertApplicationForm.short_bio)
        await context.show(
            callback,
            "Кратко опишите опыт, до 500 символов:",
            keyboard([[("⬅️ Назад", "application:back_specialization")]]),
        )
        await callback.answer()

    @router.callback_query(F.data == "application:back_bio")
    async def application_back_bio(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(ExpertApplicationForm.bio)
        await context.show(
            callback,
            "Отправьте полное резюме, до 4000 символов:",
            keyboard([[("⬅️ Назад", "application:back_short_bio")]]),
        )
        await callback.answer()

    @router.callback_query(F.data == "application:back_photo")
    async def application_back_photo(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(ExpertApplicationForm.photo)
        await context.show(
            callback,
            "Отправьте фотографию для карточки эксперта:",
            keyboard([[("⬅️ Назад", "application:back_bio")]]),
        )
        await callback.answer()

    @router.message(ExpertApplicationForm.photo, F.photo)
    async def application_photo(message: Message, state: FSMContext) -> None:
        if not message.photo:
            return
        await state.update_data(photo_file_id=message.photo[-1].file_id)
        data = await state.get_data()
        await state.set_state(ExpertApplicationForm.confirm)
        await context.show_photo(
            message,
            str(data["photo_file_id"]),
            (
                "<b>Проверьте заявку</b>\n\n"
                f"<b>{safe_html_limit(str(data['full_name']), 160)}</b>\n"
                f"{safe_html_limit(str(data['specialization']), 160)}\n\n"
                f"{safe_html_limit(str(data['short_bio']), 220)}\n\n"
                f"{safe_html_limit(str(data['bio']), 300)}"
            ),
            keyboard(
                [
                    [("✅ Отправить", "application:confirm")],
                    [("⬅️ Назад", "application:back_photo")],
                    [("❌ Отменить", "menu")],
                ]
            ),
        )

    @router.callback_query(ExpertApplicationForm.confirm, F.data == "application:confirm")
    async def application_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        data = await state.get_data()
        async with context.session_factory() as session:
            user = await context.user(session, callback.from_user.id)
            application = await ExpertService(session).submit_application(user, **data)
            if await notification_enabled(session, "expert_application"):
                await bot.send_photo(
                    context.config.bot_owner_id,
                    application.photo_file_id,
                    caption=events.expert_application(application, user),
                    reply_markup=notification_keyboard(
                        keyboard(
                            [
                                [("✅ Одобрить", f"owner:app_ok:{application.id}")],
                                [("❌ Отклонить", f"owner:app_no:{application.id}")],
                                [("👤 Открыть пользователя", f"owner:user:{user.id}")],
                            ]
                        )
                    ),
                )
        await state.clear()
        await context.show(callback, "Заявка отправлена владельцу.", back_menu())
        await callback.answer()

    @router.callback_query(F.data == "client:experts")
    async def experts(callback: CallbackQuery) -> None:
        async with context.session_factory() as session:
            rows = list(
                await session.scalars(
                    select(ExpertProfile)
                    .where(ExpertProfile.is_published.is_(True))
                    .order_by(ExpertProfile.sort_order, ExpertProfile.display_name)
                )
            )
            buttons = [[(item.display_name, f"client:expert:{item.id}")] for item in rows]
            buttons.append([("⬅️ Главное меню", "menu")])
            text = "<b>Выберите эксперта</b>" if rows else "Сейчас нет доступных экспертов."
            await context.show_screen(callback, "experts", text, keyboard(buttons))
        await callback.answer()

    @router.callback_query(F.data.startswith("client:expert:"))
    async def expert_card(callback: CallbackQuery) -> None:
        expert_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            expert = await session.get(ExpertProfile, expert_id)
            if expert is None or not expert.is_published:
                raise ValidationError("Эксперт недоступен")
            caption = (
                f"<b>{safe_html_limit(expert.display_name, 160)}</b>\n"
                f"<i>{safe_html_limit(expert.specialization, 160)}</i>\n\n"
                f"{safe_html_limit(expert.bio, 600)}"
            )
            markup = keyboard(
                [
                    [("✅ Подтвердить выбор", f"client:tariffs:{expert.id}")],
                    [("⬅️ Назад", "client:experts")],
                ]
            )
            await context.show_photo(callback, expert.photo_file_id, caption, markup)
        await callback.answer()

    @router.callback_query(F.data.startswith("client:tariffs:"))
    async def expert_tariffs(callback: CallbackQuery) -> None:
        expert_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            result = await session.execute(
                select(ExpertTariff, Tariff)
                .join(Tariff, Tariff.id == ExpertTariff.tariff_id)
                .where(
                    ExpertTariff.expert_id == expert_id,
                    ExpertTariff.is_active.is_(True),
                    Tariff.is_active.is_(True),
                    Tariff.is_archived.is_(False),
                )
                .order_by(Tariff.sort_order)
            )
            rows = result.all()
            buttons = [
                [
                    (
                        f"{tariff.name} | "
                        f"{(expert_tariff.custom_price or tariff.default_price):.0f} ₽",
                        f"client:tariff:{expert_tariff.id}",
                    )
                ]
                for expert_tariff, tariff in rows
            ]
            buttons.append([("⬅️ Назад", f"client:expert:{expert_id}")])
            await context.show_screen(
                callback,
                "tariffs",
                "💎 <b>Выбор тарифа</b>\n\n"
                "Выберите подходящий пакет подписки для доступа к эксперту:",
                keyboard(buttons),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("client:tariff:"))
    async def tariff_details(callback: CallbackQuery) -> None:
        expert_tariff_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            row = await session.execute(
                select(ExpertTariff, Tariff, ExpertProfile)
                .join(Tariff, Tariff.id == ExpertTariff.tariff_id)
                .join(ExpertProfile, ExpertProfile.id == ExpertTariff.expert_id)
                .where(ExpertTariff.id == expert_tariff_id)
            )
            item = row.one_or_none()
            if item is None:
                raise ValidationError("Тариф не найден")
            expert_tariff, tariff, expert = item
            price = expert_tariff.custom_price or tariff.default_price
            limit = "∞" if tariff.message_limit is None else str(tariff.message_limit)
            await context.show_screen(
                callback,
                "tariff",
                f"<b>{safe_html(tariff.name)} | {price:.0f} ₽:</b>\n"
                "<blockquote>"
                f"• <b>Лимит сообщений</b>: {limit}\n"
                f"• <b>Длительность</b>: {tariff.duration_days} дней\n"
                f"• <b>Стоимость</b>: {price:.0f} ₽\n"
                f"• <b>Эксперт</b>: {safe_html(expert.display_name)}"
                "</blockquote>",
                keyboard(
                    [
                        [("💳 Оплатить через СБП", f"client:pay:{expert_tariff.id}")],
                        [("⬅️ Назад", f"client:tariffs:{expert.id}")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("client:pay:"))
    async def create_payment(callback: CallbackQuery) -> None:
        expert_tariff_id = int((callback.data or "").rsplit(":", 1)[1])
        async with context.session_factory() as session:
            user = await context.user(session, callback.from_user.id)
            try:
                gateway = context.gateways.get(PaymentProvider.PLATEGA)
            except DomainError:
                gateway = context.gateways.get(PaymentProvider.FAKE)
            checkout = await PaymentService(session).create_checkout(
                user, expert_tariff_id=expert_tariff_id, gateway=gateway
            )
            await context.show(
                callback,
                "<b>Счет создан</b>\n\n"
                f"Сумма: <b>{checkout.amount:.2f} ₽</b>\n"
                "Подписка активируется автоматически после подтвержденного webhook.",
                InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="💳 Перейти к оплате", url=checkout.url)],
                        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu")],
                    ]
                ),
            )
        await callback.answer()

    @router.callback_query(F.data == "client:subscriptions")
    async def client_subscriptions(callback: CallbackQuery) -> None:
        async with context.session_factory() as session:
            user = await context.user(session, callback.from_user.id)
            result = await session.execute(
                select(ClientExpertSubscription, ExpertProfile)
                .join(ExpertProfile, ExpertProfile.id == ClientExpertSubscription.expert_id)
                .where(ClientExpertSubscription.client_id == user.id)
                .order_by(ClientExpertSubscription.created_at.desc())
            )
            rows = result.all()
            if not rows:
                text = "<b>Мои подписки</b>\n\nУ вас пока нет подписок."
            else:
                lines = ["<b>Мои подписки</b>"]
                for subscription, expert in rows[:20]:
                    expires = (
                        subscription.expires_at.strftime("%d.%m.%Y %H:%M")
                        if subscription.expires_at
                        else "ожидает оплаты"
                    )
                    lines.append(
                        "\n<blockquote>"
                        f"• <b>Эксперт</b>: {safe_html(expert.display_name)}\n"
                        f"• <b>Статус</b>: {subscription.status.value}\n"
                        f"• <b>До</b>: {expires}\n"
                        f"• <b>Сообщений</b>: {subscription.messages_used}/"
                        f"{subscription.message_limit or '∞'}"
                        "</blockquote>"
                    )
                text = "".join(lines)
            await context.show_screen(callback, "subscriptions", text, back_menu())
        await callback.answer()

    return router
