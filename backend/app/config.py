"""Environment-driven backend configuration (OPS-4).

Nothing operational is hard-coded: the DB path, CORS origins, photo directory
and the set of *enabled modules* (EXT-6) all come from the environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Every module the backend knows how to mount. A module absent from
# PARLAMONITOR_MODULES is never registered: its API routes 404 and the frontend,
# which reads /api/v1/meta, hides its nav entry (EXT-6).
ALL_MODULES = ("proceedings", "representatives", "bills", "votes")

# Per-speech types (felszólalás típusa) whose speeches are procedural/chairing
# and therefore excluded from representative/faction statistics (STAT-1). Kept
# configurable (OPS-4) so related chairing/ügyrendi types can be added without a
# code change. Primarily "ülésvezetés" — the chair's interjections that would
# otherwise inflate the presiding officer's totals.
DEFAULT_PROCEDURAL_SPEECH_TYPES = ("ülésvezetés",)


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: os.environ.get(
        "PARLAMONITOR_DB", str(Path(__file__).resolve().parents[1] / "parlamonitor.db")))
    photos_dir: str | None = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_PHOTOS_DIR") or _default_photos_dir())
    cors_origins: list[str] = field(default_factory=lambda: [
        o.strip() for o in os.environ.get(
            "PARLAMONITOR_CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173").split(",")
        if o.strip()])
    enabled_modules: list[str] = field(default_factory=lambda: _enabled_modules())
    frontend_dist: str | None = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_FRONTEND_DIST") or None)
    # Cap on reported search totals so a pathological query can't scan forever.
    max_search_total: int = int(os.environ.get("PARLAMONITOR_MAX_SEARCH_TOTAL", "5000"))
    # Folded set of speech types excluded from statistics (STAT-1).
    procedural_speech_types: frozenset = field(default_factory=lambda: _procedural_speech_types())

    def module_enabled(self, name: str) -> bool:
        return name in self.enabled_modules

    def is_procedural_type(self, speech_type: str | None) -> bool:
        """Whether a per-speech type is a statistics-excluded chairing type
        (STAT-1). Case/whitespace-insensitive."""
        return bool(speech_type) and speech_type.strip().casefold() in self.procedural_speech_types


def _procedural_speech_types() -> frozenset:
    raw = os.environ.get("PARLAMONITOR_PROCEDURAL_SPEECH_TYPES")
    if raw is None:
        types = DEFAULT_PROCEDURAL_SPEECH_TYPES
    else:
        types = [t.strip() for t in raw.split(",") if t.strip()]
    return frozenset(t.casefold() for t in types)


def _enabled_modules() -> list[str]:
    raw = os.environ.get("PARLAMONITOR_MODULES")
    if not raw:
        return list(ALL_MODULES)
    requested = [m.strip() for m in raw.split(",") if m.strip()]
    return [m for m in requested if m in ALL_MODULES]


def _default_photos_dir() -> str | None:
    # The scraper writes MP portraits under data/media/photos.
    guess = Path(__file__).resolve().parents[2] / "data" / "media" / "photos"
    return str(guess) if guess.exists() else None


settings = Settings()
