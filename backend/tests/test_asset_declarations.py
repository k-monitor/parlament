"""EVNYR asset declarations (REP-18): loading, linking each to its filer, serving.

The link is the part that can go wrong in a way that matters. The snapshot names
the declarant and carries no id, so the loader matches names, and a match made
too eagerly attributes one person's assets to another. Most of what follows is
therefore about the matches that must NOT be made: a namesake, an MP filing as
someone without a seat, a son and his father, two equally good candidates.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from app import loader

try:
    from tests.conftest import _asset_declarations_registry, _officeholders_registry
except ImportError:  # when pytest imports conftest as a top-level module
    from conftest import _asset_declarations_registry, _officeholders_registry

MP_OFFICE = "Országgyűlési képviselő"
OFFICIAL_OFFICE = ("Országgyűlési képviselői megbízással nem rendelkező "
                   "politikai felsővezető")


# --- serving ----------------------------------------------------------------

def test_profile_lists_the_evnyr_declaration_with_its_contents(client):
    """The EVNYR filing is listed with the adatlap's, newest first, linking both
    its public page and its PDF, and carrying the declaration's contents."""
    decls = client.get("/api/v1/representatives/k001").json()["asset_declarations"]
    assert [d.get("source") for d in decls] == ["evnyr", None, None]
    d = decls[0]
    assert d["type"] == "Nyitó" and d["finalizedAt"] == "2026-06-02T10:00:00"
    assert d["url"].endswith("/nyilatkozat/1111aaaa-0000-4000-8000-000000000001")
    assert d["pdfUrl"].endswith("vagyonnyilatkozat_20260603_kovacs_bela.pdf")
    assert d["snapshotAt"] == "2026-10-01T01:00"
    assert len(d["content"]["ingatlanok"]) == 2
    assert d["content"]["gazdasagiErdekeltseg.nyilatkozattetelHelye"] == "Budapest"
    # Someone with no declaration in either source still gets an empty list.
    assert client.get("/api/v1/representatives/n002").json()["asset_declarations"] == []


def test_listing_keeps_the_declarations_nobody_could_be_matched_to(client):
    """A declaration the House published stays listed whether or not its filer
    is in the corpus; it just has no profile to link to."""
    d = client.get("/api/v1/representatives/asset-declarations").json()
    assert (d["total"], d["linked"]) == (2, 1)
    assert d["snapshot_at"] == "2026-10-01T01:00"
    first, second = d["items"]
    # Newest first.
    assert first["name"] == "DR. ISMERETLEN ELEK" and first["person"] is None
    # Its page has not been read yet: no PDF, but the page link is there.
    assert first["pdfUrl"] is None and first["url"].startswith("https://")
    assert second["person"] == {"person_id": "k001", "label": "Kovács Béla"}
    # The listing is an index; contents come with the profile.
    assert "content" not in first


def test_an_adatlap_entry_for_the_same_pdf_is_not_listed_twice(client, conn):
    """Should the adatlap ever report a filing the EVNYR system holds, the two
    are one declaration, kept once."""
    from app.modules.representatives.router import _asset_declarations
    pdf = conn.execute("SELECT pdf_url FROM asset_declaration "
                       "WHERE person_id='k001'").fetchone()[0]
    out = _asset_declarations(conn, "k001", [
        {"title": "Vagyonnyilatkozat 2026", "assetDate": "2026-06-01", "url": pdf},
        {"title": "Vagyonnyilatkozat 2025", "assetDate": "2025-12-31", "url": None}])
    assert [d.get("source") or d["assetDate"] for d in out] == ["evnyr", "2025-12-31"]


def test_a_db_without_the_table_serves_the_adatlap_list_alone(client, db_path):
    """A DB built before REP-18 has no table; the profile must not break."""
    c = sqlite3.connect(db_path)
    try:
        c.execute("DROP TABLE asset_declaration")
        c.commit()
    finally:
        c.close()
    decls = client.get("/api/v1/representatives/k001").json()["asset_declarations"]
    assert [d.get("source") for d in decls] == [None, None]
    d = client.get("/api/v1/representatives/asset-declarations").json()
    assert d == {"snapshot_at": None, "total": 0, "linked": 0, "items": []}


