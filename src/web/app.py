import asyncio
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from loguru import logger
from redis.asyncio import Redis
from sqlalchemy import select

from src.application.services import PaymentService, bootstrap_defaults, notification_enabled
from src.application.validation import safe_html
from src.core.config import Settings
from src.core.enums import PaymentProvider, TransactionStatus
from src.core.exceptions import DomainError, PaymentConflict
from src.infrastructure.database.models import (
    Conversation,
    ExpertProfile,
    PaymentTransaction,
    User,
)
from src.infrastructure.database.session import Database
from src.infrastructure.error_reporting import ErrorReporter
from src.infrastructure.payments import GatewayRegistry, PlategaGateway
from src.infrastructure.telegram_notifications import send_notification
from src.telegram.commands import setup_bot_commands


def create_app(
    *,
    settings: Settings,
    database: Database,
    bot: Bot,
    dispatcher: Dispatcher,
    gateways: GatewayRegistry,
    throttle_redis: Redis,
    error_reporter: ErrorReporter,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        polling_task: asyncio.Task[None] | None = None
        async with database.session_factory() as session:
            await bootstrap_defaults(session, settings)
        if not settings.bot_token.get_secret_value().startswith("development"):
            await setup_bot_commands(bot)
            me = await bot.get_me()
            if me.has_topics_enabled is not True:
                logger.error(
                    "Telegram private topics are disabled; enable Threaded Mode in BotFather"
                )
                try:
                    await send_notification(
                        bot,
                        settings.bot_owner_id,
                        "⚠️ Темы в личных чатах отключены. Включите Threaded Mode "
                        "для бота через @BotFather, иначе консультации не будут созданы.",
                    )
                except Exception as error:
                    logger.warning(
                        "Could not notify owner about disabled topics: {}",
                        type(error).__name__,
                    )
            elif me.allows_users_to_create_topics is True:
                logger.error(
                    "Telegram users can create topics; enable "
                    "'Disallow users to create new threads' in BotFather"
                )
                try:
                    await send_notification(
                        bot,
                        settings.bot_owner_id,
                        "⚠️ Пользователи всё ещё могут создавать темы сами. "
                        "В @BotFather включите настройку "
                        "«Disallow users to create new threads». "
                        "Рабочие темы должен создавать только бот.",
                    )
                except Exception as error:
                    logger.warning(
                        "Could not notify owner about user-created topics: {}",
                        type(error).__name__,
                    )
        if settings.bot_use_polling:
            await bot.delete_webhook(drop_pending_updates=settings.bot_drop_pending_updates)
            polling_task = asyncio.create_task(
                dispatcher.start_polling(
                    bot,
                    allowed_updates=dispatcher.resolve_used_update_types(),
                    close_bot_session=False,
                ),
                name="telegram-polling",
            )
            logger.info("Telegram long polling started")
        elif (
            settings.app_domain != "localhost"
            and not settings.bot_token.get_secret_value().startswith("development")
        ):
            await bot.set_webhook(
                settings.telegram_webhook_url,
                secret_token=settings.bot_secret_token.get_secret_value(),
                drop_pending_updates=settings.bot_drop_pending_updates,
                allowed_updates=dispatcher.resolve_used_update_types(),
            )
        logger.info("DoctorShop started")
        yield
        if polling_task is not None:
            if not polling_task.done():
                with suppress(RuntimeError):
                    await dispatcher.stop_polling()
            polling_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await polling_task
        for gateway in gateways._gateways.values():  # noqa: SLF001
            if isinstance(gateway, PlategaGateway):
                await gateway.close()
        await bot.session.close()
        await dispatcher.storage.close()
        await throttle_redis.aclose()
        await database.close()
        logger.info("DoctorShop stopped")

    app = FastAPI(
        title="DoctorShop",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, error: Exception) -> JSONResponse:
        await error_reporter.report(
            error,
            source=f"http:{request.method}:{request.url.path}",
            context={
                "method": request.method,
                "path": request.url.path,
                "client": request.client.host if request.client else "—",
            },
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Внутренняя ошибка сервера"},
        )

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(settings.telegram_webhook_path, include_in_schema=False)
    async def telegram_webhook(request: Request) -> Response:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if secret != settings.bot_secret_token.get_secret_value():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        update = Update.model_validate(await request.json(), context={"bot": bot})
        await dispatcher.feed_update(bot, update)
        return Response(status_code=status.HTTP_200_OK)

    @app.post("/api/v1/payments/{provider}", include_in_schema=False)
    async def payment_webhook(provider: str, request: Request) -> Response:
        try:
            provider_type = PaymentProvider(provider.upper())
            gateway = gateways.get(provider_type)
            event = await gateway.parse_webhook(
                headers={key.lower(): value for key, value in request.headers.items()},
                body=await request.body(),
            )
            async with database.session_factory() as session:
                changed = await PaymentService(session).process_webhook(event)
                if changed:
                    if event.status is TransactionStatus.COMPLETED:
                        await _queue_conversation_topics(
                            session,
                            event.provider_payment_id,
                        )
                    try:
                        await _notify_payment(
                            bot,
                            session,
                            event.provider_payment_id,
                            event.status,
                            settings.bot_owner_id,
                        )
                    except Exception as error:
                        logger.warning(
                            "Payment notification failed: {}",
                            type(error).__name__,
                        )
            return Response(status_code=status.HTTP_200_OK)
        except PaymentConflict as error:
            logger.warning("Rejected payment webhook: {}", error)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST) from error
        except (ValueError, DomainError) as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from error

    return app


