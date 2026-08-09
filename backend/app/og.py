"""Server-side page metadata: OpenGraph/Twitter cards, canonicals, structured
data and a crawlable text rendering of each page (SEO-2/SEO-5, VIE-5).

The SPA ships a single static `index.html` whose `<head>` carries only generic,
site-wide OpenGraph tags, so every shared deep link — a speech, a *specific
sentence*, an MP profile, a sitting day — previews identically as a bare
"Parlamonitor". Social/chat crawlers (Facebook, Twitter/X, Slack, Signal,
Telegram, …) do **not** run the SPA's JavaScript, so they never see the
per-page content the app would render. Search crawlers do run it, but only on a
deferred second pass they will never spend on a 260 000-page archive.

This module renders the **same app shell** with, per URL:

- OpenGraph/Twitter tags, a unique `<title>` + description, and a `<link
  rel="canonical">` that points at the page's ONE preferred address;
- JSON-LD structured data (`Person`, `Article`, `Legislation`, breadcrumbs) so
  the entities on the page are machine-readable;
- a plain-HTML rendering of the page's own content inside `#app` — the speech's
  text, the sitting's speech list, the MP's details — which Vue replaces the
  moment it mounts, but which a crawler (and a reader with no JS) can read.

Real browsers still boot the unchanged SPA over this shell.

It is deliberately best-effort: missing data or an error falls back to the plain
shell (`plain()`), never a JSON 404, so a crawler (or a user) always gets a
working app page. An id that does not resolve answers 404 *with* that shell, so
the SPA still renders while crawlers are told the truth rather than being fed a
soft 404.
"""

from __future__ import annotations

import html
import json
import os
import re
import sqlite3

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse

from .config import settings
from .db import get_db

# --- app-shell loading ------------------------------------------------------

_shell_cache: tuple[tuple, str] | None = None  # (file ident, html)

# Tags we replace/inject so we never emit duplicates of the shell's own
# generic <title> / description.
_TITLE_RE = re.compile(r"<title>.*?</title>", re.IGNORECASE | re.DOTALL)
_DESC_RE = re.compile(
    r"""<meta\s+name=["']description["'][^>]*>""", re.IGNORECASE)


def _load_shell() -> str | None:
    """Read the built `index.html` and cache it against the file's identity
    (inode/mtime), so an in-place frontend redeploy without a backend restart
    is picked up — a process-lifetime cache would keep referencing the old
    content-hashed asset bundles, blanking every shared deep link. Returns
    None when no SPA is being served (dev / API-only deployment), which is
    the signal to skip registering the share-card routes entirely."""
    global _shell_cache
    dist = settings.frontend_dist
    if not dist:
        return None
    path = os.path.join(dist, "index.html")
    try:
        st = os.stat(path)
        ident = (st.st_dev, st.st_ino, st.st_mtime_ns)
        if _shell_cache is not None and _shell_cache[0] == ident:
            return _shell_cache[1]
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        _shell_cache = (ident, content)
        return content
    except OSError:
        # Mid-deploy the file may be briefly absent — serve the last good copy.
        return _shell_cache[1] if _shell_cache is not None else None


# --- helpers ----------------------------------------------------------------

def _esc(value: str) -> str:
    """Escape for an HTML attribute value (quotes included)."""
    return html.escape(value or "", quote=True)


def _base_url(request: Request) -> str:
    """The outward-facing origin used to make og:url / og:image absolute. The
    configured canonical URL wins (correct behind a proxy); otherwise the
    request's own base URL."""
    if settings.site_url:
        return settings.site_url
    return str(request.base_url).rstrip("/")


def _abs(request: Request, path_or_url: str | None) -> str | None:
    if not path_or_url:
        return None
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url
    return _base_url(request) + "/" + path_or_url.lstrip("/")


def _truncate(text: str, limit: int = 300) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _ucfirst(text: str) -> str:
    """Upper-case only the first character (unlike str.capitalize(), which also
    lower-cases the rest — wrong for multi-word types or month names)."""
    return text[:1].upper() + text[1:] if text else text


# The transcript's opening sentence is prefixed with the speaker label in caps —
# "BALLA GYÖRGY (Fidesz): …" — which is redundant with the speaker we already
# show on the card. Strip it, mirroring the frontend's format.js::stripSpeakerLabel.
_LABEL_RE = re.compile(
    r"^\s*(?:ELNÖK|[^\s:]+(?:\s+[^\s:]+)*?)\s*(?:\([^)]*\))?\s*:\s*")


def _strip_speaker_label(text: str) -> str:
    stop = text.find(":")
    if stop < 0 or stop > 140:
        return text
    head = text[:stop].strip()
    tokens = head.split()
    def is_caps(tok: str) -> bool:
        letters = [c for c in tok if c.isalpha()]
        return bool(letters) and all(c.upper() == c for c in letters)
    is_label = (
        (len(tokens) == 1 and tokens[0].upper().startswith("ELNÖK"))
        or (len(tokens) >= 2 and is_caps(tokens[0]) and is_caps(tokens[1])))
    return text[stop + 1:].lstrip() if is_label else text


