"""Crawler-facing site plumbing: `robots.txt` and the XML sitemaps (SEO-1…SEO-4).

The site is a client-rendered SPA over ~260 000 content pages (every speech,
sitting day, MP, iromány and vote). Nothing links to most of them from a
crawlable index — the browse pages paginate through an API — so **discovery has
to come from a sitemap**, and the crawl budget has to be spent on content pages
rather than on the filter permutations of the browse pages.

Two routes do that:

- ``/robots.txt`` — opens the content pages, closes the faceted/parameterised
  views (which all carry a ``<link rel="canonical">`` back to their clean URL
  anyway, see `og.py`) and points at the sitemap index.
- ``/sitemap.xml`` — a **sitemap index** over per-section child sitemaps
  (``/sitemap-<section>-<n>.xml``), each capped at ``URLS_PER_SITEMAP`` entries.
  Children are generated straight from SQLite on request and cached in-process
  against the DB file's identity, so a sync that swaps the DB invalidates them.

Only sections whose feature module is mounted are advertised (EXT-6): a
deployment running without the votes module must not sitemap ``/votes/…``.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from urllib.parse import quote
from xml.sax.saxutils import escape

from fastapi import Depends, Request
from fastapi.responses import PlainTextResponse, Response

from .config import settings
from .db import get_db, period_and, period_sql

logger = logging.getLogger(__name__)

# A sitemap may hold 50 000 URLs / 50 MB; half that keeps each file small enough
# to fetch quickly and re-generate cheaply.
URLS_PER_SITEMAP = 25_000

# How many sentences a speech needs before its page is worth offering up. A
# one-line procedural interjection ("Köszönöm.") is thin content: Google crawls
# it, declines to index it, and the 40 000 of them drown out the pages that
# matter. Three is the floor for a page that stands on its own.
MIN_SENTENCES = 3

# Sitemaps change only when the DB is rebuilt (every sync interval at most), and
# a crawler re-reads them rarely. Cache them hard at the edge.
SITEMAP_CACHE_CONTROL = "public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800"
ROBOTS_CACHE_CONTROL = "public, max-age=3600, s-maxage=86400"


# --- what goes in the sitemaps ----------------------------------------------

# Static routes, with the module each belongs to (None = always present). These
# are the entry points a crawler can actually reach the corpus through.
STATIC_PATHS: tuple[tuple[str, str | None], ...] = (
    ("/", None),
    ("/about", None),
    ("/search", "proceedings"),
    ("/sessions", "proceedings"),
    ("/representatives", "representatives"),
    ("/representatives/factions", "representatives"),
    # The two mandate-less categories of the Felszólalók page. Its "all" chip
    # (`/representatives/all`) is deliberately absent: it is the union of these
    # three, so listing it would offer a crawler a fourth URL with nothing on it
    # the other three don't already have. It stays reachable and indexable — just
    # not advertised as an entry point of its own.
    ("/representatives/advocates", "representatives"),
    ("/representatives/speakers", "representatives"),
    ("/representatives/officials", "representatives"),
    ("/representatives/portfolios", "portfolios"),
    ("/bills", "bills"),
    ("/documents", "bills"),
    ("/questions", "bills"),
    ("/votes", "votes"),
)


@dataclass(frozen=True)
class _Section:
    """One paginated slice of the corpus.

    `sql` selects ``(path, lastmod)`` — an app-relative path and a W3C date (or
    NULL) — and takes ``:limit``/``:offset``; `count_sql` counts the same set.
    The two must agree on the WHERE clause, or pagination drifts.
    """
    name: str
    module: str
    count_sql: str
    sql: str


def _sections() -> tuple[_Section, ...]:
    """The corpus slices, built against the cycles this deployment serves.

    Built per call rather than kept as a constant because that window is config
    (``PARLAMONITOR_SITE_CYCLES``, §4A CYC-7): with none set every query below is
    the plain corpus-wide one it has always been, and with one set each grows the
    same period predicate the API's own queries carry — so the sitemap never
    offers a crawler a page the site now answers 404 to.

    People are the exception the API makes too: a profile is biography rather
    than a cycle's record (REP-2), so it stays readable whatever the window, but
    it is only *advertised* for people who sat or spoke inside it — otherwise a
    windowed site would submit tens of thousands of profiles whose every page has
    nothing to show. Portfolios (a fixed ministry list, cycle-scoped inside the
    page) are advertised unchanged."""
    window = settings.site_periods
    ses = period_and(window, "period_number")                       # "" when unwindowed
    sp_where = f"WHERE {period_sql(window, 'sp.period_number')}" if window else ""
    votes_where = f" WHERE {period_sql(window, 'period_number')}" if window else ""
    return (
      _Section(
        "sessions", "proceedings",
        "SELECT COUNT(*) FROM session "
        f"WHERE COALESCE(status,'published')='published'{ses}",
        f"""SELECT '/sessions/' || id AS path, date AS lastmod
             FROM session
            WHERE COALESCE(status,'published')='published'{ses}
            ORDER BY date DESC, id DESC
            LIMIT :limit OFFSET :offset""",
      ),
      # Speeches are the corpus — everything with enough text to be a page of its
      # own (MIN_SENTENCES).
      _Section(
        "speeches", "proceedings",
        """SELECT COUNT(*) FROM (SELECT speech_id FROM sentence
                                  GROUP BY speech_id
                                  HAVING COUNT(*) >= :min_sentences)"""
        if not window else
        f"""SELECT COUNT(*) FROM speech sp
              JOIN (SELECT speech_id FROM sentence
                     GROUP BY speech_id HAVING COUNT(*) >= :min_sentences) t
                ON t.speech_id = sp.uid
             {sp_where}""",
        f"""SELECT '/proceedings/' || sp.uid AS path, ss.date AS lastmod
             FROM speech sp
             JOIN session ss ON ss.id = sp.session_id
             JOIN (SELECT speech_id FROM sentence
                    GROUP BY speech_id HAVING COUNT(*) >= :min_sentences) t
               ON t.speech_id = sp.uid
            {sp_where}
            ORDER BY sp.session_id DESC, sp.speech_index
            LIMIT :limit OFFSET :offset""",
      ),
      # Everyone with a profile page worth reading: MPs, nationality advocates and
      # anyone who has spoken in the House (REP-12). A bare person row carrying
      # nothing but a name is not a page.
      _Section(
        "representatives", "representatives",
        f"SELECT COUNT(*) FROM person WHERE {_people_where(window)}",
        f"""SELECT '/representatives/' || person_id AS path, NULL AS lastmod
             FROM person
            WHERE {_people_where(window)}
            ORDER BY is_mp DESC, label
            LIMIT :limit OFFSET :offset""",
      ),
      # Törvényjavaslatok live under /bills, every other iromány type under
      # /documents — the same split the two browse pages make (main_type 'T'), and
      # the one `og.py` canonicalises to.
      _Section(
        "bills", "bills",
        f"SELECT COUNT(*) FROM bill WHERE main_type='T'{ses}",
        f"""SELECT '/bills/' || id AS path, submitted_date AS lastmod
             FROM bill WHERE main_type='T'{ses}
            ORDER BY submitted_date DESC, id
            LIMIT :limit OFFSET :offset""",
      ),
      _Section(
        "documents", "bills",
        "SELECT COUNT(*) FROM bill "
        f"WHERE (main_type IS NULL OR main_type<>'T'){ses}",
        f"""SELECT '/documents/' || id AS path, submitted_date AS lastmod
             FROM bill WHERE (main_type IS NULL OR main_type<>'T'){ses}
            ORDER BY submitted_date DESC, id
            LIMIT :limit OFFSET :offset""",
      ),
      # The tárcák (§6C). Few pages, but each is a standing entry point into a
      # ministry's whole record — the kind of page a search for "Belügyminisztérium
      # kérdések" should be able to land on.
      _Section(
        "portfolios", "portfolios",
        "SELECT COUNT(*) FROM portfolio",
        """SELECT '/representatives/portfolios/' || slug AS path, NULL AS lastmod
             FROM portfolio ORDER BY ord
            LIMIT :limit OFFSET :offset""",
      ),
      _Section(
        "votes", "votes",
        f"SELECT COUNT(*) FROM vote{votes_where}",
        f"""SELECT '/votes/' || id AS path, substr(vote_datetime, 1, 10) AS lastmod
             FROM vote{votes_where}
            ORDER BY vote_datetime DESC, id
            LIMIT :limit OFFSET :offset""",
      ),
    )


def _people_where(window: tuple[int, ...]) -> str:
    """Which people get a sitemap entry — corpus-wide, or only those who sat or
    spoke inside the served window (see `_sections`). Membership covers MPs and
    nationality advocates alike; the speech arm adds ministers and other
    non-members who spoke, which is what earns a profile a page (REP-12)."""
    if not window:
        return ("is_mp=1 OR is_advocate=1 "
                "OR person_id IN (SELECT person_id FROM speech "
                                  "WHERE person_id IS NOT NULL)")
    return (f"person_id IN (SELECT person_id FROM membership "
            f"WHERE {period_sql(window, 'period_number')}) "
            f"OR person_id IN (SELECT person_id FROM speech "
            f"WHERE person_id IS NOT NULL "
            f"AND {period_sql(window, 'period_number')})")


def _enabled_sections() -> list[_Section]:
    return [s for s in _sections() if settings.module_enabled(s.module)]


def _enabled_static() -> list[str]:
    return [p for p, mod in STATIC_PATHS
            if mod is None or settings.module_enabled(mod)]


# --- URL building -----------------------------------------------------------

def _base_url(request: Request) -> str:
    """The outward-facing origin. The configured canonical URL wins (correct
    behind a proxy, and the only right answer for a sitemap, whose URLs must be
    absolute and on the indexed host); otherwise the request's own base."""
    if settings.site_url:
        return settings.site_url
    return str(request.base_url).rstrip("/")


