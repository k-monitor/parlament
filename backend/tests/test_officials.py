"""The two "people who are not (only) MPs" listings.

* ``/representatives/officials`` — the office-holder listing (tisztségviselők,
  REP-11): the parliament's own all-time registry of who held which government or
  House office and when, served one row per term.
* ``/representatives?role=other`` — the other speakers (REP-12): everyone who has
  spoken in the House holding neither a mandate nor an advocacy — non-MP ministers
  and state secretaries, the President of the Republic, invited guests.

The two overlap on purpose (a non-MP state secretary is on both) but are defined
by different things: an office term vs. having spoken.
"""

from __future__ import annotations

import pytest

from tests.test_api import _load_minister_speech


def _load_minister(db_path):
    """A non-MP minister's sitting day, aggregates rebuilt — which is what a real
    load does at the end of every run, and what makes them count as a speaker."""
    from app import loader
    c = _load_minister_speech(db_path)
    loader.rebuild_aggregates(c)
    c.commit()
    c.close()


# --- office-holder listing (REP-11) -----------------------------------------

def test_officials_lists_one_row_per_term_with_its_category(client):
    """Every registry term is a row, newest first by default, carrying its dates
    and the portal's own office category. The same person appears once per office."""
    d = client.get("/api/v1/representatives/officials",
                   params={"period": []}).json()
    rows = [(o["person_id"], o["title"], o["category"], o["current"])
            for o in d["officials"]]
    assert rows == [
        ("k001", "az Országgyűlés jegyzője", "parliamentary", True),
        ("k001", "Belügyminisztérium államtitkára", "state-secretary", False),
        ("zzz9", "köztársasági elnök", "senior", False),
    ]
    assert d["total"] == 3
    # An open end IS "still in office" — the caller never has to compare dates.
    assert d["officials"][0]["end"] is None
    assert d["officials"][1]["end"] == "2022-05-24T12:00:00Z"


def test_officials_covers_holders_who_never_spoke(client):
    """The point of loading the whole registry: an office holder outside the
    corpus (over half of it) is listed like anyone else, with a working profile
    link — not silently dropped."""
    d = client.get("/api/v1/representatives/officials",
                   params={"period": [], "q": "Sosem"}).json()
    assert [o["person_id"] for o in d["officials"]] == ["zzz9"]
    assert d["officials"][0]["is_mp"] is False
    # The profile the row links to exists and is an office history.
    prof = client.get("/api/v1/representatives/zzz9")
    assert prof.status_code == 200
    assert [o["title"] for o in prof.json()["offices"]] == ["köztársasági elnök"]


def test_officials_search_matches_person_or_office(client):
    """One search box for two things people look for here — a name and an office
    title — and it ignores accents/case like every other search on the site."""
    by_name = client.get("/api/v1/representatives/officials",
                         params={"period": [], "q": "KOVACS"}).json()
    assert {o["person_id"] for o in by_name["officials"]} == {"k001"}
    by_office = client.get("/api/v1/representatives/officials",
                           params={"period": [], "q": "allamtitkar"}).json()
    assert [o["title"] for o in by_office["officials"]] == [
        "Belügyminisztérium államtitkára"]


def test_officials_status_filter_splits_current_from_past(client):
    current = client.get("/api/v1/representatives/officials",
                         params={"period": [], "status": "current"}).json()
    past = client.get("/api/v1/representatives/officials",
                      params={"period": [], "status": "past"}).json()
    assert [o["title"] for o in current["officials"]] == ["az Országgyűlés jegyzője"]
    assert current["total"] + past["total"] == 3
    assert all(o["end"] for o in past["officials"])


def test_officials_category_filter_and_facet(client):
    """The facet counts the filtered set with the category filter itself lifted, so
    picking a category can never land on an empty page."""
    d = client.get("/api/v1/representatives/officials", params={"period": []}).json()
    # Listed in the portal's own order (most specific office first), and a category
    # nobody in scope holds is left out rather than shown as a zero.
    assert d["categories"] == [
        {"key": "state-secretary", "count": 1},
        {"key": "parliamentary", "count": 1},
        {"key": "senior", "count": 1},
    ]
    one = client.get("/api/v1/representatives/officials",
                     params={"period": [], "category": "senior"}).json()
    assert one["total"] == 1
    assert [o["person_id"] for o in one["officials"]] == ["zzz9"]
    # The facet still reports every category, not just the selected one.
    assert len(one["categories"]) == 3


