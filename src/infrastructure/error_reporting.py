from __future__ import annotations

import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from aiogram import Bot
from aiogram.types import BufferedInputFile
from loguru import logger

from src.core.config import Settings
from src.infrastructure.telegram_notifications import notification_keyboard


class ErrorReporter:
    def __init__(self, bot: Bot, settings: Settings) -> None:
        self.bot = bot
        self.settings = settings
        self._reporting = False

    async def report(
        self,
        error: BaseException,
        *,
        source: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        event_id = uuid4().hex[:12]
        logger.opt(exception=error).error("Unhandled error [{}] in {}", event_id, source)
        if self._reporting or self.settings.bot_owner_id <= 0:
            return event_id
        self._reporting = True
        try:
            report = self._build_report(event_id, error, source, context or {})
            stamp = datetime.now(UTC).strftime("%Y-%m-%d_%H-%M-%S")
            document = BufferedInputFile(
                report.encode("utf-8", errors="replace"),
                filename=f"error_{stamp}_{event_id}.log",
            )
            user_id = (context or {}).get("telegram_id", "—")
            await self.bot.send_document(
                chat_id=self.settings.bot_owner_id,
                document=document,
                reply_markup=notification_keyboard(),
                caption=(
                    "⚠️ <b>Внутренняя ошибка</b>\n"
                    f"Источник: <code>{source[:120]}</code>\n"
                    f"Тип: <code>{type(error).__name__}</code>\n"
                    f"Пользователь: <code>{user_id}</code>\n"
                    f"Event ID: <code>{event_id}</code>"
                ),
            )
        except Exception as report_error:
            logger.error(
                "Failed to send error report {}: {}",
                event_id,
                type(report_error).__name__,
            )
        finally:
            self._reporting = False
        return event_id

    def _build_report(
        self,
        event_id: str,
        error: BaseException,
        source: str,
        context: dict[str, Any],
    ) -> str:
        now_utc = datetime.now(UTC)
        local_now = datetime.now().astimezone()
        traceback_text = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        context_text = "\n".join(f"{key}: {value}" for key, value in sorted(context.items()))
        log_tail = self._log_tail()
        content = (
            f"event_id: {event_id}\n"
            f"utc_time: {now_utc.isoformat()}\n"
            f"local_time: {local_now.isoformat()}\n"
            f"source: {source}\n"
            f"exception_type: {type(error).__name__}\n"
            f"exception_message: {error}\n\n"
            f"CONTEXT\n{context_text or '—'}\n\n"
            f"TRACEBACK\n{traceback_text}\n"
            f"APPLICATION LOG TAIL\n{log_tail}"
        )
        return self._redact(content)

    def _log_tail(self, max_lines: int = 400) -> str:
        path = Path(self.settings.app_log_path)
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return "Log file is unavailable."
        return "\n".join(lines[-max_lines:])

    def _redact(self, value: str) -> str:
        secrets = [
            self.settings.bot_token.get_secret_value(),
            self.settings.bot_secret_token.get_secret_value(),
            self.settings.database_password.get_secret_value(),
            self.settings.redis_password.get_secret_value() if self.settings.redis_password else "",
            self.settings.platega_api_key.get_secret_value()
            if self.settings.platega_api_key
            else "",
            self.settings.platega_merchant_id or "",
        ]
        redacted = value
        for secret in secrets:
            if secret:
                redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
