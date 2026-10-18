import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.dto import Checkout, WebhookPayment
from src.application.money import calculate_commissions, money
from src.application.policy import require_role
from src.application.ports import PaymentGateway
from src.application.topics import ensure_conversation_topics
from src.application.validation import normalize_phone, validate_full_name, validate_text
from src.core.config import Settings as AppSettings
from src.core.enums import (
    ExpertApplicationStatus,
    LedgerEntryType,
    MessageStatus,
    PaymentProvider,
    ReferralAccrual,
    ReferralRewardKind,
    Role,
    SubscriptionStatus,
    TransactionStatus,
    WithdrawalStatus,
)
from src.core.exceptions import (
    AccessDenied,
    InsufficientBalance,
    MessageLimitReached,
    PaymentConflict,
    SubscriptionRequired,
    ValidationError,
)
from src.infrastructure.database.models import (
    ClientExpertSubscription,
    CommissionSettings,
    Conversation,
    ConversationMessage,
    ExpertApplication,
    ExpertProfile,
    ExpertTariff,
    FinancialLedgerEntry,
    PaymentTransaction,
    ReferralReward,
    Settings,
    Tariff,
    User,
    WithdrawalRequest,
)
from src.infrastructure.database.models import (
    PaymentGateway as PaymentGatewayModel,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def referral_code(telegram_id: int) -> str:
    return hashlib.sha256(f"doctor-shop:{telegram_id}".encode()).hexdigest()[:12]


async def bootstrap_defaults(session: AsyncSession, config: AppSettings) -> None:
    if await session.get(Settings, 1) is None:
        session.add(Settings(id=1))
    if await session.get(CommissionSettings, 1) is None:
        session.add(
            CommissionSettings(
                id=1,
                service_percent=Decimal(config.service_commission_percent),
            )
        )
    tariff_exists = await session.scalar(select(func.count(Tariff.id)))
    if not tariff_exists:
        session.add(
            Tariff(
                name="Недельный",
                description="Доступ к эксперту на 7 дней",
                duration_days=7,
                message_limit=None,
                default_price=Decimal("1000.00"),
                is_active=True,
            )
        )
    gateway_exists = await session.scalar(
        select(PaymentGatewayModel).where(PaymentGatewayModel.provider == PaymentProvider.PLATEGA)
    )
    if gateway_exists is None:
        session.add(
            PaymentGatewayModel(
                provider=PaymentProvider.PLATEGA,
                is_active=bool(config.platega_merchant_id and config.platega_api_key),
                settings={"sbp_payment_method": config.platega_sbp_payment_method},
            )
        )
    await session.commit()


async def notification_enabled(session: AsyncSession, event_key: str) -> bool:
    settings = await session.get(Settings, 1)
    if settings is None:
        return True
    return settings.notifications.get(event_key, True)


class UserService:
    def __init__(self, session: AsyncSession, config: AppSettings) -> None:
        self.session = session
        self.config = config

    async def get_or_create(
        self,
        *,
        telegram_id: int,
        username: str | None,
        telegram_name: str,
        language: str | None,
        referral: str | None = None,
    ) -> User:
        user = await self.session.scalar(select(User).where(User.telegram_id == telegram_id))
        now = utcnow()
        if user:
            user.username = username
            user.telegram_name = telegram_name[:128]
            user.last_activity_at = now
            await self.session.commit()
            return user

        role = Role.OWNER if telegram_id == self.config.bot_owner_id else Role.CLIENT
        global_settings = await self.session.get(Settings, 1)
        if (
            role is not Role.OWNER
            and global_settings is not None
            and not global_settings.registration_enabled
        ):
            raise AccessDenied("Регистрация новых пользователей временно отключена")
        referrer_id = None
        if referral:
            referrer_id = await self.session.scalar(
                select(User.id).where(
                    User.referral_code == referral,
                    User.telegram_id != telegram_id,
                )
            )
        user = User(
            telegram_id=telegram_id,
            username=username,
            telegram_name=telegram_name[:128],
            full_name=telegram_name[:160] if role is Role.OWNER else None,
            language=(language or "ru")[:10],
            role=role,
            registration_completed=role is Role.OWNER,
            referral_code=referral_code(telegram_id),
            referrer_id=referrer_id,
            last_activity_at=now,
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def complete_registration(self, user_id: int, full_name: str, phone: str) -> User:
        user = await self.session.get(User, user_id, with_for_update=True)
        if user is None:
            raise ValidationError("Пользователь не найден")
        if user.role is Role.OWNER:
            return user
        user.full_name = validate_full_name(full_name)
        user.phone = normalize_phone(phone)
        user.registration_completed = True
        await self.session.commit()
        return user

    async def ensure_available(self, user: User) -> None:
        if user.is_blocked:
            raise AccessDenied("Ваш аккаунт заблокирован")
        if not user.registration_completed:
            raise AccessDenied("Сначала завершите регистрацию")


class ExpertService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def submit_application(
        self,
        actor: User,
        *,
        full_name: str,
        specialization: str,
        short_bio: str,
        bio: str,
        photo_file_id: str,
    ) -> ExpertApplication:
        require_role(actor.role, Role.CLIENT)
        pending = await self.session.scalar(
            select(ExpertApplication.id).where(
                ExpertApplication.user_id == actor.id,
                ExpertApplication.status == ExpertApplicationStatus.PENDING,
            )
        )
        if pending:
            raise ValidationError("У вас уже есть заявка на рассмотрении")
        application = ExpertApplication(
            user_id=actor.id,
            full_name=validate_full_name(full_name),
            specialization=validate_text(specialization, maximum=160),
            short_bio=validate_text(short_bio, maximum=500),
            bio=validate_text(bio, maximum=4000),
            photo_file_id=photo_file_id[:512],
        )
        self.session.add(application)
        await self.session.commit()
        await self.session.refresh(application)
        return application

    async def approve(self, owner: User, application_id: int) -> ExpertProfile:
        require_role(owner.role, Role.OWNER)
        application = await self.session.scalar(
            select(ExpertApplication)
            .where(ExpertApplication.id == application_id)
            .with_for_update()
        )
        if application is None or application.status is not ExpertApplicationStatus.PENDING:
            raise ValidationError("Заявка не найдена или уже обработана")
        user = await self.session.get(User, application.user_id, with_for_update=True)
        if user is None:
            raise ValidationError("Пользователь заявки не найден")
        profile = await self.session.scalar(
            select(ExpertProfile).where(ExpertProfile.user_id == user.id)
        )
        if profile is None:
            profile = ExpertProfile(
                user_id=user.id,
                display_name=application.full_name,
                specialization=application.specialization,
                short_bio=application.short_bio,
                bio=application.bio,
                photo_file_id=application.photo_file_id,
                is_published=True,
            )
            self.session.add(profile)
            await self.session.flush()
            tariffs = list(
                await self.session.scalars(
                    select(Tariff).where(Tariff.is_active.is_(True), Tariff.is_archived.is_(False))
                )
            )
            self.session.add_all(
                [ExpertTariff(expert_id=profile.id, tariff_id=item.id) for item in tariffs]
            )
        user.role = Role.EXPERT
        application.status = ExpertApplicationStatus.APPROVED
        application.reviewed_by_id = owner.id
        application.reviewed_at = utcnow()
        await self.session.commit()
        await self.session.refresh(profile)
        return profile

    async def reject(self, owner: User, application_id: int, reason: str = "") -> None:
        require_role(owner.role, Role.OWNER)
        application = await self.session.scalar(
            select(ExpertApplication)
            .where(ExpertApplication.id == application_id)
            .with_for_update()
        )
        if application is None or application.status is not ExpertApplicationStatus.PENDING:
            raise ValidationError("Заявка не найдена или уже обработана")
        application.status = ExpertApplicationStatus.REJECTED
        application.reviewed_by_id = owner.id
        application.reviewed_at = utcnow()
        application.rejection_reason = reason[:500] or None
        await self.session.commit()

    async def set_custom_price(
        self, actor: User, expert_tariff_id: int, price: Decimal | None
    ) -> ExpertTariff:
        require_role(actor.role, Role.EXPERT)
        row = await self.session.scalar(
            select(ExpertTariff)
            .join(ExpertProfile, ExpertProfile.id == ExpertTariff.expert_id)
            .where(ExpertTariff.id == expert_tariff_id, ExpertProfile.user_id == actor.id)
            .with_for_update()
        )
        if row is None:
            raise AccessDenied("Тариф эксперта не найден")
        if price is not None and money(price) < 0:
            raise ValidationError("Цена не может быть отрицательной")
        row.custom_price = money(price) if price is not None else None
        await self.session.commit()
        return row


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_checkout(
        self,
        actor: User,
        *,
        expert_tariff_id: int,
        gateway: PaymentGateway,
    ) -> Checkout:
        require_role(actor.role, Role.CLIENT, Role.OWNER)
        if actor.is_blocked or not actor.registration_completed:
            raise AccessDenied("Покупка недоступна")
        global_settings = await self.session.get(Settings, 1)
        if global_settings is not None and not global_settings.payments_enabled:
            raise AccessDenied("Платежи временно отключены владельцем")
        gateway_settings = await self.session.scalar(
            select(PaymentGatewayModel).where(PaymentGatewayModel.provider == gateway.provider)
        )
        if gateway.provider is not PaymentProvider.FAKE and (
            gateway_settings is None or not gateway_settings.is_active
        ):
            raise AccessDenied("Платежная система отключена")
        row = await self.session.execute(
            select(ExpertTariff, ExpertProfile, Tariff)
            .join(ExpertProfile, ExpertProfile.id == ExpertTariff.expert_id)
            .join(Tariff, Tariff.id == ExpertTariff.tariff_id)
            .where(
                ExpertTariff.id == expert_tariff_id,
                ExpertTariff.is_active.is_(True),
                ExpertProfile.is_published.is_(True),
                Tariff.is_active.is_(True),
                Tariff.is_archived.is_(False),
            )
        )
        data = row.one_or_none()
        if data is None:
            raise ValidationError("Тариф недоступен")
        expert_tariff, expert, tariff = data
        commission = await self.session.get(CommissionSettings, 1)
        if commission is None:
            raise ValidationError("Комиссии не настроены")
        price = expert_tariff.custom_price or tariff.default_price
        breakdown = calculate_commissions(price, commission.service_percent)
        snapshot = {
            "expert_name": expert.display_name,
            "tariff_name": tariff.name,
            "description": tariff.description,
            "duration_days": tariff.duration_days,
            "message_limit": tariff.message_limit,
            "price": str(breakdown.gross),
            "service_commission_percent": str(breakdown.service_percent),
        }
        transaction = PaymentTransaction(
            client_id=actor.id,
            expert_id=expert.id,
            tariff_id=tariff.id,
            provider=gateway.provider,
            amount=breakdown.gross,
            service_commission_percent=breakdown.service_percent,
            service_commission_amount=breakdown.service_fee,
            expert_income_amount=breakdown.expert_income,
            snapshot=snapshot,
        )
        self.session.add(transaction)
        await self.session.flush()
        subscription = ClientExpertSubscription(
            client_id=actor.id,
            expert_id=expert.id,
            tariff_id=tariff.id,
            transaction_id=transaction.id,
            message_limit=tariff.message_limit,
            snapshot=snapshot,
        )
        self.session.add(subscription)
        await self.session.commit()
        try:
            payment = await gateway.create_payment(
                transaction_id=transaction.id,
                amount=breakdown.gross,
                description=f"{tariff.name}: доступ к эксперту {expert.display_name}",
            )
            transaction.provider_payment_id = payment.provider_payment_id
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            failed = await self.session.get(PaymentTransaction, transaction.id)
            if failed:
                failed.status = TransactionStatus.FAILED
                await self.session.commit()
            raise
        return Checkout(
            transaction_id=transaction.id,
            provider_payment_id=payment.provider_payment_id,
            url=payment.url,
            amount=breakdown.gross,
        )

    async def process_webhook(self, event: WebhookPayment) -> bool:
        transaction = await self.session.scalar(
            select(PaymentTransaction)
            .where(PaymentTransaction.provider_payment_id == event.provider_payment_id)
            .with_for_update()
        )
        if transaction is None:
            raise PaymentConflict("Транзакция webhook не найдена")
        if transaction.provider is not event.provider:
            raise PaymentConflict("Платеж пришел от другого провайдера")
        if money(transaction.amount) != money(event.amount):
            raise PaymentConflict("Сумма webhook не совпадает с суммой транзакции")
        if transaction.status is event.status:
            await self.session.rollback()
            return False

        transaction.provider_status = event.raw_status[:64]
        transaction.provider_payload = event.safe_payload
        if event.status is TransactionStatus.COMPLETED:
            if transaction.status is not TransactionStatus.PENDING:
                raise PaymentConflict("Завершить можно только ожидающий платеж")
            await self._complete(transaction)
        elif event.status is TransactionStatus.REFUNDED:
            if transaction.status is not TransactionStatus.COMPLETED:
                raise PaymentConflict("Вернуть можно только завершенный платеж")
            await self._refund(transaction)
        elif event.status in {TransactionStatus.CANCELED, TransactionStatus.FAILED}:
            if transaction.status is not TransactionStatus.PENDING:
                raise PaymentConflict("Статус ожидающего платежа уже изменен")
            transaction.status = event.status
        else:
            raise PaymentConflict("Неподдерживаемый статус платежа")
        await self.session.commit()
        return True

    async def _complete(self, transaction: PaymentTransaction) -> None:
        now = utcnow()
        subscription = await self.session.scalar(
            select(ClientExpertSubscription)
            .where(ClientExpertSubscription.transaction_id == transaction.id)
            .with_for_update()
        )
        if subscription is None:
            raise PaymentConflict("Подписка транзакции не найдена")
        duration = int(transaction.snapshot["duration_days"])
        transaction.status = TransactionStatus.COMPLETED
        transaction.completed_at = now
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.started_at = now
        subscription.expires_at = now + timedelta(days=duration)
        await ensure_conversation_topics(
            self.session,
            client_id=transaction.client_id,
            expert_id=transaction.expert_id,
        )
        self.session.add_all(
            [
                FinancialLedgerEntry(
                    expert_id=transaction.expert_id,
                    client_id=transaction.client_id,
                    transaction_id=transaction.id,
                    type=LedgerEntryType.PAYMENT_GROSS,
                    amount=transaction.amount,
                    affects_expert_balance=False,
                ),
                FinancialLedgerEntry(
                    expert_id=transaction.expert_id,
                    client_id=transaction.client_id,
                    transaction_id=transaction.id,
                    type=LedgerEntryType.SERVICE_FEE,
                    amount=-transaction.service_commission_amount,
                    affects_expert_balance=False,
                ),
                FinancialLedgerEntry(
                    expert_id=transaction.expert_id,
                    client_id=transaction.client_id,
                    transaction_id=transaction.id,
                    type=LedgerEntryType.EXPERT_INCOME,
                    amount=transaction.expert_income_amount,
                    affects_expert_balance=True,
                ),
            ]
        )
        await self._apply_referral(transaction)

    async def _apply_referral(self, transaction: PaymentTransaction) -> None:
        settings = await self.session.get(Settings, 1)
        client = await self.session.get(User, transaction.client_id)
        if not settings or not settings.referral_enabled or not client or not client.referrer_id:
            return
        if settings.referral_accrual is ReferralAccrual.FIRST_PAYMENT:
            completed = await self.session.scalar(
                select(func.count(PaymentTransaction.id)).where(
                    PaymentTransaction.client_id == client.id,
                    PaymentTransaction.status == TransactionStatus.COMPLETED,
                )
            )
            if completed and completed > 1:
                return
        parent_id: int | None = client.referrer_id
        visited = {client.id}
        for level in range(1, settings.referral_levels + 1):
            if parent_id is None or parent_id in visited:
                break
            visited.add(parent_id)
            referrer = await self.session.get(User, parent_id, with_for_update=True)
            if referrer is None:
                break
            existing = await self.session.scalar(
                select(ReferralReward.id).where(
                    ReferralReward.referrer_id == referrer.id,
                    ReferralReward.transaction_id == transaction.id,
                    ReferralReward.level == level,
                )
            )
            if existing is None:
                value = settings.referral_level_1 if level == 1 else settings.referral_level_2
                reward = (
                    value
                    if settings.referral_reward_kind is ReferralRewardKind.FIXED
                    else transaction.amount * value / Decimal("100")
                )
                reward = money(reward)
                referrer.bonus_balance = money(referrer.bonus_balance + reward)
                self.session.add(
                    ReferralReward(
                        referrer_id=referrer.id,
                        referred_id=client.id,
                        transaction_id=transaction.id,
                        level=level,
                        amount=reward,
                    )
                )
            parent_id = referrer.referrer_id

    async def _refund(self, transaction: PaymentTransaction) -> None:
        transaction.status = TransactionStatus.REFUNDED
        subscription = await self.session.scalar(
            select(ClientExpertSubscription).where(
                ClientExpertSubscription.transaction_id == transaction.id
            )
        )
        if subscription:
            subscription.status = SubscriptionStatus.REFUNDED
        self.session.add(
            FinancialLedgerEntry(
                expert_id=transaction.expert_id,
                client_id=transaction.client_id,
                transaction_id=transaction.id,
                type=LedgerEntryType.REFUND,
                amount=-transaction.expert_income_amount,
                affects_expert_balance=True,
            )
        )
        rewards = list(
            await self.session.scalars(
                select(ReferralReward).where(
                    ReferralReward.transaction_id == transaction.id,
                    ReferralReward.reversed_at.is_(None),
                )
            )
        )
        for reward in rewards:
            referrer = await self.session.get(User, reward.referrer_id, with_for_update=True)
            if referrer:
                referrer.bonus_balance = money(referrer.bonus_balance - reward.amount)
            reward.reversed_at = utcnow()


class MessagingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def send_question(self, actor: User, expert_id: int, text: str) -> ConversationMessage:
        require_role(actor.role, Role.CLIENT, Role.OWNER)
        now = utcnow()
        subscription = await self.session.scalar(
            select(ClientExpertSubscription)
            .where(
                ClientExpertSubscription.client_id == actor.id,
                ClientExpertSubscription.expert_id == expert_id,
                ClientExpertSubscription.status == SubscriptionStatus.ACTIVE,
                ClientExpertSubscription.expires_at > now,
            )
            .order_by(ClientExpertSubscription.expires_at.desc())
            .with_for_update()
        )
        if subscription is None:
            raise SubscriptionRequired("Для отправки сообщения нужна активная подписка")
        if (
            subscription.message_limit is not None
            and subscription.messages_used >= subscription.message_limit
        ):
            raise MessageLimitReached("Лимит сообщений по тарифу исчерпан")
        conversation = await self._conversation(actor.id, expert_id)
        message = ConversationMessage(
            conversation_id=conversation.id,
            sender_id=actor.id,
            sender_role=Role.CLIENT,
            text=validate_text(text),
        )
        subscription.messages_used += 1
        self.session.add(message)
        await self.session.commit()
        await self.session.refresh(message)
        return message

    async def reply(self, actor: User, message_id: int, text: str) -> ConversationMessage:
        require_role(actor.role, Role.EXPERT)
        question = await self.session.scalar(
            select(ConversationMessage)
            .where(ConversationMessage.id == message_id)
            .with_for_update()
        )
        if question is None:
            raise ValidationError("Сообщение не найдено")
        conversation = await self.session.get(Conversation, question.conversation_id)
        profile = await self.session.scalar(
            select(ExpertProfile).where(ExpertProfile.user_id == actor.id)
        )
        if not conversation or not profile or conversation.expert_id != profile.id:
            raise AccessDenied("Нельзя отвечать в чужом диалоге")
        active = await self.session.scalar(
            select(ClientExpertSubscription.id).where(
                ClientExpertSubscription.client_id == conversation.client_id,
                ClientExpertSubscription.expert_id == profile.id,
                ClientExpertSubscription.status == SubscriptionStatus.ACTIVE,
                ClientExpertSubscription.expires_at > utcnow(),
            )
        )
        if active is None:
            raise SubscriptionRequired("Подписка клиента завершена")
        answer = ConversationMessage(
            conversation_id=conversation.id,
            sender_id=actor.id,
            sender_role=Role.EXPERT,
            text=validate_text(text),
            reply_to_id=question.id,
        )
        self.session.add(answer)
        await self.session.commit()
        await self.session.refresh(answer)
        return answer

    async def history(self, actor: User, conversation_id: int) -> list[ConversationMessage]:
        conversation = await self.session.get(Conversation, conversation_id)
        if conversation is None:
            raise ValidationError("Диалог не найден")
        allowed = actor.role is Role.OWNER or conversation.client_id == actor.id
        if actor.role is Role.EXPERT:
            profile_id = await self.session.scalar(
                select(ExpertProfile.id).where(ExpertProfile.user_id == actor.id)
            )
            allowed = profile_id == conversation.expert_id
        if not allowed:
            raise AccessDenied("Нельзя открыть чужой диалог")
        return list(
            await self.session.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation_id)
                .order_by(ConversationMessage.created_at, ConversationMessage.id)
            )
        )

    async def mark_delivered(self, message_id: int) -> None:
        message = await self.session.get(ConversationMessage, message_id)
        if message:
            message.status = MessageStatus.DELIVERED
            message.delivered_at = utcnow()
            await self.session.commit()

    async def _conversation(self, client_id: int, expert_id: int) -> Conversation:
        conversation = await self.session.scalar(
            select(Conversation).where(
                Conversation.client_id == client_id,
                Conversation.expert_id == expert_id,
            )
        )
        if conversation is None:
            conversation = Conversation(client_id=client_id, expert_id=expert_id)
            self.session.add(conversation)
            await self.session.flush()
        return conversation


class FinanceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def available_balance(self, expert_id: int) -> Decimal:
        value = await self.session.scalar(
            select(func.coalesce(func.sum(FinancialLedgerEntry.amount), 0)).where(
                FinancialLedgerEntry.expert_id == expert_id,
                FinancialLedgerEntry.affects_expert_balance.is_(True),
            )
        )
        return money(value or 0)

    async def create_withdrawal(self, actor: User, bank_name: str) -> WithdrawalRequest:
        require_role(actor.role, Role.EXPERT)
        profile = await self.session.scalar(
            select(ExpertProfile).where(ExpertProfile.user_id == actor.id).with_for_update()
        )
        if profile is None or not actor.phone:
            raise ValidationError("Профиль эксперта или телефон не заполнен")
        pending = await self.session.scalar(
            select(WithdrawalRequest.id).where(
                WithdrawalRequest.expert_id == profile.id,
                WithdrawalRequest.status.in_([WithdrawalStatus.PENDING, WithdrawalStatus.APPROVED]),
            )
        )
        if pending:
            raise ValidationError("У вас уже есть активная заявка на вывод")
        balance = await self.available_balance(profile.id)
        if balance <= 0:
            raise InsufficientBalance("Нет средств для вывода")
        request = WithdrawalRequest(
            expert_id=profile.id,
            amount=balance,
            bank_name=validate_text(bank_name, maximum=120),
            phone_snapshot=actor.phone,
        )
        self.session.add(request)
        await self.session.flush()
        self.session.add(
            FinancialLedgerEntry(
                expert_id=profile.id,
                withdrawal_id=request.id,
                type=LedgerEntryType.WITHDRAWAL_RESERVE,
                amount=-balance,
                affects_expert_balance=True,
            )
        )
        await self.session.commit()
        await self.session.refresh(request)
        return request

    async def process_withdrawal(
        self, owner: User, request_id: int, *, paid: bool
    ) -> WithdrawalRequest:
        require_role(owner.role, Role.OWNER)
        request = await self.session.scalar(
            select(WithdrawalRequest).where(WithdrawalRequest.id == request_id).with_for_update()
        )
        if request is None or request.status not in {
            WithdrawalStatus.PENDING,
            WithdrawalStatus.APPROVED,
        }:
            raise ValidationError("Заявка не найдена или уже обработана")
        request.processed_by_id = owner.id
        request.processed_at = utcnow()
        if paid:
            request.status = WithdrawalStatus.PAID
            self.session.add(
                FinancialLedgerEntry(
                    expert_id=request.expert_id,
                    withdrawal_id=request.id,
                    type=LedgerEntryType.WITHDRAWAL_PAID,
                    amount=Decimal("0.00"),
                    affects_expert_balance=False,
                )
            )
        else:
            request.status = WithdrawalStatus.REJECTED
            self.session.add(
                FinancialLedgerEntry(
                    expert_id=request.expert_id,
                    withdrawal_id=request.id,
                    type=LedgerEntryType.WITHDRAWAL_RELEASE,
                    amount=request.amount,
                    affects_expert_balance=True,
                )
            )
        await self.session.commit()
        return request


async def expire_subscriptions(session: AsyncSession) -> int:
    result = await session.execute(
        update(ClientExpertSubscription)
        .where(
            ClientExpertSubscription.status == SubscriptionStatus.ACTIVE,
            ClientExpertSubscription.expires_at <= utcnow(),
        )
        .values(status=SubscriptionStatus.EXPIRED)
    )
    await session.commit()
    return int(result.rowcount)  # type: ignore[attr-defined]
