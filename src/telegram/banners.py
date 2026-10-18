from __future__ import annotations

from pathlib import Path

from aiogram.types import FSInputFile, InputFile

from src.core.config import Settings

BANNER_KEYS = frozenset(
    {
        "welcome",
        "client_main",
        "expert_main",
        "admin_main",
        "experts",
        "tariffs",
        "tariff",
        "subscriptions",
        "consultations",
        "referral",
        "help",
        "expert_profile",
        "admin_user",
        "finance",
    }
)

BANNER_FALLBACKS = {
    "welcome": "client_main",
    "tariff": "tariffs",
    "expert_profile": "expert_main",
    "admin_user": "admin_main",
}


class BannerCatalog:
    """Resolves optional Telegram file IDs or local menu banner files."""

    def __init__(self, settings: Settings) -> None:
        self.directory = Path(settings.banner_directory)
        self.file_ids = settings.banner_file_ids

    def resolve(self, key: str) -> str | InputFile | None:
        for candidate in (key, BANNER_FALLBACKS.get(key, "client_main")):
            file_id = self.file_ids.get(candidate)
            if file_id:
                return file_id
            for suffix in (".jpg", ".jpeg", ".png", ".webp"):
                path = self.directory / f"{candidate}{suffix}"
                if path.is_file():
                    return FSInputFile(path)
        return None
