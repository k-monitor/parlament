"""Parlamonitor backend — FastAPI app (NFR-1/NFR-2/NFR-3).

A stateless API over a read-only SQLite file. Feature modules are mounted under
a stable, versioned, namespaced API (`/api/v1/<module>/…`, EXT-3); OpenAPI/Swagger
docs are served at `/api/docs`. The frontend discovers which modules are live via
`/api/v1/meta` and lazy-loads them (EXT-4/EXT-6).
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import readability, seo
from .analytics import search_analytics
from .caching import CacheControlMiddleware
from .config import settings
from .db import get_db
from .modules.registry import load_modules

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the privacy-respecting search-analytics flush thread (PRIV-1). It
    # runs per worker process; a no-op / graceful self-disable when turned off or
    # unwritable. Only runs in a real server — the test client doesn't enter the
    # lifespan, so the suite never spawns the thread or writes the file.
    search_analytics.start()
    try:
        yield
    finally:
        search_analytics.stop()  # flush the in-progress hour on shutdown


app = FastAPI(
    title="Parlamonitor API",
    version="1.0.0",
    description=(
        "Harmadik feles, civil-tech API a Magyar Országgyűlés nyilvános "
        "jegyzőkönyveihez és képviselői statisztikáihoz. Minden adat forrása a "
        "parlament.hu. / Third-party civic-tech API for the Hungarian National "
        "Assembly's public proceedings and representative statistics."),
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# High-traffic hardening (NFR-1): compress the sizeable JSON/HTML payloads
# (origin egress is the bottleneck behind a CDN — Cloudflare pulls whatever the
# origin sends), and stamp path-based Cache-Control so the CDN/browser can
# absorb repeat traffic instead of the API (see caching.py).
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(CacheControlMiddleware)

# Mount the enabled feature modules (EXT-3/EXT-6).
_MODULES = [m for m in load_modules() if settings.module_enabled(m.name)]
for spec in _MODULES:
    app.include_router(spec.router, prefix=API_PREFIX)

# robots.txt + the XML sitemaps (SEO-1/SEO-3). Registered here, ahead of the SPA
# mount below: they carry a file extension, so the static handler would 404 them
# rather than fall through to the app shell.
seo.register(app)


def _metric_totals(db: sqlite3.Connection) -> dict:
    """How much of the corpus actually carries readability / diversity numbers.

    ``scored`` also gates the feature flag: the pass is skippable and its
    diversity half needs a lemmatizer, so "is this DB annotated at all" is a fact
    about the data, not about the config — and the SPA must hide the annotations
    rather than render a page of blanks. ``with_diversity`` says how much of it
    got the lemma-backed half."""
    try:
        scored, diverse = db.execute(
            "SELECT COUNT(*), COUNT(mattr) FROM speech_metrics").fetchone()
    except sqlite3.OperationalError:      # DB built before the metrics pass
        return {"scored": 0, "with_diversity": 0}
    return {"scored": scored, "with_diversity": diverse}


@app.get(f"{API_PREFIX}/meta", tags=["core"])
def meta(db: sqlite3.Connection = Depends(get_db)):
    """Site metadata + the live module manifest the SPA registers against (EXT-4)."""
    # Only surface cycles we can label by their year span. A partially scraped
    # cycle (sessions/bills/votes loaded but not its representatives registry)
    # has a bare electoral_period row with NULL date_start/date_end, which the
    # SPA would otherwise show as just the ordinal number ("39"). Hide those
    # until the cycle is set up enough to carry a start–end year label.
    periods = [dict(r) for r in db.execute(
        "SELECT number, label, date_start, date_end FROM electoral_period "
        "WHERE date_start IS NOT NULL "
        "ORDER BY number DESC")]
    counts = {
        "sessions": db.execute("SELECT COUNT(*) AS c FROM session").fetchone()["c"],
        "speeches": db.execute("SELECT COUNT(*) AS c FROM speech").fetchone()["c"],
        "sentences": db.execute("SELECT COUNT(*) AS c FROM sentence").fetchone()["c"],
        "representatives": db.execute(
            "SELECT COUNT(*) AS c FROM person WHERE is_mp=1").fetchone()["c"],
    }
    build = {r["key"]: r["value"] for r in db.execute(
        "SELECT key, value FROM build_meta")}
    metric_coverage = _metric_totals(db)
    return {
        "name": "Parlamonitor",
        "source_attribution": {  # LEGAL-1 / TRUST-1
            "name": "Magyar Országgyűlés",
            "url": "https://www.parlament.hu",
            "license_url": "https://www.parlament.hu/web/guest/felhasznalasi-feltetelek",
            "note": "Az adatok forrása a parlament.hu; a feldolgozást a "
                    "Parlamonitor végzi.",
        },
        "modules": [{"name": m.name, "label": m.label_hu} for m in _MODULES],
        "periods": periods,
        "counts": counts,
        "build": build,
        # Optional capabilities the SPA gates a nav entry on, beyond the module
        # manifest above. The constituency lookup (REP-10) depends on an external
        # source, so it can be turned off without disabling the whole
        # representatives module — the tab then vanishes rather than erroring.
        "features": {"constituency_lookup": settings.evk_lookup,
                     "speech_metrics": (settings.speech_metrics
                                        and bool(metric_coverage["scored"]))},
        # How the per-speech readability / lexical-diversity annotations were
        # measured (READ-7). Every derived number on the site has to be able to
        # say what produced it (TRUST-1 / REP-5), and these two carry parameters
        # (the Hungarian long-word threshold, the MATTR window) that a reader
        # cannot guess and that change what the score means.
        "speech_metrics": {**readability.methodology(), **metric_coverage},
        "timing_disclaimer": (  # VIE-6 / TIM-3
            "A felszólalások videóidőzítése a v1-ben pozícióalapú becslés "
            "(karakterarányos), ezért közelítő pontosságú."),
    }


@app.get(f"{API_PREFIX}/health", tags=["core"])
def health():
    from .db import open_connection
    try:
        conn = open_connection()
        try:
            conn.execute("SELECT 1")
        finally:
            conn.close()
        return {"status": "ok"}
    except Exception as e:  # pragma: no cover
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=503)


# MP portrait passthrough (LEGAL-1 attribution lives in /meta). Served only if a
# photos directory was produced by the scraper.
if settings.photos_dir and os.path.isdir(settings.photos_dir):
    app.mount("/media/photos", StaticFiles(directory=settings.photos_dir),
              name="photos")

# Optionally serve the built SPA so the whole thing runs from one process
# (OPS-1: single stateless backend). In dev the SPA runs under Vite instead.

# Namespaces whose 404s must NEVER become the SPA shell. `api/`/`media/` stay
# JSON. `assets/` is the dangerous one: it holds Vite's content-hashed bundles,
# which the cache layer stamps `immutable` for a year (caching.py). If a missing
# hashed asset (e.g. a request racing a blue-green deploy) returned the HTML
# shell with 200, Cloudflare/browsers would cache HTML under a .css/.js URL for
# a YEAR — poisoning the site until the entry expired. That actually happened to
# a stylesheet, so Firefox (strict MIME) refused to apply it. A missing asset
# must be a real 404, which the cache middleware never stamps immutable.
_NON_SHELL_PREFIXES = ("api/", "media/", "assets/")
_NON_SHELL_EXACT = frozenset({"api", "media", "assets"})


def _should_serve_shell(path: str) -> bool:
    """Whether `path` (mount-relative, no leading slash) should resolve to the
    SPA shell — the home page and every client-side *route*. True for the root
    (StaticFiles normalizes "/" to "" / "."), and for extensionless paths
    outside the api/media/assets namespaces. Anything that looks like a file
    (has an extension in its last segment) is a real static request: a miss must
    404, not hand back HTML under a file URL (see _NON_SHELL_PREFIXES)."""
    if path in ("", "."):
        return True  # the root request → the app shell (home page)
    if path.startswith(_NON_SHELL_PREFIXES) or path in _NON_SHELL_EXACT:
        return False
    return "." not in path.rsplit("/", 1)[-1]


class SPAStaticFiles(StaticFiles):
    """Static files with HTML5-history fallback: unknown *client routes* (e.g.
    /proceedings/43001-1) return index.html so a deep link / refresh resolves to
    the app shell (VIE-5). Missing *files* (assets, images, …) stay a real 404 —
    returning the shell for them lets the edge cache HTML under a file URL.

    Every shell we hand back — the root, an index/list route, a history
    fallback — is served with the site-wide DEFAULT OpenGraph/Twitter card
    injected (og.plain), so every page previews with the default image. The
    explicit per-page card routes (og.register) are mounted ahead of this and
    never reach here; they override the default with a specific card."""

    async def get_response(self, path, scope):
        from starlette.exceptions import HTTPException as StarletteHTTPException
        serve_shell = _should_serve_shell(path)
        # Serve a client-side route (the root included) as the app shell with
        # the default OG card injected. HEAD carries no body, so the card is
        # moot — let StaticFiles answer it directly.
        if serve_shell and scope.get("method") == "GET":
            shell = _default_card_shell(scope)
            if shell is not None:
                return shell
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and serve_shell:
                # Fallback: og couldn't render (or a HEAD request) — hand back
                # the bare shell so the route still resolves to the app.
                return await super().get_response("index.html", scope)
            raise


def _default_card_shell(scope):
    """The app shell + site-wide default OG card for `scope`, or None if it
    can't be rendered (then the caller serves the bare shell)."""
    from starlette.requests import Request
    from . import og
    try:
        return og.plain(Request(scope))
    except Exception:  # pragma: no cover - defensive; fall back to bare shell
        return None


if settings.frontend_dist and os.path.isdir(settings.frontend_dist):
    # Server-render per-URL OpenGraph/Twitter share cards for the deep-link
    # routes (a shared sentence shows its quote + speaker, etc.). Registered
    # BEFORE the catch-all SPA mount so it shadows the static shell for these
    # exact paths; every other client route still gets the generic shell.
    from . import og
    og.register(app)

    app.mount("/", SPAStaticFiles(directory=settings.frontend_dist, html=True),
              name="frontend")
