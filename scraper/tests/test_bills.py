"""Offline tests for the Felicitas iromány detail sheet (`bill_detail`) and the
incremental detail cache of `parlamonitor.bills.scrape.fetch_bills`.

In October 2026 parlament.hu moved the adatlap sub-queries from ``pOnalId`` to
``pId`` (they answer the old key with zero rows) and retired the per-bill motions
query, whose last answers were every dependent iromány since 1990 in one page.
These pin the current wire contract, and the guards that keep such an answer
out of the registry.
"""

from __future__ import annotations

import json

import pytest

from parlamonitor import felicitas as fel
from parlamonitor.bills.scrape import fetch_bills
from parlamonitor.http_client import HttpError

BILL = "c8f74f91-75bf-4cfd-af51-230fa037c7e2"
OTHER = "0d1a0b8e-0000-4000-8000-000000000001"


def _sub(fields: list[str], rows: list[list]) -> dict:
    """A nested Felicitas sub-table, as the API embeds it in a row."""
    return {"rows": rows,
            "metadata": {"fieldnames": {f: i for i, f in enumerate(fields)}}}


def _motion(number: str, sort: int, parent: str = BILL) -> dict:
    return {
        "modositoId": f"m-{number}",
        "onalloIromany": _sub(["iromanyId", "iromanyCim", "iromanyszam"],
                              [[parent, "A pénzmosás megelőzéséről", "T/816"]]),
        "iromanyszam": number,
        "iromanyszamSorrendhez": sort,
        "fotipus": "egyéb",
        "tipus": "Sürgősségi javaslat",
        "benyujtasDatuma": "2026-10-12T06:42:27.872Z",
        "benyujto": _sub(["id", "kepviseloId", "kepviseloFrakcioId", "bizottsagId",
                          "csatlakozasTol", "csatlakozasIg", "benyujto"],
                         [["b1", None, None, None, None, None, "kormány"]]),
        "iromanyszoveg": _sub(["iromanyszovegCaption", "iromanyszovegLink",
                               "iromanyszovegCim"],
                              [["szöveges PDF", f"/irom43/00816/{sort}.pdf", None]]),
        "nincsSzoveg": False,
        "vanSzavazas": False,
        "megjegyzes": None,
    }


class FakeHttp:
    """Answers each query by name; records the body every query was sent."""

    def __init__(self, motions: list[dict]):
        self.motions = motions
        self.bodies: dict[str, dict] = {}

    def polite_sleep(self):
        pass

    @staticmethod
    def _page(rows: list[dict]) -> dict:
        fields = sorted({k for r in rows for k in r})
        return {"metadata": {"fieldnames": {f: i for i, f in enumerate(fields)}},
                "rows": [[r.get(f) for f in fields] for r in rows],
                "response": {"totalSize": len(rows), "pageSize": 1000}}

    def post_json(self, url, body, headers=None):
        query = url.split("/")[-1].split("?")[0]
        self.bodies[query] = body
        if query == fel.NEM_ONALLO_LIST_QUERY:
            return self._page(self.motions)
        if query == "iromany-esemenyek-lista-query":
            return self._page([{"esemenyIdeje": "2026-10-09", "esemenyfajtaNev":
                                "benyújtva"}])
        return self._page([])


def test_adatlap_sub_queries_are_keyed_by_pid():
    """``pOnalId`` is silently ignored by them now: zero rows, not an error."""
    http = FakeHttp([])
    detail = fel.FelicitasClient(http).bill_detail(BILL)

    for query in ("iromany-esemenyek-lista-query",
                  "iromany-bizottsagi-esemenyek-lista-query",
                  "iromany-szavazasai-lista-query",
                  "iromany-hataridok-lista-query",
                  "iromanyt-targyalo-bizottsagok-lista-query",
                  "indoklasok-lista-query",
                  "iromanyhoz-kapcsolodo-hatteranyag-lista-query",
                  "nemonallo-inditvany-osszesito-lista-query"):
        assert http.bodies[query] == {"pId": BILL}, query
    assert detail["events"][0]["name"] == "benyújtva"


def test_motions_come_from_the_list_query_keyed_by_the_bill():
    http = FakeHttp([_motion("816/2", 816002), _motion("816/1", 816001)])
    detail = fel.FelicitasClient(http).bill_detail(BILL)

    assert http.bodies[fel.NEM_ONALLO_LIST_QUERY] == {"pOnalId": BILL}
    assert "nem-onallo-iromany-for-onallo-iromany-query" not in http.bodies
    # Oldest first, as the retired query listed them (bill_motion.ord).
    assert [m["billNumber"] for m in detail["motions"]] == ["816/1", "816/2"]
    first = detail["motions"][0]
    assert first["iromanyId"] == "m-816/1"
    assert first["textUrl"] == "https://www.parlament.hu/irom43/00816/816001.pdf"
    assert first["sponsors"] == [{"personID": None, "factionId": None,
                                  "committeeId": None, "label": "kormány"}]


def test_a_motions_list_that_ignores_the_bill_is_refused():
    """The October 2026 failure: every dependent iromány on record, whatever
    bill was asked for. Raising (→ detail None, refetched next pass) keeps it out
    of the registry and the DB."""
    http = FakeHttp([_motion("816/1", 816001), _motion("20405/1", 20405001, OTHER)])
    with pytest.raises(HttpError, match="ignored the bill filter"):
        fel.FelicitasClient(http).bill_detail(BILL)


# --- the incremental cache --------------------------------------------------

def _row(bill_id: str, number: str) -> dict:
    return {"billId": bill_id, "billNumber": number, "billNumberSort": 1,
            "status": "tárgysorozatba vették", "stages": [],
            "submittedDate": "2026-10-09", "sponsors": []}


class FakeClient:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.fetched: list[str] = []

    def bills(self, cycle, main_type=""):
        return [dict(r) for r in self.rows]

    def bill_detail(self, bill_id):
        self.fetched.append(bill_id)
        return {"events": [{"name": "fresh"}], "motions": []}


def test_a_cached_detail_with_another_bills_motions_is_refetched(tmp_path):
    """Its list row is unchanged, so the fingerprint alone would keep reusing
    the poisoned detail forever."""
    rows = [_row("b-816", "T/816"), _row("b-817", "T/817")]
    cached = {"meta": {}, "data": [
        {**rows[0], "detail": {"events": [], "motions": [
            {"billNumber": "816/1"}, {"billNumber": "20405/1"}]}},
        {**rows[1], "detail": {"events": [{"name": "kept"}], "motions": [
            {"billNumber": "817/1"}, {"billNumber": None}]}},
    ]}
    path = tmp_path / "bills-43.json"
    path.write_text(json.dumps(cached))
    client = FakeClient(rows)

    registry = fetch_bills(client, 43, cache_path=path)

    assert client.fetched == ["b-816"]
    details = {r["billId"]: r["detail"] for r in registry["data"]}
    assert details["b-816"]["events"] == [{"name": "fresh"}]
    assert details["b-817"]["events"] == [{"name": "kept"}]
