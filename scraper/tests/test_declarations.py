"""Offline tests for the EVNYR asset-declaration stage (parlamonitor/declarations).

Focus: the CSV's shape, which is the thing easiest to get quietly wrong. The
export zips a declaration's independent lists side by side, so a parser that
reads it row by row ("row 0 = one property, its car, its bank account") produces
records that look plausible and are false. The header below is the export's
real one, verbatim (2026-10-01 snapshot); the rows are invented.

Nothing here touches the network.
"""

from __future__ import annotations

import csv
import io
import json

from parlamonitor import sync
from parlamonitor.config import Paths
from parlamonitor.declarations import evnyr, scrape
from parlamonitor.http_client import CappedFetch

HEADER = [
    "id", "nev", "szervezet", "tisztseg", "nyilatkozatTipusa", "veglegesites",
    "schemaVersion", "ingatlanok.telepules", "ingatlanok.teruletNagysag",
    "ingatlanok.muvelesiAg", "ingatlanok.epuletJelleg", "ingatlanok.alapterulet",
    "ingatlanok.jogiJelleg", "ingatlanok.jogallas", "ingatlanok.tulajdoniHanyad",
    "ingatlanok.szerzesJogcime", "ingatlanok.szerzesEllenerteke",
    "ingatlanok.jogviszonyKezdete",
    "nagyErtekuIngosagok.szemelygepjarmuvek.jarmuTipusa",
    "nagyErtekuIngosagok.szemelygepjarmuvek.szerzesIdeje",
    "nagyErtekuIngosagok.szemelygepjarmuvek.szerzesJogcime",
    "nagyErtekuIngosagok.tehergepjarmuvek.jarmuTipusa",
    "nagyErtekuIngosagok.tehergepjarmuvek.szerzesIdeje",
    "nagyErtekuIngosagok.tehergepjarmuvek.szerzesJogcime",
    "nagyErtekuIngosagok.motorkerekparok.jarmuTipusa",
    "nagyErtekuIngosagok.motorkerekparok.szerzesIdeje",
    "nagyErtekuIngosagok.motorkerekparok.szerzesJogcime",
    "nagyErtekuIngosagok.viziLegiJarmuvek.jarmuJellege",
    "nagyErtekuIngosagok.viziLegiJarmuvek.jarmuTipusa",
    "nagyErtekuIngosagok.viziLegiJarmuvek.szerzesIdeje",
    "nagyErtekuIngosagok.viziLegiJarmuvek.szerzesJogcime",
    "nagyErtekuIngosagok.vedettMualkotasok.megnevezes",
    "nagyErtekuIngosagok.vedettMualkotasok.szerzesIdeje",
    "nagyErtekuIngosagok.vedettMualkotasok.szerzesJogcime",
    "nagyErtekuIngosagok.vedettGyujtemenyek.megnevezes",
    "nagyErtekuIngosagok.vedettGyujtemenyek.szerzesIdeje",
    "nagyErtekuIngosagok.vedettGyujtemenyek.szerzesJogcime",
    "nagyErtekuIngosagok.egyebIngosagok.megnevezes",
    "nagyErtekuIngosagok.egyebIngosagok.szerzesIdeje",
    "nagyErtekuIngosagok.egyebIngosagok.szerzesJogcime",
    "kovetelesek.ertekpapirok.megnevezes", "kovetelesek.ertekpapirok.isin",
    "kovetelesek.ertekpapirok.nevErtek", "kovetelesek.ertekpapirok.atvaltasiArfolyam",
    "kovetelesek.szamlakovetelesek.megnevezes",
    "kovetelesek.szamlakovetelesek.swiftBic", "kovetelesek.szamlakovetelesek.osszeg",
    "kovetelesek.szamlakovetelesek.atvaltasiArfolyam", "kovetelesek.keszpenz.osszeg",
    "kovetelesek.keszpenz.atvaltasiArfolyam",
    "kovetelesek.szerzodesesKovetelesek.megnevezes",
    "kovetelesek.szerzodesesKovetelesek.osszeg",
    "kovetelesek.szerzodesesKovetelesek.atvaltasiArfolyam",
    "kovetelesek.szerzodesesKovetelesek.szerzodesIdeje",
    "tartozasok.koztartozasok.jelleg", "tartozasok.koztartozasok.osszeg",
    "tartozasok.koztartozasok.atvaltasiArfolyam",
    "tartozasok.hitelintezetiTartozasok.osszeg",
    "tartozasok.hitelintezetiTartozasok.atvaltasiArfolyam",
    "tartozasok.maganszemelyiTartozasok.osszeg",
    "tartozasok.maganszemelyiTartozasok.atvaltasiArfolyam", "egyebKozlendok",
    "protokollAjandekok.megnevezes", "protokollAjandekok.ajandekozoNeve",
    "jovedelemnyilatkozat.korabbiFoglalkozasok.megnevezes",
    "jovedelemnyilatkozat.korabbiFoglalkozasok.reszesultDijazasban",
    "jovedelemnyilatkozat.korabbiFoglalkozasok.haviJovedelem",
    "jovedelemnyilatkozat.korabbiFoglalkozasok.atvaltasiArfolyam",
    "jovedelemnyilatkozat.jelenlegiTevekenysegek.megnevezes",
    "jovedelemnyilatkozat.jelenlegiTevekenysegek.kifizetoSzemelye",
    "jovedelemnyilatkozat.jelenlegiTevekenysegek.reszesulDijazasban",
    "jovedelemnyilatkozat.jelenlegiTevekenysegek.haviJovedelem",
    "jovedelemnyilatkozat.jelenlegiTevekenysegek.atvaltasiArfolyam",
    "gazdasagiErdekeltseg.tagsagok.szervezetNeve",
    "gazdasagiErdekeltseg.tagsagok.tagsag",
    "gazdasagiErdekeltseg.tagsagok.reszesulDijazasban",
    "gazdasagiErdekeltseg.tagsagok.haviJovedelem",
    "gazdasagiErdekeltseg.tagsagok.atvaltasiArfolyam",
    "gazdasagiErdekeltseg.tarsasagiErdekeltsegek.tarsasagNeve",
    "gazdasagiErdekeltseg.tarsasagiErdekeltsegek.erdekeltsegFormaja",
    "gazdasagiErdekeltseg.tarsasagiErdekeltsegek.tulajdoniArany",
    "gazdasagiErdekeltseg.tarsasagiErdekeltsegek.reszesulDijazasban",
    "gazdasagiErdekeltseg.tarsasagiErdekeltsegek.haviJovedelem",
    "gazdasagiErdekeltseg.tarsasagiErdekeltsegek.atvaltasiArfolyam",
    "gazdasagiErdekeltseg.egyebErdekek.leiras",
    "gazdasagiErdekeltseg.nyilatkozattetelHelye",
]