async def _queue_conversation_topics(
    session: object,
    provider_payment_id: str,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa: PLC0415

    if not isinstance(session, AsyncSession):
        return
    transaction = await session.scalar(
        select(PaymentTransaction).where(
            PaymentTransaction.provider_payment_id == provider_payment_id
        )
    )
    if transaction is None:
        return
    conversation_id = await session.scalar(
        select(Conversation.id).where(
            Conversation.client_id == transaction.client_id,
            Conversation.expert_id == transaction.expert_id,
        )
    )
    if conversation_id is None:
        return
    try:
        from src.infrastructure.taskiq.tasks import (  # noqa: PLC0415
            provision_conversation_topics_task,
        )

        await provision_conversation_topics_task.kiq(conversation_id)
    except Exception as error:
        logger.warning(
            "Topic provisioning enqueue failed for conversation {}: {}",
            conversation_id,
            type(error).__name__,
        )


async def _notify_payment(
    bot: Bot,
    session: object,
    provider_payment_id: str,
    payment_status: TransactionStatus,
    owner_telegram_id: int,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession  # noqa: PLC0415

    if not isinstance(session, AsyncSession):
        return
    row = await session.execute(
        select(PaymentTransaction, User, ExpertProfile)
        .join(User, User.id == PaymentTransaction.client_id)
        .join(ExpertProfile, ExpertProfile.id == PaymentTransaction.expert_id)
        .where(PaymentTransaction.provider_payment_id == provider_payment_id)
    )
    item = row.one_or_none()
    if item is None:
        return
    if not await notification_enabled(session, "payment"):
        return
    transaction, client, expert = item
    expert_user = await session.get(User, expert.user_id)
    if payment_status is TransactionStatus.COMPLETED:
        client_text = (
            f"✅ Оплата {transaction.amount:.2f} ₽ подтверждена. "
            f"Доступ к эксперту {safe_html(expert.display_name)} активирован."
        )
        expert_text = (
            f"💎 Клиент {safe_html(client.full_name or client.telegram_name)} "
            "оформил подписку. "
            f"Ваш доход: {transaction.expert_income_amount:.2f} ₽."
        )
    elif payment_status is TransactionStatus.REFUNDED:
        client_text = f"↩️ Платеж {transaction.amount:.2f} ₽ возвращен."
        expert_text = f"↩️ По подписке клиента выполнен возврат {transaction.amount:.2f} ₽."
    else:
        client_text = f"Платеж получил статус {payment_status.value}."
        expert_text = client_text
    await send_notification(bot, client.telegram_id, client_text)
    if expert_user:
        await send_notification(bot, expert_user.telegram_id, expert_text)
    await send_notification(
        bot,
        owner_telegram_id,
        "#SubscriptionPurchasedEvent\n\n"
        f"<b>Платеж:</b> {transaction.amount:.2f} ₽ — {payment_status.value}\n"
        f"<b>Клиент:</b> {safe_html(client.full_name or client.telegram_name)}\n"
        f"<b>Эксперт:</b> {safe_html(expert.display_name)}\n"
        f"<b>ID:</b> <code>{transaction.id}</code>",
    )
