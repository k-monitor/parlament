"""Server-side OpenGraph / Twitter-card injection for shared deep links.

The SPA ships a single static `index.html` whose `<head>` carries only generic,
site-wide OpenGraph tags, so every shared deep link — a speech, a *specific
sentence*, an MP profile, a sitting day — previews identically as a bare
"Parlamonitor". Social/chat crawlers (Facebook, Twitter/X, Slack, Signal,
Telegram, …) do **not** run the SPA's JavaScript, so they never see the
per-page content the app would render.

This module renders the **same app shell** but with per-URL OpenGraph/Twitter
meta tags injected server-side, so a shared sentence shows the quote + the
speaker's name (+ faction + date + their portrait), a profile shows the MP,
etc. Real browsers still boot the unchanged SPA over this shell — only the
`<head>` metadata differs, and the SPA controls `#app`, never the head, so
there is no conflict.

It is deliberately best-effort: any missing data or error falls back to the
plain shell (`plain()`), never a JSON 404, so a crawler (or a user) always gets
a working app page.
"""

from __future__ import annotations

import html
import os
import re
import sqlite3

from fastapi import Depends, Request
from fastapi.responses import HTMLResponse

from .config import settings
from .db import get_db

# --- app-shell loading ------------------------------------------------------

_shell_cache: str | None = None

# Tags we replace/inject so we never emit duplicates of the shell's own
# generic <title> / description.
_TITLE_RE = re.compile(r"<title>.*?</title>", re.IGNORECASE | re.DOTALL)
_DESC_RE = re.compile(
    r"""<meta\s+name=["']description["'][^>]*>""", re.IGNORECASE)


def _load_shell() -> str | None:
    """Read the built `index.html` once and cache it. Returns None when no SPA
    is being served (dev / API-only deployment), which is the signal to skip
    registering the share-card routes entirely."""
    global _shell_cache
    if _shell_cache is not None:
        return _shell_cache
    dist = settings.frontend_dist
    if not dist:
        return None
    path = os.path.join(dist, "index.html")
    try:
        with open(path, encoding="utf-8") as fh:
            _shell_cache = fh.read()
    except OSError:
        return None
    return _shell_cache


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

def render(request: Request, *, title: str, description: str,
           image: str | None = None, url_path: str | None = None,
           og_type: str = "website", card: str = "summary_large_image",
           extra: list[tuple[str, str]] | None = None) -> HTMLResponse:
    """Render the app shell with per-page OpenGraph/Twitter tags injected.

    `title`/`description` are the human-readable card text; `image`/`url_path`
    are made absolute against the canonical base URL; `extra` is optional
    property→content pairs (e.g. article:author) appended verbatim."""
    shell = _load_shell()
    if shell is None:  # pragma: no cover - guarded by the caller
        raise RuntimeError("no app shell")

    full_title = title if title.endswith("Parlamonitor") else f"{title} · Parlamonitor"
    abs_url = _abs(request, url_path) if url_path else _base_url(request)
    abs_image = _abs(request, image) or _abs(request, "/parlamonitor.png")

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
    if abs_image:
        tags.append(f'<meta property="og:image" content="{_esc(abs_image)}" />')
        tags.append(f'<meta name="twitter:image" content="{_esc(abs_image)}" />')
    for prop, content in (extra or []):
        attr = "name" if prop.startswith(("twitter:", "description")) else "property"
        tags.append(f'<meta {attr}="{_esc(prop)}" content="{_esc(content)}" />')

    block = "\n    " + "\n    ".join(tags)
    # Drop the shell's generic title + description so we don't duplicate them,
    # then inject our block just before </head>.
    out = _TITLE_RE.sub("", shell, count=1)
    out = _DESC_RE.sub("", out, count=1)
    out = out.replace("</head>", block + "\n  </head>", 1)
    return HTMLResponse(out)


def plain(request: Request) -> HTMLResponse:
    """The unmodified app shell — the graceful fallback when a URL has no
    special card (unknown id, missing data, or any error)."""
    shell = _load_shell()
    if shell is None:  # pragma: no cover
        raise RuntimeError("no app shell")
    return HTMLResponse(shell)


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

