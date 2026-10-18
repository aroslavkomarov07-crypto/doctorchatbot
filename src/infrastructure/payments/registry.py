from src.application.ports import PaymentGateway
from src.core.enums import PaymentProvider
from src.core.exceptions import ValidationError


class GatewayRegistry:
    def __init__(self, *gateways: PaymentGateway) -> None:
        self._gateways = {gateway.provider: gateway for gateway in gateways}

    def get(self, provider: PaymentProvider) -> PaymentGateway:
        gateway = self._gateways.get(provider)
        if gateway is None:
            raise ValidationError(f"Платежная система {provider} не настроена")
        return gateway
