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
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import parlacap, readability, seo
from .analytics import RESERVED_PARAMS, SOURCES, search_analytics
from .caching import CacheControlMiddleware
from .config import settings
from .db import get_db, period_and, period_sql
from .modules.registry import load_modules
from .query_cache import cached_aggregate

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
    # The API is read-only; the one POST is the anonymous search-quality ping
    # below, which writes nothing but a counter.
    allow_methods=["GET", "POST"],
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


def _topic_totals(db: sqlite3.Connection) -> dict:
    """How much of the corpus carries CAP topic predictions.

    ``speeches`` gates the feature flag the same way the metrics count does: the
    pass needs a model or a shipped cache, so "is this DB annotated at all" is a
    fact about the data rather than the config, and the SPA must hide the badge
    instead of rendering a column of blanks.

    Counted over *stored* paragraphs, before any confidence threshold — the
    threshold is applied per request and would make this number move without the
    data changing."""
    try:
        paragraphs, speeches = db.execute(
            "SELECT COUNT(*), COUNT(DISTINCT speech_id) FROM speech_topic").fetchone()
    except sqlite3.OperationalError:      # DB built before the topic pass
        return {"paragraphs": 0, "speeches": 0}
    return {"paragraphs": paragraphs, "speeches": speeches}


def _bill_topic_totals(db: sqlite3.Connection) -> dict:
    """How many irományok carry CAP topic predictions (TOPIC-8).

    Gates the badge exactly as the speech count does: the pass needs a document
    mirror or a shipped cache, so whether this DB is annotated is a fact about
    the data rather than the config, and the SPA must hide the chip instead of
    rendering a column of blanks. Counted before any confidence threshold, which
    is applied per request."""
    try:
        blocks, bills = db.execute(
            "SELECT COUNT(*), COUNT(DISTINCT bill_id) FROM bill_topic").fetchone()
    except sqlite3.OperationalError:      # DB built before the iromány topic pass
        return {"blocks": 0, "bills": 0}
    return {"blocks": blocks, "bills": bills}


def _corpus_counts(db: sqlite3.Connection) -> dict:
    """The homepage's headline totals, over the cycles the site serves (CYC-7).

    With no window these are the four corpus-wide counts they have always been;
    inside one each grows the same period predicate the API's own queries carry —
    the sentence count through a join on `speech`, since sentences are stamped
    only by the speech they belong to, and MPs through `membership`, since
    `person.is_mp` records having *ever* held a seat rather than holding one in
    these cycles.

    Memoized per DB build (the sentence count alone is a ~1 s scan of the full
    corpus, and this runs on every cold SPA load); the loader's atomic swap
    invalidates it via the DB identity folded into the cache key."""
    window = settings.site_periods

    def compute() -> dict:
        return {
            "sessions": db.execute(
                "SELECT COUNT(*) AS c FROM session WHERE 1=1"
                + period_and(window, "period_number")).fetchone()["c"],
            "speeches": db.execute(
                "SELECT COUNT(*) AS c FROM speech WHERE 1=1"
                + period_and(window, "period_number")).fetchone()["c"],
            "sentences": db.execute(
                "SELECT COUNT(*) AS c FROM sentence" if not window else
                "SELECT COUNT(*) AS c FROM sentence s "
                "JOIN speech sp ON sp.uid = s.speech_id "
                f"WHERE {period_sql(window, 'sp.period_number')}").fetchone()["c"],
            "representatives": db.execute(
                "SELECT COUNT(*) AS c FROM person WHERE is_mp=1" if not window else
                "SELECT COUNT(DISTINCT person_id) AS c FROM membership "
                f"WHERE {period_sql(window, 'period_number')}").fetchone()["c"],
        }

    return cached_aggregate("meta_counts", window, compute)


