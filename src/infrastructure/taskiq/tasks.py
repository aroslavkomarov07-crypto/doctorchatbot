from datetime import timedelta

from aiogram import Bot
from redis.asyncio import Redis
from sqlalchemy import func, or_, select, update

from src.application.services import expire_subscriptions, utcnow
from src.application.topics import TopicProvisioningService, ensure_conversation_topics
from src.core.config import get_settings
from src.core.enums import (
    BroadcastAudience,
    BroadcastStatus,
    ConversationTopicSide,
    ConversationTopicStatus,
    Role,
    SubscriptionStatus,
)
from src.infrastructure.database.models import (
    Broadcast,
    BroadcastMessage,
    ClientExpertSubscription,
    Conversation,
    ConversationTopic,
    User,
)
from src.infrastructure.database.session import Database
from src.infrastructure.taskiq.broker import broker
from src.infrastructure.telegram_notifications import send_notification
from src.infrastructure.telegram_topics import AiogramTopicGateway


@broker.task(schedule=[{"cron": "*/10 * * * *"}])
async def expire_subscriptions_task() -> int:
    database = Database(get_settings())
    try:
        async with database.session_factory() as session:
            return await expire_subscriptions(session)
    finally:
        await database.close()


@broker.task(schedule=[{"cron": "0 * * * *"}])
async def subscription_reminders_task() -> int:
    settings = get_settings()
    database = Database(settings)
    bot = Bot(settings.bot_token.get_secret_value())
    sent = 0
    try:
        async with database.session_factory() as session:
            subscriptions = list(
                await session.scalars(
                    select(ClientExpertSubscription).where(
                        ClientExpertSubscription.status == SubscriptionStatus.ACTIVE,
                        ClientExpertSubscription.expires_at > func.now(),
                        ClientExpertSubscription.expires_at <= utcnow() + timedelta(days=1),
                        ClientExpertSubscription.expiration_notice_sent_at.is_(None),
                    )
                )
            )
            for subscription in subscriptions:
                user = await session.get(User, subscription.client_id)
                if user:
                    conversation = await session.scalar(
                        select(Conversation).where(
                            Conversation.client_id == subscription.client_id,
                            Conversation.expert_id == subscription.expert_id,
                        )
                    )
                    topic = None
                    if conversation:
                        topic = await session.scalar(
                            select(ConversationTopic).where(
                                ConversationTopic.conversation_id == conversation.id,
                                ConversationTopic.side == ConversationTopicSide.CLIENT,
                                ConversationTopic.status == ConversationTopicStatus.READY,
                            )
                        )
                    await send_notification(
                        bot,
                        user.telegram_id,
                        "⏳ Ваша подписка на эксперта закончится менее чем через сутки.",
                        message_thread_id=topic.message_thread_id if topic else None,
                    )
                    subscription.expiration_notice_sent_at = utcnow()
                    sent += 1
            await session.commit()
    finally:
        await bot.session.close()
        await database.close()
    return sent


@broker.task
async def send_broadcast_task(broadcast_id: int) -> tuple[int, int]:
    settings = get_settings()
    database = Database(settings)
    bot = Bot(settings.bot_token.get_secret_value())
    success = 0
    failed = 0
    try:
        async with database.session_factory() as session:
            broadcast = await session.get(Broadcast, broadcast_id, with_for_update=True)
            if broadcast is None or broadcast.status not in {
                BroadcastStatus.DRAFT,
                BroadcastStatus.PROCESSING,
            }:
                return (0, 0)
            broadcast.status = BroadcastStatus.PROCESSING
            query = select(User).where(User.is_blocked.is_(False))
            if broadcast.audience is BroadcastAudience.CLIENTS:
                query = query.where(User.role == Role.CLIENT)
            elif broadcast.audience is BroadcastAudience.EXPERTS:
                query = query.where(User.role == Role.EXPERT)
            elif broadcast.audience is BroadcastAudience.ACTIVE_SUBSCRIBERS:
                query = query.join(
                    ClientExpertSubscription,
                    ClientExpertSubscription.client_id == User.id,
                ).where(
                    ClientExpertSubscription.status == SubscriptionStatus.ACTIVE,
                    ClientExpertSubscription.expires_at > func.now(),
                )
            elif broadcast.audience is BroadcastAudience.INACTIVE_CLIENTS:
                active_ids = select(ClientExpertSubscription.client_id).where(
                    ClientExpertSubscription.status == SubscriptionStatus.ACTIVE,
                    ClientExpertSubscription.expires_at > func.now(),
                )
                query = query.where(User.role == Role.CLIENT, User.id.not_in(active_ids))
            elif broadcast.audience is BroadcastAudience.EXPERT_SUBSCRIBERS:
                query = query.join(
                    ClientExpertSubscription,
                    ClientExpertSubscription.client_id == User.id,
                ).where(
                    ClientExpertSubscription.expert_id
                    == int(broadcast.audience_filter["expert_id"])
                )
            elif broadcast.audience is BroadcastAudience.TARIFF_SUBSCRIBERS:
                query = query.join(
                    ClientExpertSubscription,
                    ClientExpertSubscription.client_id == User.id,
                ).where(
                    ClientExpertSubscription.tariff_id
                    == int(broadcast.audience_filter["tariff_id"])
                )
            users = list(await session.scalars(query.distinct()))
            broadcast.total_count = len(users)
            await session.commit()
            for user in users:
                delivery = BroadcastMessage(broadcast_id=broadcast.id, user_id=user.id)
                try:
                    message = await send_notification(bot, user.telegram_id, broadcast.text)
                    delivery.telegram_message_id = message.message_id
                    delivery.is_sent = True
                    success += 1
                except Exception as error:
                    delivery.error = str(error)[:500]
                    failed += 1
                session.add(delivery)
                await session.commit()
            broadcast = await session.get(Broadcast, broadcast_id, with_for_update=True)
            if broadcast:
                broadcast.success_count = success
                broadcast.failed_count = failed
                broadcast.status = BroadcastStatus.COMPLETED
                await session.commit()
    finally:
        await bot.session.close()
        await database.close()
    return success, failed


