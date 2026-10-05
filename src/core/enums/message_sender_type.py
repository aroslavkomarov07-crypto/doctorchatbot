from enum import StrEnum


class MessageSenderType(StrEnum):
    """
    Кто отправил сообщение.
    """

    USER = "user"
    EXPERT = "expert"