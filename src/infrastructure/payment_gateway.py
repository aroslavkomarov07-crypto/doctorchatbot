import hashlib
import hmac
from decimal import Decimal
from urllib.parse import urlparse
from uuid import UUID

import httpx

from core.config import Settings


class PlategaGateway:
    """Create SBP transactions and authenticate Platega JSON callbacks."""

    name = "platega"
    api_base = "https://app.platega.io"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def credentials_configured(self) -> bool:
        return bool(
            self.settings.platega_merchant_id
            and self.settings.platega_api_key
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.payment_mode == "platega"
            and self.credentials_configured
            and self.settings.platega_sbp_payment_method is not None
            and self.settings.platega_sbp_payment_method > 0
        )

    def _headers(self) -> dict[str, str]:
        if not self.credentials_configured:
            raise RuntimeError("Ключи Platega не заданы.")
        assert self.settings.platega_merchant_id and self.settings.platega_api_key
        return {
            "X-MerchantId": self.settings.platega_merchant_id,
            "X-Secret": self.settings.platega_api_key.get_secret_value(),
            "Accept": "application/json",
        }

    async def create_payment(
        self, amount: Decimal, description: str, return_url: str,
        user_id: int, merchant_reference: UUID,
    ) -> tuple[UUID, str]:
        if not self.configured:
            raise RuntimeError("Способ оплаты СБП в Platega не настроен.")
        if not return_url.startswith("https://t.me/"):
            raise ValueError("Некорректная ссылка возврата в бот.")
        payload = {
            "paymentMethod": self.settings.platega_sbp_payment_method,
            "paymentDetails": {"amount": float(amount), "currency": "RUB"},
            "description": description[:255],
            "return": return_url,
            "failedUrl": return_url,
            "payload": str(merchant_reference),
            "metadata": {"userId": str(user_id)},
        }
        async with httpx.AsyncClient(base_url=self.api_base, timeout=20) as client:
            response = await client.post(
                "/transaction/process", json=payload, headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict) or data.get("status") != "PENDING":
            raise ValueError("Platega вернула неожиданный статус платежа.")
        payment_id = UUID(str(data["transactionId"]))
        redirect = str(data["redirect"])
        parsed = urlparse(redirect)
        if parsed.scheme != "https" or not parsed.netloc or len(redirect) > 2048:
            raise ValueError("Platega вернула некорректную ссылку оплаты.")
        return payment_id, redirect

    def verify_callback(
        self, headers: dict[str, str], data: dict[str, object],
    ) -> tuple[UUID, Decimal, str]:
        if not self.credentials_configured:
            raise PermissionError("Platega не настроена.")
        assert self.settings.platega_merchant_id and self.settings.platega_api_key
        merchant_ok = hmac.compare_digest(
            headers.get("x-merchantid", ""), self.settings.platega_merchant_id,
        )
        secret_ok = hmac.compare_digest(
            headers.get("x-secret", ""), self.settings.platega_api_key.get_secret_value(),
        )
        if not merchant_ok or not secret_ok:
            raise PermissionError("Неверные заголовки callback Platega.")
        status = str(data.get("status", ""))
        if status not in {"CONFIRMED", "CANCELED", "CHARGEBACKED"}:
            raise ValueError("Неизвестный статус Platega.")
        if data.get("currency") != "RUB":
            raise ValueError("Валюта callback не совпадает.")
        amount = Decimal(str(data["amount"]))
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Некорректная сумма callback.")
        return UUID(str(data["id"])), amount, status

    async def fetch_status(self, provider_id: UUID) -> tuple[str, Decimal]:
        async with httpx.AsyncClient(base_url=self.api_base, timeout=20) as client:
            response = await client.get(
                f"/transaction/{provider_id}", headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict) or UUID(str(data["id"])) != provider_id:
            raise ValueError("Platega вернула другой ID платежа.")
        details = data.get("paymentDetails")
        if not isinstance(details, dict) or details.get("currency") != "RUB":
            raise ValueError("Platega вернула другую валюту.")
        amount = Decimal(str(details["amount"]))
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Platega вернула некорректную сумму.")
        return str(data["status"]), amount


class MockGateway:
    name = "mock"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.allow_mock_payments
            and self.settings.payment_mode == "mock"
            and self.settings.mock_secret
            and self.settings.public_base_url
        )

    def payment_url(self, payment_id: UUID, amount: Decimal, description: str) -> str:
        if not self.configured:
            raise RuntimeError("Mock-оплата отключена.")
        assert self.settings.mock_secret and self.settings.public_base_url
        token = hmac.new(
            self.settings.mock_secret.get_secret_value().encode(),
            f"{payment_id}:{amount:.2f}".encode(), hashlib.sha256,
        ).hexdigest()
        return f"{self.settings.public_base_url.rstrip('/')}/mock/pay/{payment_id}?token={token}"

    def verify_token(self, payment_id: UUID, amount: Decimal, token: str) -> bool:
        if not self.configured:
            return False
        assert self.settings.mock_secret
        expected = hmac.new(
            self.settings.mock_secret.get_secret_value().encode(),
            f"{payment_id}:{amount:.2f}".encode(), hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, token)
