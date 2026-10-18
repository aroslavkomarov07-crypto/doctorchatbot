from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.dto import IncomingTopicMessage, TopicRelayResult
from src.application.ports import TelegramTopicGateway
from src.application.validation import safe_html
from src.core.enums import (
    ConversationTopicSide,
    ConversationTopicStatus,
    MessageStatus,
    Role,
    SubscriptionStatus,
)
from src.infrastructure.database.models import (
    ClientExpertSubscription,
    Conversation,
    ConversationMessage,
    ConversationTopic,
    ExpertProfile,
    User,
)


def _topic_name(value: str) -> str:
    normalized = " ".join(value.replace("<", "").replace(">", "").split())
    return (normalized or "Консультация")[:128]


def utcnow() -> datetime:
    return datetime.now(UTC)


def safe_telegram_error(error: Exception) -> str:
    message = str(error).lower()
    if "message thread not found" in message or "message_thread_invalid" in message:
        return "MESSAGE_THREAD_NOT_FOUND"
    if "chat not found" in message:
        return "CHAT_NOT_FOUND"
    if "forbidden" in message or "bot was blocked" in message:
        return "FORBIDDEN"
    if "topic" in message or "forum" in message:
        return "TOPICS_UNAVAILABLE"
    return type(error).__name__.upper()[:120]


async def ensure_conversation_topics(
    session: AsyncSession,
    *,
    client_id: int,
    expert_id: int,
) -> Conversation:
    """Persist the durable work records in the caller's transaction."""
    await session.scalar(select(User.id).where(User.id == client_id).with_for_update())
    conversation = await session.scalar(
        select(Conversation)
        .where(Conversation.client_id == client_id, Conversation.expert_id == expert_id)
        .with_for_update()
    )
    if conversation is None:
        conversation = Conversation(client_id=client_id, expert_id=expert_id)
        session.add(conversation)
        await session.flush()

    client = await session.get(User, client_id)
    expert = await session.get(ExpertProfile, expert_id)
    expert_user = await session.get(User, expert.user_id if expert else 0)
    if client is None or expert is None or expert_user is None:
        raise RuntimeError("Conversation participants are missing")

    existing = {
        topic.side: topic
        for topic in await session.scalars(
            select(ConversationTopic).where(ConversationTopic.conversation_id == conversation.id)
        )
    }
    specs = (
        (
            ConversationTopicSide.CLIENT,
            client,
            _topic_name(f"🩺 {expert.specialization} — {expert.display_name}"),
        ),
        (
            ConversationTopicSide.EXPERT,
            expert_user,
            _topic_name(f"👤 {client.full_name or client.telegram_name}"),
        ),
    )
    for side, participant, name in specs:
        topic = existing.get(side)
        if topic is None:
            session.add(
                ConversationTopic(
                    conversation_id=conversation.id,
                    participant_user_id=participant.id,
                    side=side,
                    telegram_chat_id=participant.telegram_id,
                    topic_name_snapshot=name,
                    status=ConversationTopicStatus.PENDING,
                )
            )
        elif topic.message_thread_id is None:
            topic.telegram_chat_id = participant.telegram_id
            topic.topic_name_snapshot = name
            if topic.status is ConversationTopicStatus.UNAVAILABLE:
                topic.status = ConversationTopicStatus.PENDING
    await session.flush()
    return conversation