def test_officials_cycle_scope_is_date_overlap_not_start(client, db_path):
    """§4A on a table with no cycle column: a term counts for a cycle when it
    *overlaps* it. A minister appointed in an earlier cycle and still serving must
    show up in this one — scoping on the start date would hide exactly the people
    the page is about."""
    # Cycle 43 starts 2026-05-09. The still-held jegyző term starts inside it; the
    # 2018–2022 state-secretary term is wholly before it; the president's
    # 2012–2017 term likewise.
    scoped = client.get("/api/v1/representatives/officials",
                        params={"period": 43}).json()
    assert [o["title"] for o in scoped["officials"]] == ["az Országgyűlés jegyzője"]

    # A term that began before the cycle and is still open overlaps it.
    from app import loader
    c = loader.connect(db_path)
    loader._load_person_offices(c, "zzz9", [
        {"title": "az Alkotmánybíróság elnöke", "category": "senior",
         "start": "2020-01-01T23:00:00Z", "end": None}], "registry")
    c.commit()
    c.close()
    scoped = client.get("/api/v1/representatives/officials",
                        params={"period": 43}).json()
    assert sorted(o["title"] for o in scoped["officials"]) == [
        "az Alkotmánybíróság elnöke", "az Országgyűlés jegyzője"]


def test_officials_cycle_boundary_is_an_instant_not_a_date_prefix(client, db_path):
    """A term beginning at the first midnight of a cycle belongs to **that** cycle.

    Upstream stores it as the UTC instant of local midnight — 9 May 2026 arrives as
    "2026-05-08T22:00:00Z" — so comparing date prefixes against cycle 42's last day
    (2026-05-08) let the whole opening cohort of cycle 43 (every korjegyző, every
    House officer sworn in on day one) show up under the previous cycle."""
    from app import loader
    c = loader.connect(db_path)
    c.execute("INSERT INTO electoral_period(number, date_start, date_end) "
              "VALUES (42, '2022-05-02', '2026-05-08') "
              "ON CONFLICT(number) DO UPDATE SET date_start=excluded.date_start, "
              "date_end=excluded.date_end")
    loader._load_person_offices(c, "zzz9", [
        {"title": "korjegyző", "category": "parliamentary",
         "start": "2026-05-08T22:00:00Z", "end": "2026-05-09T11:15:00Z"}], "registry")
    c.commit()
    c.close()

    prev = client.get("/api/v1/representatives/officials", params={"period": 42}).json()
    assert "korjegyző" not in [o["title"] for o in prev["officials"]]
    now = client.get("/api/v1/representatives/officials", params={"period": 43}).json()
    assert "korjegyző" in [o["title"] for o in now["officials"]]


def test_officials_report_how_many_terms_began_in_the_cycle(client, db_path):
    """The number that explains the page. A cycle lists every term *held during* it,
    so most of its rows can predate it — which reads as a broken filter unless the
    page can say "…of which N began in this cycle" and narrow to them."""
    from app import loader
    c = loader.connect(db_path)
    c.execute("INSERT INTO electoral_period(number, date_start, date_end) "
              "VALUES (42, '2022-05-02', '2026-05-08') "
              "ON CONFLICT(number) DO UPDATE SET date_start=excluded.date_start, "
              "date_end=excluded.date_end")
    # Began two cycles ago, never ended: held throughout cycle 43, began in none of it.
    loader._load_person_offices(c, "zzz9", [
        {"title": "a Kúria elnöke", "category": "senior",
         "start": "2020-01-01T23:00:00Z", "end": None}], "registry")
    c.commit()
    c.close()

    d = client.get("/api/v1/representatives/officials", params={"period": 43}).json()
    titles = [o["title"] for o in d["officials"]]
    assert "a Kúria elnöke" in titles          # held during the cycle → in scope
    assert d["starts"] == {"all": d["total"], "in_cycle": d["total"] - 1}

    narrowed = client.get("/api/v1/representatives/officials",
                          params={"period": 43, "started": "in-cycle"}).json()
    assert "a Kúria elnöke" not in [o["title"] for o in narrowed["officials"]]
    assert narrowed["total"] == d["starts"]["in_cycle"]
    # The counts stay put under the narrowing, so the control can offer the way back.
    assert narrowed["starts"] == d["starts"]


