from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    MenuButtonCommands,
)


async def setup_bot_commands(bot: Bot) -> None:
    """Expose the command menu button at the bottom of every private chat."""
    commands = [
        BotCommand(command="start", description="Перезапустить бота"),
        BotCommand(command="menu", description="Открыть главное меню"),
    ]
    scope = BotCommandScopeAllPrivateChats()
    await bot.set_my_commands(commands, scope=scope)
    await bot.set_my_commands(commands, scope=scope, language_code="ru")
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
