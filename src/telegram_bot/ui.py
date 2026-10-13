from html import escape
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class Action(CallbackData, prefix="a"):
    name: str
    ref: str = "0"
    page: int = 0


def button(label: str, name: str, ref: str = "0", page: int = 0) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=label, callback_data=Action(name=name, ref=ref, page=page).pack())


def markup(*rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


async def screen(
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    text: str,
    keyboard: InlineKeyboardMarkup,
    *,
    photo: str | None = None,
) -> None:
    data: dict[str, Any] = await state.get_data()
    old_id = data.get("screen_id")
    old_photo = bool(data.get("screen_photo"))
    old_aux_id = data.get("screen_aux_id")
    # Telegram limits photo captions to 1024 characters. Keep the complete
    # profile text in the menu and show its photo as a companion message.
    long_photo = photo if photo and len(text) > 950 else None
    menu_photo = None if long_photo else photo
    if old_id and old_aux_id is None and long_photo is None and old_photo == bool(menu_photo):
        try:
            if menu_photo:
                from aiogram.types import InputMediaPhoto

                await bot.edit_message_media(
                    chat_id=chat_id, message_id=old_id,
                    media=InputMediaPhoto(media=menu_photo, caption=text, parse_mode="HTML"),
                    reply_markup=keyboard,
                )
            else:
                await bot.edit_message_text(
                    text=text, chat_id=chat_id, message_id=old_id,
                    reply_markup=keyboard, parse_mode="HTML",
                )
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
    if old_id:
        try:
            await bot.delete_message(chat_id, old_id)
        except TelegramBadRequest:
            pass
    if old_aux_id:
        try:
            await bot.delete_message(chat_id, old_aux_id)
        except TelegramBadRequest:
            pass
    aux_id = None
    if long_photo:
        companion = await bot.send_photo(chat_id=chat_id, photo=long_photo)
        aux_id = companion.message_id
    if menu_photo:
        sent = await bot.send_photo(
            chat_id=chat_id, photo=menu_photo, caption=text,
            reply_markup=keyboard, parse_mode="HTML",
        )
    else:
        sent = await bot.send_message(
            chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode="HTML"
        )
    await state.update_data(
        screen_id=sent.message_id,
        screen_photo=bool(menu_photo),
        screen_aux_id=aux_id,
    )


def safe(value: object) -> str:
    return escape(str(value), quote=False)