# --- rendering --------------------------------------------------------------

# The site-wide default share card — used for the home page and any client route
# without a more specific card (see plain()). The default image is a landscape
# 1200×630 card art served from the frontend's static root (frontend/public/).
DEFAULT_OG_IMAGE = "/og-image.png"
_DEFAULT_TITLE = "Parlamonitor"
_DEFAULT_DESC = (
    "Parlamonitor — a Magyar Országgyűlés nyilvános jegyzőkönyvei mondatszinten "
    "kereshetően és videóval szinkronizálva. Civil-tech projekt.")


def render(request: Request, *, title: str, description: str,
           image: str | None = None, url_path: str | None = None,
           og_type: str = "website", card: str = "summary_large_image",
           extra: list[tuple[str, str]] | None = None,
           jsonld: list[dict] | None = None, body: str | None = None,
           noindex: bool = False, status_code: int = 200) -> HTMLResponse:
    """Render the app shell with per-page metadata injected.

    `title`/`description` are the human-readable card text; `image`/`url_path`
    are made absolute against the canonical base URL; `extra` is optional
    property→content pairs (e.g. article:author) appended verbatim; `jsonld`
    is emitted as schema.org structured data; `body` is a crawlable HTML
    rendering of the page dropped into `#app` (Vue clears it on mount, so it is
    what a crawler and a JS-less reader see, and nothing more); `noindex` keeps
    a page that is not a real destination out of the index."""
    shell = _load_shell()
    if shell is None:  # pragma: no cover - guarded by the caller
        raise RuntimeError("no app shell")

    full_title = title if title.endswith("Parlamonitor") else f"{title} · Parlamonitor"
    abs_url = _abs(request, url_path) if url_path else _base_url(request)
    abs_image = _abs(request, image) or _abs(request, DEFAULT_OG_IMAGE)

    tags = [
        f'<title>{_esc(full_title)}</title>',
        f'<meta name="description" content="{_esc(description)}" />',
        f'<meta property="og:site_name" content="Parlamonitor" />',
        f'<meta property="og:type" content="{_esc(og_type)}" />',
        f'<meta property="og:title" content="{_esc(full_title)}" />',
        f'<meta property="og:description" content="{_esc(description)}" />',
        f'<meta property="og:url" content="{_esc(abs_url)}" />',
        f'<meta property="og:locale" content="hu_HU" />',
        f'<meta name="twitter:card" content="{_esc(card)}" />',
        f'<meta name="twitter:title" content="{_esc(full_title)}" />',
        f'<meta name="twitter:description" content="{_esc(description)}" />',
        f'<link rel="canonical" href="{_esc(abs_url)}" />',
    ]
    if noindex:
        # Kept out of the index but still crawlable, so the links on it are
        # followed and the canonical it points at is still discovered.
        tags.append('<meta name="robots" content="noindex, follow" />')
    if abs_image:
        tags.append(f'<meta property="og:image" content="{_esc(abs_image)}" />')
        tags.append(f'<meta name="twitter:image" content="{_esc(abs_image)}" />')
    for prop, content in (extra or []):
        attr = "name" if prop.startswith(("twitter:", "description")) else "property"
        tags.append(f'<meta {attr}="{_esc(prop)}" content="{_esc(content)}" />')
    for block_json in (jsonld or []):
        tags.append('<script type="application/ld+json">'
                    + _jsonld(block_json) + '</script>')

    block = "\n    " + "\n    ".join(tags)
    # Drop the shell's generic title + description so we don't duplicate them,
    # then inject our block just before </head>.
    out = _TITLE_RE.sub("", shell, count=1)
    out = _DESC_RE.sub("", out, count=1)
    out = out.replace("</head>", block + "\n  </head>", 1)
    if body:
        out = out.replace('<div id="app"></div>',
                          f'<div id="app">{body}</div>', 1)
    return HTMLResponse(out, status_code=status_code)


def _jsonld(data: dict) -> str:
    """Serialise a JSON-LD block for inline embedding. `<` is escaped so no
    value can close the surrounding <script> element."""
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


