class DomainError(Exception):
    """Expected business-rule violation safe to show to a user."""


class AccessDenied(DomainError): ...


class ValidationError(DomainError): ...


class SubscriptionRequired(DomainError): ...


class MessageLimitReached(DomainError): ...


class PaymentConflict(DomainError): ...


class InsufficientBalance(DomainError): ...
