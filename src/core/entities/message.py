from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from core.enums.message_sender_type import MessageSenderType


@dataclass
class Message:
    """
    Одно сообщение в переписке пользователя с экспертом.
    """

    id: UUID = field(default_factory=uuid4)

    # Пользователь, которому принадлежит переписка
    user_id: UUID = field(default_factory=uuid4)

    # Эксперт, с которым идёт переписка
    expert_id: UUID = field(default_factory=uuid4)

    # Текст сообщения
    text: str = ""

    # Кто отправил сообщение
    sender_type: MessageSenderType = MessageSenderType.USER

    # время отправки
    created_at: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )

    def __post_init__(self) -> None:
        self.text = self.text.strip()
        if not self.text:
            raise ValueError(
                "Текст сообщения не может быть пустым."
            )