DID = "01a094a2-46a7-7682-902d-246b1106cd51"
OTHER = "f3c1d2e4-0000-4000-8000-00000000abcd"
DECOY = "9e9e9e9e-0000-4000-8000-000000000000"
HEAD = {"id": DID, "nev": "DR. MINTA ISTVÁN", "szervezet": "Országgyűlés Hivatala",
        "tisztseg": "Országgyűlési képviselő", "nyilatkozatTipusa": "Nyitó",
        "veglegesites": "2026-09-20T11:48:04", "schemaVersion": "1"}


def _csv(rows: list[dict]) -> str:
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=HEADER, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return "\ufeff" + out.getvalue()


# Three properties, one car, two cash entries, one text field: three rows, the
# car and the cash riding along in the first rows of the properties.
ROWS = [
    {**HEAD, "ingatlanok.telepules": "Budapest XVIII. kerület ",
     "ingatlanok.alapterulet": "292m2", "ingatlanok.tulajdoniHanyad": "28/100",
     "nagyErtekuIngosagok.szemelygepjarmuvek.jarmuTipusa": "VW Golf",
     "nagyErtekuIngosagok.szemelygepjarmuvek.szerzesIdeje": "2015-11-18",
     "kovetelesek.keszpenz.osszeg": "34632 ",
     "kovetelesek.keszpenz.atvaltasiArfolyam": "1 EUR=364,55",
     "egyebKozlendok": "Egy sor,\nmásik sor.",
     "gazdasagiErdekeltseg.tagsagok.szervezetNeve": "Tőzsdei rt.",
     "gazdasagiErdekeltseg.nyilatkozattetelHelye": "Budapest"},
    {**HEAD, "ingatlanok.telepules": "Budapest IV. kerület",
     "ingatlanok.tulajdoniHanyad": "1/2", "kovetelesek.keszpenz.osszeg": "31128"},
    {**HEAD, "ingatlanok.telepules": "Budapest XIII. kerület"},
    {**HEAD, "id": OTHER, "nev": "KISS ÉVA", "nyilatkozatTipusa": "Éves",
     "veglegesites": "2026-09-25T08:00:00"},
]

