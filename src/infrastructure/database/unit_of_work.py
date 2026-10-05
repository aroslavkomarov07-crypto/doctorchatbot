from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from infrastructure.repositories.expert_repository import (
    ExpertRepository,
)
from infrastructure.repositories.message_repository import (
    MessageRepository,
)
from infrastructure.repositories.payment_repository import (
    PaymentRepository,
)
from infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from infrastructure.repositories.user_repository import (
    UserRepository,
)


class UnitOfWork:
    """
    Управляет одной бизнес-транзакцией.

    Все репозитории внутри UnitOfWork используют
    одну и ту же SQLAlchemy-сессию.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ):
        self.session_factory = session_factory
        self.session: AsyncSession | None = None

        self.users: UserRepository | None = None
        self.experts: ExpertRepository | None = None
        self.subscriptions: SubscriptionRepository | None = None
        self.messages: MessageRepository | None = None
        self.payments: PaymentRepository | None = None

    async def __aenter__(self) -> Self:
        self.session = self.session_factory()

        self.users = UserRepository(self.session)
        self.experts = ExpertRepository(self.session)
        self.subscriptions = SubscriptionRepository(self.session)
        self.messages = MessageRepository(self.session)
        self.payments = PaymentRepository(self.session)

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.session is None:
            return

        try:
            if exc_type is not None:
                await self.session.rollback()
            else:
                await self.session.commit()
        finally:
            await self.session.close()