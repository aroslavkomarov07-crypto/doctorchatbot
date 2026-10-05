from collections.abc import AsyncGenerator
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from infrastructure.database.base import Base
from infrastructure.database import models


# Корень проекта:
# doctorchatbot/
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """
    Настройки приложения.
    """

    database_url: str

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()


engine = create_async_engine(
    settings.database_url,
    echo=True,
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


async def create_tables() -> None:
    """
    Создаёт все таблицы, зарегистрированные в Base.metadata.
    """

    async with engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all
        )