def _loc(base: str, path: str) -> str:
    return escape(base + quote(path, safe="/"))


def _urlset(base: str, rows: list[tuple[str, str | None]]) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, lastmod in rows:
        out.append("<url><loc>" + _loc(base, path) + "</loc>"
                   + (f"<lastmod>{escape(lastmod)}</lastmod>" if lastmod else "")
                   + "</url>")
    out.append("</urlset>")
    return "\n".join(out)


def _xml(body: str) -> Response:
    return Response(body, media_type="application/xml",
                    headers={"Cache-Control": SITEMAP_CACHE_CONTROL})


# --- generation + caching ---------------------------------------------------

# key → (corpus version, rendered XML). A sitemap is a pure function of the
# corpus, so it only has to be built once per load — a full speeches page costs
# a ~200 000-row scan, which is not something to repeat per crawler hit.
#
# Bounded, because a full child sitemap is a couple of megabytes of string and
# there are a dozen of them per worker: a crawler walking the whole index would
# otherwise pin ~30 MB in every process, on a box sized for the DB to sit in the
# page cache. Crawlers fetch these rarely and sequentially, and the edge holds
# them for a day, so a small window is enough to absorb the bursts that matter.
MAX_CACHED_SITEMAPS = 4

_cache: dict[str, tuple[str, str]] = {}


