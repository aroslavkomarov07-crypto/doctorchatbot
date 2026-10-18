from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

DISMISS_NOTIFICATION = "notification:close"


def notification_keyboard(
    markup: InlineKeyboardMarkup | None = None,
) -> InlineKeyboardMarkup:
    """Keep notification actions and append a single dismiss button."""
    rows = [list(row) for row in markup.inline_keyboard] if markup else []
    if not any(button.callback_data == DISMISS_NOTIFICATION for row in rows for button in row):
        rows.append([InlineKeyboardButton(text="✖️ Закрыть", callback_data=DISMISS_NOTIFICATION)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def is_notification(markup: InlineKeyboardMarkup | None) -> bool:
    return bool(
        markup
        and any(
            button.callback_data == DISMISS_NOTIFICATION
            for row in markup.inline_keyboard
            for button in row
        )
    )


async def send_notification(
    bot: Bot,
    chat_id: int,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    message_thread_id: int | None = None,
) -> Message:
    return await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=notification_keyboard(reply_markup),
        message_thread_id=message_thread_id,
    )
