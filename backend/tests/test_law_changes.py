"""What a bill changes (BILL-14..16): loader + API contract.

The fixture is one processed `law-changes-43.json` record for the conftest's
T/100 (`bill-uuid-1`), shaped like the scraper's output: one law with two
changed provisions, an instruction that could not be applied, the bill's
evolution into its egységes javaslat, and its módosító javaslat T/100/3 as
points.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import zlib

import pytest

from app import lawtext, loader
from app.textdiff import word_diff


def _law_changes_registry():
    return {
        "meta": {"cycle": 43, "source": "parlamonitor-lawdiff",
                 "scrapedAt": "2026-10-14T00:00:00+00:00"},
        "data": [{
            "billId": "bill-uuid-1", "billNumber": "T/100",
            "computedAt": "2026-10-14T00:00:00+00:00", "final": False,
            "source": {"number": "T/100", "url": "https://www.parlament.hu/irom43/00100/00100.pdf",
                       "kind": "bill"},
            "scan": False, "act": None,
            "laws": [{
                "docId": "2019-80-00-00", "title": "2019. évi LXXX. törvény a szakképzésről",
                "abbr": "Szkt.", "url": "https://njt.hu/jogszabaly/2019-80-00-00",
                "method": "computed",
                "base": {"version": 33, "from": "2027-01-01", "to": None}, "target": None,
                "changes": [
                    {"jh": "SZ26@BE4", "ref": "26. § (4) bekezdés", "provisions": ["1. §"],
                     "ops": ["replace"],
                     "before": "(4) A főigazgató felel a centrum működéséért.",
                     "after": "(4) A főigazgató felel a centrum törvényes működéséért.",
                     "url": "https://njt.hu/jogszabaly/2019-80-00-00.33#SZ26@BE4",
                     "afterUnits": [["SZ26@BE4", "bekezdesNyito", "(4)",
                                     "A főigazgató felel a centrum törvényes működéséért."]]},
                    {"jh": "SZ26@BE4A", "ref": "26. § (4a) bekezdés", "provisions": ["2. §"],
                     "ops": ["insert"], "before": "",
                     "after": "(4a) A helyettest a miniszter bízza meg.",
                     "url": "https://njt.hu/jogszabaly/2019-80-00-00.33#SZ26@BE4A",
                     "afterUnits": [["SZ26@BE4A", "bekezdesNyito", "(4a)",
                                     "A helyettest a miniszter bízza meg."]],
                     "anchor": "SZ26@BE4"},
                ]}],
            "unresolved": [{"provision": "3. §", "docId": "2019-80-00-00",
                            "reason": "melléklet",
                            "text": "Az Szkt. 1. melléklete helyébe az 1. melléklet lép."}],
            "evolution": {"from": {"number": "T/100", "url": "u1"},
                          "to": {"number": "T/100/9", "url": "u2", "type": "Egységes javaslat"},
                          "provisions": [{"key": "2. §", "before": "Hatályba lép 2026. szeptember 1-jén.",
                                          "after": "Hatályba lép a kihirdetését követő napon."},
                                         # renumbered by a § the consolidation inserted
                                         {"key": "4. §", "from": "3. §", "before": "Régi.",
                                          "after": "Új."}],
                          "unchanged": 3},
            "amendments": [{"number": "T/100/3", "type": "Módosító javaslat",
                            "url": "https://www.parlament.hu/irom43/00100/00100-0003.pdf",
                            "scan": False, "marked": True, "error": None,
                            "points": [{"n": "1", "target": "7. §", "kind": "módosítás",
                                        "segments": [["eq", "Ez a törvény 2026. "],
                                                     ["del", "szeptember"], ["ins", "október"],
                                                     ["eq", " 1-jén lép hatályba."]]}]}],
            "stats": {"instructions": 3, "unparsed": 1, "changes": 2, "unresolved": 1},
        }],
    }


# The njt state the fixture's changes were read against (2019-80-00-00, v33).
_BASE_UNITS = [
    ["SZ26", "szakasz", "26. §", ""],
    ["SZ26@BE3", "bekezdesNyito", "(3)", "A centrumot a főigazgató vezeti."],
    ["SZ26@BE4", "bekezdesNyito", "(4)", "A főigazgató felel a centrum működéséért."],
    ["SZ26@BE5", "bekezdesNyito", "(5)", "A kancellár a gazdálkodásért felel."],
]


def _write_law_texts(processed, states=(("2019-80-00-00", 33),)):
    with gzip.open(processed / "law-texts-43.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"meta": {"cycle": 43, "fingerprint": "x"}}) + "\n")
        for doc, ver in states:
            fh.write(json.dumps({"docId": doc, "version": ver,
                                 "title": "2019. évi LXXX. törvény a szakképzésről",
                                 "units": _BASE_UNITS}, ensure_ascii=False) + "\n")


@pytest.fixture
def lc_data_dir(data_dir):
    (data_dir / "processed" / "law-changes-43.json").write_text(
        json.dumps(_law_changes_registry(), ensure_ascii=False))
    _write_law_texts(data_dir / "processed")
    return data_dir


@pytest.fixture
def lc_db(tmp_path, lc_data_dir):
    out = tmp_path / "lc.db"
    loader.build_database(lc_data_dir, out)
    return out


@pytest.fixture
def lc_client(lc_db, monkeypatch):
    from app import db as db_module
    from app.config import settings
    monkeypatch.setattr(settings, "db_path", str(lc_db))
    monkeypatch.setattr(db_module.settings, "db_path", str(lc_db))
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def _q(db, sql, *args):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


# --- loader ---------------------------------------------------------------------

def test_loader_writes_every_part(lc_db):
    assert _q(lc_db, "SELECT source_number, source_kind FROM law_change_bill") == [("T/100", "bill")]
    assert _q(lc_db, "SELECT doc_id, abbr, method FROM law_change_law") == [
        ("2019-80-00-00", "Szkt.", "computed")]
    assert [r[0] for r in _q(lc_db, "SELECT ref FROM law_change ORDER BY ord")] == [
        "26. § (4) bekezdés", "26. § (4a) bekezdés"]
    assert _q(lc_db, "SELECT reason FROM law_change_unresolved") == [("melléklet",)]
    assert _q(lc_db, "SELECT provision, provision_from FROM bill_evolution ORDER BY ord") == [
        ("2. §", None), ("4. §", "3. §")]
    assert _q(lc_db, "SELECT COUNT(*) FROM amendment_point")[0][0] == 1


def test_reload_replaces_the_cycle(lc_db, lc_data_dir):
    c = loader.connect(lc_db)
    reg = _law_changes_registry()
    reg["data"][0]["laws"][0]["changes"] = reg["data"][0]["laws"][0]["changes"][:1]
    loader.load_law_changes(c, reg)
    loader.load_law_changes(c, reg)
    c.close()
    assert _q(lc_db, "SELECT COUNT(*) FROM law_change")[0][0] == 1
    assert _q(lc_db, "SELECT COUNT(*) FROM law_change_law")[0][0] == 1
    assert _q(lc_db, "SELECT COUNT(*) FROM amendment_doc")[0][0] == 1


def test_bills_reload_leaves_law_changes_alone(lc_db):
    """No foreign key ties them to `bill`, so a bills refresh neither fails on
    them nor wipes them (they reload on their own file's schedule)."""
    try:
        from tests.conftest import _bills_registry
    except ImportError:
        from conftest import _bills_registry
    c = loader.connect(lc_db)
    loader.load_bills(c, _bills_registry())
    c.close()
    assert _q(lc_db, "SELECT COUNT(*) FROM law_change")[0][0] == 2


def test_update_path_lands_the_new_file(db_path, data_dir):
    """A DB built before the feature gets it through `--update` alone: the new
    glob is unseen in load_state, and the tables are created in place."""
    (data_dir / "processed" / "law-changes-43.json").write_text(
        json.dumps(_law_changes_registry(), ensure_ascii=False))
    _write_law_texts(data_dir / "processed")
    assert loader.update_database(data_dir, db_path)
    assert _q(db_path, "SELECT COUNT(*) FROM law_change")[0][0] == 2
    assert _q(db_path, "SELECT doc_id, version FROM law_text") == [("2019-80-00-00", 33)]


def test_law_texts_are_stored_compressed_and_replaced_per_cycle(lc_db, lc_data_dir):
    (title, blob), = _q(lc_db, "SELECT title, units FROM law_text")
    assert title.startswith("2019. évi LXXX.")
    assert json.loads(zlib.decompress(blob)) == _BASE_UNITS
    _write_law_texts(lc_data_dir / "processed", [("2019-80-00-00", 34)])
    c = loader.connect(lc_db)
    loader.load_law_texts(c, lc_data_dir / "processed" / "law-texts-43.jsonl.gz")
    c.close()
    assert _q(lc_db, "SELECT version FROM law_text") == [(34,)]


def test_a_db_from_before_the_full_view_gets_its_columns_in_place(lc_db):
    """The law-change tables shipped before `after_units`/`anchor`/`law_text`:
    an `--update` of such a DB adds them rather than failing on the insert."""
    c = loader.connect(lc_db)
    c.execute("DROP TABLE law_text")
    c.execute("ALTER TABLE law_change DROP COLUMN after_units")
    c.execute("ALTER TABLE law_change DROP COLUMN anchor")
    loader.load_law_changes(c, _law_changes_registry())
    c.close()
    assert _q(lc_db, "SELECT anchor FROM law_change ORDER BY ord") == [(None,), ("SZ26@BE4",)]
    assert _q(lc_db, "SELECT COUNT(*) FROM law_text")[0][0] == 0


# --- API -----------------------------------------------------------------------------

def test_bill_detail_carries_the_summary_and_motion_flag(lc_client):
    b = lc_client.get("/api/v1/bills/bill-uuid-1").json()
    s = b["law_changes"]
    assert s["laws"] == 1 and s["changes"] == 2 and s["unresolved"] == 1
    assert s["evolution"] == 2 and s["amendment_points"] == 1 and s["method"] == "computed"
    by_number = {m["bill_number"]: m for m in b["motions"]}
    assert by_number["T/100/3"]["amendment"] == {"points": 1, "scan": False}
    assert by_number["T/100/1"]["amendment"] is None


def test_bill_without_law_changes_says_so(lc_client):
    assert lc_client.get("/api/v1/bills/bill-uuid-2").json()["law_changes"] is None
    assert lc_client.get("/api/v1/bills/bill-uuid-2/changes").json() == {"available": False}
    assert lc_client.get("/api/v1/bills/nope/changes").status_code == 404


def test_changes_endpoint_serves_word_diffs(lc_client):
    r = lc_client.get("/api/v1/bills/bill-uuid-1/changes").json()
    assert r["available"] and r["source"]["kind"] == "bill" and r["act"] is None
    (law,) = r["laws"]
    assert law["count"] == 2 and not law["truncated"]
    first, second = law["changes"]
    assert first["kind"] == "modified" and first["provisions"] == ["1. §"]
    assert ["ins", "törvényes "] in first["diff"]
    assert second["kind"] == "new" and second["diff"] == [
        ["ins", "(4a) A helyettest a miniszter bízza meg."]]
    assert r["unresolved"][0]["reason"] == "melléklet"
    evo = r["evolution"]
    assert evo["to"]["number"] == "T/100/9" and evo["unchanged"] == 3
    assert any(op == "del" for op, _ in evo["provisions"][0]["diff"])
    assert [(p["provision"], p["from"]) for p in evo["provisions"]] == [("2. §", None), ("4. §", "3. §")]
    assert r["amendments"] == [{"number": "T/100/3", "type": "Módosító javaslat",
                                "url": "https://www.parlament.hu/irom43/00100/00100-0003.pdf",
                                "scan": False, "marked": True, "error": None, "points": 1}]


def test_changes_past_the_budget_come_back_on_request(lc_client, monkeypatch):
    from app.modules.bills import router
    monkeypatch.setattr(router, "_CHANGE_BUDGET", 1)
    reg = lc_client.get("/api/v1/bills/bill-uuid-1/changes").json()
    # the first law is always served whole, however long it is
    assert reg["laws"][0]["changes"] is not None
    one = lc_client.get("/api/v1/bills/bill-uuid-1/changes",
                        params={"law": "2019-80-00-00"}).json()
    assert one["laws"][0]["count"] == 2 and len(one["laws"][0]["changes"]) == 2
    assert "evolution" not in one


def test_amendment_endpoint(lc_client):
    r = lc_client.get("/api/v1/bills/bill-uuid-1/amendments", params={"number": "T/100/3"}).json()
    assert r["marked"] and r["points"][0]["target"] == "7. §"
    assert r["points"][0]["segments"][1] == ["del", "szeptember"]
    assert lc_client.get("/api/v1/bills/bill-uuid-1/amendments",
                         params={"number": "T/100/99"}).status_code == 404


def test_meta_feature_flag(lc_client):
    assert lc_client.get("/api/v1/meta").json()["features"]["law_changes"] is True


def test_meta_feature_flag_off_without_data(client):
    assert client.get("/api/v1/meta").json()["features"]["law_changes"] is False


# --- the diff ------------------------------------------------------------------------

def test_word_diff_rebuilds_both_sides():
    a = "(5) A kancellár felel a centrum működéséért. A vezető megbízására a kancellár jogosult."
    b = "(5) A vezető megbízására a főigazgató jogosult."
    ops = word_diff(a, b)
    assert "".join(t for op, t in ops if op != "ins") == a
    assert "".join(t for op, t in ops if op != "del") == b
    assert ["del", "kancellár"] in ops and ["ins", "főigazgató"] in ops


def test_word_diff_folds_confetti_into_the_change():
    """A rewritten clause shares only short function words with the old one;
    those islands are folded into the change instead of being shown as kept."""
    ops = word_diff("a főigazgató és a kancellár dönt", "az igazgató vagy a tanács határoz")
    kept = [t for op, t in ops if op == "eq"]
    assert kept == [] or all(len(t) > 12 for t in kept)


def test_evolution_head_and_the_rest_on_request(lc_client, monkeypatch):
    from app.modules.bills import router
    monkeypatch.setattr(router, "_EVOLUTION_HEAD", 0)
    head = lc_client.get("/api/v1/bills/bill-uuid-1/changes").json()["evolution"]
    # 0 = no limit in the helper; a real head is a positive number
    assert head["total"] == 2
    monkeypatch.setattr(router, "_EVOLUTION_HEAD", 1)
    head = lc_client.get("/api/v1/bills/bill-uuid-1/changes").json()["evolution"]
    assert head["total"] == 2 and len(head["provisions"]) == 1
    full = lc_client.get("/api/v1/bills/bill-uuid-1/changes",
                         params={"part": "evolution"}).json()["evolution"]
    assert full["total"] == 2 and len(full["provisions"]) == 2
    assert full["provisions"][0]["provision"] == "2. §"


# --- the full-law view -----------------------------------------------------------------

def test_full_text_flag_and_endpoint(lc_client):
    laws = lc_client.get("/api/v1/bills/bill-uuid-1/changes").json()["laws"]
    assert laws[0]["full_text"] is True
    r = lc_client.get("/api/v1/bills/bill-uuid-1/changes/full",
                      params={"law": "2019-80-00-00"}).json()
    assert r["available"] and r["law"]["base"]["version"] == 33
    assert [c["kind"] for c in r["changes"]] == ["modified", "new"]
    assert [b.get("unit", {}).get("jh") or ("change", b["change"]) for b in r["blocks"]] == [
        "SZ26", "SZ26@BE3", ("change", 0), ("change", 1), "SZ26@BE5"]
    (mod,) = r["blocks"][2]["units"]
    assert mod["op"] == "mod" and mod["label"] == "(4)" and ["ins", "törvényes "] in mod["diff"]
    (new,) = r["blocks"][3]["units"]
    assert new["op"] == "ins" and new["diff"] == [["ins", "A helyettest a miniszter bízza meg."]]


def test_full_view_unavailable_without_the_text(lc_client, lc_db):
    c = sqlite3.connect(lc_db)
    c.execute("DELETE FROM law_text")
    c.commit()
    c.close()
    assert lc_client.get("/api/v1/bills/bill-uuid-1/changes").json()["laws"][0]["full_text"] is False
    r = lc_client.get("/api/v1/bills/bill-uuid-1/changes/full", params={"law": "2019-80-00-00"})
    assert r.json()["available"] is False and r.json()["law"]["doc_id"] == "2019-80-00-00"


def test_full_view_unknown_law_or_bill(lc_client):
    full = "/api/v1/bills/{}/changes/full"
    assert lc_client.get(full.format("bill-uuid-1"), params={"law": "1999-1-00-00"}).status_code == 404
    assert lc_client.get(full.format("bill-uuid-2"), params={"law": "2019-80-00-00"}).status_code == 404
    assert lc_client.get(full.format("nope"), params={"law": "2019-80-00-00"}).status_code == 404


def _change(jh, before="", after="", units=None, anchor=None):
    return {"jh": jh, "before_text": before, "after_text": after,
            "after_units": units, "anchor": anchor}


def _shape(blocks):
    return [b["unit"]["jh"] if "unit" in b
            else (b["change"], [(u["jh"], u["op"]) for u in b["units"]]) for b in blocks]


def test_merge_replaces_a_subtree_unit_by_unit():
    units = [["SZ7", "szakasz", "7. §", ""],
             ["SZ7@BE4", "bekezdesNyito", "(4)", "A Kormány"],
             ["SZ7@BE4@POA", "betusPontNyito", "a)", "az első,"],
             ["SZ7@BE4@POB", "betusPontNyito", "b)", "a második,"],
             ["SZ7@BE4@ZARO", "bekezdesZaro", "", "esetet szabályozza."],
             ["SZ7@BE5", "bekezdesNyito", "(5)", "Más."]]
    after = [["SZ7@BE4", "bekezdesNyito", "(4)", "A Kormány"],
             ["SZ7@BE4@POA", "betusPontNyito", "a)", "az első,"],
             ["SZ7@BE4@POC", "betusPontNyito", "c)", "a harmadik"],
             ["SZ7@BE4@ZARO", "bekezdesZaro", "", "esetet rendeletben szabályozza."]]
    blocks = lawtext.merge(units, [_change("SZ7@BE4", "x", "y", after)])
    assert _shape(blocks) == ["SZ7", (0, [("SZ7@BE4", "eq"), ("SZ7@BE4@POA", "eq"),
                                          ("SZ7@BE4@POB", "del"), ("SZ7@BE4@POC", "ins"),
                                          ("SZ7@BE4@ZARO", "mod")]), "SZ7@BE5"]
    zaro = blocks[1]["units"][-1]
    assert ["ins", "rendeletben "] in zaro["diff"]


def test_merge_places_new_units_by_their_anchor():
    units = [["SZ1", "szakasz", "1. §", ""], ["SZ2", "szakasz", "2. §", ""]]
    blocks = lawtext.merge(units, [
        _change("SZ1A", after="1/A. §", units=[["SZ1A", "szakasz", "1/A. §", ""]], anchor="SZ1"),
        _change("SZ0", after="0. §", units=[], anchor=""),          # before everything
        _change("SZ9", after="9. §", units=[], anchor=None),         # place unknown: at the end
    ])
    assert _shape(blocks) == [(1, []), "SZ1", (0, [("SZ1A", "ins")]), "SZ2", (2, [])]


def test_merge_gathers_units_sharing_an_id_like_njt_does():
    """njt now and then gives "9.", "9/A." and "9/B." the same id; the change's
    text was flattened over all of them, so the block holds all of them."""
    units = [["SZ87@BE0@PO9", "szamosPontNyito", "9.", "egy"],
             ["SZ87@BE0@PO9", "szamosPontNyito", "9/A.", "kettő"],
             ["SZ87@BE0@PO10", "szamosPontNyito", "10.", "három"]]
    after = [["SZ87@BE0@PO9", "szamosPontNyito", "9.", "EGY"],
             ["SZ87@BE0@PO9", "szamosPontNyito", "9/A.", "kettő"]]
    blocks = lawtext.merge(units, [_change("SZ87@BE0@PO9", "9. egy 9/A. kettő",
                                           "9. EGY 9/A. kettő", after)])
    assert _shape(blocks) == [(0, [("SZ87@BE0@PO9", "mod"), ("SZ87@BE0@PO9", "eq")]),
                              "SZ87@BE0@PO10"]


def test_merge_whole_law_repeal_and_flat_fallback():
    units = [["SZ1", "szakasz", "1. §", ""], ["SZ1@BE0", "bekezdesNyito", "", "Szöveg."]]
    (whole,) = lawtext.merge(units, [_change("", "A törvény", "")])
    assert [u["op"] for u in whole["units"]] == ["del", "del"]
    # a change without its units (the applier's flat text, or loaded before
    # they were kept) shows its flattened diff in place
    blocks = lawtext.merge(units, [_change("SZ1@BE0", "Szöveg.", "Új szöveg.")])
    (flat,) = blocks[1]["units"]
    assert flat["cls"] == "flat" and flat["diff"] == word_diff("Szöveg.", "Új szöveg.")
