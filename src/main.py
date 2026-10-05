import asyncio
import logging

from aiogram import Bot

from core.config import get_settings
from infrastructure.database.database import AsyncSessionLocal
from telegram_bot.dispatcher import create_dispatcher


async def main() -> None:
    settings = get_settings()
    if settings.bot_token is None:
        raise RuntimeError("BOT_TOKEN не задан. Добавьте его в .env.")

    bot = Bot(token=settings.bot_token.get_secret_value())
    dispatcher = create_dispatcher(AsyncSessionLocal)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