# --- loading ------------------------------------------------------------------

def test_a_withdrawn_declaration_leaves_with_the_snapshot(conn):
    """The snapshot is the whole published set, so the load replaces it."""
    reg = _asset_declarations_registry()
    reg["data"] = reg["data"][:1]
    loader.load_asset_declarations(conn, reg)
    assert [r[0] for r in conn.execute("SELECT name FROM asset_declaration")] \
        == ["KOVÁCS BÉLA"]


def test_update_relinks_when_only_the_people_changed(data_dir, db_path):
    """The declarant entered the corpus after their declaration (here: the next
    office-holder refresh names them a state secretary). The snapshot did not
    change, yet the declaration must gain its profile link."""
    def person_of(name):
        c = sqlite3.connect(db_path)
        try:
            return c.execute("SELECT person_id FROM asset_declaration WHERE name=?",
                             (name,)).fetchone()[0]
        finally:
            c.close()

    assert person_of("DR. ISMERETLEN ELEK") is None
    reg = _officeholders_registry()
    reg["data"].append({
        "personID": "u777", "label": "Ismeretlen Elek",
        "labelFull": "Dr. Ismeretlen Elek", "firstname": "Elek",
        "lastname": "Ismeretlen",
        "offices": [{"title": "Belügyminisztérium államtitkára",
                     "category": "state-secretary",
                     "start": "2026-08-28T22:00:00Z", "end": None}]})
    (data_dir / "processed" / "officeholders.json").write_text(
        json.dumps(reg, ensure_ascii=False))
    assert loader.update_database(data_dir, db_path) is True
    assert person_of("DR. ISMERETLEN ELEK") == "u777"


# --- linking ------------------------------------------------------------------