# The browse/index routes. Each gets its own title + description: a site whose
# every page is titled "Parlamonitor" gives Google nothing to tell them apart
# and nothing to rank ("Duplicate, Google chose a different canonical").
_ROUTE_CARDS: dict[str, tuple[str, str]] = {
    "/about": (
        "A projektről",
        "Mi a Parlamonitor, honnan származnak az adatai, hogyan készül a "
        "jegyzőkönyv–videó szinkronizálás, és hogyan használható fel szabadon."),
    "/search": (
        "Keresés a parlamenti jegyzőkönyvekben",
        "Keress mondatszinten a Magyar Országgyűlés teljes nyilvános "
        "jegyzőkönyvében — képviselő, frakció, ülésnap és ciklus szerint "
        "szűrve, a találatot videóval együtt megnyitva."),
    "/sessions": (
        "Ülésnapok",
        "A Magyar Országgyűlés ülésnapjai: jegyzőkönyv, felszólalások, "
        "szó-felhő és a legtöbbet beszélő képviselők ülésnaponként."),
    "/representatives": (
        "Képviselők",
        "A Magyar Országgyűlés képviselői: felszólalások, szavazatok, "
        "benyújtott irományok és beszédstatisztikák képviselőnként."),
    "/representatives/factions": (
        "Frakciók",
        "A parlamenti frakciók összehasonlítása: létszám, felszólalások, "
        "beszédidő és szavazási aktivitás ciklusonként."),
    "/representatives/advocates": (
        "Nemzetiségi szószólók",
        "A nemzetiségi szószólók névsora és parlamenti tevékenysége — "
        "felszólalásaik és irományaik a Parlamonitoron."),
    "/representatives/speakers": (
        "Egyéb felszólalók",
        "Akik képviselői mandátum nélkül szólaltak fel a Házban: miniszterek, "
        "államtitkárok, meghívott vendégek felszólalásai."),
    "/representatives/officials": (
        "Tisztségviselők",
        "Az Országgyűlés tisztségviselői ciklusról ciklusra: házelnökök, "
        "alelnökök, jegyzők és háznagyok."),
    "/representatives/lookup": (
        "Ki a képviselőm?",
        "Keresd meg a saját választókerületedet és az ott megválasztott "
        "országgyűlési képviselőt."),
    "/bills": (
        "Törvényjavaslatok",
        "A benyújtott törvényjavaslatok: benyújtók, státusz, jogalkotási "
        "állomások és a kapcsolódó felszólalások."),
    "/documents": (
        "Egyéb irományok",
        "Kérdések, interpellációk, határozati javaslatok és minden további "
        "iromány típus — benyújtók és állapotuk szerint böngészve."),
    "/questions": (
        "Kérdések és interpellációk",
        "Ki kérdez és ki válaszol az Országgyűlésben: a kérdések és "
        "interpellációk útja a kérdezőtől a válaszadó tárcáig."),
    "/votes": (
        "Szavazások",
        "Az Országgyűlés név szerinti szavazásai: eredmények, frakciók "
        "szerinti megoszlás és képviselőnkénti szavazatok."),
    "/votes/cohesion": (
        "Frakcióelemzés",
        "Mennyire szavaznak együtt a frakciók: együttszavazási arányok és "
        "frakciófegyelem cikluson belül."),
}

# Route shapes the site actually serves. Anything else resolves to the SPA's
# 404 view, which must not be indexed as a real page (a 200-with-"not found"
# body is the "Soft 404" Search Console reports).
_KNOWN_PREFIXES = ("/proceedings/", "/representatives/", "/sessions/",
                   "/bills/", "/documents/", "/votes/", "/embed/")


def _is_known_path(path: str) -> bool:
    return (path in ("", "/") or path in _ROUTE_CARDS
            or path.startswith(_KNOWN_PREFIXES))


def _should_index(path: str) -> bool:
    """Whether this path is a destination worth indexing. Embeds are the chart
    *inside* someone else's page (§4C), not a page of their own — they stay
    crawlable (so robots.txt doesn't have to block them, which would leave
    Google unable to read this very tag) but out of the index."""
    return _is_known_path(path) and not path.startswith("/embed/")


def _site_jsonld(request: Request) -> list[dict]:
    """Site-level structured data for the home page: who publishes this and how
    its search works."""
    base = _base_url(request)
    return [{
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "Parlamonitor",
        "url": base + "/",
        "inLanguage": "hu",
        "description": _DEFAULT_DESC,
        "publisher": {
            "@type": "Organization",
            "name": "K-Monitor",
            "url": "https://k-monitor.hu",
        },
        "potentialAction": {
            "@type": "SearchAction",
            "target": {"@type": "EntryPoint",
                       "urlTemplate": base + "/search?q={search_term_string}"},
            "query-input": "required name=search_term_string",
        },
    }]


def _breadcrumbs(request: Request, trail: list[tuple[str, str]]) -> dict:
    """A BreadcrumbList for `trail` (label, path pairs, root first) — the one
    piece of structured data with a visible effect in the result itself."""
    base = _base_url(request)
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": label,
             "item": base + path}
            for i, (label, path) in enumerate(trail, start=1)],
    }


def plain(request: Request, *, status_code: int = 200,
          noindex: bool | None = None) -> HTMLResponse:
    """The app shell carrying a card for a route with no per-entity data.

    This serves the home page and every browse/index route (routed here by the
    SPA mount) from `_ROUTE_CARDS`, and is also the fallback for missing data or
    an error. `og:url`/canonical follow the request path *without* its query, so
    the filtered/paginated variants of a browse page consolidate onto it.

    `noindex` defaults to "whatever this path is": a URL matching no route the
    site serves renders the SPA's 404 view and is marked accordingly."""
    path = request.url.path or "/"
    route = path.rstrip("/") or "/"
    title, description = _ROUTE_CARDS.get(route, (_DEFAULT_TITLE, _DEFAULT_DESC))
    if noindex is None:
        noindex = not _should_index(route)
    return render(request, title=title, description=description,
                  url_path=path, og_type="website",
                  jsonld=_site_jsonld(request) if path == "/" else None,
                  # A browse page's rows come from the API, so there is nothing
                  # to render server-side — but the site's own navigation is
                  # exactly what a first-pass crawler needs, and the shell's is
                  # inside the app. A page we are asking not to index gets none.
                  body=None if noindex else _browse_body(title, description),
                  noindex=noindex, status_code=status_code)


