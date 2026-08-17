"""Module registry — the seam that makes the backend extensible (EXT-1/EXT-3).

Each feature module exposes an APIRouter under its own namespace. Mounting a new
domain (Bills, Votes, …) is purely additive: write its router, add one entry
here, list it in ``ALL_MODULES`` (config). Existing routes are untouched (EXT-3),
and a module left out of ``PARLAMONITOR_MODULES`` is simply never mounted (EXT-6).
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
    from .bills.router import router as bills_router
    from .portfolios.router import router as portfolios_router
    from .proceedings.router import router as proceedings_router
    from .representatives.router import router as representatives_router
    from .settlements.router import router as settlements_router
    from .votes.router import router as votes_router
    return [
        ModuleSpec("proceedings", "Felszólalások", proceedings_router),
        ModuleSpec("representatives", "Képviselők", representatives_router),
        ModuleSpec("bills", "Törvényjavaslatok", bills_router),
        ModuleSpec("votes", "Szavazások", votes_router),
        # Települések (§6D): the local dimension — which places the House names and
        # which it never does. Reads the shared sentence/speech/person rows through
        # its own derived tables (EXT-2) and owns no scraping.
        ModuleSpec("settlements", "Települések", settlements_router),
        # Its pages live in the Representatives section's tab bar (MIN-5), but it
        # is its own slice: it reads the bills module's tables and owns none of
        # them, so it switches off independently of both (EXT-1/EXT-6).
        ModuleSpec("portfolios", "Tárcák", portfolios_router),
    ]