@app.get(f"{API_PREFIX}/meta", tags=["core"])
def meta(db: sqlite3.Connection = Depends(get_db)):
    """Site metadata + the live module manifest the SPA registers against (EXT-4)."""
    # Only surface cycles we can label by their year span. A partially scraped
    # cycle (sessions/bills/votes loaded but not its representatives registry)
    # has a bare electoral_period row with NULL date_start/date_end, which the
    # SPA would otherwise show as just the ordinal number ("39"). Hide those
    # until the cycle is set up enough to carry a start–end year label.
    #
    # This list is also what the header's cycle chooser offers, so a deployment
    # serving a window of cycles (CYC-7) hides the rest here: a cycle the site
    # does not serve is not one the reader can pick, and a saved/`?cycle=`
    # selection naming one falls back to the default (store.js `parseCycles`).
    periods = [dict(r) for r in db.execute(
        "SELECT number, label, date_start, date_end FROM electoral_period "
        "WHERE date_start IS NOT NULL"
        + period_and(settings.site_periods, "number")
        + " ORDER BY number DESC")]
    counts = _corpus_counts(db)
    build = {r["key"]: r["value"] for r in db.execute(
        "SELECT key, value FROM build_meta")}
    metric_coverage = _metric_totals(db)
    topic_coverage = _topic_totals(db)
    bill_topic_coverage = _bill_topic_totals(db)
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
        # The cycles this deployment serves (CYC-7), or [] when it serves the
        # whole corpus. `periods` above is already filtered to them; this states
        # the window itself, so a client can tell "the corpus has one cycle" from
        # "the site is showing one cycle of a larger corpus".
        "site_cycles": list(settings.site_periods),
        "counts": counts,
        "build": build,
        # Optional capabilities the SPA gates a nav entry on, beyond the module
        # manifest above. The constituency lookup (REP-10) depends on an external
        # source, so it can be turned off without disabling the whole
        # representatives module — the tab then vanishes rather than erroring.
        "features": {"constituency_lookup": settings.evk_lookup,
                     "speech_metrics": (settings.speech_metrics
                                        and bool(metric_coverage["scored"])),
                     "speech_topics": (settings.parlacap
                                       and bool(topic_coverage["speeches"])),
                     "bill_topics": (settings.parlacap and settings.bill_topics
                                     and bool(bill_topic_coverage["bills"])),
                     # The order paper for the coming sitting (NR-5). On only
                     # when the scrape has actually produced one, so a
                     # deployment that does not run the stage shows nothing
                     # rather than an empty promise.
                     "upcoming_agenda": _has_upcoming_agenda(db)},
        # How the per-speech readability / lexical-diversity annotations were
        # measured (READ-7). Every derived number on the site has to be able to
        # say what produced it (TRUST-1 / REP-5), and these two carry parameters
        # (the Hungarian long-word threshold, the MATTR window) that a reader
        # cannot guess and that change what the score means.
        "speech_metrics": {**readability.methodology(), **metric_coverage},
        # How the CAP topic labels were produced, including the confidence
        # threshold in force — at 0.90 a third of paragraphs are deliberately left
        # unlabelled, which changes what the reader is looking at, so the UI has to
        # be able to say so (TRUST-1 / REP-5).
        "speech_topics": {**parlacap.methodology(), **topic_coverage},
        # The same labels over the irományok's own document text (TOPIC-8). Same
        # model, same threshold, different unit — so the UI can say what it is
        # looking at rather than reusing the speech wording (TRUST-1 / REP-5).
        "bill_topics": {**parlacap.methodology(), "unit": "document block",
                        **bill_topic_coverage},
        "timing_disclaimer": (  # VIE-6 / TIM-3
            "A felszólalások videóidőzítése a v1-ben pozícióalapú becslés "
            "(karakterarányos), ezért közelítő pontosságú."),
    }


def _has_upcoming_agenda(db) -> bool:
    """Whether an order paper has been loaded (NR-5).

    Two conditions, both of which have to hold for the home page to have
    anything to show: the DB carries the NR tables at all, and the last scrape
    actually parsed a napirend out of the Aktuális page."""
    try:
        return bool(db.execute(
            "SELECT 1 FROM agenda_doc WHERE kind='agenda' AND item_count > 0 "
            "LIMIT 1").fetchone())
    except sqlite3.OperationalError:
        return False


class SearchClick(BaseModel):
    """One search-quality ping: which search box, which search, and where in the
    result list the opened result sat."""

    source: str = Field(description="Which search box the search ran in: "
                                    + " | ".join(sorted(SOURCES)))
    rank: int = Field(description="1-based position of the opened result in the "
                                  "whole result list — page offset included, so "
                                  "the first hit on page 2 of 20 is 21")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="The query (`q`) and filters the search itself ran with, "
                    "echoed back so the click is counted onto that search's "
                    "bucket. Parameters the search box does not have are ignored.")


# A ping carrying more keys than any search box has is not a search of ours;
# ignore it rather than walking a hand-crafted dictionary.
_MAX_CLICK_PARAMS = 40


@app.post(f"{API_PREFIX}/search/click", status_code=204, tags=["core"])
def search_click(click: SearchClick) -> None:
    """Count the FIRST result a reader opened from a search (SEA-12).

    The quality half of the search analytics (PRIV-2): with the search itself
    already counted, `clicks / searches` gives the click-through rate and
    `click_rank_sum / clicks` the mean position of the result readers actually
    open — 1.0 meaning the top hit answers them. Like every other analytics
    write it is anonymous and aggregated: no identifier of any kind is read or
    stored, the ping carries none, and it only ever increments two counters on
    the `(hour, keyword, filters)` bucket the search was counted into. One
    executed search contributes at most one click (the SPA disarms its ping on
    the first result opened).

    Always answers 204: a ping that names an unknown search box, carries no
    keyword or reports an implausible position is dropped silently — analytics
    must never argue with the page."""
    params = dict(click.params or {})
    if len(params) > _MAX_CLICK_PARAMS:
        return None
    query = params.pop("q", None)
    # Everything that isn't a filter is dropped here: the reserved names would
    # collide with record_click()'s own arguments, and paging is not part of the
    # key (`rank` already counts from the top of the whole list).
    filters = {k: v for k, v in params.items() if k not in RESERVED_PARAMS}
    search_analytics.record_click(source=click.source, query=query,
                                  rank=click.rank, **filters)
    return None


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
    from . import og, redirects
    # Pages that have moved keep resolving: a 301 from every old address, ahead
    # of both the card routes and the SPA mount so neither answers them (§4E).
    redirects.register(app)
    og.register(app)

    app.mount("/", SPAStaticFiles(directory=settings.frontend_dist, html=True),
              name="frontend")
