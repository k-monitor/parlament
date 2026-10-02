"""Deep links into parlament.hu's single-page apps.

The public site keeps its whole navigation state in a ``#page=`` URL fragment:
a gzip-compressed, url-safe-base64-encoded JSON blob prefixed with ``cv1gzb-``.
Rebuilding that fragment lets our "view on parlament.hu" links land on the exact
record's adatlap (detail sheet) rather than a generic listing page. They are
built per request from the record's upstream id, so a renamed page is fixed for
every row at once. (The scraper also stamps a sitting-day link onto each day it
writes, ``parlamonitor.proceedings.transform.plenary_day_page_url``.)

Every adatlap the portal links to is a page with an ``open`` contract that takes
the record's ``id`` and fans it out to the page's own datasources, so the state is
just ``{"page": <page>, "hydration": {"open": {"id": <id>}}}`` — what the portal's
own client builds for its in-page links (``renderPageUrlForHydration`` in
``felicitas/static/js/felicitas_client_full.js``). It names the page and nothing
inside it; the full ``binding`` state the portal writes after navigating has to
spell out every internal dao, and is not needed to open a sheet.

When a link starts showing "Failed to load page", the page was renamed (the vote
adatlap was, by 2026-10-02): the list page that links to it names the current
one in a ``pageReference.pageName`` — fetch
``/felicitas/api/page-info/page-item/<list page>``, the ``data-page`` of e.g.
``/web/guest/szavazasok``.
"""

from __future__ import annotations

import base64
import gzip
import json
import re

_FRAGMENT_PREFIX = "cv1gzb-"

# Felicitas record ids: a UUID from the 2026 backend on, a plain integer for
# everything the portal carried over from before it (votes, bills and speeches
# back to 1990 all open by it). Anything else — the legacy archive's "35-1234",
# a test fixture's "bill-uuid-1" — has no adatlap.
_RECORD_ID_RE = re.compile(
    r"^(?:\d+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", re.I)
# A person's kepviseloId: "a011", "004O", "0005" — MPs, nationality advocates
# and the non-MP office holders (ministers, state secretaries) who spoke alike.
_PERSON_ID_RE = re.compile(r"^[0-9a-z]{4}$", re.I)

BASE = "https://www.parlament.hu"

# (page that hosts the portal app, adatlap page name)
VOTE_PAGE = (f"{BASE}/web/guest/szavazasok",
             "szavazasokexportok/exported-szavazas-adatlap-page/exported-szavazas-adatlap-page")
BILL_PAGE = (f"{BASE}/web/guest/iromanyok",
             "iromanyexportok/iromany-adatlap-with-contract/iromany-adatlap-with-contract")
PERSON_PAGE = (f"{BASE}/web/guest/kepviselok",
               "kepviseloexportok/kepviselo-adatlap-with-contract/kepviselo-adatlap-with-contract")
COMMITTEE_PAGE = (f"{BASE}/web/guest/bizottsagok1",
                  "bizottsagexportok/exported-bizottsag-adatlap/exported-bizottsag-adatlap")
SITTING_DAY_PAGE = (f"{BASE}/ulesnapok-ulesidok",
                    "plenarisulesexportok/ulesnap-felszolalasai-with-contract/"
                    "ulesnap-felszolalasai-with-contract")
SPEECH_PAGE = (f"{BASE}/ulesnapok-ulesidok",
               "plenarisulesexportok/ulesnap-felszolalas-adata-with-contract/"
               "ulesnap-felszolalas-adata-with-contract")


def _fragment(state: dict) -> str:
    payload = json.dumps(state, separators=(",", ":"), ensure_ascii=False)
    # mtime=0: the same record always yields the same URL (the bill's is stored).
    gz = gzip.compress(payload.encode("utf-8"), mtime=0)
    b64 = base64.b64encode(gz).decode("ascii").translate(str.maketrans("/+", "_-"))
    return _FRAGMENT_PREFIX + b64.rstrip("=")


def _adatlap_url(page: tuple[str, str], record_id, id_re: re.Pattern) -> str | None:
    if record_id is None or not id_re.match(str(record_id)):
        return None
    host, name = page
    state = {"page": name, "hydration": {"open": {"id": str(record_id)}}}
    return f"{host}#page={_fragment(state)}"


def vote_page_url(vote_id) -> str | None:
    """Deep link to a vote's adatlap on parlament.hu."""
    return _adatlap_url(VOTE_PAGE, vote_id, _RECORD_ID_RE)


def bill_page_url(bill_id) -> str | None:
    """Deep link to a bill's (iromány's) adatlap on parlament.hu."""
    return _adatlap_url(BILL_PAGE, bill_id, _RECORD_ID_RE)


def person_page_url(person_id) -> str | None:
    """Deep link to a person's adatlap on parlament.hu."""
    return _adatlap_url(PERSON_PAGE, person_id, _PERSON_ID_RE)


def committee_page_url(committee_id) -> str | None:
    """Deep link to a committee's (or subcommittee's) adatlap on parlament.hu."""
    return _adatlap_url(COMMITTEE_PAGE, committee_id, _RECORD_ID_RE)


def sitting_day_page_url(day_id) -> str | None:
    """Deep link to a sitting day's speech listing on parlament.hu."""
    return _adatlap_url(SITTING_DAY_PAGE, day_id, _RECORD_ID_RE)


def speech_page_url(speech_uuid) -> str | None:
    """Deep link to one plenary speech's adatlap on parlament.hu: speaker, text
    and its video clip, with links to the neighbouring speeches."""
    return _adatlap_url(SPEECH_PAGE, speech_uuid, _RECORD_ID_RE)