HOME = ('<a href="/media/napi/csv-snapshot/vagyonnyilatkozatok_napi_adatok_'
        '2026-09-30_01-00.csv">old</a> ... "href":"/media/napi/csv-snapshot/'
        'vagyonnyilatkozatok_napi_adatok_2026-10-01_01-00.csv"')


def _page(did: str, stamp: str = "20260929") -> str:
    # A link to some other declaration's PDF first, which must not be taken.
    return (f'<a href="/media/nyilatkozat/9/e/9/{DECOY}/x.pdf">x</a>'
            f'<dap-ds-button variant="subtle" href="/media/nyilatkozat/'
            f'{did[0]}/{did[1]}/{did[2]}/{did}/vagyonnyilatkozat_{stamp}_minta.pdf">')


# --- the parser ---------------------------------------------------------------

def test_lists_are_read_down_their_own_columns():
    decl = next(d for d in evnyr.parse_csv(_csv(ROWS)) if d["id"] == DID)
    c = decl["content"]
    assert [p["telepules"] for p in c["ingatlanok"]] == [
        "Budapest XVIII. kerület", "Budapest IV. kerület", "Budapest XIII. kerület"]
    # The car sits in row 0 next to the first property, and is not part of it.
    assert "jarmuTipusa" not in c["ingatlanok"][0]
    assert c["nagyErtekuIngosagok.szemelygepjarmuvek"] == [
        {"jarmuTipusa": "VW Golf", "szerzesIdeje": "2015-11-18"}]
    assert [x["osszeg"] for x in c["kovetelesek.keszpenz"]] == ["34632", "31128"]
    # Empty lists are left out rather than served as [].
    assert "tartozasok.koztartozasok" not in c


def test_header_fields_and_single_values():
    decls = evnyr.parse_csv(_csv(ROWS))
    assert [d["id"] for d in decls] == [DID, OTHER]
    d = decls[0]
    assert (d["name"], d["type"], d["finalizedAt"], d["schemaVersion"]) == (
        "DR. MINTA ISTVÁN", "Nyitó", "2026-09-20T11:48:04", "1")
    assert d["modifiedAt"] is None
    # A field of a section that also holds lists is one value, not a list...
    assert d["content"]["gazdasagiErdekeltseg.nyilatkozattetelHelye"] == "Budapest"
    # ...while the section's lists stay lists.
    assert d["content"]["gazdasagiErdekeltseg.tagsagok"] == [
        {"szervezetNeve": "Tőzsdei rt."}]
    # Multi-line free text survives the CSV quoting.
    assert d["content"]["egyebKozlendok"] == "Egy sor,\nmásik sor."
    # A declaration with nothing filled in has empty content, not a crash.
    assert decls[1]["content"] == {}


