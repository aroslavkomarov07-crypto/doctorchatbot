import json
from decimal import InvalidOperation
from uuid import UUID

from aiogram import Bot
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from application.payments.payment_service import PaymentService
from core.config import get_settings
from core.enums.payment_status import PaymentStatus
from infrastructure.database.database import AsyncSessionLocal
from infrastructure.database.unit_of_work import UnitOfWork
from infrastructure.payment_gateway import MockGateway, PlategaGateway

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


async def notify_client(telegram_id: int, message: str) -> None:
    settings = get_settings()
    if settings.bot_token is None:
        return
    bot = Bot(settings.bot_token.get_secret_value())
    try:
        await bot.send_message(telegram_id, message)
    finally:
        await bot.session.close()


@app.post("/webhooks/platega", response_class=Response)
async def platega_callback(request: Request) -> Response:
    gateway = PlategaGateway(get_settings())
    if not gateway.credentials_configured:
        raise HTTPException(status_code=503)
    raw = await request.body()
    if len(raw) > 16_384:
        raise HTTPException(status_code=413)
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Некорректный JSON callback.")
        payment_id, amount, status = gateway.verify_callback(
            {key.lower(): value for key, value in request.headers.items()}, data,
        )
    except PermissionError:
        raise HTTPException(status_code=401) from None
    except (ValueError, KeyError, InvalidOperation, UnicodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400) from None
    target = {
        "CONFIRMED": PaymentStatus.SUCCEEDED,
        "CANCELED": PaymentStatus.FAILED,
        "CHARGEBACKED": PaymentStatus.REFUNDED,
    }[status]
    service = PaymentService(UnitOfWork(AsyncSessionLocal))
    try:
        completed, changed = await service.apply_provider_event(
            "platega", str(payment_id), amount, target,
        )
    except ValueError:
        raise HTTPException(status_code=409) from None
    if changed and target in {PaymentStatus.SUCCEEDED, PaymentStatus.REFUNDED}:
        async with UnitOfWork(AsyncSessionLocal) as uow:
            assert uow.users is not None
            user = await uow.users.get_by_id(completed.user_id)
        if user is not None:
            try:
                message = (
                    "Оплата подтверждена. Подписка активна — откройте /start, чтобы написать эксперту."
                    if target == PaymentStatus.SUCCEEDED else
                    "Platega сообщила о возврате платежа. Подписка отменена."
                )
                await notify_client(user.telegram_id, message)
            except Exception:
                pass  # A valid provider event must still be acknowledged.
    return Response(status_code=200)


@app.get("/mock/pay/{payment_id}", response_class=HTMLResponse)
async def mock_pay(payment_id: UUID, token: str) -> HTMLResponse:
    gateway = MockGateway(get_settings())
    service = PaymentService(UnitOfWork(AsyncSessionLocal))
    payment = await service.get_payment(payment_id)
    if payment is None or not gateway.verify_token(payment_id, payment.amount, token):
        raise HTTPException(status_code=404)
    completed = await service.complete_provider_payment("mock", str(payment_id), payment.amount)
    async with UnitOfWork(AsyncSessionLocal) as uow:
        assert uow.users is not None
        user = await uow.users.get_by_id(completed.user_id)
    if user is not None:
        try:
            await notify_client(
                user.telegram_id,
                "Оплата подтверждена. Подписка активна — откройте /start, чтобы написать эксперту.",
            )
        except Exception:
            pass
    return HTMLResponse("<h1>Тестовая оплата завершена</h1><p>Вернитесь в Telegram.</p>")