class TopicProvisioningService:
    def __init__(self, session: AsyncSession, gateway: TelegramTopicGateway) -> None:
        self.session = session
        self.gateway = gateway

    async def provision(self, conversation_id: int) -> int:
        conversation = await self.session.get(Conversation, conversation_id)
        if conversation is None:
            return 0
        conversation = await ensure_conversation_topics(
            self.session,
            client_id=conversation.client_id,
            expert_id=conversation.expert_id,
        )
        await self.session.commit()

        subscription = await self._latest_active_subscription(conversation)
        if subscription is None:
            return 0
        if not await self.gateway.topics_enabled():
            await self._mark_all_failed(conversation.id, "TOPICS_DISABLED")
            return 0

        topics = list(
            await self.session.scalars(
                select(ConversationTopic)
                .where(ConversationTopic.conversation_id == conversation.id)
                .order_by(ConversationTopic.side)
            )
        )
        ready = 0
        for topic in topics:
            created_now = False
            try:
                if (
                    topic.status is not ConversationTopicStatus.READY
                    or topic.message_thread_id is None
                ):
                    topic.status = ConversationTopicStatus.CREATING
                    topic.retry_count += 1
                    topic.last_retry_at = utcnow()
                    topic.last_error_code = None
                    await self.session.commit()
                    topic.message_thread_id = await self.gateway.create_topic(
                        chat_id=topic.telegram_chat_id,
                        name=topic.topic_name_snapshot,
                    )
                    topic.status = ConversationTopicStatus.READY
                    created_now = True
                    await self.session.commit()
                if topic.last_announced_subscription_id != subscription.id:
                    await self._announce(topic, conversation, subscription.expires_at, created_now)
                    topic.last_announced_subscription_id = subscription.id
                    await self.session.commit()
                ready += 1
            except Exception as error:
                await self.session.rollback()
                current = await self.session.get(ConversationTopic, topic.id, with_for_update=True)
                if current is not None:
                    current.status = ConversationTopicStatus.FAILED
                    current.last_error_code = safe_telegram_error(error)
                    current.last_retry_at = utcnow()
                    await self.session.commit()
        return ready

    async def _latest_active_subscription(
        self, conversation: Conversation
    ) -> ClientExpertSubscription | None:
        return await self.session.scalar(
            select(ClientExpertSubscription)
            .where(
                ClientExpertSubscription.client_id == conversation.client_id,
                ClientExpertSubscription.expert_id == conversation.expert_id,
                ClientExpertSubscription.status == SubscriptionStatus.ACTIVE,
                ClientExpertSubscription.expires_at > utcnow(),
            )
            .order_by(ClientExpertSubscription.expires_at.desc())
        )

    async def _mark_all_failed(self, conversation_id: int, code: str) -> None:
        topics = list(
            await self.session.scalars(
                select(ConversationTopic).where(
                    ConversationTopic.conversation_id == conversation_id,
                    ConversationTopic.status != ConversationTopicStatus.READY,
                )
            )
        )
        for topic in topics:
            topic.status = ConversationTopicStatus.FAILED
            topic.last_error_code = code
            topic.retry_count += 1
            topic.last_retry_at = utcnow()
        await self.session.commit()

    async def _announce(
        self,
        topic: ConversationTopic,
        conversation: Conversation,
        expires_at: datetime | None,
        created_now: bool,
    ) -> None:
        if topic.message_thread_id is None:
            return
        client = await self.session.get(User, conversation.client_id)
        expert = await self.session.get(ExpertProfile, conversation.expert_id)
        if client is None or expert is None:
            return
        expires = expires_at.strftime("%d.%m.%Y %H:%M") if expires_at else "не указана"
        if created_now and topic.side is ConversationTopicSide.CLIENT:
            text = (
                "<b>Консультация с экспертом</b>\n\n"
                f"Эксперт: {safe_html(expert.display_name)}\n"
                f"Специализация: {safe_html(expert.specialization)}\n"
                f"Подписка действует до: {expires}\n\n"
                "Напишите сообщение в этой теме — бот передаст его эксперту."
            )
        elif created_now:
            text = (
                "<b>Новый клиент</b>\n\n"
                f"Клиент: {safe_html(client.full_name or client.telegram_name)}\n"
                f"Подписка действует до: {expires}\n\n"
                "Все сообщения этого клиента будут находиться в данной теме."
            )
        else:
            text = (
                "✅ <b>Консультация возобновлена</b>\n\n"
                f"Новый срок подписки: {expires}. Можно продолжить переписку в этой теме."
            )
        await self.gateway.send_text(
            chat_id=topic.telegram_chat_id,
            thread_id=topic.message_thread_id,
            text=text,
            dismissible=True,
        )


