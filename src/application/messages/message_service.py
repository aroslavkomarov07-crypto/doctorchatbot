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
        reply_to_id: UUID | None = None,
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
            reply_to_id=reply_to_id,
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
            if sender_type == MessageSenderType.USER:
                if not await uow.subscriptions.reserve_message_slot(
                    user_id, expert_id, datetime.now(UTC)
                ):
                    raise PermissionError("Лимит сообщений по подписке исчерпан.")
            if reply_to_id is not None:
                original = await uow.messages.get_by_id(reply_to_id)
                if original is None or original.user_id != user_id or original.expert_id != expert_id:
                    raise ValueError("Исходное сообщение не найдено.")
                if sender_type != MessageSenderType.EXPERT or original.sender_type != MessageSenderType.USER:
                    raise ValueError("Ответить можно только на вопрос клиента.")
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

    async def get_message(self, message_id: UUID) -> Message | None:
        async with self.unit_of_work as uow:
            assert uow.messages is not None
            return await uow.messages.get_by_id(message_id)

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