# --- crawlable page bodies --------------------------------------------------
#
# What goes inside `#app` until Vue mounts and clears it. This is the page's own
# content in plain HTML: it is what a crawler reads on the first pass (the one
# that decides whether 260 000 URLs are worth indexing) and what a reader with no
# JS gets instead of a blank page. It is deliberately the SAME content the SPA
# renders — never a crawler-only variant.

def _wrap(inner: str) -> str:
    """The container for a page's crawlable rendering. Constrained to a readable
    column so the moment before Vue mounts looks like a page, not raw markup."""
    return ('<article style="max-width:44rem;margin:0 auto;padding:2rem 1rem;'
            'line-height:1.6">' + inner + "</article>")


def _paragraphs(rows) -> str:
    """Sentence rows → `<p>` per source paragraph, so the transcript keeps the
    shape it has in the record."""
    out, current, para = [], [], None
    for row in rows:
        if current and row["paragraph"] != para:
            out.append("<p>" + _esc(" ".join(current)) + "</p>")
            current = []
        para = row["paragraph"]
        current.append(row["text"])
    if current:
        out.append("<p>" + _esc(" ".join(current)) + "</p>")
    return "".join(out)


def _link(path: str, label: str) -> str:
    return f'<a href="{_esc(path)}">{_esc(label)}</a>'


def _browse_body(title: str, description: str) -> str:
    """A browse page's crawlable rendering: what the page is, plus the site's
    navigation. The nav follows the sitemap's own list of static routes, so a
    module that isn't mounted (EXT-6) disappears from both at once."""
    from .seo import STATIC_PATHS
    items = [
        "<li>" + _link(path, _ROUTE_CARDS[path][0]) + "</li>"
        for path, module in STATIC_PATHS
        if path in _ROUTE_CARDS
        and (module is None or settings.module_enabled(module))]
    nav = "<nav><ul>" + "".join(items) + "</ul></nav>" if items else ""
    return _wrap(f"<h1>{_esc(title)}</h1><p>{_esc(description)}</p>{nav}")


def _speech_body(db: sqlite3.Connection, uid: str, *, speaker: str,
                 faction: str | None, date_hu: str, session_id: str,
                 quote: str) -> str:
    """The speech itself — the single most valuable text on the site."""
    rows = db.execute(
        "SELECT text, paragraph FROM sentence WHERE speech_id = ? ORDER BY ord",
        (uid,)).fetchall()
    head = _esc(speaker) + (f" ({_esc(faction)})" if faction else "")
    meta = _link(f"/sessions/{session_id}", date_hu or "ülésnap")
    # A video-only speech (VIE-8) has no transcript to render — say so rather
    # than leaving an empty paragraph behind.
    text = _paragraphs(rows) or (
        f"<p>{_esc(quote)}</p>" if quote else
        "<p>Ehhez a felszólaláshoz még nem érhető el jegyzőkönyvi szöveg, "
        "csak a videófelvétel.</p>")
    return _wrap(f"<h1>{head}</h1><p>{meta}</p>{text}")


def _profile_body(db: sqlite3.Connection, person_id: str, p) -> str:
    """The MP, plus links into their most recent speeches — which is also how a
    crawler walks from a profile into the corpus."""
    facts = []
    if p["faction_label"]:
        facts.append("Frakció: " + _esc(p["faction_label"]))
    if p["constituency"]:
        facts.append("Választókerület: " + _esc(p["constituency"]))
    # Ordered and cut down to 20 BEFORE joining the sitting for its date: an
    # MP with 28 000 speeches otherwise costs a 28 000-row join and sort on
    # every page load (367 ms → 22 ms). `session_id` is "<period><sitting>",
    # so ordering by it is chronological without touching `session`.
    recent = db.execute(
        """SELECT t.uid, ss.date,
                  (SELECT text FROM sentence WHERE speech_id = t.uid
                    ORDER BY ord LIMIT 1) AS opening
             FROM (SELECT uid, session_id FROM speech WHERE person_id = ?
                    ORDER BY session_id DESC, speech_index DESC LIMIT 20) t
             JOIN session ss ON ss.id = t.session_id""",
        (person_id,)).fetchall()
    items = "".join(
        "<li>" + _link(f"/proceedings/{r['uid']}",
                       f"{_hu_date(r['date'])} – "
                       f"{_truncate(_strip_speaker_label(r['opening'] or ''), 120)}")
        + "</li>" for r in recent)
    body = f"<h1>{_esc(p['label'])}</h1>"
    if facts:
        body += "<p>" + " · ".join(facts) + "</p>"
    if items:
        body += "<h2>Legutóbbi felszólalások</h2><ul>" + items + "</ul>"
    return _wrap(body)


