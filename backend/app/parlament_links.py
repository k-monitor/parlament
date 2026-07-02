"""Deep links into parlament.hu's single-page apps.

The public site keeps its whole navigation state in a ``#page=`` URL fragment:
a gzip-compressed, url-safe-base64-encoded JSON blob prefixed with ``cv1gzb-``.
Rebuilding that fragment lets our "view on parlament.hu" links land on the exact
bill / vote sheet rather than a generic listing page. The scraper builds the
sitting-day variant (see ``parlamonitor.proceedings.transform``); bills and votes
are assembled here because they are loaded straight from the scrape output.
"""

from __future__ import annotations

import base64
import gzip
import json
import re

_FRAGMENT_PREFIX = "cv1gzb-"
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

BILL_BASE = "https://www.parlament.hu/web/guest/iromanyok"
BILL_PAGE = "iromanyexportok/iromany-adatlap-with-contract/iromany-adatlap-with-contract"

# Bindings on the bill adatlap keyed by the bill's own iromány id (`pOnalId`);
# the primary `dao` binding keys on `pId`. Captured verbatim from a real URL.
_BILL_ONAL_DAOS = (
    "azonnalikerdesDao", "bizottsagiNemOnalloDao", "felszolalasDao",
    "hataridokDao", "hatteranyagokDao", "indoklasokDao",
    "iromanyBizottsagiEsemenyekDao", "iromanyEsemenyekDao",
    "iromanySzavazasaiDao", "iromanyokhozhatteranyagokDao",
    "iromanytTargyaloBizottsagokDao", "nemOnalloDao", "nemOnalloosszesitoDao",
)


def _fragment(state: dict) -> str:
    payload = json.dumps(state, separators=(",", ":"), ensure_ascii=False)
    gz = gzip.compress(payload.encode("utf-8"))
    b64 = base64.b64encode(gz).decode("ascii").translate(str.maketrans("/+", "_-"))
    return _FRAGMENT_PREFIX + b64


def _datasource(param: str, value: str) -> dict:
    return {"type": "datasource",
            "content": {"parameters": {param: value}, "open": True,
                        "openPossibleCounts": False, "state": {"page": 0}}}


def _parameter(param: str, value: str) -> dict:
    return {"type": "parameter", "content": {param: value}}


VOTE_BASE = "https://www.parlament.hu/web/guest/szavazasok"
VOTE_PAGE = "szavazasokexportok/szavazas-adatlap-with-contract/szavazas-adatlap-with-contract"

# The vote adatlap keys most daos on the vote's own id as `pId`, but the per-MP
# roll-call dao (`szavazasByKepviseloDao`) names it `pSzavazasId`. Captured
# verbatim (names + order) from a real URL.
_VOTE_PID_DAOS = ("szavazasPatkoDao", "szavazasAlapadatokDao", "szavazasByFrakcioDao")


def vote_page_url(vote_id: str | None) -> str | None:
    """Deep link to a vote's adatlap (detail sheet) on parlament.hu."""
    if not vote_id or not _UUID_RE.match(str(vote_id)):
        return None
    binding: dict = {
        "emptyIntBinding": {"type": "normal", "content": None},
        "kepviselo": {"type": "state", "content": {"content": {
            "dao.dataSource": {"type": "datasource",
                               "content": {"parameters": {}, "open": True,
                                           "openPossibleCounts": False,
                                           "state": {"page": 0}}},
            "dao.parameter": {"type": "parameter", "content": {}},
        }, "open": False}},
        "kepviseloId": {"type": "normal", "content": None},
        "szavazasPatkoDao.dataSource": _datasource("pId", vote_id),
        "szavazasAlapadatokDao.dataSource": _datasource("pId", vote_id),
        "szavazasAlapadatokDao.parameter": _parameter("pId", vote_id),
        "szavazasByFrakcioDao.dataSource": _datasource("pId", vote_id),
        "szavazasByFrakcioDao.parameter": _parameter("pId", vote_id),
        "szavazasByKepviseloDao.dataSource": _datasource("pSzavazasId", vote_id),
        "szavazasByKepviseloDao.parameter": _parameter("pSzavazasId", vote_id),
        "szavazasPatkoDao.parameter": _parameter("pId", vote_id),
    }
    state = {"page": VOTE_PAGE, "binding": binding, "globals": {}}
    return f"{VOTE_BASE}#page={_fragment(state)}"


def bill_page_url(bill_id: str | None) -> str | None:
    """Deep link to a bill's adatlap (detail sheet) on parlament.hu."""
    if not bill_id or not _UUID_RE.match(str(bill_id)):
        return None
    binding: dict = {
        "bizottsagMiniAdatlap": {"type": "state", "content": {}},
        "emptyIntBinding": {"type": "normal", "content": None},
        "emptyIntListParameter": {"type": "normal", "content": None},
        "emptyStringListParameter": {"type": "normal", "content": None},
    }
    for dao in _BILL_ONAL_DAOS:
        binding[f"{dao}.dataSource"] = _datasource("pOnalId", bill_id)
        binding[f"{dao}.parameter"] = _parameter("pOnalId", bill_id)
    binding["dao.dataSource"] = _datasource("pId", bill_id)
    binding["dao.parameter"] = _parameter("pId", bill_id)
    state = {"page": BILL_PAGE, "binding": binding, "globals": {}}
    return f"{BILL_BASE}#page={_fragment(state)}"
