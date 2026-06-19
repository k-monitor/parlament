"""Module registry — the seam that makes the backend extensible (EXT-1/EXT-3).

Each feature module exposes an APIRouter under its own namespace. Mounting a new
domain (Bills, Votes, …) is purely additive: write its router, add one entry
here, list it in ``ALL_MODULES`` (config). Existing routes are untouched (EXT-3),
and a module left out of ``OGYWATCH_MODULES`` is simply never mounted (EXT-6).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter


@dataclass(frozen=True)
class ModuleSpec:
    name: str               # url namespace + config key, e.g. "proceedings"
    label_hu: str           # human label for the frontend nav
    router: APIRouter


def load_modules() -> list[ModuleSpec]:
    # Imported lazily so a syntax error in one module can't break import order.
    from .proceedings.router import router as proceedings_router
    from .representatives.router import router as representatives_router
    return [
        ModuleSpec("proceedings", "Felszólalások", proceedings_router),
        ModuleSpec("representatives", "Képviselők", representatives_router),
    ]
