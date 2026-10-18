import hmac
from decimal import Decimal
from typing import Any
from uuid import UUID

import orjson
from httpx import AsyncClient, HTTPStatusError, Timeout

from src.application.dto import ProviderPayment, WebhookPayment
from src.core.config import Settings
from src.core.enums import PaymentProvider, TransactionStatus
from src.core.exceptions import PaymentConflict, ValidationError


class PlategaGateway:
    provider = PaymentProvider.PLATEGA
    api_base = "https://app.platega.io"

    def __init__(self, settings: Settings) -> None:
        if not settings.platega_merchant_id or not settings.platega_api_key:
            raise ValidationError("Platega не настроена")
        if settings.platega_sbp_payment_method is None:
            raise ValidationError("Не указан способ СБП Platega")
        self.merchant_id = settings.platega_merchant_id
        self.api_key = settings.platega_api_key.get_secret_value()
        self.sbp_payment_method = settings.platega_sbp_payment_method
        self.return_url = f"https://t.me/{settings.bot_support_username}"
        self.client = AsyncClient(
            base_url=self.api_base,
            timeout=Timeout(30),
            headers={
                "X-MerchantId": self.merchant_id,
                "X-Secret": self.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    async def create_payment(
        self, *, transaction_id: UUID, amount: Decimal, description: str
    ) -> ProviderPayment:
        payload = {
            "command": {"orderId": str(transaction_id)},
            "paymentMethod": self.sbp_payment_method,
            "paymentDetails": {"amount": str(amount), "currency": "RUB"},
            "description": description,
            "return": self.return_url,
            "failedUrl": self.return_url,
        }
        try:
            response = await self.client.post("/transaction/process", json=payload)
            response.raise_for_status()
        except HTTPStatusError as error:
            raise PaymentConflict(
                f"Платежный провайдер вернул HTTP {error.response.status_code}"
            ) from error
        data: dict[str, Any] = orjson.loads(response.content)
        payment_id = data.get("transactionId")
        url = data.get("redirect")
        if not payment_id or not url:
            raise PaymentConflict("Platega вернула неполный ответ")
        return ProviderPayment(provider_payment_id=str(payment_id), url=str(url))

    async def parse_webhook(self, *, headers: dict[str, str], body: bytes) -> WebhookPayment:
        merchant = headers.get("x-merchantid", "")
        secret = headers.get("x-secret", "")
        if not (
            hmac.compare_digest(merchant, self.merchant_id)
            and hmac.compare_digest(secret, self.api_key)
        ):
            raise PaymentConflict("Неверная подпись Platega webhook")
        try:
            data: dict[str, Any] = orjson.loads(body)
            payment_id = str(data["id"])
            raw_status = str(data["status"])
            raw_amount = data.get("amount")
            if raw_amount is None:
                raw_amount = data.get("paymentDetails", {}).get("amount")
            if raw_amount is None:
                raise KeyError("amount")
            amount = Decimal(str(raw_amount))
        except (KeyError, ValueError, TypeError, orjson.JSONDecodeError) as error:
            raise PaymentConflict("Некорректный payload Platega webhook") from error
        status_map = {
            "CONFIRMED": TransactionStatus.COMPLETED,
            "CANCELED": TransactionStatus.CANCELED,
            "CHARGEBACKED": TransactionStatus.REFUNDED,
            "FAILED": TransactionStatus.FAILED,
        }
        status = status_map.get(raw_status)
        if status is None:
            raise PaymentConflict(f"Неподдерживаемый статус Platega: {raw_status}")
        return WebhookPayment(
            provider=self.provider,
            provider_payment_id=payment_id,
            status=status,
            amount=amount,
            raw_status=raw_status,
            safe_payload={"id": payment_id, "status": raw_status, "amount": str(amount)},
        )

    async def close(self) -> None:
        await self.client.aclose()