def test_a_file_without_ids_is_refused():
    try:
        evnyr.parse_csv("nev,szervezet\nA,B\n")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_the_newest_snapshot_link_and_its_time():
    url = evnyr.snapshot_url(HOME)
    assert url == (evnyr.HOST + "/media/napi/csv-snapshot/"
                   "vagyonnyilatkozatok_napi_adatok_2026-10-01_01-00.csv")
    assert evnyr.snapshot_time(url) == "2026-10-01T01:00"
    assert evnyr.snapshot_url("<html>nothing here</html>") is None


def test_the_pdf_link_is_the_declarations_own():
    assert evnyr.pdf_url(_page(DID), DID) == (
        f"{evnyr.HOST}/media/nyilatkozat/0/1/a/{DID}/vagyonnyilatkozat_20260929_minta.pdf")
    assert evnyr.pdf_url("<html>no button</html>", DID) is None


# --- fetching -------------------------------------------------------------------

class FakeHttp:
    """Stands in for HttpClient: a home page, a CSV, declaration pages."""

    def __init__(self, home=HOME, rows=ROWS):
        self.home = home
        self.csv = _csv(rows).encode("utf-8")
        self.pages = {DID: _page(DID), OTHER: _page(OTHER)}
        self.calls: list[str] = []

    def get_text(self, url, **kw):
        self.calls.append(url)
        if url == evnyr.HOME_URL:
            return self.home
        did = url.rsplit("/", 1)[-1]
        return self.pages.get(did, "<html>404</html>")

    def get_capped(self, url, *, max_bytes=0, headers=None):
        self.calls.append(url)
        return CappedFetch(data=self.csv, content_type="text/csv", status=200,
                           over_cap=False)

    def polite_sleep(self):
        pass


def test_first_run_reads_the_csv_and_every_page():
    http = FakeHttp()
    reg = scrape.fetch_declarations(http)
    assert len(http.calls) == 4          # home, csv, two pages
    assert reg["meta"]["count"] == 2 and reg["meta"]["snapshotAt"] == "2026-10-01T01:00"
    # Newest first.
    assert [d["id"] for d in reg["data"]] == [OTHER, DID]
    d = next(d for d in reg["data"] if d["id"] == DID)
    assert d["url"] == f"{evnyr.HOST}/nyilatkozat/{DID}"
    assert d["pdfUrl"].endswith("vagyonnyilatkozat_20260929_minta.pdf")


def test_an_idle_poll_is_one_request():
    first = scrape.fetch_declarations(FakeHttp())
    http = FakeHttp()
    again = scrape.fetch_declarations(http, previous=first)
    assert http.calls == [evnyr.HOME_URL]
    assert again["data"] == first["data"] and again["meta"]["csvReused"] is True


def test_a_new_snapshot_rereads_only_the_changed_declaration():
    first = scrape.fetch_declarations(FakeHttp())
    rows = [dict(r) for r in ROWS]
    rows[3]["veglegesites"] = "2026-09-30T10:00:00"   # KISS ÉVA corrected hers
    http = FakeHttp(home=HOME.replace("2026-10-01", "2026-10-02"), rows=rows)
    http.pages[OTHER] = _page(OTHER, stamp="20261001")
    reg = scrape.fetch_declarations(http, previous=first)
    assert [c for c in http.calls if "/nyilatkozat/" in c] == [
        f"{evnyr.HOST}/nyilatkozat/{OTHER}"]
    other = next(d for d in reg["data"] if d["id"] == OTHER)
    assert other["pdfUrl"].endswith("vagyonnyilatkozat_20261001_minta.pdf")