def register(app) -> None:
    """Register the share-card routes on `app` BEFORE the SPA catch-all mount,
    so they shadow the static shell for these exact deep-link paths. Only called
    when an app shell is actually being served."""

    @app.get("/proceedings/{uid}", response_class=HTMLResponse, include_in_schema=False)
    def share_speech(uid: str, request: Request,
                     s: int | None = None,
                     db: sqlite3.Connection = Depends(get_db)):
        """Share card for the proceedings viewer (VIE-5): a speech, or — with
        `?s=<ord>` — one specific sentence. Shows the quote, the speaker's name,
        faction, sitting date, and the speaker's portrait as the card image."""
        try:
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
                return plain(request)

            speaker = sp["person_label"] or sp["speaker_label"] or "Ismeretlen felszólaló"
            faction = sp["faction_label"]
            date_hu = _hu_date(sp["session_date"])

            # The quote: the chosen sentence, or the opening of the speech.
            quote = ""
            if s is not None:
                row = db.execute(
                    "SELECT text FROM sentence WHERE speech_id = ? AND ord = ?",
                    (uid, s)).fetchone()
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

            url_path = f"/proceedings/{uid}" + (f"?s={s}" if s is not None else "")
            has_photo = bool(sp["photo_uri"])
            return render(
                request, title=title, description=description,
                image=sp["photo_uri"], url_path=url_path, og_type="article",
                # A portrait suits the small square summary card; the generic
                # logo fallback (landscape) suits the large card.
                card="summary" if has_photo else "summary_large_image",
                extra=[("article:author", speaker)])
        except sqlite3.Error:
            return plain(request)

    @app.get("/representatives/{person_id}", response_class=HTMLResponse,
             include_in_schema=False)
    def share_profile(person_id: str, request: Request,
                      db: sqlite3.Connection = Depends(get_db)):
        """Share card for an MP profile. `/representatives` and
        `/representatives/factions` fall through to the plain shell (no match)."""
        try:
            p = db.execute(
                """SELECT p.label, p.photo_uri, f.label AS faction_label
                   FROM person p
                   LEFT JOIN membership m ON m.person_id = p.person_id
                   LEFT JOIN faction f ON f.id = m.faction_id
                   WHERE p.person_id = ?
                   ORDER BY m.period_number DESC
                   LIMIT 1""", (person_id,)).fetchone()
            if not p:
                return plain(request)
            label = p["label"]
            faction = p["faction_label"]
            title = label if not faction else f"{label} ({faction})"
            description = (f"{label} országgyűlési képviselő – felszólalásai, "
                           "szavazatai, benyújtott irományai és statisztikái a "
                           "Parlamonitoron.")
            has_photo = bool(p["photo_uri"])
            return render(
                request, title=title, description=description,
                image=p["photo_uri"], url_path=f"/representatives/{person_id}",
                og_type="profile",
                card="summary" if has_photo else "summary_large_image")
        except sqlite3.Error:
            return plain(request)

    @app.get("/sessions/{session_id}", response_class=HTMLResponse,
             include_in_schema=False)
    def share_session(session_id: str, request: Request,
                      db: sqlite3.Connection = Depends(get_db)):
        """Share card for a sitting-day page."""
        try:
            ss = db.execute(
                "SELECT date, sitting FROM session WHERE id = ?",
                (session_id,)).fetchone()
            if not ss:
                return plain(request)
            date_hu = _hu_date(ss["date"])
            title = f"Országgyűlési ülésnap – {date_hu}" if date_hu else "Országgyűlési ülésnap"
            description = (
                (f"A(z) {date_hu} tartott ülésnap" if date_hu else "Ülésnap")
                + " jegyzőkönyve, felszólalásai, szó-felhője és a legtöbbet "
                  "beszélő képviselők a Parlamonitoron.")
            return render(
                request, title=title, description=description,
                url_path=f"/sessions/{session_id}", og_type="article")
        except sqlite3.Error:
            return plain(request)