def _corpus_version(db: sqlite3.Connection) -> str:
    """What the sitemaps are a function of, as a cache key.

    Taken from `build_meta` rather than the DB file's mtime: SQLite runs in WAL
    mode here, so a committed update need not touch the main file at all, and a
    blue-green deploy may swap the file without changing what is in it. The
    loader stamps these rows on every run (ING-*), which is exactly when a
    sitemap goes stale."""
    try:
        rows = db.execute(
            "SELECT key, value FROM build_meta WHERE key IN "
            "('data_updated_at', 'sessions_loaded', 'last_update_sessions') "
            "ORDER BY key").fetchall()
        return "|".join(f"{r['key']}={r['value']}" for r in rows)
    except sqlite3.Error:  # pragma: no cover - a DB this broken fails below too
        return ""


def _cached(db: sqlite3.Connection, key: str, build) -> str:
    version = _corpus_version(db)
    hit = _cache.get(key)
    if hit is not None and hit[0] == version:
        _cache[key] = _cache.pop(key)  # keep it: most recently used goes last
        return hit[1]
    value = build()
    _cache[key] = (version, value)
    while len(_cache) > MAX_CACHED_SITEMAPS:
        _cache.pop(next(iter(_cache)))  # evict the least recently used
    return value


def _params(**extra) -> dict:
    """Query parameters shared by every section (the filters its SQL may or may
    not mention — SQLite binds only the placeholders that appear)."""
    return {"min_sentences": MIN_SENTENCES, **extra}


def _section_counts(db: sqlite3.Connection) -> dict[str, int]:
    """How many URLs each enabled section holds, so the index knows how many
    child sitemaps to advertise.

    A section whose table the loaded DB doesn't have yet (an enabled module whose
    derived tables post-date the file being served — §6C's portfolios are the
    first) counts zero rather than taking the whole sitemap down with it: the
    sitemap is the site's only route into the corpus for a crawler (SEO-1)."""
    counts = {}
    for s in _enabled_sections():
        try:
            counts[s.name] = db.execute(s.count_sql, _params()).fetchone()[0]
        except sqlite3.OperationalError:
            logger.warning("Sitemap section %r skipped: %s not in this DB yet",
                           s.name, s.module)
            counts[s.name] = 0
    return counts