@pytest.fixture
def people():
    """A tiny corpus: cycle 42 (ended) and 43 (current), and the people the
    match rules have to tell apart."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    loader.init_schema(c)
    c.executemany("INSERT INTO electoral_period(number, date_start, date_end) "
                  "VALUES (?,?,?)",
                  [(40, "2014-05-06", "2018-05-07"), (42, "2022-05-02", "2026-05-08"),
                   (43, "2026-05-09", None)])
    persons = [
        # id, label, label_full, is_mp, is_advocate
        ("m1", "Szabó Anna", "Dr. Szabó Anna", 1, 0),
        ("m2", "Kiss Éva", "Kiss Éva", 1, 0),     # two MPs of one name
        ("m3", "Kiss Éva", "Kiss Éva", 1, 0),
        ("m4", "Tóth Gábor", "Tóth Gábor", 1, 0),  # ex-state secretary, now an MP
        ("m5", "Varga Ödön", "Varga Ödön", 1, 0),  # left at the end of cycle 42
        ("m6", "Nagy Péter", "Nagy Péter", 1, 0),  # a namesake MP from 2014
        ("o1", "Nagy Péter", "Dr. Nagy Péter", 0, 0),  # a sitting state secretary
        ("a1", "Lakatos Rózsa", "Lakatos Rózsa", 0, 1),
    ]
    c.executemany("INSERT INTO person(person_id, label, label_full, is_mp, is_advocate) "
                  "VALUES (?,?,?,?,?)", persons)
    c.executemany("INSERT INTO membership(person_id, period_number) VALUES (?,?)",
                  [("m1", 43), ("m2", 43), ("m3", 43), ("m4", 43), ("m5", 42),
                   ("m6", 40), ("a1", 43)])
    c.executemany("INSERT INTO person_office(person_id, title, category, date_start, "
                  "date_end, source) VALUES (?,?,?,?,?, 'registry')",
                  [("m4", "Pénzügyminisztérium államtitkára", "state-secretary",
                    "2022-05-24T22:00:00Z", "2026-05-12T21:59:59Z"),
                   ("o1", "Belügyminisztérium államtitkára", "state-secretary",
                    "2026-05-20T22:00:00Z", None),
                   # A parliamentary office is not a government post.
                   ("m1", "az Országgyűlés jegyzője", "parliamentary",
                    "2026-05-09T22:00:00Z", None)])
    yield c
    c.close()


def _link(conn, *declarations) -> list[str | None]:
    """Load ``(name, office, finalised at)`` declarations; return each one's
    matched person id, in the order given."""
    reg = {"meta": {"snapshotAt": "2026-10-01T01:00"},
           "data": [{"id": f"dddddddd-{i:04d}", "name": name, "office": office,
                     "type": "Nyitó", "finalizedAt": on, "url": f"https://x/{i}"}
                    for i, (name, office, on) in enumerate(declarations)]}
    loader.load_asset_declarations(conn, reg)
    return [r[0] for r in conn.execute(
        "SELECT person_id FROM asset_declaration ORDER BY declaration_id")]


def test_an_mp_is_matched_whatever_the_case_and_titles(people):
    assert _link(people, ("SZABÓ ANNA", MP_OFFICE, "2026-06-01T09:00:00")) == ["m1"]
    assert _link(people, ("DR. SZABÓ ANNA", MP_OFFICE, "2026-06-01T09:00:00")) == ["m1"]


def test_two_people_who_both_fit_get_neither(people):
    assert _link(people, ("KISS ÉVA", MP_OFFICE, "2026-06-01T09:00:00")) == [None]


def test_an_official_without_a_seat_is_told_from_a_namesake_mp(people):
    """The 2014 MP Nagy Péter holds no current role; the state secretary does,
    and has no seat, which is what an official's declaration says."""
    assert _link(people, ("DR. NAGY PÉTER", OFFICIAL_OFFICE,
                          "2026-06-15T09:00:00")) == ["o1"]


def test_a_sitting_mp_is_never_taken_for_a_seatless_official(people):
    """Tóth Gábor was a state secretary until May and now sits: a declaration
    "without an MP mandate" cannot be his, whatever the name says; one filed as
    an MP is."""
    assert _link(people, ("TÓTH GÁBOR", OFFICIAL_OFFICE, "2026-06-15T09:00:00"),
                 ("TÓTH GÁBOR", MP_OFFICE, "2026-06-16T09:00:00")) == [None, "m4"]


def test_a_parliamentary_office_does_not_make_an_mp_an_official(people):
    assert _link(people, ("SZABÓ ANNA", OFFICIAL_OFFICE, "2026-06-01T09:00:00")) == [None]


def test_a_closing_declaration_after_the_seat_ended_still_matches(people):
    """Filed after the mandate ended, within the lookback; a seat that ended
    years earlier is not a candidate at all."""
    assert _link(people, ("VARGA ÖDÖN", MP_OFFICE, "2026-06-05T09:00:00"),
                 ("NAGY PÉTER", MP_OFFICE, "2026-06-05T09:00:00")) == ["m5", None]


def test_ifj_is_part_of_the_name(people):
    """"ifj." tells a son from his father: it is never folded away."""
    assert _link(people, ("IFJ. SZABÓ ANNA", MP_OFFICE, "2026-06-01T09:00:00")) == [None]


def test_an_advocate_is_matched_on_an_advocate_seat(people):
    assert _link(people, ("LAKATOS RÓZSA", "nemzetiségi szószóló", "2026-06-01T09:00:00"),
                 ("LAKATOS RÓZSA", MP_OFFICE, "2026-06-02T09:00:00")) == ["a1", None]


def test_a_declaration_before_the_role_began_does_not_match(people):
    assert _link(people, ("NAGY PÉTER", OFFICIAL_OFFICE, "2026-05-01T09:00:00")) == [None]
