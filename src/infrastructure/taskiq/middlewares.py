from __future__ import annotations

from typing import Any

from aiogram import Bot
from taskiq import TaskiqMessage, TaskiqResult
from taskiq.abc.middleware import TaskiqMiddleware

from src.core.config import get_settings
from src.infrastructure.error_reporting import ErrorReporter
from src.infrastructure.logging import configure_logging


class TaskErrorReportingMiddleware(TaskiqMiddleware):
    async def on_error(
        self,
        message: TaskiqMessage,
        result: TaskiqResult[Any],
        exception: BaseException,
    ) -> None:
        del result
        settings = get_settings()
        configure_logging(settings)
        bot = Bot(settings.bot_token.get_secret_value())
        try:
            await ErrorReporter(bot, settings).report(
                exception,
                source=f"taskiq:{message.task_name}",
                context={"task_name": message.task_name},
            )
        finally:
            await bot.session.close()
