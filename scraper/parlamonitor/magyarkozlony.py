"""Resolve a promulgated bill's Magyar Közlöny (Hungarian Gazette) link.

When a bill reaches the *kihirdetve* (promulgated) stage, parlament.hu records
the Magyar Közlöny issue number (``mkSzama``) and the promulgation date
(``kihirdetesDatuma``) but **not** a link to the gazette itself. The official
gazette lives on a separate site, ``magyarkozlony.hu``, whose issue-listing page
is addressable by year + issue number:

    https://magyarkozlony.hu/?year=<year>&month=&serial=<issue>

That listing page is server-rendered and embeds the canonical, hash-addressed
PDF-viewer URL for the issue (``/dokumentumok/<hash>/megtekintes``) in a
schema.org ``<meta itemprop="url">`` tag. We fetch the listing once and lift
that direct link out so the bill page can point straight at the gazette PDF,
falling back to the listing URL itself when the direct link can't be extracted
(SCR-5 — a fetch failure degrades gracefully, it never aborts the scrape).
"""

from __future__ import annotations

import logging
import re

from .http_client import HttpClient, HttpError

logger = logging.getLogger(__name__)

BASE = "https://magyarkozlony.hu"
# The schema.org Newspaper item on the listing page carries the canonical PDF
# viewer URL; both the <meta itemprop="url"> and the visible <a href> use it.
_DOC_RE = re.compile(r"https://magyarkozlony\.hu/dokumentumok/[0-9a-f]+/megtekintes")
_YEAR_RE = re.compile(r"(\d{4})")


def listing_url(year, serial) -> str:
    """The issue-listing page for Magyar Közlöny issue ``serial`` in ``year``."""
    return f"{BASE}/?year={year}&month=&serial={serial}"


def resolve(http: HttpClient, mk_number, promulgation_date) -> dict | None:
    """Resolve the gazette links for a promulgated bill.

    ``mk_number`` is the Magyar Közlöny issue number and ``promulgation_date``
    the promulgation date (its year selects the issue's volume). Returns
    ``{"url": <listing page>, "docUrl": <direct PDF viewer or None>}``, or
    ``None`` when there isn't enough to build a link. A failed fetch (or a page
    with no extractable direct link) degrades to the listing URL only — it never
    raises."""
    if not mk_number or not promulgation_date:
        return None
    ym = _YEAR_RE.search(str(promulgation_date))
    if not ym:
        return None
    url = listing_url(ym.group(1), mk_number)
    doc_url = None
    try:
        html = http.get_text(url)
        m = _DOC_RE.search(html)
        if m:
            doc_url = m.group(0)
        else:
            logger.warning("No Magyar Közlöny doc link found at %s", url)
    except HttpError:
        logger.warning("Magyar Közlöny fetch failed (%s); using listing URL", url)
    return {"url": url, "docUrl": doc_url}
