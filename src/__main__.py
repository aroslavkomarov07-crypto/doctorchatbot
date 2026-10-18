from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from fastapi import FastAPI
from redis.asyncio import Redis

from src.application.ports import PaymentGateway
from src.core.config import get_settings
from src.infrastructure.database.session import Database
from src.infrastructure.error_reporting import ErrorReporter
from src.infrastructure.logging import configure_logging
from src.infrastructure.payments import FakeGateway, GatewayRegistry, PlategaGateway
from src.telegram import build_router
from src.telegram.middlewares import CallbackRateLimitMiddleware, GeneralInputCleanupMiddleware
from src.telegram.ui import ContextMenu
from src.web import create_app


def application() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    database = Database(settings)
    bot = Bot(
        settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    storage = RedisStorage.from_url(settings.redis_dsn)
    dispatcher = Dispatcher(storage=storage)
    throttle_redis = Redis.from_url(settings.redis_dsn)
    dispatcher.callback_query.outer_middleware(
        CallbackRateLimitMiddleware(throttle_redis, limit=3, window_ms=1000)
    )
    dispatcher.message.outer_middleware(GeneralInputCleanupMiddleware())
    context_menu = ContextMenu(throttle_redis, database.session_factory)
    error_reporter = ErrorReporter(bot, settings)
    gateway_items: list[PaymentGateway] = [FakeGateway()]
    if (
        settings.platega_merchant_id
        and settings.platega_api_key
        and settings.platega_sbp_payment_method is not None
    ):
        gateway_items.append(PlategaGateway(settings))
    gateways = GatewayRegistry(*gateway_items)
    dispatcher.include_router(
        build_router(
            session_factory=database.session_factory,
            config=settings,
            gateways=gateways,
            menu=context_menu,
            error_reporter=error_reporter,
        )
    )
    return create_app(
        settings=settings,
        database=database,
        bot=bot,
        dispatcher=dispatcher,
        gateways=gateways,
        throttle_redis=throttle_redis,
        error_reporter=error_reporter,
    )
