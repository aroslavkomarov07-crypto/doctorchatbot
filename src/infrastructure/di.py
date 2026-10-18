from dishka import AsyncContainer, Provider, Scope, make_async_container, provide

from src.core.config import Settings
from src.infrastructure.database.session import Database


class InfrastructureProvider(Provider):
    scope = Scope.APP

    @provide
    def database(self, settings: Settings) -> Database:
        return Database(settings)


def create_container(settings: Settings) -> AsyncContainer:
    return make_async_container(InfrastructureProvider(), context={Settings: settings})