def test_officials_started_filter_is_a_no_op_without_a_cycle(client):
    """Under "all cycles" there is no cycle for a term to have begun in: the filter
    narrows nothing and the count is reported as absent rather than as zero."""
    everything = client.get("/api/v1/representatives/officials",
                            params={"period": []}).json()
    assert everything["starts"]["in_cycle"] is None
    same = client.get("/api/v1/representatives/officials",
                      params={"period": [], "started": "in-cycle"}).json()
    assert same["total"] == everything["total"]


def test_officials_undated_cycle_lists_nothing_rather_than_everything(client):
    """A cycle this DB cannot date has no span to overlap. Answering "no terms" is
    honest; falling through to an unfiltered list would silently present the whole
    archive as that cycle's office holders."""
    d = client.get("/api/v1/representatives/officials", params={"period": 99}).json()
    assert d["total"] == 0 and d["officials"] == [] and d["categories"] == []


def test_officials_sorting(client):
    by_name = client.get("/api/v1/representatives/officials",
                         params={"period": [], "sort": "name"}).json()
    assert [o["person_id"] for o in by_name["officials"]] == ["k001", "k001", "zzz9"]
    by_office = client.get("/api/v1/representatives/officials",
                           params={"period": [], "sort": "office"}).json()
    assert [o["title"] for o in by_office["officials"]] == [
        "az Országgyűlés jegyzője",
        "Belügyminisztérium államtitkára",
        "köztársasági elnök"]


def test_officials_reads_the_registry_not_the_roster(client, db_path):
    """The per-MP roster reports the same terms without a category, so counting
    both sources would double every MP's office and leave half the rows
    unfilterable. Only the registry rows are listed."""
    from app import loader
    c = loader.connect(db_path)
    loader._load_person_offices(c, "k001", [
        {"title": "az Országgyűlés jegyzője",          # the registry has this one
         "start": "2026-05-09T22:00:00Z", "end": None},
        {"title": "a Közbeszerzések Tanácsának tagja",  # roster-only
         "start": "2006-12-01T23:00:00Z", "end": "2010-04-28T21:59:59Z"},
    ], "roster")
    c.commit()
    c.close()
    d = client.get("/api/v1/representatives/officials", params={"period": []}).json()
    assert d["total"] == 3
    titles = [o["title"] for o in d["officials"]]
    assert titles.count("az Országgyűlés jegyzője") == 1
    assert "a Közbeszerzések Tanácsának tagja" not in titles


# --- other speakers (REP-12) -------------------------------------------------

def test_other_speakers_are_non_mps_who_actually_spoke(client, db_path):
    """`role=other` is defined by having spoken, not by "not an MP": `person` also
    holds the office-holder registry's people, most of whom never spoke here."""
    _load_minister(db_path)
    d = client.get("/api/v1/representatives", params={"role": "other"}).json()
    assert [r["person_id"] for r in d["representatives"]] == ["0052"]
    assert d["total"] == 1
    assert d["representatives"][0]["speech_count"] == 1
    # The registry-only person is in `person` but never spoke → not a speaker.
    assert "zzz9" not in {r["person_id"] for r in d["representatives"]}
    # ...and MPs/advocates are not in this list either: it is the *other* people.
    assert "k001" not in {r["person_id"] for r in d["representatives"]}