def _pages(count: int) -> int:
    """Child sitemaps needed for `count` URLs — at least one, so a section that
    is momentarily empty still resolves instead of 404ing a listed sitemap."""
    return max(1, -(-count // URLS_PER_SITEMAP))


# --- routes -----------------------------------------------------------------

def register(app) -> None:
    """Register the crawler routes on `app`, BEFORE the SPA catch-all mount —
    otherwise the static-file handler answers `/robots.txt` with a 404 (they
    carry an extension, so they never fall through to the app shell)."""

    @app.get("/robots.txt", response_class=PlainTextResponse,
             include_in_schema=False)
    def robots(request: Request):
        base = _base_url(request)
        # Faceted browse URLs (`/votes?person=…&value=missed`, `/documents?sponsor=…`)
        # multiply combinatorially and duplicate a page that is already indexable
        # on its clean URL. Google was spending the crawl on them; keeping it out
        # leaves the budget for the corpus.
        facets = ("q", "sponsor", "person", "value", "faction", "status", "type",
                  "sort", "offset", "page", "from", "to")
        lines = [
            "# Parlamonitor — civic-tech mirror of the Hungarian National Assembly's",
            "# public proceedings. Source: parlament.hu.",
            "User-agent: *",
            "Allow: /",
            "",
            "# Filter/pagination permutations of the browse pages: same content as the",
            "# clean URL they canonicalise to, and effectively unbounded in number.",
        ]
        lines += [f"Disallow: /*?*{name}=" for name in facets]
        lines += [
            "",
            "# Not a page: the JSON API. (The chrome-free chart iframes under",
            "# /embed/ stay crawlable on purpose — they carry their own noindex,",
            "# which a crawler can only read if it is allowed to fetch them.)",
            "Disallow: /api/",
            "",
            f"Sitemap: {base}/sitemap.xml",
            "",
        ]
        return PlainTextResponse("\n".join(lines),
                                 headers={"Cache-Control": ROBOTS_CACHE_CONTROL})

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap_index(request: Request,
                      db: sqlite3.Connection = Depends(get_db)):
        """The sitemap index: the static entry points plus every child sitemap."""
        base = _base_url(request)

        def build() -> str:
            counts = _section_counts(db)
            out = ['<?xml version="1.0" encoding="UTF-8"?>',
                   '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
            for name in ["core"] + [s.name for s in _enabled_sections()]:
                for page in range(1, _pages(counts.get(name, 0)) + 1):
                    out.append("<sitemap><loc>"
                               + _loc(base, f"/sitemap-{name}-{page}.xml")
                               + "</loc></sitemap>")
            out.append("</sitemapindex>")
            return "\n".join(out)

        return _xml(_cached(db, f"index:{base}", build))

    @app.get("/sitemap-{name}-{page}.xml", include_in_schema=False)
    def sitemap_child(name: str, page: int, request: Request,
                      db: sqlite3.Connection = Depends(get_db)):
        """One child sitemap. An unknown section or an out-of-range page is a
        real 404 — a crawler must not be handed an empty urlset for a URL the
        index never advertised."""
        base = _base_url(request)
        if page < 1:
            return Response(status_code=404)

        if name == "core":
            if page != 1:
                return Response(status_code=404)
            rows = [(p, None) for p in _enabled_static()]
            return _xml(_urlset(base, rows))

        section = next((s for s in _enabled_sections() if s.name == name), None)
        if section is None:
            return Response(status_code=404)

        def build() -> str:
            try:
                rows = db.execute(section.sql, _params(
                    limit=URLS_PER_SITEMAP,
                    offset=(page - 1) * URLS_PER_SITEMAP)).fetchall()
            except sqlite3.OperationalError:
                # Same guard as the index (_section_counts): a section whose
                # table this DB doesn't carry yet is empty, not an error.
                rows = []
            return _urlset(base, [(r[0], r[1]) for r in rows])

        body = _cached(db, f"{name}:{page}:{base}", build)
        # A page past the end yields an empty urlset; 404 it so a stale index
        # (a crawler's cached copy after the corpus shrank) doesn't advertise
        # a page of nothing.
        if "<url>" not in body and page > 1:
            return Response(status_code=404)
        return _xml(body)
