"""Crawler-facing site plumbing: `robots.txt`, the XML sitemaps and `llms.txt`
(SEO-1…SEO-4, SEO-7).

The site is a client-rendered SPA over ~260 000 content pages (every speech,
sitting day, MP, iromány and vote). Nothing links to most of them from a
crawlable index — the browse pages paginate through an API — so **discovery has
to come from a sitemap**, and the crawl budget has to be spent on content pages
rather than on the filter permutations of the browse pages.

Three routes do that:

- ``/robots.txt`` — opens the content pages, closes the faceted/parameterised
  views (which all carry a ``<link rel="canonical">`` back to their clean URL
  anyway, see `og.py`) and points at the sitemap index.
- ``/sitemap.xml`` — a **sitemap index** over per-section child sitemaps
  (``/sitemap-<section>-<n>.xml``), each capped at ``URLS_PER_SITEMAP`` entries.
  Children are generated straight from SQLite on request and cached in-process
  against the DB file's identity, so a sync that swaps the DB invalidates them.
- ``/llms.txt`` — the same site, addressed to a language model rather than to a
  crawler (llmstxt.org): what this corpus *is*, the Hungarian vocabulary it is
  written in, what it does and does not cover, how to cite it, and where the
  machine-readable data actually lives (the API, not the HTML).

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

# The modules the Elemzések section (§4E) gathers its pages from — the backend's
# half of `frontend/src/modules/analyses/registry.js`. The section's landing page
# is there as long as any one of them is mounted.
ANALYSIS_MODULES = ("votes", "bills", "settlements", "interjections",
                    "proceedings")

# Static routes, with the module each belongs to (None = always present; a tuple
# = present while any one of them is). These are the entry points a crawler can
# actually reach the corpus through.
STATIC_PATHS: tuple[tuple[str, str | tuple[str, ...] | None], ...] = (
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
    ("/representatives/committees", "committees"),
    ("/bills", "bills"),
    # Kérdések (BILL-13) — the questions browse page, its own entry point into the
    # 90 000-odd question-type irományok the all-irományok list below buries.
    ("/bills/questions", "bills"),
    ("/documents", "bills"),
    ("/votes", "votes"),
    # Elemzések (§4E). The section index, and the analyses that stand as entry
    # points of their own. Települések is deliberately absent, exactly as it was
    # under its old address: its pages carry no card and stay off the index (see
    # `_ROUTE_CARDS` in og.py).
    ("/analyses", ANALYSIS_MODULES),
    ("/analyses/faction-cohesion", "votes"),
    ("/analyses/questions", "bills"),
    ("/analyses/interjections", "interjections"),
    # Témák (TOPIC-9) — the CAP topic mix of the floor and of the irományok. Its
    # data is the proceedings module's (the iromány half is an addition to the
    # picture, not a precondition for it), and like every other analysis it is
    # advertised on the strength of that module alone: a deployment that mounts
    # proceedings but never ran the classification pass serves the page's "not
    # classified" state, exactly as an unbuilt interjection table does.
    ("/analyses/topics", "proceedings"),
)


def static_enabled(module: str | tuple[str, ...] | None) -> bool:
    """Whether a `STATIC_PATHS` entry's page is mounted in this deployment
    (EXT-6). Shared with og.py, which builds the crawlable nav from the same
    list, so the sitemap and that nav can never disagree."""
    if module is None:
        return True
    if isinstance(module, tuple):
        return any(settings.module_enabled(m) for m in module)
    return settings.module_enabled(module)


@dataclass(frozen=True)
class _Section:
    """One paginated slice of the corpus.

    `sql` selects ``(path, lastmod)`` — an app-relative path and a W3C date (or
    NULL) — and takes ``:limit``/``:offset``; `count_sql` counts the same set.
    The two must agree on the WHERE clause, or pagination drifts.

    `pattern` and `blurb` are what `llms.txt` says about the slice: the shape of
    one of its addresses, and what a reader finds there. They live here rather
    than in a table of their own so a section can never be described to a model
    without also being in the sitemap — and so an unmounted module (EXT-6) drops
    out of both at once.
    """
    name: str
    module: str
    count_sql: str
    sql: str
    pattern: str
    blurb: str


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
    # Appended to an existing WHERE (the committee sections already filter on
    # `parent_id IS NULL`), so this is an AND rather than a WHERE of its own.
    committees_and = period_and(window, "period_number")
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
        "/sessions/{ulesnap_id}",
        "Egy ülésnap (sitting day): a nap teljes jegyzőkönyve felszólalásokra "
        "bontva, napirendi pontok, szó-felhő és beszédidő-toplista.",
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
        "/proceedings/{felszolalas_uid}",
        "Egy felszólalás (speech): a jegyzőkönyvi szöveg mondatonként, a "
        "felszólalóval, az ülésnappal és a hozzá tartozó videórészlettel.",
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
        "/representatives/{szemely_id}",
        "Egy felszólaló profilja: mandátumai, frakciói, felszólalásai, "
        "szavazatai, benyújtott irományai és beszédstatisztikái.",
      ),
      # Törvényjavaslatok live under /bills, every other iromány type under
      # /documents — the split `og.py` canonicalises a detail page to. It is a
      # split of the *detail* addresses only: the browse pages now number three
      # (törvényjavaslatok, kérdések, all irományok) and overlap by design, so
      # each iromány is still listed here exactly once, under its one canonical.
      _Section(
        "bills", "bills",
        f"SELECT COUNT(*) FROM bill WHERE main_type='T'{ses}",
        f"""SELECT '/bills/' || id AS path, submitted_date AS lastmod
             FROM bill WHERE main_type='T'{ses}
            ORDER BY submitted_date DESC, id
            LIMIT :limit OFFSET :offset""",
        "/bills/{iromany_id}",
        "Egy törvényjavaslat (bill): benyújtói, jogalkotási állomásai, "
        "kapcsolódó irományai, vitái és szavazásai.",
      ),
      _Section(
        "documents", "bills",
        "SELECT COUNT(*) FROM bill "
        f"WHERE (main_type IS NULL OR main_type<>'T'){ses}",
        f"""SELECT '/documents/' || id AS path, submitted_date AS lastmod
             FROM bill WHERE (main_type IS NULL OR main_type<>'T'){ses}
            ORDER BY submitted_date DESC, id
            LIMIT :limit OFFSET :offset""",
        "/documents/{iromany_id}",
        "Minden más iromány (parliamentary document): határozati javaslat, "
        "kérdés, interpelláció, beszámoló, tájékoztató és a többi típus.",
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
        "/representatives/portfolios/{tarca_slug}",
        "Egy tárca (ministry): kik vezették, milyen kérdéseket kapott és mit "
        "nyújtott be a Parlament elé.",
      ),
      # The committees (§6F). Like the tárcák: a couple of hundred pages, each a
      # standing entry point into one body's whole record — the page a search
      # for "Mentelmi Bizottság tagjai" should be able to land on. Only the main
      # committees are advertised: a subcommittee's page is reachable from its
      # parent and carries three names and a handful of meetings, which is not
      # an entry point into anything.
      _Section(
        "committees", "committees",
        f"SELECT COUNT(*) FROM committee WHERE parent_id IS NULL{committees_and}",
        f"""SELECT '/representatives/committees/' || id AS path,
                   NULL AS lastmod
             FROM committee WHERE parent_id IS NULL{committees_and}
            ORDER BY period_number DESC, ord, id
            LIMIT :limit OFFSET :offset""",
        "/representatives/committees/{bizottsag_id}",
        "Egy bizottság (committee): tagjai és tisztségviselői, ülései a "
        "jegyzőkönyvekkel, és az általa tárgyalt és benyújtott irományok.",
      ),
      # The committee minutes (BIZ-15) — by some distance the most substantial
      # pages the site has. Each is a full sitting: the agenda, who was in the
      # room, and every word said. Committee debate is published nowhere else in
      # a readable form, so these are pages a search for what a committee
      # actually said about a bill has nothing else to land on.
      #
      # Only sittings we could actually read are listed. A meeting whose PDF
      # 404'd or came back as a scan keeps its row in the API (the page says so)
      # but has nothing on it worth crawling, and advertising it would be
      # offering a search engine an empty page.
      _Section(
        "committee-minutes", "committees",
        f"""SELECT COUNT(*) FROM committee_minutes
             WHERE error IS NULL AND speeches > 0{committees_and}""",
        f"""SELECT '/representatives/committees/meetings/' || meeting_id AS path,
                   held_on AS lastmod
             FROM committee_minutes
            WHERE error IS NULL AND speeches > 0{committees_and}
            ORDER BY held_on DESC, meeting_id
            LIMIT :limit OFFSET :offset""",
        "/representatives/committees/meetings/{ules_id}",
        "Egy bizottsági ülés jegyzőkönyve: a napirend, a résztvevők és az "
        "ülésen elhangzott összes felszólalás.",
      ),
      _Section(
        "votes", "votes",
        f"SELECT COUNT(*) FROM vote{votes_where}",
        f"""SELECT '/votes/' || id AS path, substr(vote_datetime, 1, 10) AS lastmod
             FROM vote{votes_where}
            ORDER BY vote_datetime DESC, id
            LIMIT :limit OFFSET :offset""",
        "/votes/{szavazas_id}",
        "Egy név szerinti szavazás (roll-call vote): tárgya, eredménye és "
        "minden képviselő leadott szavazata.",
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
    return [p for p, mod in STATIC_PATHS if static_enabled(mod)]


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


# --- llms.txt ---------------------------------------------------------------

# llms.txt (llmstxt.org) is the site described for a language model: one markdown
# file, at a fixed address, saying what this is, what it covers and where the
# machine-readable data lives — so an assistant answering a question about the
# Hungarian Parliament can start from the corpus instead of from a rendered SPA
# page it has to reverse-engineer.
#
# It restates the site rather than describing it a second time: the page titles
# and blurbs come from og.py's card table (the same text the <title> and the
# crawlable nav carry), the URL shapes and their descriptions from the sitemap
# sections above, and the figures from the home page's own counter. Nothing here
# is a fact about the corpus that only this file knows — a model quoting a number
# the site itself does not show is the one failure mode this file can cause.


def _hu_int(n: int) -> str:
    """A figure the way the site writes it: 2 582, not 2,582."""
    return f"{n:,}".replace(",", "\u00a0")


def _llms_coverage(db: sqlite3.Connection) -> list[str]:
    """What this deployment actually holds, in figures.

    From the DB rather than from a constant, because both halves are deployment
    config: which cycles are served is CYC-7, and how much of them is loaded
    changes with every sync. A model told that the corpus stops in 2022 when it
    stops today will answer confidently and wrongly, so the one number this file
    must never carry is a stale one — and the counts come from the same memoized
    counter the home page reads, so the two can never disagree.

    A DB too old to answer (a section's table missing, as the sitemap guards for
    above) simply contributes no coverage line: llms.txt must still serve."""
    from .main import _corpus_counts  # deferred: main imports this module

    try:
        periods = db.execute(
            "SELECT number, date_start, date_end FROM electoral_period "
            "WHERE date_start IS NOT NULL"
            + period_and(settings.site_periods, "number")
            + " ORDER BY number").fetchall()
        counts = _corpus_counts(db)
    except sqlite3.Error:  # pragma: no cover - a DB this broken fails elsewhere
        logger.warning("llms.txt: no coverage figures available", exc_info=True)
        return []
    if not periods:
        return []

    first, last = periods[0], periods[-1]
    span = (f"{first['number']}–{last['number']}. ciklus" if len(periods) > 1
            else f"{first['number']}. ciklus")
    years = (f"{first['date_start'][:4]}–{last['date_end'][:4]}"
             if last["date_end"] else
             f"{first['date_start'][:4]}-től napjainkig")
    return [
        f"Lefedettség / Coverage: {span} ({years}) — "
        f"{_hu_int(counts['sessions'])} ülésnap, "
        f"{_hu_int(counts['speeches'])} felszólalás, "
        f"{_hu_int(counts['sentences'])} mondat, "
        f"{_hu_int(counts['representatives'])} képviselő.",
    ]


def _llms_updated(db: sqlite3.Connection) -> list[str]:
    """When the corpus was last refreshed, as a date. The site syncs daily, so
    "today" is a wrong answer about yesterday's sitting either way — but a model
    can only say how fresh this is if the file tells it."""
    try:
        row = db.execute("SELECT value FROM build_meta "
                         "WHERE key='data_updated_at'").fetchone()
    except sqlite3.Error:  # pragma: no cover - see _llms_coverage
        return []
    if not row or not row[0]:
        return []
    return [f"Az adatok utolsó frissítése / Data last refreshed: {row[0][:10]}."]


def _llms_browse(base: str) -> list[str]:
    """The browse pages as link lines, in the site's own words.

    Titles and descriptions come from og.py's card table, so this file, the
    page's `<title>` and the crawlable nav say the same thing about a page; the
    list itself is STATIC_PATHS, so an unmounted module (EXT-6) drops out of all
    three at once."""
    from .og import _DEFAULT_DESC, _ROUTE_CARDS  # deferred: og imports us too

    lines = []
    for path, module in STATIC_PATHS:
        if not static_enabled(module):
            continue
        title, desc = _ROUTE_CARDS.get(path, ("Nyitólap", _DEFAULT_DESC))
        lines.append(f"- [{title}]({base}{path}): {desc}")
    return lines


def _llms_patterns() -> list[str]:
    """The shape of a detail URL in each mounted section — what a model needs to
    build a link to a specific speech or vote, rather than guessing one."""
    return [f"- `{s.pattern}` — {s.blurb}" for s in _enabled_sections()]


def _llms_txt(base: str, db: sqlite3.Connection) -> str:
    """The whole document (llmstxt.org layout: H1, a blockquote summary, free
    prose, then H2 sections of links)."""
    from .main import TIMING_DISCLAIMER  # deferred: main imports this module

    out = [
        "# Parlamonitor",
        "",
        "> A Magyar Országgyűlés nyilvános jegyzőkönyveinek, képviselőinek,",
        "> irományainak és szavazásainak kereshető, civil-tech tükre. /",
        "> A searchable civic-tech mirror of the Hungarian National Assembly's",
        "> public proceedings, representatives, documents and votes.",
        "",
        "A Parlamonitor a K-Monitor projektje. **Nem hivatalos oldal**: minden",
        "adat forrása a parlament.hu, amit a Parlamonitor újraközöl, mondatszinten",
        "kereshetővé tesz, videóval összekapcsol és származtatott statisztikákkal",
        "egészít ki. Eltérés esetén az Országgyűlés saját közlése az irányadó. /",
        "Not an official site: all data originates from parlament.hu, and where",
        "the two differ, the Assembly's own publication governs.",
        "",
    ]
    out += _llms_coverage(db)
    out += _llms_updated(db)
    out += [
        "",
        "Amire egy idézetnél figyelni kell / What to watch when citing:",
        "",
        "- A jegyzőkönyvek szövege az Országgyűlésé. A videóidőzítés, a "
        "szó-felhők, a beszédmetrikák, a témacímkék és minden összesítés a "
        "Parlamonitor feldolgozása — ezeket a Parlamonitornak kell "
        "tulajdonítani, nem a Parlamentnek.",
        f"- {TIMING_DISCLAIMER} Idézésnél a jegyzőkönyvi szöveg az irányadó, "
        "nem a videó másodperce.",
        "- Az oldal magyar nyelvű korpuszt közöl; a felület magyarul és angolul "
        "olvasható, de a felszólalások szövege mindig magyar.",
        "- A képviselőkről szóló oldalak életrajzi jellegűek, a listák és "
        "statisztikák viszont mindig egy választási ciklusra vonatkoznak: egy "
        "szám csak a ciklusával együtt jelent valamit.",
        "",
        "Fogalmak / Key terms: *ülésnap* = sitting day; *felszólalás* = speech;",
        "*iromány* = any document submitted to the Assembly (bill, resolution,",
        "question, report); *törvényjavaslat* = bill; *kérdés*, *interpelláció* =",
        "parliamentary question; *frakció* = parliamentary group; *ciklus* =",
        "electoral term; *szószóló* = nationality advocate; *tárca* = ministry;",
        "*napirend* = order paper; *közbeszólás* = interjection from the floor.",
        "",
        "## Böngészés / Browse",
        "",
    ]
    out += _llms_browse(base)
    out += [
        "",
        "## Oldalcímek / URL patterns",
        "",
    ]
    out += _llms_patterns()
    out += [
        "",
        "## Gépi hozzáférés / Machine-readable access",
        "",
        f"- [API dokumentáció / API docs]({base}/api/docs): a nyilvános, csak "
        "olvasható JSON API (`/api/v1/…`) — felszólalások, képviselők, irományok "
        "és szavazások ugyanazokkal a szűrőkkel, amelyekkel az oldal maga "
        "dolgozik. Egy adat kinyeréséhez ezt érdemes hívni, nem a HTML-t "
        "értelmezni.",
        f"- [OpenAPI séma / OpenAPI schema]({base}/api/openapi.json): minden "
        "végpont és paramétere gépi formában.",
        f"- [Metaadatok / Site metadata]({base}/api/v1/meta): a betöltött "
        "ciklusok, a korpusz méretei, a származtatott adatok módszertana és a "
        "forrásmegjelölés — érdemes ezzel kezdeni.",
        f"- [Sitemap index]({base}/sitemap.xml): minden tartalmi oldal címe.",
        f"- [robots.txt]({base}/robots.txt): a keresőknek szóló szabályok. Az "
        "`/api/` útvonalat elzárja a *keresőktől* (nem oldal, nem indexelendő) — "
        "az API maga nyilvános, és ez a fájl éppen erre irányít.",
        "",
        "## Optional",
        "",
        f"- [A projektről / About]({base}/about): mi a Parlamonitor, honnan "
        "származnak az adatai, hogyan készül a jegyzőkönyv–videó "
        "összekapcsolás, és mi használható fel szabadon.",
        "- [Országgyűlés / National Assembly](https://www.parlament.hu): az "
        "elsődleges forrás — minden itt közölt adat innen származik.",
        "- [K-Monitor](https://k-monitor.hu): az oldalt fejlesztő és üzemeltető "
        "antikorrupciós szervezet.",
        "- [Forráskód / Source code](https://github.com/k-monitor/parlament): a "
        "teljes feldolgozási lánc, AGPL-3.0 alatt.",
        "- [Figyusz](https://figyusz.k-monitor.hu/): a K-Monitor értesítője az "
        "országgyűlési irományok tematikus követésére.",
        "",
    ]
    return "\n".join(out)


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

    @app.get("/llms.txt", response_class=PlainTextResponse,
             include_in_schema=False)
    def llms_txt(request: Request,
                 db: sqlite3.Connection = Depends(get_db)):
        """The site, described for a language model (SEO-7).

        Markdown, but served as text/plain under a .txt address — the same
        content type robots.txt gets, and the one every fetcher renders rather
        than offers to download.

        Cached against the corpus version like the sitemaps: the figures in it
        are a pure function of the DB, and it is otherwise a couple of counts
        per request."""
        base = _base_url(request)
        return PlainTextResponse(
            _cached(db, f"llms:{base}", lambda: _llms_txt(base, db)),
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