def test_other_speakers_are_cycle_scoped(client, db_path):
    """Scoped like every other list (§4A): a speaker shows up in the cycles they
    actually spoke in. Their speech is in cycle 43."""
    _load_minister(db_path)
    assert client.get("/api/v1/representatives",
                      params={"role": "other", "period": 43}).json()["total"] == 1
    assert client.get("/api/v1/representatives",
                      params={"role": "other", "period": 42}).json()["total"] == 0


def test_other_speakers_carry_the_office_that_identifies_them(client, db_path):
    """A non-MP speaker has no faction and no constituency, so the card would say
    nothing but a name. Their office goes in that slot — from the registry when it
    dates one, otherwise from the office their own speeches carry."""
    _load_minister(db_path)
    d = client.get("/api/v1/representatives", params={"role": "other"}).json()
    # No registry term for them: the office comes off their speeches.
    assert d["representatives"][0]["office"] == "igazságügyi miniszter"

    from app import loader
    c = loader.connect(db_path)
    loader._load_person_offices(c, "0052", [
        {"title": "igazságügyi miniszter", "category": "minister",
         "start": "2026-05-12T22:00:00Z", "end": None},
        {"title": "Igazságügyi Minisztérium államtitkára", "category": "state-secretary",
         "start": "2022-05-24T22:00:00Z", "end": "2026-05-12T21:59:59Z"},
    ], "registry")
    c.commit()
    c.close()
    d = client.get("/api/v1/representatives", params={"role": "other"}).json()
    assert d["representatives"][0]["office"] == "igazságügyi miniszter"
    # Scoped like everything else (§4A): in cycle 43 the earlier state-secretary
    # term is out of scope, so the office shown is the one they hold in it.
    scoped = client.get("/api/v1/representatives",
                        params={"role": "other", "period": 43}).json()
    assert [r["office"] for r in scoped["representatives"]] == ["igazságügyi miniszter"]


def test_merged_list_identifies_a_speaker_by_office_but_not_an_mp(client, db_path):
    """`role=all` backs the "Összes" chip of the Felszólalók page, which mixes the
    three categories into one list of cards. A card there must read exactly as it
    does under its own chip: an MP is identified by their faction and an advocate
    by their nationality, so the office slot is filled in only for the speakers
    who have neither — even though the MP in this fixture holds registry offices
    of their own."""
    _load_minister(db_path)
    d = client.get("/api/v1/representatives",
                   params={"role": "all", "period": []}).json()
    offices = {r["person_id"]: r["office"] for r in d["representatives"]}
    assert offices["0052"] == "igazságügyi miniszter"   # the non-MP minister
    assert offices["k001"] is None                      # an MP, though on the registry


def test_office_holder_stubs_never_leak_into_the_mp_list(client):
    """The 500-odd registry people added to `person` hold no mandate, so no
    mandate-scoped list may grow by one of them."""
    mps = client.get("/api/v1/representatives", params={"period": []}).json()
    assert "zzz9" not in {r["person_id"] for r in mps["representatives"]}
    advocates = client.get("/api/v1/representatives",
                           params={"role": "advocate", "period": []}).json()
    assert "zzz9" not in {r["person_id"] for r in advocates["representatives"]}
    # Nor into search's speaker suggestions — they never spoke.
    sug = client.get("/api/v1/proceedings/suggest", params={"q": "Sosem"}).json()
    assert sug["speakers"] == []


def test_non_mp_speaker_is_suggestible_in_search(client, db_path):
    """A non-MP minister speaks in plenary, so filtering the transcript by them has
    to be possible — they are offered by search-as-you-type like an MP (REP-12)."""
    _load_minister(db_path)
    sug = client.get("/api/v1/proceedings/suggest",
                     params={"q": "Törőcsikné"}).json()
    assert [s["person_id"] for s in sug["speakers"]] == ["0052"]


@pytest.mark.parametrize("params", [
    {"category": "no-such-category"},
    {"status": "current", "q": "nobody"},
])
def test_officials_empty_result_is_a_clean_empty_page(client, params):
    d = client.get("/api/v1/representatives/officials",
                   params={"period": [], **params}).json()
    assert d["total"] == 0 and d["officials"] == []
