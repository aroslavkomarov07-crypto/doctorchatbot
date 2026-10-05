from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4


@dataclass
class User:
    """
    Пользователь бота, который покупает подписку
    на общение с экспертом.
    """

    id: UUID = field(default_factory=uuid4)

    # Telegram ID пользователя
    telegram_id: int = 0

    # ФИО
    full_name: str = ""

    # Номер телефона
    phone_number: str = ""

    # Дата регистрации
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        if self.telegram_id <= 0:
            raise ValueError(
                "Telegram ID должен быть положительным числом."
            )

        if not self.full_name.strip():
            raise ValueError(
                "ФИО пользователя не может быть пустым."
            )

        if not self.phone_number.strip():
            raise ValueError(
                "Номер телефона не может быть пустым."
            )