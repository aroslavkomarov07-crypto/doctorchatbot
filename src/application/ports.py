from decimal import Decimal
from typing import Protocol
from uuid import UUID

from src.application.dto import IncomingTopicMessage, ProviderPayment, TopicDelivery, WebhookPayment
from src.core.enums import PaymentProvider


class PaymentGateway(Protocol):
    provider: PaymentProvider

    async def create_payment(
        self,
        *,
        transaction_id: UUID,
        amount: Decimal,
        description: str,
    ) -> ProviderPayment: ...

    async def parse_webhook(
        self,
        *,
        headers: dict[str, str],
        body: bytes,
    ) -> WebhookPayment: ...


class TelegramTopicGateway(Protocol):
    async def topics_enabled(self) -> bool: ...

    async def create_topic(self, *, chat_id: int, name: str) -> int: ...

    async def send_text(
        self,
        *,
        chat_id: int,
        thread_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        dismissible: bool = False,
    ) -> TopicDelivery: ...

    async def copy_message(
        self,
        *,
        incoming: IncomingTopicMessage,
        destination_chat_id: int,
        destination_thread_id: int,
        reply_to_message_id: int | None = None,
    ) -> TopicDelivery: ...
