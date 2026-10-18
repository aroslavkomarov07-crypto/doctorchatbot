from aiogram import Bot

from src.application.dto import IncomingTopicMessage, TopicDelivery
from src.infrastructure.telegram_notifications import notification_keyboard


class AiogramTopicGateway:
    """Thin Telegram adapter; application services never receive aiogram updates."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def topics_enabled(self) -> bool:
        me = await self.bot.get_me()
        return me.has_topics_enabled is True

    async def create_topic(self, *, chat_id: int, name: str) -> int:
        topic = await self.bot.create_forum_topic(chat_id=chat_id, name=name[:128])
        return topic.message_thread_id

    async def send_text(
        self,
        *,
        chat_id: int,
        thread_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        dismissible: bool = False,
    ) -> TopicDelivery:
        message = await self.bot.send_message(
            chat_id=chat_id,
            message_thread_id=thread_id,
            text=text,
            reply_markup=notification_keyboard() if dismissible else None,
            parse_mode="HTML",
            reply_to_message_id=reply_to_message_id,
            allow_sending_without_reply=True,
        )
        return TopicDelivery(telegram_message_id=message.message_id)

    async def copy_message(
        self,
        *,
        incoming: IncomingTopicMessage,
        destination_chat_id: int,
        destination_thread_id: int,
        reply_to_message_id: int | None = None,
    ) -> TopicDelivery:
        copied = await self.bot.copy_message(
            chat_id=destination_chat_id,
            from_chat_id=incoming.chat_id,
            message_id=incoming.telegram_message_id,
            message_thread_id=destination_thread_id,
            reply_to_message_id=reply_to_message_id,
            allow_sending_without_reply=True,
        )
        return TopicDelivery(telegram_message_id=copied.message_id)
