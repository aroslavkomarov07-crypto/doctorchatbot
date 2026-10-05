from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.entities.user import User
from infrastructure.database.models.user_model import UserModel


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, user: User) -> User:
        user_model = UserModel(
            id=user.id,
            telegram_id=user.telegram_id,
            full_name=user.full_name,
            phone_number=user.phone_number,
            created_at=user.created_at,
        )

        self.session.add(user_model)

        await self.session.flush()

        return user

    async def get_by_id(self, user_id: UUID) -> User | None:
        result = await self.session.execute(
            select(UserModel).where(UserModel.id == user_id)
        )

        user_model = result.scalar_one_or_none()

        if user_model is None:
            return None

        return User(
            id=user_model.id,
            telegram_id=user_model.telegram_id,
            full_name=user_model.full_name,
            phone_number=user_model.phone_number,
            created_at=user_model.created_at,
        )

    async def get_by_telegram_id(
        self,
        telegram_id: int,
    ) -> User | None:
        result = await self.session.execute(
            select(UserModel).where(
                UserModel.telegram_id == telegram_id
            )
        )

        user_model = result.scalar_one_or_none()

        if user_model is None:
            return None

        return User(
            id=user_model.id,
            telegram_id=user_model.telegram_id,
            full_name=user_model.full_name,
            phone_number=user_model.phone_number,
            created_at=user_model.created_at,
        )