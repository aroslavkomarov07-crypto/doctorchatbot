from core.entities.expert import Expert
from infrastructure.database.unit_of_work import UnitOfWork


class ExpertService:
    def __init__(
        self,
        unit_of_work: UnitOfWork,
    ):
        self.unit_of_work = unit_of_work

    async def register_expert(
        self,
        telegram_id: int,
        full_name: str,
        description: str | None = None,
    ) -> Expert:
        async with self.unit_of_work as uow:
            if uow.experts is None:
                raise RuntimeError(
                    "ExpertRepository не инициализирован."
                )

            existing_expert = (
                await uow.experts.get_by_telegram_id(
                    telegram_id
                )
            )

            if existing_expert is not None:
                return existing_expert

            expert = Expert(
                telegram_id=telegram_id,
                full_name=full_name,
                description=description,
            )

            return await uow.experts.create(expert)

    async def get_expert_by_telegram_id(
        self,
        telegram_id: int,
    ) -> Expert | None:
        async with self.unit_of_work as uow:
            if uow.experts is None:
                raise RuntimeError(
                    "ExpertRepository не инициализирован."
                )

            return await uow.experts.get_by_telegram_id(
                telegram_id
            )

    async def get_active_experts(self) -> list[Expert]:
        async with self.unit_of_work as uow:
            if uow.experts is None:
                raise RuntimeError(
                    "ExpertRepository не инициализирован."
                )

            return await uow.experts.get_active_experts()