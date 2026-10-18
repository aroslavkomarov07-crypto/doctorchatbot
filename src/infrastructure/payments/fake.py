from decimal import Decimal
from uuid import UUID

import orjson

from src.application.dto import ProviderPayment, WebhookPayment
from src.core.enums import PaymentProvider, TransactionStatus
from src.core.exceptions import PaymentConflict


class FakeGateway:
    provider = PaymentProvider.FAKE

    async def create_payment(
        self, *, transaction_id: UUID, amount: Decimal, description: str
    ) -> ProviderPayment:
        del amount, description
        payment_id = f"fake-{transaction_id}"
        return ProviderPayment(
            provider_payment_id=payment_id,
            url=f"https://example.test/pay/{payment_id}",
        )

    async def parse_webhook(self, *, headers: dict[str, str], body: bytes) -> WebhookPayment:
        if headers.get("x-test-secret") != "doctor-shop-test":
            raise PaymentConflict("Неверная подпись тестового webhook")
        data = orjson.loads(body)
        return WebhookPayment(
            provider=self.provider,
            provider_payment_id=str(data["id"]),
            status=TransactionStatus(str(data["status"])),
            amount=Decimal(str(data["amount"])),
            raw_status=str(data["status"]),
            safe_payload={"id": str(data["id"]), "status": str(data["status"])},
        )
