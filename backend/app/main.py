"""Parlamonitor backend — FastAPI app (NFR-1/NFR-2/NFR-3).

A stateless API over a read-only SQLite file. Feature modules are mounted under
a stable, versioned, namespaced API (`/api/v1/<module>/…`, EXT-3); OpenAPI/Swagger
docs are served at `/api/docs`. The frontend discovers which modules are live via
`/api/v1/meta` and lazy-loads them (EXT-4/EXT-6).
"""

from __future__ import annotations

import os
import sqlite3

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .caching import CacheControlMiddleware
from .config import settings
from .db import get_db
from .modules.registry import load_modules

API_PREFIX = "/api/v1"

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


@app.get(f"{API_PREFIX}/meta", tags=["core"])
def meta(db: sqlite3.Connection = Depends(get_db)):
    """Site metadata + the live module manifest the SPA registers against (EXT-4)."""
    periods = [dict(r) for r in db.execute(
        "SELECT number, label, date_start, date_end FROM electoral_period "
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
class SPAStaticFiles(StaticFiles):
    """Static files with HTML5-history fallback: unknown paths (the SPA's
    client-side routes, e.g. /proceedings/43001-1) return index.html so a
    deep link / refresh resolves to the app shell (VIE-5)."""

    async def get_response(self, path, scope):
        from starlette.exceptions import HTTPException as StarletteHTTPException
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # Never mask API/media 404s with the SPA shell — those must stay JSON.
            is_app_route = not (path.startswith("api/") or path.startswith("media/")
                                or path in ("api", "media"))
            if exc.status_code == 404 and is_app_route:
                return await super().get_response("index.html", scope)
            raise


if settings.frontend_dist and os.path.isdir(settings.frontend_dist):
    # Server-render per-URL OpenGraph/Twitter share cards for the deep-link
    # routes (a shared sentence shows its quote + speaker, etc.). Registered
    # BEFORE the catch-all SPA mount so it shadows the static shell for these
    # exact paths; every other client route still gets the generic shell.
    from . import og
    og.register(app)

    app.mount("/", SPAStaticFiles(directory=settings.frontend_dist, html=True),
              name="frontend")
