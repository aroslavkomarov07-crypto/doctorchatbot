from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.config import get_settings

settings = get_settings()


engine = create_async_engine(
    settings.database_url,
    echo=settings.sql_echo,
)


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Создаёт асинхронную сессию базы данных.
    """

    async with AsyncSessionLocal() as session:
        yield session
