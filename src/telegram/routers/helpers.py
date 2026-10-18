from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.application.validation import safe_html


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
