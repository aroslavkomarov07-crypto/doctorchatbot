from __future__ import annotations

from typing import Any
from urllib.parse import quote

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter, Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.dto import IncomingTopicMessage
from src.application.services import (
    UserService,
    notification_enabled,
)
from src.application.topics import TopicRelayService
from src.application.validation import safe_html, validate_full_name
from src.core.config import Settings as AppSettings
from src.core.enums import (
    ConversationTopicStatus,
    Role,
)
from src.core.exceptions import DomainError, ValidationError
from src.infrastructure.database.models import (
    ConversationTopic,
    User,
)
from src.infrastructure.error_reporting import ErrorReporter
from src.infrastructure.payments import GatewayRegistry
from src.infrastructure.telegram_notifications import send_notification
from src.infrastructure.telegram_topics import AiogramTopicGateway
from src.telegram import events
from src.telegram.banners import BannerCatalog
from src.telegram.main_menu import show_main as _show_main
from src.telegram.routers.client import build_client_router
from src.telegram.routers.consultations import build_consultations_router
from src.telegram.routers.context import FeatureRouterContext
from src.telegram.routers.expert import build_expert_router
from src.telegram.routers.notifications import build_notifications_router
from src.telegram.routers.owner.commerce import build_owner_commerce_router
from src.telegram.routers.owner.context import OwnerRouterContext
from src.telegram.routers.owner.dashboard import build_owner_dashboard_router
from src.telegram.routers.owner.referrals import build_owner_referrals_router
from src.telegram.routers.owner.settings import build_owner_settings_router
from src.telegram.routers.owner.users import build_owner_users_router
from src.telegram.states import (
    Registration,
)
from src.telegram.topic_menus import TopicMenuRefresher
from src.telegram.ui import ContextMenu

SUPPORTED_TOPIC_CONTENT = {
    "text",
    "photo",
    "document",
    "voice",
    "video",
    "audio",
    "animation",
    "video_note",
    "sticker",
}


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


class ConsultationTopicMessageFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        if (
            message.chat.type != ChatType.PRIVATE
            or message.is_topic_message is not True
            or message.message_thread_id is None
            or message.from_user is None
            or message.from_user.is_bot
        ):
            return False
        if message.text and message.text.startswith("/"):
            return False
        return message.content_type in SUPPORTED_TOPIC_CONTENT


def _topic_input(message: Message) -> IncomingTopicMessage:
    content_type = message.content_type
    file_id: str | None = None
    if message.photo:
        file_id = message.photo[-1].file_id
    else:
        media = getattr(message, content_type, None)
        file_id = getattr(media, "file_id", None)
    return IncomingTopicMessage(
        chat_id=message.chat.id,
        thread_id=message.message_thread_id or 0,
        telegram_message_id=message.message_id,
        sender_telegram_id=message.from_user.id if message.from_user else 0,
        content_type=content_type,
        text=message.text or message.caption or "",
        file_id=file_id,
        reply_to_telegram_message_id=(
            message.reply_to_message.message_id if message.reply_to_message else None
        ),
    )


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows
        ]
    )


def back_menu() -> InlineKeyboardMarkup:
    return keyboard([[("⬅️ Главное меню", "menu")]])


def admin_back() -> InlineKeyboardMarkup:
    return keyboard([[("⬅️ В панель управления", "owner:panel")]])


async def _user(session: AsyncSession, telegram_id: int) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        raise ValidationError("Пользователь не зарегистрирован. Нажмите /start")
    if user.is_blocked:
        raise ValidationError("Ваш аккаунт заблокирован")
    return user


async def _is_managed_topic(session: AsyncSession, message: Message) -> bool:
    if message.message_thread_id is None:
        return False
    return bool(
        await session.scalar(
            select(ConversationTopic.id).where(
                ConversationTopic.telegram_chat_id == message.chat.id,
                ConversationTopic.message_thread_id == message.message_thread_id,
            )
        )
    )


