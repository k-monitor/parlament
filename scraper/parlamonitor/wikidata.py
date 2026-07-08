"""Link representatives to their Wikidata item and Wikipedia article.

Every MP whose parlament.hu identity is recorded on Wikidata carries property
**P4966** ("Hungarian National Assembly ID"), and its value is *exactly* the
Felicitas ``kepviseloId`` we key people on (e.g. P4966 ``g056`` ==
``Gyöngyösi Márton``, ``n026`` == ``Navracsics Tibor``). So an MP links to its
Wikidata item — and thence to a Wikipedia article — by an **exact id join**,
never by fragile name matching (EXT-2 in spirit).

One SPARQL query against the Wikidata Query Service fetches the whole
``P4966 -> (QID, Wikipedia URL)`` map; :func:`fetch_mp_links` returns it keyed by
the id, and the caller joins the roster's ``personID``s against it. A failed
query degrades gracefully to an empty map (SCR-5): the roster still loads, MPs
just get no Wikidata/Wikipedia link that run.
"""

from __future__ import annotations

import logging

from .http_client import HttpClient, HttpError

logger = logging.getLogger(__name__)

ENDPOINT = "https://query.wikidata.org/sparql"

# Property P4966 = "Hungarian National Assembly ID"; its value is the parlament.hu
# kepviseloId. We pull each holder's QID plus its Hungarian- and English-Wikipedia
# sitelinks (preferring the Hungarian article for a Hungarian site). No label
# service — we only need the identifiers and the article URLs.
_QUERY = """
SELECT ?p4966 ?item ?huArticle ?enArticle WHERE {
  ?item wdt:P4966 ?p4966 .
  OPTIONAL { ?huArticle schema:about ?item ; schema:isPartOf <https://hu.wikipedia.org/> . }
  OPTIONAL { ?enArticle schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> . }
}
"""


def _qid(item_uri: str) -> str | None:
    """``http://www.wikidata.org/entity/Q57641`` -> ``Q57641``."""
    if not item_uri:
        return None
    qid = item_uri.rstrip("/").rsplit("/", 1)[-1]
    return qid if qid.startswith("Q") else None


def fetch_mp_links(http: HttpClient) -> dict[str, dict]:
    """Return ``{p4966_id: {"wikidataId": QID, "wikipediaUrl": url|None}}``.

    ``p4966_id`` is the parlament.hu ``kepviseloId`` (our ``personID``). The
    Wikipedia URL prefers the Hungarian article, falling back to English. A fetch
    or parse failure degrades to an empty map — it never raises (SCR-5)."""
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
        # An id can (rarely) be reused across items; keep the first, but prefer a
        # row that carries a Wikipedia article over one that doesn't.
        prev = out.get(pid)
        if prev is None or (wiki and not prev.get("wikipediaUrl")):
            out[pid] = {"wikidataId": qid, "wikipediaUrl": wiki}
    logger.info("Wikidata: %d MP identifiers, %d with a Wikipedia article",
                len(out), sum(1 for v in out.values() if v.get("wikipediaUrl")))
    return out
