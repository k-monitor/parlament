"""Environment-driven backend configuration (OPS-4).

Nothing operational is hard-coded: the DB path, CORS origins, photo directory
and the set of *enabled modules* (EXT-6) all come from the environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Every module the backend knows how to mount. A module absent from
# OGYWATCH_MODULES is never registered: its API routes 404 and the frontend,
# which reads /api/v1/meta, hides its nav entry (EXT-6).
ALL_MODULES = ("proceedings", "representatives", "bills")


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: os.environ.get(
        "OGYWATCH_DB", str(Path(__file__).resolve().parents[1] / "ogywatch.db")))
    photos_dir: str | None = field(default_factory=lambda:
        os.environ.get("OGYWATCH_PHOTOS_DIR") or _default_photos_dir())
    cors_origins: list[str] = field(default_factory=lambda: [
        o.strip() for o in os.environ.get(
            "OGYWATCH_CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173").split(",")
        if o.strip()])
    enabled_modules: list[str] = field(default_factory=lambda: _enabled_modules())
    frontend_dist: str | None = field(default_factory=lambda:
        os.environ.get("OGYWATCH_FRONTEND_DIST") or None)
    # Cap on reported search totals so a pathological query can't scan forever.
    max_search_total: int = int(os.environ.get("OGYWATCH_MAX_SEARCH_TOTAL", "5000"))

    def module_enabled(self, name: str) -> bool:
        return name in self.enabled_modules


def _enabled_modules() -> list[str]:
    raw = os.environ.get("OGYWATCH_MODULES")
    if not raw:
        return list(ALL_MODULES)
    requested = [m.strip() for m in raw.split(",") if m.strip()]
    return [m for m in requested if m in ALL_MODULES]


def _default_photos_dir() -> str | None:
    # The scraper writes MP portraits under data/media/photos.
    guess = Path(__file__).resolve().parents[2] / "data" / "media" / "photos"
    return str(guess) if guess.exists() else None


settings = Settings()
