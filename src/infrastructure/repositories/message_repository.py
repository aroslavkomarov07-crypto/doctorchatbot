from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.entities.message import Message
from infrastructure.database.models.message_model import MessageModel


class MessageRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        message: Message,
    ) -> Message:
        message_model = MessageModel(
            id=message.id,
            user_id=message.user_id,
            expert_id=message.expert_id,
            text=message.text,
            sender_type=message.sender_type,
            created_at=message.created_at,
        )

        self.session.add(message_model)

        await self.session.flush()

        return message

    async def get_by_id(
        self,
        message_id: UUID,
    ) -> Message | None:
        result = await self.session.execute(
            select(MessageModel).where(
                MessageModel.id == message_id
            )
        )

        message_model = result.scalar_one_or_none()

        if message_model is None:
            return None

        return self._to_entity(message_model)

    async def get_conversation(
        self,
        user_id: UUID,
        expert_id: UUID,
    ) -> list[Message]:
        result = await self.session.execute(
            select(MessageModel)
            .where(
                MessageModel.user_id == user_id,
                MessageModel.expert_id == expert_id,
            )
            .order_by(
                MessageModel.created_at.asc()
            )
        )

        message_models = result.scalars().all()

        return [
            self._to_entity(message)
            for message in message_models
        ]

    async def get_latest_messages(
        self,
        user_id: UUID,
        expert_id: UUID,
        limit: int = 50,
    ) -> list[Message]:
        result = await self.session.execute(
            select(MessageModel)
            .where(
                MessageModel.user_id == user_id,
                MessageModel.expert_id == expert_id,
            )
            .order_by(
                MessageModel.created_at.desc()
            )
            .limit(limit)
        )

        message_models = result.scalars().all()

        messages = [
            self._to_entity(message)
            for message in message_models
        ]

        messages.reverse()

        return messages

    @staticmethod
    def _to_entity(
        message_model: MessageModel,
    ) -> Message:
        return Message(
            id=message_model.id,
            user_id=message_model.user_id,
            expert_id=message_model.expert_id,
            text=message_model.text,
            sender_type=message_model.sender_type,
            created_at=message_model.created_at,
        )