def test_the_page_limit_leaves_the_rest_for_later():
    http = FakeHttp()
    reg = scrape.fetch_declarations(http, page_limit=1)
    assert reg["meta"]["pagesFetched"] == 1 and reg["meta"]["pdfPending"] == 1
    pending = [d for d in reg["data"] if d["pdfCheckedAt"] is None]
    assert len(pending) == 1 and pending[0]["pdfUrl"] is None
    # The next run reads only the page it skipped.
    http = FakeHttp()
    again = scrape.fetch_declarations(http, previous=reg, page_limit=1)
    assert len([c for c in http.calls if "/nyilatkozat/" in c]) == 1
    assert all(d["pdfUrl"] for d in again["data"])


def test_a_page_without_a_pdf_is_not_asked_about_on_every_poll():
    http = FakeHttp()
    http.pages[OTHER] = "<html>no button yet</html>"
    first = scrape.fetch_declarations(http)
    assert first["meta"]["pdfMissing"] == 1
    http = FakeHttp()
    scrape.fetch_declarations(http, previous=first)        # same snapshot
    assert http.calls == [evnyr.HOME_URL]
    http = FakeHttp(home=HOME.replace("2026-10-01", "2026-10-02"))
    scrape.fetch_declarations(http, previous=first)        # next day: ask again
    assert f"{evnyr.HOST}/nyilatkozat/{OTHER}" in http.calls


def test_a_home_page_without_a_snapshot_fails_loudly():
    try:
        scrape.fetch_declarations(FakeHttp(home="<html>redesigned</html>"))
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError")


# --- the sync step --------------------------------------------------------------

def test_sync_skips_a_same_content_snapshot_without_rewriting(tmp_path):
    """A new day's snapshot with the same contents leaves the file alone (the
    loader would copy the DB to reload it), yet is not downloaded again."""
    paths = Paths(tmp_path)
    paths.ensure()
    state: dict = {}
    assert sync._sync_asset_declarations(paths, FakeHttp(), state, force=False) is True
    written = paths.asset_declarations_file().stat().st_mtime_ns

    state["assetDeclarations"]["ts"] = 0           # cadence elapsed
    next_day = HOME.replace("2026-10-01", "2026-10-02")
    http = FakeHttp(home=next_day)
    assert sync._sync_asset_declarations(paths, http, state, force=False) is False
    assert paths.asset_declarations_file().stat().st_mtime_ns == written
    assert state["assetDeclarations"]["csvUrl"].endswith("2026-10-02_01-00.csv")

    state["assetDeclarations"]["ts"] = 0
    http = FakeHttp(home=next_day)
    assert sync._sync_asset_declarations(paths, http, state, force=False) is False
    assert http.calls == [evnyr.HOME_URL]


def test_sync_respects_its_cadence_but_drains_a_backlog(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    paths.ensure()
    state: dict = {}
    monkeypatch.setattr(sync, "DECLARATION_PAGES_PER_SYNC", 1)
    assert sync._sync_asset_declarations(paths, FakeHttp(), state, force=False) is True
    assert state["assetDeclarations"]["pending"] == 1
    # Within the cadence, but a page is still unread: the pass runs anyway.
    http = FakeHttp()
    assert sync._sync_asset_declarations(paths, http, state, force=False) is True
    assert state["assetDeclarations"]["pending"] == 0
    # Nothing left: within the cadence, nothing is asked at all.
    http = FakeHttp()
    assert sync._sync_asset_declarations(paths, http, state, force=False) is False
    assert http.calls == []


def test_an_empty_snapshot_never_replaces_a_full_one(tmp_path):
    paths = Paths(tmp_path)
    paths.ensure()
    state: dict = {}
    sync._sync_asset_declarations(paths, FakeHttp(), state, force=False)
    state["assetDeclarations"]["ts"] = 0
    http = FakeHttp(home=HOME.replace("2026-10-01", "2026-10-02"), rows=[])
    assert sync._sync_asset_declarations(paths, http, state, force=False) is False
    kept = json.loads(paths.asset_declarations_file().read_text(encoding="utf-8"))
    assert kept["meta"]["count"] == 2
