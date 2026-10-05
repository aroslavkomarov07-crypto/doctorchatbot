import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
        default_factory=lambda: datetime.now(UTC)
    )

    def __post_init__(self) -> None:
        if self.telegram_id <= 0:
            raise ValueError(
                "Telegram ID должен быть положительным числом."
            )

        self.full_name = self.full_name.strip()
        if not self.full_name:
            raise ValueError(
                "ФИО пользователя не может быть пустым."
            )

        self.phone_number = self.phone_number.strip().replace(" ", "")
        if not re.fullmatch(r"\+?[1-9]\d{7,14}", self.phone_number):
            raise ValueError(
                "Номер телефона должен быть указан в международном формате."
            )
