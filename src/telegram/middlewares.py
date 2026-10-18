from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, TelegramObject
from redis.asyncio import Redis


class CallbackRateLimitMiddleware(BaseMiddleware):
    _SCRIPT = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local window = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    local member = ARGV[4]
    redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
    local count = redis.call('ZCARD', key)
    if count >= limit then
        redis.call('PEXPIRE', key, window)
        return 0
    end
    redis.call('ZADD', key, now, member)
    redis.call('PEXPIRE', key, window)
    return 1
    """

    def __init__(self, redis: Redis, *, limit: int = 3, window_ms: int = 1000) -> None:
        self.redis = redis
        self.limit = limit
        self.window_ms = window_ms

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)
        now = int(time.time() * 1000)
        allowed = await cast(
            Awaitable[Any],
            self.redis.eval(
                self._SCRIPT,
                1,
                f"doctorshop:clicks:{event.from_user.id}",
                now,
                self.window_ms,
                self.limit,
                f"{now}:{event.id}",
            ),
        )
        if int(allowed) == 1:
            return await handler(event, data)
        warning = await self.redis.set(
            f"doctorshop:click-warning:{event.from_user.id}",
            "1",
            px=self.window_ms,
            nx=True,
        )
        await event.answer("Слишком много нажатий" if warning else "", show_alert=False)
        return None


class GeneralInputCleanupMiddleware(BaseMiddleware):
    """Deletes control-plane input while preserving consultation topic history."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)
        should_delete = (
            event.chat.type == ChatType.PRIVATE
            and event.is_topic_message is not True
            and event.from_user is not None
            and not event.from_user.is_bot
            and event.forum_topic_created is None
            and event.forum_topic_edited is None
        )
        try:
            return await handler(event, data)
        finally:
            if should_delete:
                try:
                    await event.delete()
                except TelegramAPIError:
                    pass


ThrottlingMiddleware = CallbackRateLimitMiddleware
