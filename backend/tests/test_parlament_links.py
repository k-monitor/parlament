"""Unit tests for parlament.hu deep-link fragment building."""
import base64
import gzip
import json

import pytest

from app.parlament_links import (bill_page_url, committee_page_url, person_page_url,
                                 speech_page_url, vote_page_url)


def _decode(url: str) -> dict:
    tok = url.split("cv1gzb-", 1)[1].translate(str.maketrans("_-", "/+"))
    tok += "=" * (-len(tok) % 4)
    return json.loads(gzip.decompress(base64.b64decode(tok)).decode("utf-8"))


def _opens(page: str, record_id: str) -> dict:
    return {"page": page, "hydration": {"open": {"id": record_id}}}


# Each verified by hand in a browser (2026-10-02) to open the right record's sheet.
@pytest.mark.parametrize("build, record_id, host, page", [
    (vote_page_url, "bf782497-f6f2-4248-8346-3725f4d5e29f",
     "https://www.parlament.hu/web/guest/szavazasok",
     "szavazasokexportok/exported-szavazas-adatlap-page/exported-szavazas-adatlap-page"),
    (vote_page_url, "83296",  # cycle 41: pre-2026 ids are plain integers
     "https://www.parlament.hu/web/guest/szavazasok",
     "szavazasokexportok/exported-szavazas-adatlap-page/exported-szavazas-adatlap-page"),
    (bill_page_url, "988b700c-0acb-4573-a15d-a8fe96bae550",
     "https://www.parlament.hu/web/guest/iromanyok",
     "iromanyexportok/iromany-adatlap-with-contract/iromany-adatlap-with-contract"),
    (bill_page_url, "2353650",  # T/12663, cycle 34
     "https://www.parlament.hu/web/guest/iromanyok",
     "iromanyexportok/iromany-adatlap-with-contract/iromany-adatlap-with-contract"),
    (person_page_url, "a011",
     "https://www.parlament.hu/web/guest/kepviselok",
     "kepviseloexportok/kepviselo-adatlap-with-contract/kepviselo-adatlap-with-contract"),
    (person_page_url, "004O",  # a nationality advocate
     "https://www.parlament.hu/web/guest/kepviselok",
     "kepviseloexportok/kepviselo-adatlap-with-contract/kepviselo-adatlap-with-contract"),
    (committee_page_url, "f80042de-578f-4b7c-8a73-a92574a47414",
     "https://www.parlament.hu/web/guest/bizottsagok1",
     "bizottsagexportok/exported-bizottsag-adatlap/exported-bizottsag-adatlap"),
    (committee_page_url, "101145",  # cycle 40
     "https://www.parlament.hu/web/guest/bizottsagok1",
     "bizottsagexportok/exported-bizottsag-adatlap/exported-bizottsag-adatlap"),
    (speech_page_url, "2952408",
     "https://www.parlament.hu/ulesnapok-ulesidok",
     "plenarisulesexportok/ulesnap-felszolalas-adata-with-contract/"
     "ulesnap-felszolalas-adata-with-contract"),
])
def test_adatlap_url_opens_the_record(build, record_id, host, page):
    url = build(record_id)
    assert url.startswith(f"{host}#page=cv1gzb-")
    assert _decode(url) == _opens(page, record_id)


def test_adatlap_url_is_stable():
    # The bill's link is stored in the DB, so a reload must not churn it.
    assert bill_page_url("2353650") == bill_page_url("2353650")
    assert "=" not in bill_page_url("2353650").split("#page=", 1)[1]


def test_adatlap_url_accepts_int_ids():
    assert _decode(vote_page_url(83296)) == _decode(vote_page_url("83296"))


@pytest.mark.parametrize("build, bad", [
    (vote_page_url, None), (vote_page_url, ""), (vote_page_url, "v-1"),
    (bill_page_url, "T/174"),          # a bill number, not an id
    (bill_page_url, "bill-uuid-1"),    # fixture-style placeholder id
    (bill_page_url, "35-04754"),       # the 1994-98 static archive: no adatlap
    (person_page_url, None), (person_page_url, "Kovács Béla"),
    (committee_page_url, "TAB"),
    (speech_page_url, "43031-4"),      # our own speech uid, not upstream's
])
def test_adatlap_url_requires_an_upstream_id(build, bad):
    assert build(bad) is None
