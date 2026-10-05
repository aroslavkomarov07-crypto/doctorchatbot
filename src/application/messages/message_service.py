from datetime import UTC, datetime
from uuid import UUID

from core.entities.message import Message
from core.enums.message_sender_type import MessageSenderType
from infrastructure.database.unit_of_work import UnitOfWork


class MessageService:
    def __init__(
        self,
        unit_of_work: UnitOfWork,
    ):
        self.unit_of_work = unit_of_work

    async def send_message(
        self,
        user_id: UUID,
        expert_id: UUID,
        text: str,
        sender_type: MessageSenderType,
    ) -> Message:
        if not text.strip():
            raise ValueError(
                "Сообщение не может быть пустым."
            )

        message = Message(
            user_id=user_id,
            expert_id=expert_id,
            text=text,
            sender_type=sender_type,
        )

        async with self.unit_of_work as uow:
            if uow.messages is None or uow.subscriptions is None:
                raise RuntimeError(
                    "MessageRepository не инициализирован."
                )

            subscription = await uow.subscriptions.get_active_for_user(
                user_id=user_id,
                expert_id=expert_id,
                now=datetime.now(UTC),
            )
            if subscription is None:
                raise PermissionError(
                    "Для переписки с экспертом нужна активная подписка."
                )
            return await uow.messages.create(message)

    async def get_conversation(
        self,
        user_id: UUID,
        expert_id: UUID,
    ) -> list[Message]:
        async with self.unit_of_work as uow:
            if uow.messages is None:
                raise RuntimeError(
                    "MessageRepository не инициализирован."
                )

            return await uow.messages.get_conversation(
                user_id=user_id,
                expert_id=expert_id,
            )

    async def get_latest_messages(
        self,
        user_id: UUID,
        expert_id: UUID,
        limit: int = 50,
    ) -> list[Message]:
        if limit <= 0:
            raise ValueError(
                "Количество сообщений должно быть больше нуля."
            )

        async with self.unit_of_work as uow:
            if uow.messages is None:
                raise RuntimeError(
                    "MessageRepository не инициализирован."
                )

            return await uow.messages.get_latest_messages(
                user_id=user_id,
                expert_id=expert_id,
                limit=limit,
            )
