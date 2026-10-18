from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InputFile, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.config import Settings as AppSettings
from src.core.enums import Role
from src.core.exceptions import ValidationError
from src.infrastructure.database.models import User
from src.telegram.banners import BannerCatalog
from src.telegram.ui import ContextMenu


@dataclass(slots=True)
class OwnerRouterContext:
    """Shared dependencies and access checks for owner feature routers."""

    session_factory: async_sessionmaker[AsyncSession]
    config: AppSettings
    menu: ContextMenu
    banners: BannerCatalog

    async def owner(self, session: AsyncSession, telegram_id: int) -> User:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None or not user.registration_completed:
            raise ValidationError("Пользователь не зарегистрирован. Нажмите /start")
        if user.is_blocked:
            raise ValidationError("Ваш аккаунт заблокирован")
        if user.role is not Role.OWNER:
            raise ValidationError("Доступно только владельцу")
        return user

    async def show(
        self,
        event: Message | CallbackQuery,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        banner = self.banners.resolve("admin_main")
        if banner is not None and len(text) <= 1024:
            return await self.menu.render_photo(event, banner, text, reply_markup)
        return await self.menu.render(event, text, reply_markup)

    async def show_photo(
        self,
        event: Message | CallbackQuery,
        photo: str | InputFile,
        caption: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        return await self.menu.render_photo(event, photo, caption, reply_markup)

    async def show_screen(
        self,
        event: Message | CallbackQuery,
        banner_key: str,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        banner = self.banners.resolve(banner_key)
        if banner is not None and len(text) <= 1024:
            return await self.show_photo(event, banner, text, reply_markup)
        return await self.show(event, text, reply_markup)
