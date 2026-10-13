from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    database_url: str
    bot_token: SecretStr | None = None
    sql_echo: bool = False
    owner_telegram_id: int = 0
    public_base_url: str | None = None
    payment_mode: str = "platega"
    platega_merchant_id: str | None = None
    platega_api_key: SecretStr | None = None
    platega_sbp_payment_method: int | None = None
    webhook_port: int = 8080
    allow_mock_payments: bool = False
    mock_secret: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
