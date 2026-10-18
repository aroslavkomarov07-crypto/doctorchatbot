import asyncio

from aiogram import Bot
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.dto import TopicRelayResult
from src.infrastructure.database.models import ConversationTopic, User
from src.infrastructure.database.models import Settings as DatabaseSettings
from src.infrastructure.error_reporting import ErrorReporter
from src.telegram.banners import BannerCatalog
from src.telegram.main_menu import main_menu_content
from src.telegram.ui import ContextMenu


class TopicMenuRefresher:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        menu: ContextMenu,
        banners: BannerCatalog,
        error_reporter: ErrorReporter,
    ) -> None:
        self.session_factory = session_factory
        self.menu = menu
        self.banners = banners
        self.error_reporter = error_reporter

    async def after_message(
        self,
        bot: Bot,
        storage: BaseStorage,
        actor: User | None,
        result: TopicRelayResult,
    ) -> None:
        if (
            actor is None
            or result.conversation_id is None
            or result.status in {"DUPLICATE", "FORBIDDEN"}
        ):
            return
        user_ids = {actor.id}
        async with self.session_factory() as session:
            if result.status == "DELIVERED" and result.destination_topic_id is not None:
                topic = await session.get(ConversationTopic, result.destination_topic_id)
                if topic is not None:
                    user_ids.add(topic.participant_user_id)
            users = list(await session.scalars(select(User).where(User.id.in_(user_ids))))
            settings = await session.get(DatabaseSettings, 1)
            referral_enabled = settings is None or settings.referral_enabled
        await asyncio.gather(
            *(
                self._refresh(bot, storage, user, referral_enabled)
                for user in users
                if user.registration_completed and not user.is_blocked
            )
        )

    async def _refresh(
        self, bot: Bot, storage: BaseStorage, user: User, referral_enabled: bool
    ) -> None:
        try:
            active_state = await storage.get_state(
                StorageKey(bot_id=bot.id, chat_id=user.telegram_id, user_id=user.telegram_id)
            )
            text, banner_key, markup = main_menu_content(user, referral_enabled=referral_enabled)
            await self.menu.move_to_bottom(
                bot,
                user.telegram_id,
                text,
                markup,
                photo=self.banners.resolve(banner_key),
                preserve_current=active_state is not None,
            )
        except Exception as error:
            # Menu failures must not turn an already delivered consultation into a retry.
            await self.error_reporter.report(
                error,
                source="telegram:topic-menu",
                context={"telegram_id": user.telegram_id},
            )