async def _delete_unmanaged_topic(
    message: Message,
    bot: Bot,
    session: AsyncSession,
) -> bool:
    if (
        message.chat.type != ChatType.PRIVATE
        or message.is_topic_message is not True
        or message.message_thread_id is None
        or await _is_managed_topic(session, message)
    ):
        return False
    try:
        await bot.delete_forum_topic(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
        )
    except TelegramAPIError:
        return False
    return True


def build_router(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    config: AppSettings,
    gateways: GatewayRegistry,
    menu: ContextMenu,
    error_reporter: ErrorReporter,
) -> Router:
    router = Router(name="doctor-shop")
    router.include_router(build_notifications_router())
    banners = BannerCatalog(config)
    topic_menus = TopicMenuRefresher(session_factory, menu, banners, error_reporter)

    async def show(
        event: Message | CallbackQuery,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        return await menu.render(event, text, reply_markup)

    async def show_photo(
        event: Message | CallbackQuery,
        photo: str | InputFile,
        caption: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        return await menu.render_photo(event, photo, caption, reply_markup)

    async def show_screen(
        event: Message | CallbackQuery,
        banner_key: str,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        banner = banners.resolve(banner_key)
        if banner is not None and len(text) <= 1024:
            return await show_photo(event, banner, text, reply_markup)
        return await show(event, text, reply_markup)

    @router.error()
    async def on_error(event: Any) -> bool:
        error = getattr(event, "exception", None)
        update = getattr(event, "update", None)
        message = getattr(update, "message", None)
        callback = getattr(update, "callback_query", None)
        if isinstance(error, DomainError):
            if callback:
                await callback.answer(str(error), show_alert=True)
            elif message:
                await show(message, f"⚠️ {safe_html(str(error))}", back_menu())
            return True
        context: dict[str, Any] = {}
        if message and message.from_user:
            context = {
                "telegram_id": message.from_user.id,
                "username": message.from_user.username or "—",
                "chat_id": message.chat.id,
                "message_id": message.message_id,
                "command": message.text or "—",
            }
        elif callback:
            context = {
                "telegram_id": callback.from_user.id,
                "username": callback.from_user.username or "—",
                "callback_data": callback.data or "—",
            }
        event_id = await error_reporter.report(
            error if isinstance(error, BaseException) else RuntimeError("Unknown update error"),
            source="telegram:update",
            context=context,
        )
        try:
            target = callback if callback else message
            if target:
                await show(
                    target,
                    f"⚠️ Произошла внутренняя ошибка. Код: <code>{event_id}</code>",
                    back_menu(),
                )
            if callback:
                await callback.answer()
        except Exception:
            pass
        return True

    @router.message(F.forum_topic_created)
    async def reject_user_created_topic(message: Message, bot: Bot) -> None:
        if message.chat.type != ChatType.PRIVATE or message.message_thread_id is None:
            return
        async with session_factory() as session:
            if await _is_managed_topic(session, message):
                return
            provisioning = await session.scalar(
                select(ConversationTopic.id).where(
                    ConversationTopic.telegram_chat_id == message.chat.id,
                    ConversationTopic.status == ConversationTopicStatus.CREATING,
                )
            )
            if provisioning is not None:
                return
            deleted = await _delete_unmanaged_topic(message, bot, session)
        if deleted:
            await show(
                message,
                "Темы консультаций создаёт только бот после оформления подписки. "
                "Используйте главное меню.",
                back_menu(),
            )

    @router.message(ConsultationTopicMessageFilter())
    async def relay_consultation_topic(message: Message, bot: Bot, state: FSMContext) -> None:
        incoming = _topic_input(message)
        async with session_factory() as session:
            result = await TopicRelayService(
                session,
                AiogramTopicGateway(bot),
            ).relay(incoming)
            actor = await session.scalar(
                select(User).where(User.telegram_id == incoming.sender_telegram_id)
            )

        if result.status == "DELIVERED":
            await topic_menus.after_message(bot, state.storage, actor, result)
            return
        if result.status == "DUPLICATE":
            return
        if result.status in {"TOPIC_PENDING", "DELIVERY_FAILED"} and result.conversation_id:
            from src.infrastructure.taskiq.tasks import (  # noqa: PLC0415
                provision_conversation_topics_task,
            )

            await provision_conversation_topics_task.kiq(result.conversation_id)
            text = (
                "⏳ Вторая сторона темы временно недоступна. "
                "Бот уже запустил восстановление; повторите отправку немного позже."
            )
            markup = back_menu()
        elif result.status == "SUBSCRIPTION_REQUIRED":
            text = (
                "Срок подписки закончился. История останется в этой теме, "
                "но передача новых сообщений остановлена до продления доступа."
            )
            markup = (
                keyboard(
                    [
                        [("💎 Продлить доступ", "client:experts")],
                        [("⬅️ Главное меню", "menu")],
                    ]
                )
                if actor and actor.role in {Role.CLIENT, Role.OWNER}
                else back_menu()
            )
        elif result.status == "MESSAGE_LIMIT":
            text = "Лимит сообщений по текущему тарифу исчерпан."
            markup = back_menu()
        elif result.status == "FORBIDDEN":
            text = "Эта тема недоступна вашему аккаунту."
            markup = back_menu()
        else:
            text = (
                "Эта тема не привязана к консультации. Откройте рабочую тему эксперта "
                "в списке тем Telegram."
            )
            markup = back_menu()
        await send_notification(
            bot,
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text=text,
            reply_markup=markup,
        )
        await topic_menus.after_message(bot, state.storage, actor, result)

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext, bot: Bot) -> None:
        if message.from_user is None:
            return
        await state.clear()
        await menu.clear(message.from_user.id, bot)
        referral = None
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 2 and parts[1].startswith("ref_"):
            referral = parts[1][4:]
        async with session_factory() as session:
            await _delete_unmanaged_topic(message, bot, session)
            service = UserService(session, config)
            user = await service.get_or_create(
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                telegram_name=message.from_user.full_name,
                language=message.from_user.language_code,
                referral=referral,
            )
            if user.registration_completed:
                await _show_main(message, session, user, menu, banners)
                return
        await state.set_state(Registration.full_name)
        await show_screen(
            message,
            "welcome",
            "<b>Регистрация</b>\n\nВведите ваши фамилию, имя и отчество.",
        )

    @router.message(Command("menu"))
    async def show_menu(message: Message, state: FSMContext, bot: Bot) -> None:
        if message.from_user is None:
            return
        await state.clear()
        async with session_factory() as session:
            await _delete_unmanaged_topic(message, bot, session)
            user = await _user(session, message.from_user.id)
            await _show_main(message, session, user, menu, banners)

    @router.message(Registration.full_name, F.text)
    async def registration_name(message: Message, state: FSMContext) -> None:
        name = validate_full_name(message.text or "")
        await state.update_data(full_name=name)
        await state.set_state(Registration.phone)
        await show(
            message,
            "Введите номер телефона:",
            keyboard([[("⬅️ Назад", "registration:back_name")]]),
        )

    @router.callback_query(Registration.phone, F.data == "registration:back_name")
    async def registration_back_name(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(Registration.full_name)
        await show(callback, "Введите ваши фамилию, имя и отчество.")
        await callback.answer()

    @router.message(Registration.phone)
    async def registration_phone(message: Message, state: FSMContext) -> None:
        if message.contact:
            if not message.from_user or message.contact.user_id != message.from_user.id:
                raise ValidationError("Отправьте собственный контакт")
            phone = message.contact.phone_number
        else:
            phone = message.text or ""
        from src.application.validation import normalize_phone  # noqa: PLC0415

        phone = normalize_phone(phone)
        await state.update_data(phone=phone)
        data = await state.get_data()
        await state.set_state(Registration.confirm)
        await show(
            message,
            "<b>Проверьте данные</b>\n\n"
            f"• <b>ФИО</b>: {safe_html(data['full_name'])}\n"
            f"• <b>Телефон</b>: {safe_html(phone)}\n\n"
            "Сохранить эти данные?",
            keyboard(
                [
                    [("✅ Подтвердить", "registration:confirm")],
                    [("✏️ Изменить", "registration:edit")],
                ]
            ),
        )

    @router.callback_query(Registration.confirm, F.data == "registration:edit")
    async def registration_edit(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(Registration.full_name)
        await show(callback, "Введите ФИО заново:")
        await callback.answer()

    @router.callback_query(Registration.confirm, F.data == "registration:confirm")
    async def registration_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        if callback.from_user is None:
            return
        data = await state.get_data()
        async with session_factory() as session:
            user = await _user(session, callback.from_user.id)
            user = await UserService(session, config).complete_registration(
                user.id, str(data["full_name"]), str(data["phone"])
            )
            await state.clear()
            await _show_main(callback, session, user, menu, banners)
            if await notification_enabled(session, "user_registered"):
                await send_notification(
                    bot,
                    config.bot_owner_id,
                    events.user_registered(user),
                    reply_markup=keyboard([[("👤 Открыть пользователя", f"owner:user:{user.id}")]]),
                )
        await callback.answer("Регистрация завершена")

    @router.callback_query(F.data == "menu")
    async def main_menu(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        async with session_factory() as session:
            user = await _user(session, callback.from_user.id)
            await _show_main(callback, session, user, menu, banners)
        await callback.answer()

    @router.callback_query(F.data == "owner:client_view")
    async def owner_client_view(callback: CallbackQuery) -> None:
        async with session_factory() as session:
            user = await _user(session, callback.from_user.id)
            if user.role is not Role.OWNER:
                raise ValidationError("Доступно только владельцу")
            await _show_main(callback, session, user, menu, banners)
        await callback.answer()

    @router.callback_query(F.data == "owner:panel")
    async def owner_panel(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        async with session_factory() as session:
            user = await _user(session, callback.from_user.id)
            if user.role is not Role.OWNER:
                raise ValidationError("Доступно только владельцу")
            await _show_main(callback, session, user, menu, banners, admin=True)
        await callback.answer()

    @router.callback_query(F.data == "common:support")
    async def support(callback: CallbackQuery) -> None:
        support_username = config.bot_support_username.lstrip("@")
        support_url = (
            f"https://t.me/{support_username}?text={quote('Здравствуйте, мне нужна помощь')}"
        )
        privacy = (
            InlineKeyboardButton(
                text="Политика конфиденциальности",
                url=config.privacy_policy_url,
            )
            if config.privacy_policy_url
            else InlineKeyboardButton(
                text="Политика конфиденциальности",
                callback_data="common:legal_unset",
            )
        )
        agreement = (
            InlineKeyboardButton(
                text="Пользовательское соглашение",
                url=config.user_agreement_url,
            )
            if config.user_agreement_url
            else InlineKeyboardButton(
                text="Пользовательское соглашение",
                callback_data="common:legal_unset",
            )
        )
        await show_screen(
            callback,
            "help",
            "<b>🆘 Помощь</b>\n\nВыберите нужный раздел:",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [privacy],
                    [agreement],
                    [InlineKeyboardButton(text="Написать в поддержку", url=support_url)],
                    [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu")],
                ]
            ),
        )
        await callback.answer()

    @router.callback_query(F.data == "common:legal_unset")
    async def legal_unset(callback: CallbackQuery) -> None:
        await callback.answer("Ссылка пока не настроена", show_alert=False)

    feature_context = FeatureRouterContext(
        session_factory=session_factory,
        config=config,
        gateways=gateways,
        menu=menu,
        banners=banners,
    )
    router.include_router(build_client_router(feature_context))
    router.include_router(build_consultations_router(feature_context))
    router.include_router(build_expert_router(feature_context))

    owner_context = OwnerRouterContext(
        session_factory=session_factory,
        config=config,
        menu=menu,
        banners=banners,
    )
    router.include_router(build_owner_users_router(owner_context))
    router.include_router(build_owner_referrals_router(owner_context))
    router.include_router(build_owner_dashboard_router(owner_context))
    router.include_router(build_owner_commerce_router(owner_context))
    router.include_router(build_owner_settings_router(owner_context))

    return router
