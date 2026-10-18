from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.policy import menu_for_role
from src.core.enums import Role
from src.infrastructure.database.models import Settings as DatabaseSettings
from src.infrastructure.database.models import User
from src.telegram.banners import BannerCatalog
from src.telegram.routers.helpers import keyboard
from src.telegram.ui import ContextMenu


def main_menu_keyboard(
    role: Role,
    *,
    referral_enabled: bool = True,
    is_owner: bool = False,
) -> InlineKeyboardMarkup:
    if role is Role.CLIENT:
        buttons = [("💎 Мои подписки", "client:subscriptions")]
        if referral_enabled:
            buttons.append(("👥 Реферальная программа", "client:referral"))
        buttons.append(("🆘 Помощь", "common:support"))
        buttons.append(
            ("⚙️ Административная панель", "owner:panel")
            if is_owner
            else ("📝 Стать экспертом", "client:application")
        )
        secondary = keyboard([buttons[index : index + 2] for index in range(0, len(buttons), 2)])
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="👨‍⚕️ Получить доступ к эксперту",
                        callback_data="client:experts",
                        style="primary",
                    )
                ],
                *secondary.inline_keyboard,
            ]
        )
    if role is Role.EXPERT:
        return keyboard(
            [
                [("💬 Диалоги", "expert:dialogs")],
                [("💎 Мои тарифы", "expert:tariffs")],
                [("🪪 Мой профиль", "expert:profile")],
                [("📊 Статистика", "expert:stats"), ("💰 Баланс", "expert:balance")],
                [("🏦 Вывести средства", "expert:withdraw")],
                [("🆘 Помощь", "common:support")],
            ]
        )
    return keyboard(
        [
            [("📊 Статистика", "owner:stats"), ("👥 Пользователи", "owner:users")],
            [("📝 Заявки", "owner:applications"), ("💎 Тарифы", "owner:tariffs")],
            [("🧾 Транзакции", "owner:transactions"), ("🌐 Платежи", "owner:gateways")],
            [("📐 Комиссии", "owner:commissions"), ("🏦 Выводы", "owner:withdrawals")],
            [("📣 Рассылки", "owner:broadcasts"), ("👥 Рефералы", "owner:referrals")],
            [("🔔 Уведомления", "owner:notifications"), ("⚙️ Настройки", "owner:settings")],
            [("🧵 Темы консультаций", "owner:topics")],
            [("↩️ Вернуться в пользовательскую часть", "owner:client_view")],
        ]
    )


def main_menu_content(
    user: User, *, referral_enabled: bool, admin: bool = False
) -> tuple[str, str, InlineKeyboardMarkup]:
    role = Role.OWNER if admin else (Role.CLIENT if user.role is Role.OWNER else user.role)
    descriptor = menu_for_role(role)
    text = f"<b>{descriptor.title}</b>\n\nВыберите действие:"
    markup = main_menu_keyboard(
        role,
        referral_enabled=referral_enabled,
        is_owner=user.role is Role.OWNER and not admin,
    )
    banner_key = "admin_main" if admin else "expert_main" if role is Role.EXPERT else "client_main"
    return text, banner_key, markup


async def show_main(
    event: Message | CallbackQuery,
    session: AsyncSession,
    user: User,
    menu: ContextMenu,
    banners: BannerCatalog,
    *,
    admin: bool = False,
) -> None:
    settings = await session.get(DatabaseSettings, 1)
    text, banner_key, markup = main_menu_content(
        user, referral_enabled=settings is None or settings.referral_enabled, admin=admin
    )
    banner = banners.resolve(banner_key)
    if banner is not None and len(text) <= 1024:
        await menu.render_photo(event, banner, text, markup)
    else:
        await menu.render(event, text, markup)
