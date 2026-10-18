from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from src.infrastructure.telegram_notifications import DISMISS_NOTIFICATION


def build_notifications_router() -> Router:
    router = Router(name="notifications")

    @router.callback_query(F.data == DISMISS_NOTIFICATION)
    async def close_notification(callback: CallbackQuery, bot: Bot) -> None:
        message = callback.message
        if (
            message is None
            or message.chat.type != ChatType.PRIVATE
            or message.chat.id != callback.from_user.id
        ):
            await callback.answer("Уведомление недоступно", show_alert=True)
            return
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        except TelegramBadRequest as error:
            if "message to delete not found" not in str(error).lower():
                await callback.answer(
                    "Telegram не разрешает удалить это сообщение. "
                    "Если ему больше 48 часов, удалите его вручную.",
                    show_alert=True,
                )
                return
        await callback.answer()

    return router
