from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.entities.expert import Expert
from infrastructure.database.models.expert_model import ExpertModel


class ExpertRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, expert: Expert) -> Expert:
        expert_model = ExpertModel(
            id=expert.id,
            telegram_id=expert.telegram_id,
            full_name=expert.full_name,
            description=expert.description,
            is_active=expert.is_active,
            created_at=expert.created_at,
        )

        self.session.add(expert_model)

        await self.session.flush()

        return expert

    async def get_by_id(
        self,
        expert_id: UUID,
    ) -> Expert | None:
        result = await self.session.execute(
            select(ExpertModel).where(
                ExpertModel.id == expert_id
            )
        )

        expert_model = result.scalar_one_or_none()

        if expert_model is None:
            return None

        return Expert(
            id=expert_model.id,
            telegram_id=expert_model.telegram_id,
            full_name=expert_model.full_name,
            description=expert_model.description,
            is_active=expert_model.is_active,
            created_at=expert_model.created_at,
        )

    async def get_by_telegram_id(
        self,
        telegram_id: int,
    ) -> Expert | None:
        result = await self.session.execute(
            select(ExpertModel).where(
                ExpertModel.telegram_id == telegram_id
            )
        )

        expert_model = result.scalar_one_or_none()

        if expert_model is None:
            return None

        return Expert(
            id=expert_model.id,
            telegram_id=expert_model.telegram_id,
            full_name=expert_model.full_name,
            description=expert_model.description,
            is_active=expert_model.is_active,
            created_at=expert_model.created_at,
        )

    async def get_active_experts(self) -> list[Expert]:
        result = await self.session.execute(
            select(ExpertModel)
            .where(ExpertModel.is_active.is_(True))
            .order_by(ExpertModel.created_at)
        )

        expert_models = result.scalars().all()

        return [
            Expert(
                id=expert.id,
                telegram_id=expert.telegram_id,
                full_name=expert.full_name,
                description=expert.description,
                is_active=expert.is_active,
                created_at=expert.created_at,
            )
            for expert in expert_models
        ]