def _session_body(db: sqlite3.Connection, session_id: str, date_hu: str) -> str:
    """The sitting day's running order — and the crawl path to its speeches."""
    rows = db.execute(
        """SELECT sp.uid, COALESCE(p.label, sp.speaker_label) AS speaker,
                  (SELECT text FROM sentence WHERE speech_id = sp.uid
                    ORDER BY ord LIMIT 1) AS opening
             FROM speech sp
             LEFT JOIN person p ON p.person_id = sp.person_id
            WHERE sp.session_id = ?
            ORDER BY sp.speech_index LIMIT 300""", (session_id,)).fetchall()
    items = "".join(
        "<li>" + _link(f"/proceedings/{r['uid']}", r["speaker"] or "Felszólalás")
        + (": " + _esc(_truncate(_strip_speaker_label(r["opening"] or ""), 140))
           if r["opening"] else "")
        + "</li>" for r in rows)
    return _wrap(f"<h1>Országgyűlési ülésnap – {_esc(date_hu)}</h1>"
                 + ("<ul>" + items + "</ul>" if items else ""))


def _bill_body(b, sponsors: list[tuple[str, str | None]], title: str) -> str:
    """The iromány's identifying facts, with its sponsors linked."""
    facts = []
    if b["type"]:
        facts.append("Típus: " + _esc(b["type"]))
    if b["status"]:
        facts.append("Státusz: " + _esc(b["status"]))
    date_hu = _hu_date(b["submitted_date"])
    if date_hu:
        facts.append("Benyújtva: " + _esc(date_hu))
    body = f"<h1>{_esc(title)}</h1>"
    if facts:
        body += "<p>" + " · ".join(facts) + "</p>"
    if sponsors:
        body += "<h2>Benyújtók</h2><ul>" + "".join(
            "<li>" + (_link(f"/representatives/{pid}", name) if pid
                      else _esc(name)) + "</li>"
            for name, pid in sponsors) + "</ul>"
    return _wrap(body)


def _vote_body(v, subjects: list, title: str) -> str:
    """The vote's result and tally, with the irományok it decided linked."""
    counts = " · ".join(
        f"{label}: {v[col]}" for label, col in
        (("Igen", "yes"), ("Nem", "no"), ("Tartózkodás", "abstain"))
        if v[col] is not None)
    body = f"<h1>{_esc(title)}</h1>"
    facts = [x for x in (v["result"], _hu_date((v["vote_datetime"] or "")[:10]),
                         v["voting_mode"]) if x]
    if facts:
        body += "<p>" + " · ".join(_esc(x) for x in facts) + "</p>"
    if counts:
        body += f"<p>{_esc(counts)}</p>"
    if subjects:
        body += "<h2>A szavazás tárgya</h2><ul>" + "".join(
            "<li>" + (_link(f"/bills/{s['bill_id']}", s["label"]) if s["bill_id"]
                      else _esc(s["label"])) + "</li>"
            for s in subjects) + "</ul>"
    return _wrap(body)


# --- Hungarian date formatting (matches the frontend's locale) --------------

_HU_MONTHS = ("", "január", "február", "március", "április", "május", "június",
              "július", "augusztus", "szeptember", "október", "november",
              "december")


def _hu_date(iso: str | None) -> str:
    """`2026-06-18` → `2026. június 18.` (best-effort; passes through on a
    non-ISO value)."""
    if not iso:
        return ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", iso)
    if not m:
        return iso
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if 1 <= mo <= 12:
        return f"{y}. {_HU_MONTHS[mo]} {d}."
    return iso


# --- route registration -----------------------------------------------------

def _missing(request: Request) -> HTMLResponse:
    """A deep link whose id resolves to nothing.

    The SPA's static sub-routes share these URL shapes — `/representatives/
    factions`, `/votes/cohesion` — and *are* real pages, so they answer 200 with
    their own card. Anything else is a genuine miss and answers **404 with the
    app shell**: the SPA still boots and renders its 404 view, while a crawler
    is told the truth instead of being handed a soft 404 to file under "Crawled
    – currently not indexed"."""
    path = (request.url.path or "/").rstrip("/") or "/"
    if path in _ROUTE_CARDS:
        return plain(request)
    return plain(request, status_code=404, noindex=True)


