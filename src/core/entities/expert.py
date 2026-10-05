from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4


@dataclass
class Expert:
    """
    Эксперт, с которым пользователь может общаться
    по платной подписке.
    """

    id: UUID = field(default_factory=uuid4)

    # Telegram ID эксперта
    telegram_id: int = 0

    # ФИО эксперта
    full_name: str = ""

    # Краткое описание эксперта
    description: str | None = None

    # Может ли эксперт принимать новые обращения
    is_active: bool = True

    # Дата регистрации эксперта
    created_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )

    def __post_init__(self) -> None:
        if self.telegram_id <= 0:
            raise ValueError(
                "Telegram ID эксперта должен быть положительным числом."
            )

        self.full_name = self.full_name.strip()
        if not self.full_name:
            raise ValueError(
                "ФИО эксперта не может быть пустым."
            )

        if self.description is not None:
            self.description = self.description.strip() or None
