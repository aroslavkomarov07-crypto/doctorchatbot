import re
from html import escape

from src.core.exceptions import ValidationError

PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()-]{8,19}$")
NAME_RE = re.compile(r"^[^\n\r<>]{3,160}$")


def normalize_phone(value: str) -> str:
    raw = value.strip()
    if not PHONE_RE.fullmatch(raw):
        raise ValidationError("Введите корректный номер телефона")
    digits = "".join(char for char in raw if char.isdigit())
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if not 10 <= len(digits) <= 15:
        raise ValidationError("Введите корректный номер телефона")
    return f"+{digits}"


def validate_full_name(value: str) -> str:
    normalized = " ".join(value.split())
    if not NAME_RE.fullmatch(normalized) or len(normalized.split()) < 2:
        raise ValidationError("Введите имя и фамилию, от 3 до 160 символов")
    return normalized


def validate_text(value: str, *, minimum: int = 1, maximum: int = 4000) -> str:
    normalized = value.strip()
    if not minimum <= len(normalized) <= maximum:
        raise ValidationError(f"Длина текста должна быть от {minimum} до {maximum} символов")
    return normalized


def safe_html(value: str) -> str:
    return escape(value, quote=False)
