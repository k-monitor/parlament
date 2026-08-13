"""Link representatives to their Wikidata item and Wikipedia article.

Every MP whose parlament.hu identity is recorded on Wikidata carries property
**P4966** ("Hungarian National Assembly ID"), and its value is *exactly* the
Felicitas ``kepviseloId`` we key people on (e.g. P4966 ``g056`` ==
``Gyöngyösi Márton``, ``n026`` == ``Navracsics Tibor``). So an MP links to its
Wikidata item — and thence to a Wikipedia article — by an **exact id join**,
never by fragile name matching (EXT-2 in spirit).

One SPARQL query against the Wikidata Query Service fetches the whole
``P4966 -> (QID, Wikipedia URL, date of birth)`` map; :func:`fetch_mp_links`
returns it keyed by the id, and the caller joins the roster's ``personID``s
against it. A failed query degrades gracefully to an empty map (SCR-5): the
roster still loads, MPs just get no Wikidata/Wikipedia link that run.

The birth date (P569) rides along on the same query — it is the one biographical
fact the roster itself never carries — and each date is stamped with its derived
sun sign (:mod:`parlamonitor.zodiac`) so the sign is computed once, at scrape
time, from the day it depends on.
"""

from __future__ import annotations

import logging

from . import zodiac
from .http_client import HttpClient, HttpError

logger = logging.getLogger(__name__)

ENDPOINT = "https://query.wikidata.org/sparql"

# Property P4966 = "Hungarian National Assembly ID"; its value is the parlament.hu
# kepviseloId. We pull each holder's QID plus its Hungarian- and English-Wikipedia
# sitelinks (preferring the Hungarian article for a Hungarian site), and its date
# of birth (P569). No label service — we only need the identifiers, the article
# URLs and the date.
#
# The birth date is read off the *statement node* (`p:`/`psv:`) rather than the
# truthy `wdt:` value, because only there is its precision visible: Wikidata
# serializes a year-only date as `1965-01-01T00:00:00Z`, indistinguishable from a
# real New Year's Day birth, which would invent a Capricorn out of nothing.
# `timePrecision >= 11` keeps day-precision dates (11) and the finer hour/minute/
# second ones, and drops month- and year-only statements entirely.
_QUERY = """
SELECT ?p4966 ?item ?huArticle ?enArticle ?dob WHERE {
  ?item wdt:P4966 ?p4966 .
  OPTIONAL { ?huArticle schema:about ?item ; schema:isPartOf <https://hu.wikipedia.org/> . }
  OPTIONAL { ?enArticle schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> . }
  OPTIONAL {
    ?item p:P569/psv:P569 ?dobNode .
    ?dobNode wikibase:timeValue ?dob ; wikibase:timePrecision ?dobPrecision .
    FILTER(?dobPrecision >= 11)
  }
}
"""


def _qid(item_uri: str) -> str | None:
    """``http://www.wikidata.org/entity/Q57641`` -> ``Q57641``."""
    if not item_uri:
        return None
    qid = item_uri.rstrip("/").rsplit("/", 1)[-1]
    return qid if qid.startswith("Q") else None


def _fields_found(entry: dict) -> int:
    """How many optional fields a candidate row filled in (its "richness")."""
    return sum(1 for key in ("wikipediaUrl", "dateOfBirth") if entry.get(key))


def fetch_mp_links(http: HttpClient) -> dict[str, dict]:
    """Return ``{p4966_id: {"wikidataId", "wikipediaUrl", "dateOfBirth",
    "zodiacSign", "chineseZodiacSign"}}``.

    ``p4966_id`` is the parlament.hu ``kepviseloId`` (our ``personID``). The
    Wikipedia URL prefers the Hungarian article, falling back to English;
    ``dateOfBirth`` is a plain ``YYYY-MM-DD`` day (day-precision statements only),
    with the sun sign and the Chinese zodiac animal derived from it — all ``None``
    when Wikidata records no usable date. A fetch or parse failure degrades to an
    empty map — it never raises (SCR-5)."""
    try:
        http.polite_sleep()
        data = http.get_json(
            ENDPOINT,
            params={"query": _QUERY, "format": "json"},
            headers={"Accept": "application/sparql-results+json"})
    except (HttpError, ValueError) as e:
        logger.warning("Wikidata MP-link query failed (%s); skipping links", e)
        return {}

    out: dict[str, dict] = {}
    for row in data.get("results", {}).get("bindings", []):
        pid = (row.get("p4966") or {}).get("value")
        qid = _qid((row.get("item") or {}).get("value", ""))
        if not pid or not qid:
            continue
        wiki = (row.get("huArticle") or {}).get("value") \
            or (row.get("enArticle") or {}).get("value")
        born = zodiac.iso_day((row.get("dob") or {}).get("value"))
        cand = {"wikidataId": qid, "wikipediaUrl": wiki,
                "dateOfBirth": born, "zodiacSign": zodiac.sign_for(born),
                "chineseZodiacSign": zodiac.chinese_sign_for(born)}
        # One id can (rarely) be reused across items, and one person can carry
        # several birth-date statements — so a pid may see several rows. Keep the
        # richest one (most fields filled), first row winning a tie; taking the
        # whole row keeps every field belonging to the same item.
        prev = out.get(pid)
        if prev is None or _fields_found(cand) > _fields_found(prev):
            out[pid] = cand
    logger.info("Wikidata: %d MP identifiers, %d with a Wikipedia article, "
                "%d with a birth date",
                len(out), sum(1 for v in out.values() if v.get("wikipediaUrl")),
                sum(1 for v in out.values() if v.get("dateOfBirth")))
    return out