def register(app) -> None:
    """Register the per-page metadata routes on `app` BEFORE the SPA catch-all
    mount, so they shadow the static shell for these exact deep-link paths. Only
    called when an app shell is actually being served."""

    @app.get("/proceedings/{uid}", response_class=HTMLResponse, include_in_schema=False)
    def share_speech(uid: str, request: Request,
                     s: str | None = None,
                     db: sqlite3.Connection = Depends(get_db)):
        """Share card for the proceedings viewer (VIE-5): a speech, or — with
        `?s=<ord>` — one specific sentence. Shows the quote, the speaker's name,
        faction, sitting date, and the speaker's portrait as the card image.

        `s` is declared as str and parsed by hand: a typed `int` would make
        FastAPI answer a mangled `?s=` (truncated/garbled share link) with a
        422 JSON error instead of the promised app-shell fallback."""
        try:
            try:
                s_ord = int(s) if s is not None and s.strip() else None
            except ValueError:
                s_ord = None
            sp = db.execute(
                """SELECT sp.uid, sp.session_id, sp.speaker_label,
                          p.label AS person_label, p.photo_uri,
                          f.label AS faction_label,
                          ss.date AS session_date, ss.sitting
                   FROM speech sp
                   LEFT JOIN person p ON p.person_id = sp.person_id
                   LEFT JOIN faction f ON f.id = sp.faction_id
                   LEFT JOIN session ss ON ss.id = sp.session_id
                   WHERE sp.uid = ?""", (uid,)).fetchone()
            if not sp:
                return _missing(request)

            speaker = sp["person_label"] or sp["speaker_label"] or "Ismeretlen felszólaló"
            faction = sp["faction_label"]
            date_hu = _hu_date(sp["session_date"])

            # The quote: the chosen sentence, or the opening of the speech.
            quote = ""
            if s_ord is not None:
                row = db.execute(
                    "SELECT text FROM sentence WHERE speech_id = ? AND ord = ?",
                    (uid, s_ord)).fetchone()
                if row:
                    quote = row["text"]
            if not quote:
                rows = db.execute(
                    "SELECT text FROM sentence WHERE speech_id = ? ORDER BY ord LIMIT 2",
                    (uid,)).fetchall()
                quote = " ".join(r["text"] for r in rows)
            quote = _strip_speaker_label(quote or "")

            title = speaker if not faction else f"{speaker} ({faction})"
            if date_hu:
                title = f"{title} · {date_hu}"

            if quote:
                description = f"„{_truncate(quote, 280)}”"
            else:
                # Video-only speech (no transcript, VIE-8) — nothing to quote.
                description = (f"{speaker} felszólalása"
                               + (f" – {date_hu}" if date_hu else "")
                               + " a Magyar Országgyűlésben. Nézd meg videóval, "
                                 "mondatonként szinkronizálva a Parlamonitoron.")

            # The card text follows `?s=` (a shared sentence previews with its
            # own quote), but the page's ONE address is the speech: `?s=` picks
            # out a sentence *of* this page, not a different page, so canonical
            # and og:url both drop it. Otherwise every sentence of a long speech
            # competes with the speech for the same content.
            has_photo = bool(sp["photo_uri"])
            article = {
                "@context": "https://schema.org",
                "@type": "Article",
                "headline": _truncate(title, 110),
                "inLanguage": "hu",
                "author": {"@type": "Person", "name": speaker},
                "isPartOf": {"@type": "WebPage",
                             "url": _abs(request, f"/sessions/{sp['session_id']}")},
                "publisher": {"@type": "Organization", "name": "K-Monitor"},
                "url": _abs(request, f"/proceedings/{uid}"),
            }
            if sp["session_date"]:
                article["datePublished"] = sp["session_date"]
            if quote:
                article["description"] = _truncate(quote, 280)
            return render(
                request, title=title, description=description,
                image=sp["photo_uri"], url_path=f"/proceedings/{uid}",
                og_type="article",
                # A portrait suits the small square summary card; the generic
                # logo fallback (landscape) suits the large card.
                card="summary" if has_photo else "summary_large_image",
                extra=[("article:author", speaker)],
                jsonld=[article, _breadcrumbs(request, [
                    ("Parlamonitor", "/"), ("Ülésnapok", "/sessions"),
                    (date_hu or "Ülésnap", f"/sessions/{sp['session_id']}"),
                    (speaker, f"/proceedings/{uid}")])],
                body=_speech_body(db, uid, speaker=speaker, faction=faction,
                                  date_hu=date_hu, session_id=sp["session_id"],
                                  quote=quote))
        except sqlite3.Error:
            return plain(request)

    @app.get("/representatives/{person_id}", response_class=HTMLResponse,
             include_in_schema=False)
    def share_profile(person_id: str, request: Request,
                      db: sqlite3.Connection = Depends(get_db)):
        """Metadata for an MP profile. `/representatives` and its static
        sub-pages (`/factions`, `/officials`, …) share this URL shape, find no
        person, and get their own browse-page card from `_missing`."""
        try:
            p = db.execute(
                """SELECT p.label, p.photo_uri, p.constituency, p.wikipedia_url,
                          p.wikidata_id, p.website, p.is_mp, p.is_advocate,
                          f.label AS faction_label
                   FROM person p
                   LEFT JOIN membership m ON m.person_id = p.person_id
                   LEFT JOIN faction f ON f.id = m.faction_id
                   WHERE p.person_id = ?
                   ORDER BY m.period_number DESC
                   LIMIT 1""", (person_id,)).fetchone()
            if not p:
                return _missing(request)
            label = p["label"]
            faction = p["faction_label"]
            title = label if not faction else f"{label} ({faction})"
            role = ("országgyűlési képviselő" if p["is_mp"]
                    else "nemzetiségi szószóló" if p["is_advocate"]
                    else "felszólaló")
            description = (f"{label} {role} – felszólalásai, "
                           "szavazatai, benyújtott irományai és statisztikái a "
                           "Parlamonitoron.")
            has_photo = bool(p["photo_uri"])
            # `sameAs` is what lets a search engine tie this page to the person
            # as an entity rather than to a name string (EXT-2's Wikidata link
            # earning its keep outside the site too).
            same_as = [u for u in (
                p["wikipedia_url"], p["website"],
                f"https://www.wikidata.org/wiki/{p['wikidata_id']}"
                if p["wikidata_id"] else None) if u]
            person_ld = {
                "@context": "https://schema.org",
                "@type": "Person",
                "name": label,
                "jobTitle": role,
                "url": _abs(request, f"/representatives/{person_id}"),
            }
            if faction:
                person_ld["memberOf"] = {"@type": "Organization", "name": faction}
            if p["photo_uri"]:
                person_ld["image"] = _abs(request, p["photo_uri"])
            if same_as:
                person_ld["sameAs"] = same_as
            return render(
                request, title=title, description=description,
                image=p["photo_uri"], url_path=f"/representatives/{person_id}",
                og_type="profile",
                card="summary" if has_photo else "summary_large_image",
                jsonld=[person_ld, _breadcrumbs(request, [
                    ("Parlamonitor", "/"), ("Képviselők", "/representatives"),
                    (label, f"/representatives/{person_id}")])],
                body=_profile_body(db, person_id, p))
        except sqlite3.Error:
            return plain(request)

    @app.get("/sessions/{session_id}", response_class=HTMLResponse,
             include_in_schema=False)
    def share_session(session_id: str, request: Request,
                      db: sqlite3.Connection = Depends(get_db)):
        """Metadata for a sitting-day page."""
        try:
            ss = db.execute(
                "SELECT date, sitting FROM session WHERE id = ?",
                (session_id,)).fetchone()
            if not ss:
                return _missing(request)
            date_hu = _hu_date(ss["date"])
            title = f"Országgyűlési ülésnap – {date_hu}" if date_hu else "Országgyűlési ülésnap"
            description = (
                (f"A(z) {date_hu} tartott ülésnap" if date_hu else "Ülésnap")
                + " jegyzőkönyve, felszólalásai, szó-felhője és a legtöbbet "
                  "beszélő képviselők a Parlamonitoron.")
            event = {
                "@context": "https://schema.org",
                "@type": "Event",
                "name": title,
                "eventAttendanceMode":
                    "https://schema.org/OfflineEventAttendanceMode",
                "eventStatus": "https://schema.org/EventScheduled",
                "location": {"@type": "Place", "name": "Országház",
                             "address": "Budapest, Kossuth Lajos tér 1–3."},
                "organizer": {"@type": "Organization",
                              "name": "Magyar Országgyűlés",
                              "url": "https://www.parlament.hu"},
                "url": _abs(request, f"/sessions/{session_id}"),
            }
            if ss["date"]:
                event["startDate"] = ss["date"]
            return render(
                request, title=title, description=description,
                url_path=f"/sessions/{session_id}", og_type="article",
                jsonld=[event, _breadcrumbs(request, [
                    ("Parlamonitor", "/"), ("Ülésnapok", "/sessions"),
                    (date_hu or "Ülésnap", f"/sessions/{session_id}")])],
                body=_session_body(db, session_id, date_hu))
        except sqlite3.Error:
            return plain(request)

    def _share_iromany(bill_id: str, request: Request,
                       db: sqlite3.Connection) -> HTMLResponse:
        """Metadata for an iromány detail page (a bill or any other document).

        `/bills/:id` and `/documents/:id` render the same `BillView`, so the
        same iromány is reachable at two addresses — genuine duplicate content.
        The canonical is therefore always the **preferred** one for its type
        (`main_type='T'` → `/bills/:id`, everything else → `/documents/:id`),
        which is also the split the two browse pages and the sitemaps make,
        whichever of the two URLs was actually requested."""
        try:
            b = db.execute(
                """SELECT id, bill_number, title, type, main_type, status,
                          submitted_date, text_url
                   FROM bill WHERE id = ?""", (bill_id,)).fetchone()
            if not b:
                return _missing(request)
            url_path = (f"/bills/{bill_id}" if b["main_type"] == "T"
                        else f"/documents/{bill_id}")

            number = b["bill_number"]
            doc_type = (b["type"] or "").strip()
            title_text = (b["title"] or "").strip()

            # og:title mirrors the page header: the number (canonical citation)
            # followed by the iromány's subject, falling back to type+number when
            # a document has no subject line.
            if title_text and number:
                title = f"{number} – {title_text}"
            elif title_text:
                title = title_text
            elif number:
                title = f"{_ucfirst(doc_type)} {number}".strip() if doc_type else number
            else:
                title = "Iromány"

            sponsor_rows = [
                (r["name"], r["person_id"]) for r in db.execute(
                    """SELECT COALESCE(p.label, bs.label) AS name, bs.person_id
                       FROM bill_sponsor bs
                       LEFT JOIN person p ON p.person_id = bs.person_id
                       WHERE bs.bill_id = ? ORDER BY bs.ord""", (bill_id,))
                if r["name"]]
            sponsors = [name for name, _ in sponsor_rows]
            if len(sponsors) > 3:
                sponsor_text = ", ".join(sponsors[:3]) + " és mások"
            else:
                sponsor_text = ", ".join(sponsors)

            # A natural-reading Hungarian summary from whatever fields exist.
            lead = _ucfirst(doc_type) if doc_type else "Iromány"
            if number:
                lead += f" ({number})"
            if sponsor_text:
                lead += f", benyújtó: {sponsor_text}"
            facts = []
            if b["status"]:
                facts.append(f"státusz: {b['status']}")
            date_hu = _hu_date(b["submitted_date"])
            if date_hu:
                facts.append(f"benyújtva: {date_hu}")
            description = lead + "."
            if facts:
                sentence = _ucfirst("; ".join(facts))
                # A Hungarian date already ends in "." — don't double it.
                description += " " + sentence + ("" if sentence.endswith(".") else ".")
            description += (" Az iromány adatai, jogalkotási állomásai és a "
                            "kapcsolódó felszólalások a Parlamonitoron.")

            legislation = {
                "@context": "https://schema.org",
                "@type": "Legislation",
                "name": title,
                "inLanguage": "hu",
                "legislationJurisdiction": "Magyarország",
                "url": _abs(request, url_path),
            }
            if number:
                legislation["legislationIdentifier"] = number
            if doc_type:
                legislation["legislationType"] = doc_type
            if b["submitted_date"]:
                legislation["datePublished"] = b["submitted_date"]
            if sponsors:
                legislation["creator"] = [{"@type": "Person", "name": n}
                                          for n in sponsors]
            if b["text_url"]:
                legislation["isBasedOn"] = b["text_url"]
            is_bill = b["main_type"] == "T"
            return render(
                request, title=title, description=_truncate(description),
                url_path=url_path, og_type="article",
                jsonld=[legislation, _breadcrumbs(request, [
                    ("Parlamonitor", "/"),
                    ("Törvényjavaslatok" if is_bill else "Egyéb irományok",
                     "/bills" if is_bill else "/documents"),
                    (number or title, url_path)])],
                body=_bill_body(b, sponsor_rows, title))
        except sqlite3.Error:
            return plain(request)

    @app.get("/bills/{bill_id}", response_class=HTMLResponse,
             include_in_schema=False)
    def share_bill(bill_id: str, request: Request,
                   db: sqlite3.Connection = Depends(get_db)):
        return _share_iromany(bill_id, request, db)

    @app.get("/documents/{bill_id}", response_class=HTMLResponse,
             include_in_schema=False)
    def share_document(bill_id: str, request: Request,
                       db: sqlite3.Connection = Depends(get_db)):
        return _share_iromany(bill_id, request, db)

    @app.get("/votes/{vote_id}", response_class=HTMLResponse,
             include_in_schema=False)
    def share_vote(vote_id: str, request: Request,
                   db: sqlite3.Connection = Depends(get_db)):
        """Metadata for a single vote (VOTE-*). Without this, all ~19 000 vote
        pages shared the generic site card and the same `<title>` — nothing for
        a search engine to tell them apart by. `/votes/cohesion` finds no vote
        and falls back to its own browse-page card."""
        try:
            v = db.execute(
                """SELECT id, vote_datetime, subject, result, yes, no, abstain,
                          voting_mode
                     FROM vote WHERE id = ?""", (vote_id,)).fetchone()
            if not v:
                return _missing(request)
            subjects = [
                {"bill_id": r["bill_id"],
                 "label": " – ".join(x for x in (r["bill_number"], r["title"]) if x)
                          or "Iromány"}
                for r in db.execute(
                    """SELECT vs.bill_number, vs.title, b.id AS bill_id
                         FROM vote_subject vs
                         LEFT JOIN bill b ON b.id = vs.iromany_id
                        WHERE vs.vote_id = ? ORDER BY vs.ord""", (vote_id,))]

            date_hu = _hu_date((v["vote_datetime"] or "")[:10])
            subject = (v["subject"] or "").strip()
            lead = subjects[0]["label"] if subjects else subject
            title = f"Szavazás – {lead}" if lead else "Szavazás"
            if date_hu:
                title = f"{title} · {date_hu}"

            tally = ", ".join(
                f"{label} {v[col]}" for label, col in
                (("igen", "yes"), ("nem", "no"), ("tartózkodás", "abstain"))
                if v[col] is not None)
            description = " ".join(x for x in (
                f"Az Országgyűlés {date_hu} tartott szavazása" if date_hu
                else "Az Országgyűlés szavazása",
                f"– {subject}." if subject else "–",
                f"Eredmény: {v['result']}." if v["result"] else "",
                f"Szavazatok: {tally}." if tally else "",
                "A frakciók szerinti megoszlás és a képviselőnkénti "
                "szavazatok a Parlamonitoron.") if x)

            return render(
                request, title=_truncate(title, 120),
                description=_truncate(description),
                url_path=f"/votes/{vote_id}", og_type="article",
                jsonld=[_breadcrumbs(request, [
                    ("Parlamonitor", "/"), ("Szavazások", "/votes"),
                    (_truncate(lead or "Szavazás", 60), f"/votes/{vote_id}")])],
                body=_vote_body(v, subjects, _truncate(title, 120)))
        except sqlite3.Error:
            return plain(request)
