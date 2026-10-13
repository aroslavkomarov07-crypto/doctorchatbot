import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from application.experts.expert_service import ExpertService
from application.marketplace_service import MarketplaceService, money
from application.messages.message_service import MessageService
from application.payments.payment_service import PaymentService
from application.subscriptions.subscription_service import SubscriptionService
from application.users.user_service import UserService
from core.config import get_settings
from core.enums.message_sender_type import MessageSenderType
from core.enums.payment_status import PaymentStatus
from infrastructure.database.unit_of_work import UnitOfWork
from infrastructure.payment_gateway import MockGateway, PlategaGateway
from telegram_bot.states import (
    Compose,
    ExpertForm,
    Registration,
    TariffForm,
    ValueForm,
    WithdrawalForm,
)
from telegram_bot.ui import Action, button, markup, safe, screen

logger = logging.getLogger(__name__)
MOSCOW = ZoneInfo("Europe/Moscow")


def create_dispatcher(session_factory: Any) -> Dispatcher:
    router = Router(name="marketplace")
    settings = get_settings()

    def uow() -> UnitOfWork:
        return UnitOfWork(session_factory)

    def market() -> MarketplaceService:
        return MarketplaceService(uow())

    def users() -> UserService:
        return UserService(uow())

    def experts() -> ExpertService:
        return ExpertService(uow())

    def subscriptions() -> SubscriptionService:
        return SubscriptionService(uow())

    def messages() -> MessageService:
        return MessageService(uow())

    async def draw(bot: Bot, actor: int, state: FSMContext, text: str,
                   *rows: list[InlineKeyboardButton], photo: str | None = None) -> None:
        await screen(bot, actor, state, text, markup(*rows), photo=photo)

    async def require_user(actor: int) -> Any:
        user = await users().get_user_by_telegram_id(actor)
        if user is None:
            raise PermissionError("Сначала зарегистрируйтесь через /start.")
        return user

    async def require_expert(actor: int) -> Any:
        expert = await experts().get_expert_by_telegram_id(actor)
        if expert is None or not expert.is_active:
            raise PermissionError("Действующий профиль эксперта не найден.")
        return expert

    def require_owner(actor: int) -> None:
        if actor != settings.owner_telegram_id or actor <= 0:
            raise PermissionError("Доступно только владельцу бота.")

    async def home(bot: Bot, actor: int, state: FSMContext, role: str | None = None) -> None:
        if actor == settings.owner_telegram_id and actor > 0:
            await state.update_data(role="owner")
            await draw(bot, actor, state, "<b>Панель владельца</b>",
                       [button("Пользователи", "au"), button("Эксперты", "ae")],
                       [button("Заявки экспертов", "aa"), button("Тарифы", "at")],
                       [button("Комиссии", "af"), button("Платежи", "ap")],
                       [button("Заявки на вывод", "aw"), button("Статистика", "ast")])
            return
        user = await users().get_user_by_telegram_id(actor)
        if user is None:
            await state.set_state(Registration.full_name)
            await draw(bot, actor, state, "Введите ФИО:", [button("Отмена", "cancel")])
            return
        expert = await experts().get_expert_by_telegram_id(actor)
        if role == "expert" and expert is not None and expert.is_active:
            await state.update_data(role="expert")
            await draw(bot, actor, state, "<b>Меню эксперта</b>",
                       [button("Входящие", "ein"), button("Клиенты", "eclients")],
                       [button("Тарифы и цены", "eprices"), button("Статистика", "estats")],
                       [button("Вывести средства", "ew"), button("Мой профиль", "eprofile")],
                       [button("Меню клиента", "client")])
            return
        await state.update_data(role="client")
        active = [sub for sub in await subscriptions().get_user_subscriptions(user.id)
                  if sub.is_active(datetime.now(UTC))]
        rows = [[button("Получить доступ к эксперту", "catalog")],
                [button("Стать экспертом", "apply"), button("Мои подписки", "mysubs")]]
        if active:
            rows.insert(1, [button("Написать эксперту", "write")])
        if expert is not None and expert.is_active:
            rows.append([button("Меню эксперта", "expertmenu")])
        await draw(bot, actor, state, "<b>Добро пожаловать!</b>", *rows)

    async def notify_owner(bot: Bot, text: str,
                           *rows: list[InlineKeyboardButton]) -> None:
        if settings.owner_telegram_id > 0:
            try:
                await bot.send_message(settings.owner_telegram_id, text, parse_mode="HTML",
                                       reply_markup=markup(*rows) if rows else None)
            except Exception:
                logger.exception("Could not send owner notification")

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext, bot: Bot) -> None:
        if message.from_user is None or message.chat.type != "private":
            return
        await state.clear()
        await home(bot, message.from_user.id, state)

    @router.callback_query(Action.filter())
    async def navigate(query: CallbackQuery, callback_data: Action,
                       state: FSMContext, bot: Bot) -> None:
        if query.message is None:
            return
        actor = query.from_user.id
        name, ref, page = callback_data.name, callback_data.ref, callback_data.page
        data = await state.get_data()
        notifications = {"reply", "history", "openreply", "approve", "reject", "revision", "wdpaid", "wdno"}
        if name not in notifications and data.get("screen_id") not in (None, query.message.message_id):
            await query.answer("Это меню устарело. Откройте /start.", show_alert=True)
            return
        if name in notifications:
            await state.update_data(screen_id=query.message.message_id, screen_photo=False)
        await query.answer()

        if name in {"cancel", "home", "client", "expertmenu"}:
            await state.set_state(None)
            role = "client" if name == "client" else "expert" if name == "expertmenu" else data.get("role")
            if role == "expert":
                await require_expert(actor)
            await home(bot, actor, state, role)
        elif name == "regok":
            if await users().get_user_by_telegram_id(actor) is not None:
                raise ValueError("Пользователь уже зарегистрирован.")
            form = await state.get_data()
            user = await users().register_user(actor, form["full_name"], form["phone"])
            date = user.created_at.astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M")
            await notify_owner(bot,
                "#UserRegisteredEvent\n<b>🔅 Событие: Новый пользователь!</b>\n\n"
                "<b>👤 Пользователь:</b>\n"
                f"• ID: {actor}\n"
                f"• Username: {safe('@' + query.from_user.username if query.from_user.username else 'не указан')}\n"
                f"• Имя в Telegram: {safe(query.from_user.full_name)}\n"
                f"• ФИО: {safe(user.full_name)}\n"
                f"• Телефон: {safe(user.phone_number)}\n"
                f"• Дата регистрации: {date}")
            await state.set_state(None)
            await home(bot, actor, state)
        elif name == "regedit":
            await state.set_state(Registration.full_name)
            await draw(bot, actor, state, "Введите ФИО заново:", [button("Отмена", "cancel")])
        elif name == "catalog":
            await require_user(actor)
            available = await experts().get_active_experts()
            start_at = max(0, page) * 8
            rows = [[button(expert.full_name[:40], "ecard", str(expert.id))]
                    for expert in available[start_at:start_at + 8]]
            if page > 0:
                rows.append([button("←", "catalog", page=page - 1)])
            if len(available) > start_at + 8:
                rows.append([button("→", "catalog", page=page + 1)])
            rows.append([button("Назад", "client")])
            await draw(bot, actor, state, "Выберите эксперта:", *rows)
        elif name == "ecard":
            await require_user(actor)
            async with uow() as unit:
                assert unit.experts is not None
                expert = await unit.experts.get_by_id(UUID(ref))
            if expert is None or not expert.is_active:
                raise ValueError("Эксперт недоступен.")
            await state.update_data(selected_expert=ref)
            await draw(bot, actor, state,
                       f"<b>{safe(expert.full_name)}</b>\n"
                       f"Специализация: {safe(expert.specialization or 'не указана')}\n"
                       f"Образование: {safe(expert.education or 'не указано')}\n"
                       f"Опыт: {safe(expert.experience or 'не указан')}\n"
                       f"Описание: {safe(expert.description or 'не указано')}",
                       [button("Подтвердить выбор", "tariffs")],
                       [button("Назад", "catalog"), button("Главное меню", "home")],
                       photo=expert.photo_file_id)
        elif name == "tariffs":
            await require_user(actor)
            expert_id = UUID((await state.get_data())["selected_expert"])
            rows = []
            for available_tariff in await market().tariffs(active_only=True):
                price = await market().effective_price(expert_id, available_tariff.id)
                rows.append([button(f"{available_tariff.name[:25]} | {price} ₽", "tdetail", str(available_tariff.id))])
            rows.append([button("Назад", "ecard", str(expert_id))])
            await draw(bot, actor, state,
                       "💎 <b>Выбор тарифа</b>\nВыберите пакет подписки для доступа к эксперту:",
                       *rows)
        elif name == "tdetail":
            await require_user(actor)
            expert_id = UUID((await state.get_data())["selected_expert"])
            tariff = await market().tariff(UUID(ref))
            if tariff is None or not tariff.is_active:
                raise ValueError("Тариф недоступен.")
            price = await market().effective_price(expert_id, tariff.id)
            await draw(bot, actor, state,
                       f"<b>{safe(tariff.name)} | {price} ₽</b>\n"
                       f"• Лимит сообщений: {tariff.message_limit or '∞'}\n"
                       f"• Длительность: {tariff.duration_days} дней\n"
                       f"• Стоимость: {price} ₽",
                       [button("Оплатить через СБП", "buy", ref)],
                       [button("Назад", "tariffs")])
        elif name == "buy":
            user = await require_user(actor)
            expert_id = UUID((await state.get_data())["selected_expert"])
            gateway: PlategaGateway | MockGateway
            gateway = MockGateway(settings) if settings.payment_mode == "mock" else PlategaGateway(settings)
            if not gateway.configured:
                raise ValueError("Оплата пока не настроена владельцем бота.")
            return_url = ""
            if isinstance(gateway, PlategaGateway):
                bot_user = await bot.get_me()
                if not bot_user.username:
                    raise ValueError("У бота нет username для возврата после оплаты.")
                return_url = f"https://t.me/{bot_user.username}"
            local_id = uuid4()
            payment = await market().create_purchase(
                user.id, expert_id, UUID(ref), gateway.name, str(local_id)
            )
            if isinstance(gateway, MockGateway):
                url = gateway.payment_url(payment.id, payment.amount, payment.tariff_name or "Подписка")
                await market().set_payment_url(payment.id, url)
            else:
                try:
                    provider_id, url = await gateway.create_payment(
                        payment.amount, payment.tariff_name or "Подписка",
                        return_url, actor, payment.id,
                    )
                    await market().attach_provider_payment(payment.id, provider_id, url)
                except (httpx.HTTPError, KeyError, ValueError) as exc:
                    await PaymentService(uow()).mark_as_failed(payment.id)
                    logger.warning("Could not create Platega payment: %s", type(exc).__name__)
                    raise ValueError("Не удалось создать счёт Platega. Попробуйте позже.") from exc
            await draw(bot, actor, state, f"Счёт на {payment.amount} ₽ создан.",
                       [InlineKeyboardButton(text="Оплатить через СБП", url=url)],
                       [button("Проверить оплату", "checkpay", str(payment.id))],
                       [button("Главное меню", "home")])
        elif name == "checkpay":
            user = await require_user(actor)
            checked_payment = await PaymentService(uow()).get_payment(UUID(ref))
            if checked_payment is None or checked_payment.user_id != user.id:
                raise PermissionError("Платёж не найден.")
            if (
                checked_payment.payment_provider == "robokassa"
                and checked_payment.status == PaymentStatus.PENDING
            ):
                await draw(
                    bot, actor, state,
                    "Старый счёт Robokassa больше не поддерживается. Выберите тариф и создайте новый счёт Platega.",
                    [button("К экспертам", "catalog")],
                    [button("Главное меню", "home")],
                )
                return
            if (
                checked_payment.status == PaymentStatus.PENDING
                and checked_payment.payment_provider == "platega"
                and checked_payment.provider_payment_id is not None
            ):
                try:
                    provider_status, provider_amount = await PlategaGateway(settings).fetch_status(
                        UUID(checked_payment.provider_payment_id)
                    )
                except httpx.HTTPError:
                    logger.warning("Platega status check is temporarily unavailable")
                else:
                    status_map = {
                        "CONFIRMED": PaymentStatus.SUCCEEDED,
                        "CANCELED": PaymentStatus.FAILED,
                        "CHARGEBACKED": PaymentStatus.REFUNDED,
                    }
                    if provider_status in status_map:
                        checked_payment, _ = await PaymentService(uow()).apply_provider_event(
                            "platega", checked_payment.provider_payment_id,
                            provider_amount, status_map[provider_status],
                        )
            if checked_payment.status == PaymentStatus.SUCCEEDED:
                await home(bot, actor, state)
            else:
                rows = []
                if checked_payment.payment_url:
                    rows.append([InlineKeyboardButton(text="Открыть оплату", url=checked_payment.payment_url)])
                rows.extend([[button("Проверить ещё", "checkpay", ref)],
                             [button("Главное меню", "home")]])
                await draw(bot, actor, state, f"Статус платежа: {safe(checked_payment.status.value)}", *rows)
        elif name == "mysubs":
            user = await require_user(actor)
            items = await subscriptions().get_user_subscriptions(user.id)
            text = "<b>Мои подписки</b>\n" + "\n".join(
                f"• {s.id}: {safe(s.status.value)} до {s.end_date.astimezone(MOSCOW):%d.%m.%Y}"
                for s in items[:20]
            )
            await draw(bot, actor, state, text[:4000], [button("Назад", "client")])
        elif name == "write":
            user = await require_user(actor)
            items = await subscriptions().get_user_subscriptions(user.id)
            rows = []
            for sub in items:
                if sub.is_active(datetime.now(UTC)):
                    async with uow() as unit:
                        assert unit.experts is not None
                        expert = await unit.experts.get_by_id(sub.expert_id)
                    if expert:
                        rows.append([button(expert.full_name[:40], "compose", str(expert.id))])
            rows.append([button("Назад", "client")])
            await draw(bot, actor, state, "Выберите эксперта:", *rows)
        elif name == "compose":
            user = await require_user(actor)
            if await subscriptions().get_active_subscription_for_expert(user.id, UUID(ref)) is None:
                raise PermissionError("Подписка закончилась.")
            await state.update_data(compose_user=str(user.id), compose_expert=ref, reply_to=None)
            await state.set_state(Compose.text)
            await draw(bot, actor, state, "Введите сообщение эксперту:", [button("Отмена", "cancel")])
        elif name == "reply":
            expert = await require_expert(actor)
            original = await messages().get_message(UUID(ref))
            if (
                original is None or original.expert_id != expert.id
                or original.sender_type != MessageSenderType.USER
            ):
                raise PermissionError("Сообщение недоступно.")
            if await subscriptions().get_active_subscription_for_expert(original.user_id, expert.id) is None:
                raise PermissionError("Подписка клиента закончилась.")
            await state.update_data(compose_user=str(original.user_id), compose_expert=str(expert.id), reply_to=ref)
            await state.set_state(Compose.text)
            await draw(bot, actor, state, "Введите ответ клиенту:", [button("Отмена", "cancel")])
        elif name in {"ein", "eclients"}:
            expert = await require_expert(actor)
            items = await subscriptions().get_expert_subscriptions(expert.id)
            rows = []
            for sub in items[:30]:
                if sub.is_active(datetime.now(UTC)):
                    async with uow() as unit:
                        assert unit.users is not None
                        client = await unit.users.get_by_id(sub.user_id)
                    if client:
                        rows.append([button(client.full_name[:40], "history", str(client.id))])
            rows.append([button("Назад", "expertmenu")])
            await draw(bot, actor, state, "Клиенты с активной подпиской:", *rows)
        elif name == "history":
            expert = await require_expert(actor)
            client_id = UUID(ref)
            if await subscriptions().get_active_subscription_for_expert(client_id, expert.id) is None:
                raise PermissionError("Нет действующей подписки клиента.")
            conversation = await messages().get_conversation(client_id, expert.id)
            start_at = max(0, page) * 12
            lines = [f"<b>История диалога, страница {page + 1}</b>"]
            lines.extend(
                f"{'Клиент' if msg.sender_type == MessageSenderType.USER else 'Эксперт'}: {safe(msg.text)[:260]}"
                for msg in conversation[start_at:start_at + 12]
            )
            rows = []
            if page > 0:
                rows.append([button("←", "history", ref, page - 1)])
            if len(conversation) > start_at + 12:
                rows.append([button("→", "history", ref, page + 1)])
            rows.extend([[button("Написать клиенту", "writeclient", ref)],
                         [button("Назад", "expertmenu")]])
            await draw(bot, actor, state, "\n\n".join(lines)[:4000], *rows)
        elif name == "writeclient":
            expert = await require_expert(actor)
            if await subscriptions().get_active_subscription_for_expert(UUID(ref), expert.id) is None:
                raise PermissionError("Нет действующей подписки клиента.")
            await state.update_data(compose_user=ref, compose_expert=str(expert.id), reply_to=None)
            await state.set_state(Compose.text)
            await draw(bot, actor, state, "Введите сообщение клиенту:", [button("Отмена", "cancel")])
        elif name == "send":
            form = await state.get_data()
            expert = await experts().get_expert_by_telegram_id(actor)
            sender = MessageSenderType.EXPERT if expert and str(expert.id) == form.get("compose_expert") else MessageSenderType.USER
            client_id, expert_id = UUID(form["compose_user"]), UUID(form["compose_expert"])
            if sender == MessageSenderType.USER:
                user = await require_user(actor)
                if user.id != client_id:
                    raise PermissionError("Неверный отправитель.")
            else:
                await require_expert(actor)
            sent = await messages().send_message(
                client_id, expert_id, form["compose_text"], sender,
                UUID(form["reply_to"]) if form.get("reply_to") else None,
            )
            async with uow() as unit:
                assert unit.users and unit.experts
                client = await unit.users.get_by_id(client_id)
                receiver = await unit.experts.get_by_id(expert_id)
            assert client and receiver
            if sender == MessageSenderType.USER:
                await bot.send_message(
                    receiver.telegram_id,
                    "#NewMessageEvent\n<b>🔅 Событие: Новое сообщение!</b>\n\n"
                    f"👤 Клиент: {safe(client.full_name)}\n"
                    f"Телефон: {safe(client.phone_number)}\n\n"
                    f"💬 {safe(sent.text)}",
                    parse_mode="HTML",
                    reply_markup=markup([button("Ответить", "reply", str(sent.id))],
                                        [button("История диалога", "history", str(client_id))]),
                )
            else:
                if sent.reply_to_id:
                    rows = [[button("Открыть ответ", "openreply", str(sent.id))]]
                    content = f"Эксперт {safe(receiver.full_name)} ответил на ваш вопрос."
                else:
                    rows = []
                    content = f"Новое сообщение от эксперта {safe(receiver.full_name)}:\n{safe(sent.text)}"
                await bot.send_message(client.telegram_id, content, parse_mode="HTML",
                                       reply_markup=markup(*rows) if rows else None)
            await state.set_state(None)
            await home(bot, actor, state, "expert" if sender == MessageSenderType.EXPERT else "client")
        elif name == "editmessage":
            await state.set_state(Compose.text)
            await draw(bot, actor, state, "Введите новый текст:", [button("Отмена", "cancel")])
        elif name == "openreply":
            user = await require_user(actor)
            answer = await messages().get_message(UUID(ref))
            if (
                answer is None or answer.user_id != user.id or answer.reply_to_id is None
                or answer.sender_type != MessageSenderType.EXPERT
            ):
                raise PermissionError("Ответ недоступен.")
            question = await messages().get_message(answer.reply_to_id)
            if question is None:
                raise ValueError("Вопрос не найден.")
            await draw(bot, actor, state,
                       f"<b>Ваш вопрос:</b>\n{safe(question.text)}\n\n<b>Ответ эксперта:</b>\n{safe(answer.text)}"[:4000],
                       [button("Главное меню", "client")])
        elif name == "apply":
            await require_user(actor)
            await state.set_state(ExpertForm.full_name)
            await draw(bot, actor, state, "Укажите ФИО для анкеты эксперта:", [button("Отмена", "cancel")])
        elif name == "applysend":
            user = await require_user(actor)
            form = await state.get_data()
            application = await market().apply_for_expert(
                user.id, form["app_full_name"], form["app_specialization"],
                form["app_education"], form["app_experience"],
                form["app_description"], form["app_photo"],
            )
            await notify_owner(
                bot, "<b>Заявка эксперта</b>\n"
                f"ФИО: {safe(application.full_name)}\n"
                f"Специализация: {safe(application.specialization)}\n"
                f"Образование: {safe(application.education)}\n"
                f"Опыт: {safe(application.experience)}\n"
                f"Описание: {safe(application.description)}",
                [button("Одобрить", "approve", str(application.id)),
                 button("Отклонить", "reject", str(application.id))],
                [button("На доработку", "revision", str(application.id))],
            )
            await state.set_state(None)
            await draw(bot, actor, state, "Заявка отправлена владельцу.", [button("Главное меню", "client")])
        elif name in {"approve", "reject", "revision"}:
            require_owner(actor)
            application_for_review = await market().application(UUID(ref))
            if application_for_review is None or application_for_review.status != "pending":
                raise ValueError("Заявка уже обработана.")
            decision_label = {"approve": "одобрить", "reject": "отклонить", "revision": "вернуть на доработку"}[name]
            await draw(bot, actor, state,
                       f"Подтвердить решение: {decision_label} заявку {safe(application_for_review.full_name)}?",
                       [button("Подтвердить", "appconfirm", ref, {"approve": 1, "reject": 2, "revision": 3}[name])],
                       [button("Назад", "aa")])
        elif name == "appconfirm":
            require_owner(actor)
            decision = {1: "approved", 2: "rejected", 3: "revision"}.get(page)
            if decision is None:
                raise ValueError("Недопустимое решение.")
            telegram_id = await market().decide_application(UUID(ref), decision)
            await bot.send_message(telegram_id, f"Статус заявки эксперта: {safe(decision)}. Откройте /start.")
            await draw(bot, actor, state, "Заявка обработана.", [button("Главное меню", "home")])
        elif name == "eprofile":
            expert = await require_expert(actor)
            await draw(bot, actor, state,
                       f"<b>{safe(expert.full_name)}</b>\n{safe(expert.specialization or '')}\n"
                       f"{safe(expert.description or '')}",
                       [button("Назад", "expertmenu")], photo=expert.photo_file_id)
        elif name == "eprices":
            expert = await require_expert(actor)
            rows = []
            for tariff in await market().tariffs(active_only=True):
                current = await market().effective_price(expert.id, tariff.id)
                rows.append([button(f"{tariff.name[:25]}: {current} ₽", "setprice", str(tariff.id))])
            rows.append([button("Назад", "expertmenu")])
            await draw(bot, actor, state, "Выберите тариф, чтобы установить свою цену:", *rows)
        elif name == "setprice":
            await require_expert(actor)
            await state.update_data(value_target="expert_price", value_ref=ref)
            await state.set_state(ValueForm.value)
            await draw(bot, actor, state, "Введите цену в рублях:", [button("Отмена", "cancel")])
        elif name == "estats":
            expert = await require_expert(actor)
            stats = await market().earnings(expert.id)
            await draw(bot, actor, state,
                       "<b>Статистика</b>\n"
                       f"Оборот: {stats.turnover} ₽\n"
                       f"Комиссия платежной системы: {stats.payment_fees} ₽\n"
                       f"Комиссия сервиса: {stats.service_fees} ₽\n"
                       f"Доход: {stats.income} ₽\n"
                       f"Выведено: {stats.paid} ₽\n"
                       f"Ожидает вывода: {stats.pending} ₽\n"
                       f"Доступно: {stats.available} ₽\n"
                       f"Оплаченных подписок: {stats.subscriptions}",
                       [button("Назад", "expertmenu")])
        elif name == "ew":
            expert = await require_expert(actor)
            stats = await market().earnings(expert.id)
            await state.set_state(WithdrawalForm.amount)
            await draw(bot, actor, state,
                       f"Доступно к выводу: {stats.available} ₽\nВведите сумму:",
                       [button("Отмена", "cancel")])
        elif name == "wdsend":
            expert = await require_expert(actor)
            form = await state.get_data()
            withdrawal = await market().request_withdrawal(
                expert.id, Decimal(form["withdraw_amount"]), form["withdraw_bank"]
            )
            user = await require_user(actor)
            await notify_owner(bot,
                "#WithdrawalRequestedEvent\n<b>🔅 Событие: Новая заявка на вывод!</b>\n"
                f"Эксперт: {safe(expert.full_name)}\n"
                f"Telegram ID: {actor}\nТелефон: {safe(user.phone_number)}\n"
                f"Банк: {safe(withdrawal.bank_name)}\n"
                f"Сумма: {withdrawal.amount} ₽\nID: {withdrawal.id}",
                [button("Подтвердить выплату", "wdpaid", str(withdrawal.id)),
                 button("Отклонить", "wdno", str(withdrawal.id))])
            await state.set_state(None)
            await draw(bot, actor, state, "Заявка на вывод отправлена.", [button("Меню", "expertmenu")])
        elif name in {"wdpaid", "wdno"}:
            require_owner(actor)
            pending_withdrawal = await market().withdrawal(UUID(ref))
            if pending_withdrawal is None or pending_withdrawal.status != "pending":
                raise ValueError("Заявка уже обработана.")
            if name == "wdpaid":
                await draw(bot, actor, state,
                           f"Подтвердить фактическую выплату {pending_withdrawal.amount} ₽ в {safe(pending_withdrawal.bank_name)}?",
                           [button("Подтвердить", "wdconfirm", ref)],
                           [button("Назад", "aw")])
            else:
                await state.update_data(value_target="withdraw_reject", value_ref=ref)
                await state.set_state(ValueForm.value)
                await draw(bot, actor, state, "Введите причину отказа:", [button("Отмена", "cancel")])
        elif name == "wdconfirm":
            require_owner(actor)
            telegram_id = await market().decide_withdrawal(UUID(ref), True, actor)
            await bot.send_message(telegram_id, "Ваша выплата подтверждена владельцем бота.")
            await draw(bot, actor, state, "Выплата подтверждена.", [button("Заявки", "aw")])
        elif name == "at":
            require_owner(actor)
            rows = [[button(f"{t.name[:25]} | {t.base_price} ₽", "tadmin", str(t.id))]
                    for t in await market().tariffs()]
            rows.extend([[button("Добавить тариф", "taradd")], [button("Назад", "home")]])
            await draw(bot, actor, state, "<b>Тарифы</b>", *rows)
        elif name == "taradd":
            require_owner(actor)
            await state.set_state(TariffForm.name)
            await draw(bot, actor, state, "Введите название тарифа:", [button("Отмена", "cancel")])
        elif name == "tarcreate":
            require_owner(actor)
            form = await state.get_data()
            await market().create_tariff(
                form["tar_name"], form["tar_description"], int(form["tar_days"]),
                None if form["tar_limit"] == "∞" else int(form["tar_limit"]),
                Decimal(form["tar_price"]),
            )
            await state.set_state(None)
            await draw(bot, actor, state, "Тариф создан.", [button("К тарифам", "at")])
        elif name == "tadmin":
            require_owner(actor)
            tariff = await market().tariff(UUID(ref))
            if tariff is None:
                raise ValueError("Тариф не найден.")
            await draw(bot, actor, state,
                       f"<b>{safe(tariff.name)}</b>\n{safe(tariff.description)}\n"
                       f"{tariff.duration_days} дней, лимит {tariff.message_limit or '∞'}, "
                       f"{tariff.base_price} ₽, {'активен' if tariff.is_active else 'отключён'}",
                       [button("Название", "tedit", ref, 1), button("Описание", "tedit", ref, 2)],
                       [button("Дни", "tedit", ref, 3), button("Лимит", "tedit", ref, 4)],
                       [button("Цена", "tedit", ref, 5),
                        button("Отключить" if tariff.is_active else "Включить", "ttoggle", ref)],
                       [button("Назад", "at")])
        elif name == "tedit":
            require_owner(actor)
            field = {1: "name", 2: "description", 3: "duration_days",
                     4: "message_limit", 5: "base_price"}.get(page)
            if field is None:
                raise ValueError("Недопустимое поле.")
            await state.update_data(value_target="tariff", value_ref=ref, value_field=field)
            await state.set_state(ValueForm.value)
            await draw(bot, actor, state, f"Введите новое значение ({field}):",
                       [button("Отмена", "cancel")])
        elif name == "ttoggle":
            require_owner(actor)
            tariff = await market().tariff(UUID(ref))
            if tariff is None:
                raise ValueError("Тариф не найден.")
            await state.update_data(value_target="tariff", value_ref=ref,
                                    value_field="is_active", value="false" if tariff.is_active else "true")
            await draw(bot, actor, state, "Подтвердить смену доступности тарифа?",
                       [button("Подтвердить", "valueok")], [button("Назад", "tadmin", ref)])
        elif name == "af":
            require_owner(actor)
            payment_percent, service_percent = await market().fee_settings()
            await draw(bot, actor, state,
                       f"Комиссия платёжной системы: {payment_percent}%\n"
                       f"Комиссия сервиса: {service_percent}%\n"
                       f"С 1000 ₽ эксперт получает {Decimal(1000) - money(Decimal(1000) * payment_percent / 100) - money(Decimal(1000) * service_percent / 100)} ₽",
                       [button("Изменить платёжную", "fee", page=1)],
                       [button("Изменить сервисную", "fee", page=2)],
                       [button("Назад", "home")])
        elif name == "fee":
            require_owner(actor)
            await state.update_data(value_target="fee", value_field="payment" if page == 1 else "service")
            await state.set_state(ValueForm.value)
            await draw(bot, actor, state, "Введите процент комиссии:", [button("Отмена", "cancel")])
        elif name == "valueok":
            form = await state.get_data()
            target = form.get("value_target")
            if target == "expert_price":
                await require_expert(actor)
                await market().set_expert_price(actor, UUID(form["value_ref"]), Decimal(form["value"]))
                back = "eprices"
            elif target == "tariff":
                require_owner(actor)
                await market().update_tariff(UUID(form["value_ref"]), form["value_field"], form["value"])
                back = "at"
            elif target == "fee":
                require_owner(actor)
                old_payment, old_service = await market().fee_settings()
                if form["value_field"] == "payment":
                    old_payment = Decimal(form["value"])
                else:
                    old_service = Decimal(form["value"])
                await market().set_fees(old_payment, old_service)
                back = "af"
            elif target == "withdraw_reject":
                require_owner(actor)
                telegram_id = await market().decide_withdrawal(
                    UUID(form["value_ref"]), False, actor, form["value"]
                )
                await bot.send_message(telegram_id, f"Заявка на вывод отклонена: {safe(form['value'])}", parse_mode="HTML")
                back = "aw"
            else:
                raise ValueError("Нет операции для подтверждения.")
            await state.set_state(None)
            await draw(bot, actor, state, "Изменение сохранено.", [button("Назад", back)])
        elif name in {"au", "ae", "ap"}:
            require_owner(actor)
            if name == "au":
                admin_users = await market().users(page)
                lines = [f"{user.telegram_id}: {safe(user.full_name)}" for user in admin_users]
                title = "Пользователи"
                item_count = len(admin_users)
            elif name == "ae":
                admin_experts = await market().experts(page)
                lines = [f"{expert.telegram_id}: {safe(expert.full_name)} ({'активен' if expert.is_active else 'отключён'})" for expert in admin_experts]
                title = "Эксперты"
                item_count = len(admin_experts)
            else:
                admin_payments = await market().payments(page)
                lines = [f"{payment.id}: {payment.amount} ₽, {safe(payment.status.value)}" for payment in admin_payments]
                title = "Платежи"
                item_count = len(admin_payments)
            rows = []
            if page > 0:
                rows.append([button("←", name, page=page - 1)])
            if item_count == 10:
                rows.append([button("→", name, page=page + 1)])
            if name == "ae":
                rows.extend([[button(f"Вкл/выкл: {expert.full_name[:20]}", "etoggle", str(expert.id))]
                            for expert in admin_experts])
            rows.append([button("Назад", "home")])
            await draw(bot, actor, state, f"<b>{title}</b>\n" + "\n".join(lines)[:3800], *rows)
        elif name == "etoggle":
            require_owner(actor)
            await draw(bot, actor, state, "Подтвердить смену статуса эксперта?",
                       [button("Подтвердить", "etoggleok", ref)],
                       [button("Назад", "ae")])
        elif name == "etoggleok":
            require_owner(actor)
            await market().deactivate_expert(UUID(ref))
            await draw(bot, actor, state, "Статус эксперта изменён.", [button("Назад", "ae")])
        elif name == "aa":
            require_owner(actor)
            applications = await market().applications()
            rows = [[button(app.full_name[:35], "aview", str(app.id))] for app in applications]
            rows.append([button("Назад", "home")])
            await draw(bot, actor, state, "Заявки экспертов:", *rows)
        elif name == "aview":
            require_owner(actor)
            pending_application = await market().application(UUID(ref))
            if pending_application is None or pending_application.status != "pending":
                raise ValueError("Заявка недоступна.")
            await draw(bot, actor, state,
                       f"<b>{safe(pending_application.full_name)}</b>\n"
                       f"{safe(pending_application.specialization)}\n{safe(pending_application.education)}\n"
                       f"{safe(pending_application.experience)}\n{safe(pending_application.description)}",
                       [button("Одобрить", "approve", ref), button("Отклонить", "reject", ref)],
                       [button("На доработку", "revision", ref)],
                       [button("Назад", "aa")], photo=pending_application.photo_file_id)
        elif name == "aw":
            require_owner(actor)
            withdrawals = await market().withdrawals()
            rows = [[button(f"{withdrawal.amount} ₽, {withdrawal.bank_name[:18]}", "wview", str(withdrawal.id))]
                    for withdrawal in withdrawals]
            rows.append([button("Назад", "home")])
            await draw(bot, actor, state, "Заявки на вывод:", *rows)
        elif name == "wview":
            require_owner(actor)
            viewed_withdrawal = await market().withdrawal(UUID(ref))
            if viewed_withdrawal is None or viewed_withdrawal.status != "pending":
                raise ValueError("Заявка недоступна.")
            await draw(bot, actor, state,
                       f"Заявка {viewed_withdrawal.id}\n{viewed_withdrawal.amount} ₽, банк {safe(viewed_withdrawal.bank_name)}",
                       [button("Подтвердить", "wdpaid", ref), button("Отклонить", "wdno", ref)],
                       [button("Назад", "aw")])
        elif name == "ast":
            require_owner(actor)
            expert_list = await market().experts(page)
            lines = []
            for listed_expert in expert_list:
                stats = await market().earnings(listed_expert.id)
                lines.append(f"{safe(listed_expert.full_name)}: {stats.turnover} ₽ оборот, {stats.subscriptions} оплат")
            rows = []
            if page > 0:
                rows.append([button("←", "ast", page=page - 1)])
            if len(expert_list) == 10:
                rows.append([button("→", "ast", page=page + 1)])
            rows.append([button("Назад", "home")])
            await draw(bot, actor, state, "<b>Статистика</b>\n" + "\n".join(lines), *rows)

    @router.message(Registration.full_name)
    async def registration_name(message: Message, state: FSMContext, bot: Bot) -> None:
        if message.from_user is None or not message.text:
            return
        value = message.text.strip()
        if not 3 <= len(value) <= 255:
            await message.answer("ФИО должно содержать от 3 до 255 символов.")
            return
        await state.update_data(full_name=value)
        await state.set_state(Registration.phone)
        await draw(bot, message.from_user.id, state,
                   "Введите номер телефона в международном формате (+79990000000):",
                   [button("Отмена", "cancel")])
        await message.answer(
            "Можно отправить свой контакт кнопкой ниже.",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="Отправить мой номер", request_contact=True)]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )

    @router.message(Registration.phone)
    async def registration_phone(message: Message, state: FSMContext, bot: Bot) -> None:
        if message.from_user is None:
            return
        if message.contact is not None:
            if message.contact.user_id != message.from_user.id:
                await message.answer("Отправьте свой контакт, не чужой.")
                return
            phone = message.contact.phone_number
        else:
            phone = message.text or ""
        phone = phone.replace(" ", "").replace("-", "")
        if not phone.startswith("+"):
            phone = "+" + phone
        import re

        if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
            await message.answer("Укажите номер в международном формате: +79990000000")
            return
        await state.update_data(phone=phone)
        await state.set_state(Registration.review)
        await message.answer("Контакт получен.", reply_markup=ReplyKeyboardRemove())
        form = await state.get_data()
        await draw(bot, message.from_user.id, state,
                   f"Проверьте данные:\nФИО: {safe(form['full_name'])}\nТелефон: {safe(phone)}",
                   [button("Подтвердить", "regok"), button("Изменить", "regedit")],
                   [button("Отмена", "cancel")])

    expert_steps = [
        (ExpertForm.full_name, "app_full_name", ExpertForm.specialization, "Введите специализацию:"),
        (ExpertForm.specialization, "app_specialization", ExpertForm.education, "Укажите образование:"),
        (ExpertForm.education, "app_education", ExpertForm.experience, "Укажите опыт работы:"),
        (ExpertForm.experience, "app_experience", ExpertForm.description, "Опишите себя:"),
        (ExpertForm.description, "app_description", ExpertForm.photo, "Пришлите фотографию профиля:"),
    ]

    for current_state, key, next_state, prompt in expert_steps:
        async def expert_text(
            message: Message, state: FSMContext, bot: Bot,
            storage_key: str = key, following: Any = next_state, question: str = prompt,
        ) -> None:
            if message.from_user is None or not message.text:
                return
            value = message.text.strip()
            max_length = 255 if storage_key in {"app_full_name", "app_specialization"} else 1000
            if not 2 <= len(value) <= max_length:
                await message.answer(f"Введите от 2 до {max_length} символов.")
                return
            await state.update_data({storage_key: value})
            await state.set_state(following)
            await draw(bot, message.from_user.id, state, question, [button("Отмена", "cancel")])

        router.message.register(expert_text, current_state)

    @router.message(ExpertForm.photo)
    async def expert_photo(message: Message, state: FSMContext, bot: Bot) -> None:
        if message.from_user is None or not message.photo:
            await message.answer("Пришлите фотографию как фото, не как файл.")
            return
        photo_id = message.photo[-1].file_id
        await state.update_data(app_photo=photo_id)
        await state.set_state(ExpertForm.review)
        form = await state.get_data()
        await draw(bot, message.from_user.id, state,
                   f"<b>Анкета эксперта</b>\n{safe(form['app_full_name'])}\n"
                   f"{safe(form['app_specialization'])}\n{safe(form['app_education'])}\n"
                   f"{safe(form['app_experience'])}\n{safe(form['app_description'])}",
                   [button("Отправить", "applysend")], [button("Отмена", "cancel")],
                   photo=photo_id)

    @router.message(Compose.text)
    async def compose_text(message: Message, state: FSMContext, bot: Bot) -> None:
        if message.from_user is None or not message.text:
            return
        value = message.text.strip()
        if not 1 <= len(value) <= 3000:
            await message.answer("Сообщение должно содержать от 1 до 3000 символов.")
            return
        await state.update_data(compose_text=value)
        await state.set_state(Compose.review)
        await draw(bot, message.from_user.id, state,
                   f"Подтвердите отправку:\n{safe(value)}",
                   [button("Отправить", "send"), button("Изменить", "editmessage")],
                   [button("Отмена", "cancel")])

    tariff_steps = [
        (TariffForm.name, "tar_name", TariffForm.description, "Введите описание тарифа:"),
        (TariffForm.description, "tar_description", TariffForm.days, "Введите длительность в днях:"),
        (TariffForm.days, "tar_days", TariffForm.limit, "Введите лимит сообщений или ∞:"),
        (TariffForm.limit, "tar_limit", TariffForm.price, "Введите базовую цену в рублях:"),
    ]
    for current_state, key, next_state, prompt in tariff_steps:
        async def tariff_text(
            message: Message, state: FSMContext, bot: Bot,
            storage_key: str = key, following: Any = next_state, question: str = prompt,
        ) -> None:
            if message.from_user is None or not message.text:
                return
            require_owner(message.from_user.id)
            value = message.text.strip()
            if storage_key == "tar_days" and (not value.isdigit() or int(value) <= 0):
                await message.answer("Введите положительное число дней.")
                return
            if storage_key == "tar_limit" and value != "∞" and (not value.isdigit() or int(value) <= 0):
                await message.answer("Введите положительный лимит или ∞.")
                return
            if not value or len(value) > 1000:
                await message.answer("Некорректное значение.")
                return
            await state.update_data({storage_key: value})
            await state.set_state(following)
            await draw(bot, message.from_user.id, state, question, [button("Отмена", "cancel")])

        router.message.register(tariff_text, current_state)

    @router.message(TariffForm.price)
    async def tariff_price(message: Message, state: FSMContext, bot: Bot) -> None:
        if message.from_user is None or not message.text:
            return
        require_owner(message.from_user.id)
        try:
            price = money(Decimal(message.text.replace(",", ".")))
        except InvalidOperation:
            await message.answer("Введите цену числом.")
            return
        if price <= 0:
            await message.answer("Цена должна быть положительной.")
            return
        await state.update_data(tar_price=str(price))
        await state.set_state(TariffForm.review)
        form = await state.get_data()
        await draw(bot, message.from_user.id, state,
                   f"Создать тариф {safe(form['tar_name'])}: {form['tar_days']} дней, "
                   f"лимит {safe(form['tar_limit'])}, цена {price} ₽?",
                   [button("Подтвердить", "tarcreate")], [button("Отмена", "cancel")])

    @router.message(ValueForm.value)
    async def value_input(message: Message, state: FSMContext, bot: Bot) -> None:
        if message.from_user is None or not message.text:
            return
        form = await state.get_data()
        target = form.get("value_target")
        if target == "expert_price":
            await require_expert(message.from_user.id)
        else:
            require_owner(message.from_user.id)
        value = message.text.strip().replace(",", ".")
        if not value or len(value) > 1000:
            await message.answer("Некорректное значение.")
            return
        if target in {"expert_price", "fee"}:
            try:
                number = Decimal(value)
            except InvalidOperation:
                await message.answer("Введите число.")
                return
            if number < 0 or (target == "expert_price" and number == 0):
                await message.answer("Введите положительное значение.")
                return
        await state.update_data(value=value)
        await state.set_state(ValueForm.review)
        await draw(bot, message.from_user.id, state,
                   f"Подтвердить новое значение: {safe(value)}?",
                   [button("Подтвердить", "valueok")], [button("Отмена", "cancel")])

    @router.message(WithdrawalForm.amount)
    async def withdrawal_amount(message: Message, state: FSMContext, bot: Bot) -> None:
        if message.from_user is None or not message.text:
            return
        expert = await require_expert(message.from_user.id)
        try:
            amount = money(Decimal(message.text.replace(",", ".")))
        except InvalidOperation:
            await message.answer("Введите сумму числом.")
            return
        available = (await market().earnings(expert.id)).available
        if amount <= 0 or amount > available:
            await message.answer(f"Доступно к выводу: {available} ₽")
            return
        await state.update_data(withdraw_amount=str(amount))
        await state.set_state(WithdrawalForm.bank)
        await draw(bot, message.from_user.id, state, "Введите название банка:",
                   [button("Отмена", "cancel")])

    @router.message(WithdrawalForm.bank)
    async def withdrawal_bank(message: Message, state: FSMContext, bot: Bot) -> None:
        if message.from_user is None or not message.text:
            return
        await require_expert(message.from_user.id)
        bank = message.text.strip()
        if not 2 <= len(bank) <= 255:
            await message.answer("Название банка должно быть от 2 до 255 символов.")
            return
        await state.update_data(withdraw_bank=bank)
        await state.set_state(WithdrawalForm.review)
        form = await state.get_data()
        await draw(bot, message.from_user.id, state,
                   f"Вывести {form['withdraw_amount']} ₽ в банк {safe(bank)}?",
                   [button("Создать заявку", "wdsend")], [button("Отмена", "cancel")])

    @router.error()
    async def error_handler(event: ErrorEvent, bot: Bot) -> bool:
        logger.error("Telegram handler failed: %s", type(event.exception).__name__)
        actor = (
            event.update.callback_query.from_user.id
            if isinstance(event.update.callback_query, CallbackQuery)
            else event.update.message.from_user.id
            if event.update.message and event.update.message.from_user else None
        )
        if actor is not None:
            detail = (
                str(event.exception) if isinstance(event.exception, (ValueError, PermissionError))
                else "Не удалось выполнить действие. Попробуйте позже."
            )
            try:
                await bot.send_message(actor, detail)
            except Exception:
                logger.exception("Could not send error notification")
        return True

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher
