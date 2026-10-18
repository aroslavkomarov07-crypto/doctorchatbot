from taskiq_redis import ListQueueBroker

from src.core.config import get_settings
from src.infrastructure.taskiq.middlewares import TaskErrorReportingMiddleware

broker = ListQueueBroker(get_settings().redis_dsn).with_middlewares(TaskErrorReportingMiddleware())