@broker.task
async def provision_conversation_topics_task(conversation_id: int) -> int:
    settings = get_settings()
    database = Database(settings)
    bot = Bot(settings.bot_token.get_secret_value())
    redis = Redis.from_url(settings.redis_dsn)
    lock = redis.lock(
        f"doctorshop:topic-provision:{conversation_id}",
        timeout=90,
        blocking_timeout=1,
    )
    acquired = False
    try:
        acquired = bool(await lock.acquire())
        if not acquired:
            return 0
        async with database.session_factory() as session:
            return await TopicProvisioningService(
                session,
                AiogramTopicGateway(bot),
            ).provision(conversation_id)
    finally:
        if acquired:
            try:
                await lock.release()
            except Exception:
                pass
        await redis.aclose()
        await bot.session.close()
        await database.close()


@broker.task(schedule=[{"cron": "*/5 * * * *"}])
async def provision_pending_topics_task() -> int:
    settings = get_settings()
    database = Database(settings)
    queued = 0
    try:
        async with database.session_factory() as session:
            conversation_ids = list(
                await session.scalars(
                    select(ConversationTopic.conversation_id)
                    .join(
                        Conversation,
                        Conversation.id == ConversationTopic.conversation_id,
                    )
                    .join(
                        ClientExpertSubscription,
                        (ClientExpertSubscription.client_id == Conversation.client_id)
                        & (ClientExpertSubscription.expert_id == Conversation.expert_id),
                    )
                    .where(
                        ConversationTopic.status.in_(
                            [
                                ConversationTopicStatus.PENDING,
                                ConversationTopicStatus.FAILED,
                                ConversationTopicStatus.UNAVAILABLE,
                            ]
                        ),
                        ConversationTopic.retry_count < 8,
                        or_(
                            ConversationTopic.last_retry_at.is_(None),
                            ConversationTopic.last_retry_at <= utcnow() - timedelta(minutes=5),
                        ),
                        ClientExpertSubscription.status == SubscriptionStatus.ACTIVE,
                        ClientExpertSubscription.expires_at > func.now(),
                    )
                    .distinct()
                )
            )
        for conversation_id in conversation_ids:
            await provision_conversation_topics_task.kiq(conversation_id)
            queued += 1
    finally:
        await database.close()
    return queued


@broker.task
async def backfill_conversation_topics_task() -> int:
    settings = get_settings()
    database = Database(settings)
    conversation_ids: set[int] = set()
    try:
        async with database.session_factory() as session:
            pairs = (
                await session.execute(
                    select(
                        ClientExpertSubscription.client_id,
                        ClientExpertSubscription.expert_id,
                    )
                    .where(
                        ClientExpertSubscription.status == SubscriptionStatus.ACTIVE,
                        ClientExpertSubscription.expires_at > func.now(),
                    )
                    .distinct()
                )
            ).all()
            for client_id, expert_id in pairs:
                conversation = await ensure_conversation_topics(
                    session,
                    client_id=client_id,
                    expert_id=expert_id,
                )
                conversation_ids.add(conversation.id)
                await session.commit()
        for conversation_id in conversation_ids:
            await provision_conversation_topics_task.kiq(conversation_id)
    finally:
        await database.close()
    return len(conversation_ids)


@broker.task
async def retry_failed_topics_task() -> int:
    settings = get_settings()
    database = Database(settings)
    try:
        async with database.session_factory() as session:
            result = await session.execute(
                update(ConversationTopic)
                .where(
                    ConversationTopic.status.in_(
                        [ConversationTopicStatus.FAILED, ConversationTopicStatus.UNAVAILABLE]
                    )
                )
                .values(
                    status=ConversationTopicStatus.PENDING,
                    retry_count=0,
                    last_error_code=None,
                    last_retry_at=None,
                )
            )
            await session.commit()
            count = int(result.rowcount)  # type: ignore[attr-defined]
        await provision_pending_topics_task.kiq()
        return count
    finally:
        await database.close()
