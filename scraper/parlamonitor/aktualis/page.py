"""Read the **Aktuális** portal page (NR-1).

`https://www.parlament.hu/web/guest/aktualis` is the one page where the House
publishes what it is *about to do*: the order paper (**napirend**) for the next
sitting, the month's **ülésterv**, the submission deadlines, the legislative
programme, and the date of the House Committee's next meeting. None of it is in
the Felicitas API — the page is the source.

The page is Liferay-generated, and its markup is disposable: the House restyles
it, the ``div`` ids change, and the block that carries the House Committee's
meeting is a *year navigator* widget with leftover ``data-year="2025"``
attributes on links that are not years at all. So this parser keys on nothing
structural. It finds the documents by the shape of their **href** —
``/documents/d/guest/<slug>``, where the slug itself says what the document is
and which sitting it belongs to (``nr_20260914_elfogadott``) — and the House
Committee's meeting by the two Hungarian labels the House writes in front of it
(*Helyszíne:*, *Időpontja:*). Both survive a reskin; a CSS selector would not.

What this deliberately does **not** collect is the page's general link list
(*Aktív képviselők listája*, *Irományok napi jegyzéke*, …). Those are
parlament.hu's own navigation into pages we already cover from the API, so
mirroring them would add a maintenance surface and no data.
"""

from __future__ import annotations

import html
import logging
import re
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

BASE = "https://www.parlament.hu"
PAGE_URL = BASE + "/web/guest/aktualis"

_A_RE = re.compile(r"<a\s[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]*>")
_TITLEBAR_RE = re.compile(r"<div class=\"title-bar\">(.*?)</div>", re.I | re.S)
_FOOTER_RE = re.compile(r"<footer\b", re.I)
_DOC_HREF_RE = re.compile(r"^(?:https?://(?:www\.)?parlament\.hu)?/documents/d/guest/([^/?#]+)")
# The slug's own ``YYYYMMDD`` is the sitting the document belongs to.
_SLUG_DATE_RE = re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)")

# Which document a slug is. Order matters: the first prefix that matches wins.
_KINDS = (
    ("nr_", "agenda"),
    ("ut_", "sitting_plan"),
    ("tajek_benyhatido", "deadlines"),
    ("torvenyalkotasi-program", "legislative_programme"),
    ("munkarend", "work_schedule"),
)

_MONTHS_LC = {
    "január": 1, "február": 2, "március": 3, "április": 4, "május": 5, "június": 6,
    "július": 7, "augusztus": 8, "szeptember": 9, "október": 10, "november": 11,
    "december": 12,
}
_HC_DATE_RE = re.compile(
    r"(\d{4})\.\s*([a-záéíóöőúüű]+)\s+(\d{1,2})\.(?:\s*\(([^)]*)\))?"
    r"(?:\s*(\d{1,2}):(\d{2}))?", re.I)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", fragment))).strip()


def _kind(slug: str) -> str:
    low = slug.lower()
    for prefix, kind in _KINDS:
        if low.startswith(prefix):
            return kind
    return "document"


def _slug_date(slug: str) -> str | None:
    m = _SLUG_DATE_RE.search(slug)
    if not m:
        return None
    try:
        from datetime import date
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def _group_for(page: str, bars: list[tuple[int, str]], at: int) -> str | None:
    """The heading the link sits under — the nearest ``title-bar`` above it."""
    label = None
    for pos, text in bars:
        if pos < at:
            label = text
        else:
            break
    return label


def parse_documents(page: str) -> list[dict]:
    """The page's own ``/documents/d/guest/<slug>`` links, in page order.

    Only links *inside the content* count. The site chrome links a couple of
    documents too — the House's organisational chart hangs off both the top
    menu and the footer — and those belong to the template rather than to this
    page. The content runs from the page's first ``title-bar`` to its footer.
    """
    bars = [(m.start(), _text(m.group(1))) for m in _TITLEBAR_RE.finditer(page)]
    content_from = bars[0][0] if bars else 0
    footer = _FOOTER_RE.search(page)
    content_to = footer.start() if footer else len(page)
    out: list[dict] = []
    seen: set[str] = set()
    for m in _A_RE.finditer(page):
        if not (content_from <= m.start() < content_to):
            continue
        href = html.unescape(m.group(1)).strip()
        dm = _DOC_HREF_RE.match(href)
        if not dm:
            continue
        slug = dm.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        out.append({
            "slug": slug,
            "kind": _kind(slug),
            "label": _text(m.group(2)) or None,
            "url": urljoin(BASE + "/", href),
            "date": _slug_date(slug),
            "group": _group_for(page, bars, m.start()),
        })
    return out


def parse_house_committee(page: str) -> dict | None:
    """The House Committee's next meeting (*A Házbizottság soron következő ülése*).

    Two free-text lines, labelled: a venue and a date with an hour. Both are
    optional in principle, so a missing one is left ``None`` rather than
    failing the parse.
    """
    place = when = None
    for m in _A_RE.finditer(page):
        text = _text(m.group(2))
        if text.startswith("Helyszíne:"):
            place = text.split(":", 1)[1].strip() or None
        elif text.startswith("Időpontja:"):
            when = text.split(":", 1)[1].strip() or None
    if not (place or when):
        return None
    out: dict = {"place": place, "raw": when, "date": None, "time": None,
                 "weekday": None}
    if when:
        m = _HC_DATE_RE.search(when)
        if m and m.group(2).lower() in _MONTHS_LC:
            try:
                from datetime import date
                out["date"] = date(int(m.group(1)), _MONTHS_LC[m.group(2).lower()],
                                   int(m.group(3))).isoformat()
            except ValueError:
                pass
            out["weekday"] = (m.group(4) or "").strip() or None
            if m.group(5):
                out["time"] = "%02d:%s" % (int(m.group(5)), m.group(6))
    return out


def parse(page: str) -> dict:
    """Parse the Aktuális page into its documents and the House Committee note."""
    docs = parse_documents(page)
    if not docs:
        logger.warning("Aktuális page carried no /documents/d/guest links — "
                       "the page's markup or its content may have changed")
    return {"documents": docs, "houseCommittee": parse_house_committee(page)}