class TopicRelayService:
    def __init__(self, session: AsyncSession, gateway: TelegramTopicGateway) -> None:
        self.session = session
        self.gateway = gateway

    async def relay(self, incoming: IncomingTopicMessage) -> TopicRelayResult:
        source = await self.session.scalar(
            select(ConversationTopic)
            .where(
                ConversationTopic.telegram_chat_id == incoming.chat_id,
                ConversationTopic.message_thread_id == incoming.thread_id,
            )
            .with_for_update()
        )
        if source is None:
            return TopicRelayResult(status="UNKNOWN_TOPIC")
        participant = await self.session.get(User, source.participant_user_id)
        if (
            participant is None
            or participant.telegram_id != incoming.sender_telegram_id
            or participant.is_blocked
        ):
            return TopicRelayResult(status="FORBIDDEN", conversation_id=source.conversation_id)

        duplicate = await self.session.scalar(
            select(ConversationMessage.id).where(
                ConversationMessage.source_chat_id == incoming.chat_id,
                ConversationMessage.source_telegram_message_id == incoming.telegram_message_id,
            )
        )
        if duplicate is not None:
            return TopicRelayResult(status="DUPLICATE", conversation_id=source.conversation_id)

        conversation = await self.session.get(Conversation, source.conversation_id)
        if conversation is None:
            return TopicRelayResult(status="UNKNOWN_TOPIC")
        destination = await self.session.scalar(
            select(ConversationTopic).where(
                ConversationTopic.conversation_id == conversation.id,
                ConversationTopic.side != source.side,
            )
        )
        if (
            destination is None
            or destination.status is not ConversationTopicStatus.READY
            or destination.message_thread_id is None
        ):
            return TopicRelayResult(
                status="TOPIC_PENDING",
                conversation_id=conversation.id,
                destination_topic_id=destination.id if destination else None,
            )

        subscription = await self.session.scalar(
            select(ClientExpertSubscription)
            .where(
                ClientExpertSubscription.client_id == conversation.client_id,
                ClientExpertSubscription.expert_id == conversation.expert_id,
                ClientExpertSubscription.status == SubscriptionStatus.ACTIVE,
                ClientExpertSubscription.expires_at > utcnow(),
            )
            .order_by(ClientExpertSubscription.expires_at.desc())
            .with_for_update()
        )
        if subscription is None:
            return TopicRelayResult(status="SUBSCRIPTION_REQUIRED", conversation_id=conversation.id)
        if source.side is ConversationTopicSide.CLIENT:
            if (
                subscription.message_limit is not None
                and subscription.messages_used >= subscription.message_limit
            ):
                return TopicRelayResult(status="MESSAGE_LIMIT", conversation_id=conversation.id)
            subscription.messages_used += 1

        reply = await self._reply_target(source, destination, incoming)
        stored_text = incoming.text.strip()[:4000]
        message = ConversationMessage(
            conversation_id=conversation.id,
            sender_id=participant.id,
            sender_role=(
                Role.CLIENT if source.side is ConversationTopicSide.CLIENT else Role.EXPERT
            ),
            text=stored_text,
            media_type=None if incoming.content_type == "text" else incoming.content_type,
            media_file_id=incoming.file_id,
            source_topic_id=source.id,
            destination_topic_id=destination.id,
            source_chat_id=incoming.chat_id,
            source_telegram_message_id=incoming.telegram_message_id,
            reply_to_id=reply[0] if reply else None,
            status=MessageStatus.CREATED,
        )
        self.session.add(message)
        await self.session.commit()
        await self.session.refresh(message)

        try:
            if incoming.content_type == "text":
                delivery = await self.gateway.send_text(
                    chat_id=destination.telegram_chat_id,
                    thread_id=destination.message_thread_id,
                    text=safe_html(stored_text),
                    reply_to_message_id=reply[1] if reply else None,
                )
            else:
                delivery = await self.gateway.copy_message(
                    incoming=incoming,
                    destination_chat_id=destination.telegram_chat_id,
                    destination_thread_id=destination.message_thread_id,
                    reply_to_message_id=reply[1] if reply else None,
                )
            current = await self.session.get(ConversationMessage, message.id, with_for_update=True)
            if current is not None:
                current.destination_telegram_message_id = delivery.telegram_message_id
                current.status = MessageStatus.DELIVERED
                current.delivered_at = utcnow()
                await self.session.commit()
            return TopicRelayResult(
                status="DELIVERED",
                conversation_id=conversation.id,
                destination_topic_id=destination.id,
            )
        except Exception as error:
            await self.session.rollback()
            code = safe_telegram_error(error)
            current = await self.session.get(ConversationMessage, message.id, with_for_update=True)
            if current is not None:
                current.status = MessageStatus.FAILED
                current.failure_code = code
            if source.side is ConversationTopicSide.CLIENT:
                locked_subscription = await self.session.get(
                    ClientExpertSubscription, subscription.id, with_for_update=True
                )
                if locked_subscription and locked_subscription.messages_used > 0:
                    locked_subscription.messages_used -= 1
            if code == "MESSAGE_THREAD_NOT_FOUND":
                locked_topic = await self.session.get(
                    ConversationTopic, destination.id, with_for_update=True
                )
                if locked_topic is not None:
                    locked_topic.status = ConversationTopicStatus.UNAVAILABLE
                    locked_topic.message_thread_id = None
                    locked_topic.last_error_code = code
            await self.session.commit()
            return TopicRelayResult(
                status="DELIVERY_FAILED",
                conversation_id=conversation.id,
                destination_topic_id=destination.id,
            )

    async def _reply_target(
        self,
        source: ConversationTopic,
        destination: ConversationTopic,
        incoming: IncomingTopicMessage,
    ) -> tuple[int, int] | None:
        if incoming.reply_to_telegram_message_id is None:
            return None
        referenced = await self.session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == source.conversation_id,
                or_(
                    (
                        (ConversationMessage.source_topic_id == source.id)
                        & (
                            ConversationMessage.source_telegram_message_id
                            == incoming.reply_to_telegram_message_id
                        )
                    ),
                    (
                        (ConversationMessage.destination_topic_id == source.id)
                        & (
                            ConversationMessage.destination_telegram_message_id
                            == incoming.reply_to_telegram_message_id
                        )
                    ),
                ),
            )
        )
        if referenced is None:
            return None
        if referenced.source_topic_id == destination.id:
            target = referenced.source_telegram_message_id
        elif referenced.destination_topic_id == destination.id:
            target = referenced.destination_telegram_message_id
        else:
            target = None
        return (referenced.id, target) if target is not None else None
