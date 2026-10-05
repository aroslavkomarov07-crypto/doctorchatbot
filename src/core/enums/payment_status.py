from enum import StrEnum


class PaymentStatus(StrEnum):
    """
    Состояние платежа.
    """

    # Платёж создан, но ещё не завершён
    PENDING = "pending"

    # Платёж успешно проведён
    SUCCEEDED = "succeeded"

    # Платёж завершился ошибкой
    FAILED = "failed"

    # Платёж возвращён пользователю
    REFUNDED = "refunded"