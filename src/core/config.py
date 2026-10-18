from functools import lru_cache
from typing import Annotated

from pydantic import BeforeValidator, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _empty_to_none(value: object) -> object:
    return None if value == "" else value


OptionalSecret = Annotated[SecretStr | None, BeforeValidator(_empty_to_none)]
OptionalInt = Annotated[int | None, BeforeValidator(_empty_to_none)]
OptionalString = Annotated[str | None, BeforeValidator(_empty_to_none)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_host: str = "0.0.0.0"
    app_port: int = 5000
    app_domain: str = "localhost"
    app_log_level: str = "INFO"
    app_log_path: str = "logs/doctorshop.log"
    banner_directory: str = "assets/banners"
    banner_file_ids: dict[str, str] = Field(default_factory=dict)

    bot_token: SecretStr = Field(default=SecretStr("development-token"))
    bot_secret_token: SecretStr = Field(default=SecretStr("development-secret"))
    bot_owner_id: int = 0
    bot_support_username: str = "support"
    privacy_policy_url: OptionalString = None
    user_agreement_url: OptionalString = None
    bot_drop_pending_updates: bool = False
    bot_use_polling: bool = False

    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "doctorshop"
    database_user: str = "doctorshop"
    database_password: SecretStr = Field(default=SecretStr("doctorshop"))
    database_echo: bool = False

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_name: int = 0
    redis_password: OptionalSecret = None

    platega_merchant_id: OptionalString = None
    platega_api_key: OptionalSecret = None
    platega_sbp_payment_method: OptionalInt = None

    service_commission_percent: str = "13.00"

    @property
    def database_dsn(self) -> str:
        password = self.database_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.database_user}:{password}@"
            f"{self.database_host}:{self.database_port}/{self.database_name}"
        )

    @property
    def alembic_dsn(self) -> str:
        return self.database_dsn.replace("+asyncpg", "")

    @property
    def redis_dsn(self) -> str:
        auth = ""
        if self.redis_password:
            auth = f":{self.redis_password.get_secret_value()}@"
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_name}"

    @property
    def telegram_webhook_path(self) -> str:
        return "/api/v1/telegram"

    @property
    def telegram_webhook_url(self) -> str:
        return f"https://{self.app_domain}{self.telegram_webhook_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
