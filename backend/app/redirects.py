"""Permanent redirects for site pages that have moved (§SEO-2, §4E).

The three analyses — Frakcióelemzés, Kérdések, Települések — used to be sub-tabs
of the sections whose data they read (`/votes/cohesion`, `/questions`,
`/settlements`) and now live together under `/analyses/…`. Old addresses are out
in shared links, in the index, and in already-copied embed snippets, so they must
keep resolving.

They are answered here rather than in the SPA because a client-side redirect is
invisible to a crawler until it runs the app: Google files the old URL under
"Page with redirect" only when the server says so. A **301** consolidates the
old address onto the new one and passes its standing along; the SPA carries a
mirror of the same moves (`router.js`) for navigation that happens inside the
already-running app.

The query string rides across unchanged — an old link may carry `?cycle=` (the
global scope, §4A) or `?tab=` (which of the cohesion charts) and must land on
the same view it named.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse

# (old path, new path). Order matters for the settlement pair below: the static
# `/settlements/representatives` is registered before the two-segment key route,
# so "representatives" is never taken for a county code — the same ordering the
# SPA router makes.
MOVED: tuple[tuple[str, str], ...] = (
    ("/votes/cohesion", "/analyses/faction-cohesion"),
    ("/questions", "/analyses/questions"),
    ("/settlements", "/analyses/settlements"),
    ("/settlements/representatives", "/analyses/settlements/representatives"),
)


def _to(request: Request, path: str) -> RedirectResponse:
    query = request.url.query
    return RedirectResponse(path + (f"?{query}" if query else ""), status_code=301)


def register(app) -> None:
    """Register the moved-page redirects on `app`.

    Called before `og.register` and the catch-all SPA mount, so these exact
    paths are answered with a redirect instead of the app shell."""

    for old, new in MOVED:
        # Bind both paths per iteration (a closure over the loop variable would
        # leave every route pointing at the last pair).
        def _redirect(request: Request, _new: str = new) -> RedirectResponse:
            return _to(request, _new)

        app.get(old, include_in_schema=False)(_redirect)

    @app.get("/settlements/{maz}/{taz}", include_in_schema=False)
    def moved_settlement(maz: str, taz: str, request: Request) -> RedirectResponse:
        """A single settlement, keyed by the register's own `<maz>/<taz>`. The
        key is percent-escaped into the new path: it comes from the URL, and the
        response's Location header must not be able to carry anything else."""
        return _to(request, f"/analyses/settlements/{quote(maz, safe='')}"
                            f"/{quote(taz, safe='')}")
