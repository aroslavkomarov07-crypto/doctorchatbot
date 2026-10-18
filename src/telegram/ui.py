from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import cast

import orjson
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    InputFile,
    InputMediaPhoto,
    Message,
)
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infrastructure.database.models import User
from src.infrastructure.telegram_notifications import is_notification


@dataclass(slots=True)
class MenuAnchor:
    chat_id: int
    message_id: int
    kind: str


class ContextMenu:
    """Keeps one editable control-plane message per Telegram user."""

    def __init__(
        self,
        redis: Redis,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.redis = redis
        self.session_factory = session_factory

    async def render(
        self,
        event: Message | CallbackQuery,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        _, user_id, _ = self._context(event)
        async with self.redis.lock(self._lock_key(user_id), timeout=120, blocking_timeout=60):
            return await self._render_text(event, text, reply_markup)

    async def _render_text(
        self,
        event: Message | CallbackQuery,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        bot, user_id, chat_id = self._context(event)
        anchor = await self._anchor(user_id)
        if anchor is None:
            anchor = self._anchor_from_callback(event)
        if anchor is not None and anchor.chat_id == chat_id and anchor.kind == "text":
            try:
                result = await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=anchor.message_id,
                    text=text,
                    reply_markup=reply_markup,
                )
                rendered = cast(Message, result)
                await self._remember_screen(user_id, rendered)
                await self._delete_incoming(event, bot, rendered)
                return rendered
            except TelegramBadRequest as error:
                if "message is not modified" in str(error).lower():
                    if isinstance(event, CallbackQuery) and isinstance(event.message, Message):
                        return event.message
                await self._delete_anchor(bot, anchor)
            except TelegramAPIError:
                await self._delete_anchor(bot, anchor)
        elif anchor is not None:
            await self._delete_anchor(bot, anchor)

        message = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        await self._save(user_id, MenuAnchor(chat_id, message.message_id, "text"))
        await self._remember_screen(user_id, message)
        await self._delete_incoming(event, bot, message)
        return message

    async def render_photo(
        self,
        event: Message | CallbackQuery,
        photo: str | InputFile,
        caption: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        _, user_id, _ = self._context(event)
        async with self.redis.lock(self._lock_key(user_id), timeout=120, blocking_timeout=60):
            return await self._render_photo(event, photo, caption, reply_markup)

    async def _render_photo(
        self,
        event: Message | CallbackQuery,
        photo: str | InputFile,
        caption: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        bot, user_id, chat_id = self._context(event)
        anchor = await self._anchor(user_id)
        if anchor is None:
            anchor = self._anchor_from_callback(event)
        if anchor is not None and anchor.chat_id == chat_id and anchor.kind == "photo":
            try:
                result = await bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=anchor.message_id,
                    media=InputMediaPhoto(media=photo, caption=caption),
                    reply_markup=reply_markup,
                )
                rendered = cast(Message, result)
                await self._remember_screen(user_id, rendered)
                await self._delete_incoming(event, bot, rendered)
                return rendered
            except TelegramBadRequest as error:
                if "message is not modified" in str(error).lower():
                    if isinstance(event, CallbackQuery) and isinstance(event.message, Message):
                        return event.message
                await self._delete_anchor(bot, anchor)
            except TelegramAPIError:
                await self._delete_anchor(bot, anchor)
        elif anchor is not None:
            await self._delete_anchor(bot, anchor)

        message = await bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=caption,
            reply_markup=reply_markup,
        )
        await self._save(user_id, MenuAnchor(chat_id, message.message_id, "photo"))
        await self._remember_screen(user_id, message)
        await self._delete_incoming(event, bot, message)
        return message

    async def clear(self, user_id: int, bot: Bot) -> None:
        async with self.redis.lock(self._lock_key(user_id), timeout=120, blocking_timeout=60):
            anchor = await self._anchor(user_id)
            if anchor is not None:
                await self._delete_anchor(bot, anchor)
            await self._forget(user_id)

    async def move_to_bottom(
        self,
        bot: Bot,
        user_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None,
        *,
        photo: str | InputFile | None = None,
        preserve_current: bool = False,
    ) -> Message:
        """Repost one control screen silently outside consultation topics."""
        async with self.redis.lock(self._lock_key(user_id), timeout=120, blocking_timeout=60):
            anchor = await self._anchor(user_id)
            if preserve_current and anchor is not None:
                raw = await self.redis.get(self._screen_key(user_id))
                if raw is not None:
                    try:
                        screen = orjson.loads(raw)
                        markup_data = screen["reply_markup"]
                        restored_markup = (
                            InlineKeyboardMarkup.model_validate(markup_data)
                            if markup_data is not None
                            else None
                        )
                        text = str(screen["text"])
                        photo = screen["photo"]
                        reply_markup = restored_markup
                    except (KeyError, TypeError, ValueError, orjson.JSONDecodeError):
                        await self.redis.delete(self._screen_key(user_id))
            if photo is not None:
                message = await bot.send_photo(
                    chat_id=user_id,
                    photo=photo,
                    caption=text,
                    reply_markup=reply_markup,
                    disable_notification=True,
                )
            else:
                message = await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=reply_markup,
                    disable_notification=True,
                )
            # Keep the previous screen until Telegram accepts its replacement.
            await self._save(
                user_id, MenuAnchor(user_id, message.message_id, "photo" if photo else "text")
            )
            await self._remember_screen(user_id, message)
            if anchor is not None:
                await self._delete_anchor(bot, anchor)
            return message

    async def _remember_screen(self, user_id: int, message: Message) -> None:
        await self.redis.set(
            self._screen_key(user_id),
            orjson.dumps(
                {
                    "text": message.html_text,
                    "photo": message.photo[-1].file_id if message.photo else None,
                    "reply_markup": (
                        message.reply_markup.model_dump(mode="json")
                        if message.reply_markup
                        else None
                    ),
                }
            ),
        )

    def _context(self, event: Message | CallbackQuery) -> tuple[Bot, int, int]:
        bot = event.bot
        if bot is None:
            raise RuntimeError("Telegram bot context is missing")
        if isinstance(event, Message):
            if event.from_user is None:
                raise RuntimeError("Telegram user context is missing")
            return bot, event.from_user.id, event.chat.id
        if not isinstance(event.message, Message):
            raise RuntimeError("Callback message context is missing")
        return bot, event.from_user.id, event.message.chat.id

    def _anchor_from_callback(self, event: Message | CallbackQuery) -> MenuAnchor | None:
        if not isinstance(event, CallbackQuery) or not isinstance(event.message, Message):
            return None
        if event.message.is_topic_message:
            return None
        content = event.message.text or event.message.caption or ""
        if (
            content.startswith("#")
            or (event.data or "").startswith("client:answer:")
            or is_notification(event.message.reply_markup)
        ):
            # Notifications must not become menu anchors after a cache restart.
            return None
        return MenuAnchor(
            chat_id=event.message.chat.id,
            message_id=event.message.message_id,
            kind="photo" if event.message.photo else "text",
        )

    async def _anchor(self, user_id: int) -> MenuAnchor | None:
        raw = await self.redis.get(self._key(user_id))
        if raw is not None:
            try:
                data = orjson.loads(raw)
                return MenuAnchor(
                    chat_id=int(data["chat_id"]),
                    message_id=int(data["message_id"]),
                    kind=str(data["kind"]),
                )
            except (KeyError, TypeError, ValueError, orjson.JSONDecodeError):
                await self.redis.delete(self._key(user_id))
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(
                        User.menu_chat_id,
                        User.menu_message_id,
                        User.menu_message_kind,
                    ).where(User.telegram_id == user_id)
                )
            ).one_or_none()
        if row is None or row.menu_chat_id is None or row.menu_message_id is None:
            return None
        anchor = MenuAnchor(
            chat_id=int(row.menu_chat_id),
            message_id=int(row.menu_message_id),
            kind=row.menu_message_kind or "text",
        )
        await self.redis.set(self._key(user_id), orjson.dumps(asdict(anchor)))
        return anchor

    async def _save(self, user_id: int, anchor: MenuAnchor) -> None:
        await self.redis.set(self._key(user_id), orjson.dumps(asdict(anchor)))
        async with self.session_factory() as session:
            await session.execute(
                update(User)
                .where(User.telegram_id == user_id)
                .values(
                    menu_chat_id=anchor.chat_id,
                    menu_message_id=anchor.message_id,
                    menu_message_kind=anchor.kind,
                )
            )
            await session.commit()

    async def _forget(self, user_id: int) -> None:
        await self.redis.delete(self._key(user_id))
        await self.redis.delete(self._screen_key(user_id))
        async with self.session_factory() as session:
            await session.execute(
                update(User)
                .where(User.telegram_id == user_id)
                .values(
                    menu_chat_id=None,
                    menu_message_id=None,
                    menu_message_kind=None,
                )
            )
            await session.commit()

    async def _delete_anchor(self, bot: Bot, anchor: MenuAnchor) -> None:
        try:
            await bot.delete_message(chat_id=anchor.chat_id, message_id=anchor.message_id)
        except TelegramAPIError:
            pass

    @staticmethod
    async def _delete_incoming(
        event: Message | CallbackQuery,
        bot: Bot,
        rendered: Message,
    ) -> None:
        """Remove user input after it has been consumed by the context menu."""
        if not isinstance(event, Message):
            return
        if event.chat.id == rendered.chat.id and event.message_id == rendered.message_id:
            return
        try:
            await bot.delete_message(chat_id=event.chat.id, message_id=event.message_id)
        except TelegramAPIError:
            pass

    @staticmethod
    def _key(user_id: int) -> str:
        return f"doctorshop:context-menu:{user_id}"

    @staticmethod
    def _screen_key(user_id: int) -> str:
        return f"doctorshop:context-menu-screen:{user_id}"

    @staticmethod
    def _lock_key(user_id: int) -> str:
        return f"doctorshop:context-menu-lock:{user_id}"
