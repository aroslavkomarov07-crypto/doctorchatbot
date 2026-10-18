from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from src.core.config import Settings


def configure_logging(settings: Settings) -> None:
    path = Path(settings.app_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.app_log_level.upper(),
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        path,
        level=settings.app_log_level.upper(),
        rotation="10 MB",
        retention="14 days",
        compression="gz",
        encoding="utf-8",
        backtrace=True,
        diagnose=False,
        enqueue=True,
    )
