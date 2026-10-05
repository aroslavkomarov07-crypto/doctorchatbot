from infrastructure.database.unit_of_work import UnitOfWork
from core.entities.user import User


class UserService:
    def __init__(
        self,
        unit_of_work: UnitOfWork,
    ):
        self.unit_of_work = unit_of_work

    async def register_user(
        self,
        telegram_id: int,
        full_name: str,
        phone_number: str,
    ) -> User:
        async with self.unit_of_work as uow:
            if uow.users is None:
                raise RuntimeError(
                    "UserRepository не инициализирован."
                )

            existing_user = (
                await uow.users.get_by_telegram_id(
                    telegram_id
                )
            )

            if existing_user is not None:
                return existing_user

            user = User(
                telegram_id=telegram_id,
                full_name=full_name,
                phone_number=phone_number,
            )

            return await uow.users.create(user)

    async def get_user_by_telegram_id(
        self,
        telegram_id: int,
    ) -> User | None:
        async with self.unit_of_work as uow:
            if uow.users is None:
                raise RuntimeError(
                    "UserRepository не инициализирован."
                )

            return await uow.users.get_by_telegram_id(
                telegram_id
            )