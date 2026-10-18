from decimal import ROUND_HALF_UP, Decimal

from src.application.dto import CommissionBreakdown
from src.core.exceptions import ValidationError

CENT = Decimal("0.01")
HUNDRED = Decimal("100")


def money(value: Decimal | str | int) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def calculate_commissions(
    gross: Decimal | str | int,
    service_percent: Decimal | str | int,
    legacy_service_percent: Decimal | str | int | None = None,
) -> CommissionBreakdown:
    gross_value = money(gross)
    service_rate = Decimal(service_percent)
    legacy_payment_rate = Decimal("0")
    if legacy_service_percent is not None:
        legacy_payment_rate = service_rate
        service_rate = Decimal(legacy_service_percent)
    total_rate = legacy_payment_rate + service_rate

    if gross_value < 0:
        raise ValidationError("Сумма платежа не может быть отрицательной")
    if legacy_payment_rate < 0 or service_rate < 0:
        raise ValidationError("Комиссия не может быть отрицательной")
    if total_rate > HUNDRED:
        raise ValidationError("Комиссия не может превышать 100%")

    service_fee = money(gross_value * service_rate / HUNDRED)
    payment_fee = money(gross_value * legacy_payment_rate / HUNDRED)
    expert_income = money(gross_value - payment_fee - service_fee)
    return CommissionBreakdown(
        gross=gross_value,
        service_percent=total_rate if legacy_service_percent is None else service_rate,
        service_fee=service_fee,
        expert_income=expert_income,
        payment_percent=legacy_payment_rate,
        payment_fee=payment_fee,
    )
