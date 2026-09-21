"""Offline tests for the committee stage (`parlamonitor.committees`).

Two halves. The first drives ``fetch_committees`` against a fake Felicitas
client to pin what the registry file must contain and how many requests it
costs. The second drives the *client's own* row-shaping against captured
upstream payloads — that is where the domain's traps live: the listing names a
subcommittee in different columns from a main committee, and the term registry
writes a person's faction into their name.
"""

from __future__ import annotations

import json

import pytest

from parlamonitor import felicitas as fel
from parlamonitor.committees import scrape as committees


class FakeFelicitas:
    """Answers only what the committee stage asks for; counts the calls."""

    def __init__(self, bodies=None):
        self.bodies = bodies if bodies is not None else [
            {"committeeId": "c1", "name": "Költségvetési Bizottság",
             "parentId": None, "parentName": None, "isSubcommittee": False,
             "standingCode": "KTB", "code": "002A", "cycle": 43,
             "dateStart": "2026-05-09", "dateEnd": None, "ord": 1},
            {"committeeId": "c1a", "name": "Ellenőrző Albizottság",
             "parentId": "c1", "parentName": "Költségvetési Bizottság",
             "isSubcommittee": True, "standingCode": None, "code": None,
             "cycle": 43, "dateStart": "2026-06-01", "dateEnd": None, "ord": 1},
        ]
        self.sheet_calls: list[str] = []
        self.document_calls: list[str] = []
        self.submission_calls: list[str] = []

    def committee_bodies(self, cycle, start, end):
        return [dict(b) for b in self.bodies]

    def committee_sheet(self, committee_id):
        self.sheet_calls.append(committee_id)
        if committee_id == "c1":
            return {"type": "állandó", "email": "ktb[kukac]parlament.hu",
                    "hasSite": True, "active": True}
        # A subcommittee: no type, no page of its own.
        return {"type": None, "email": None, "hasSite": False, "active": True}

    def committee_members(self, cycle, start, end, as_of):
        return [{"committeeId": "c1", "personID": "k001", "role": "chair",
                 "asOf": as_of}]

    def committee_terms(self, cycle, start, end):
        return [{"committeeId": "c1", "personID": "n002", "kind": "membership"}]

    def committee_meetings(self, cycle, start, end):
        return [{"meetingId": "u1", "committeeId": "c1"}]

    def committee_meeting_stats(self, cycle, start, end):
        return [{"committeeId": "c1", "meetings": 4, "totalMinutes": 300,
                 "quorate": 3, "inquorate": 1}]

    def committee_documents(self, cycle, start, end, committee_id):
        self.document_calls.append(committee_id)
        return [{"committeeId": committee_id, "billNumber": "T/100"}]

    def committee_submissions(self, cycle, start, end, committee_id):
        self.submission_calls.append(committee_id)
        return [{"committeeId": committee_id, "kind": "motion"}]

    def committee_upcoming(self, cycle, start, end):
        return [{"meetingId": "u-next", "committeeId": "c1",
                 "committeeName": "Költségvetési Bizottság",
                 "date": "2026-06-22", "time": "10:30",
                 "venue": "Széll Kálmán terem", "cancelled": False}]


def _fetch(**kw):
    f = FakeFelicitas()
    return f, committees.fetch_committees(f, 43, "2026-05-09", "2026-06-19", **kw)


def test_registry_shape_and_counts():
    fake, reg = _fetch()
    assert reg["meta"]["cycle"] == 43
    assert reg["meta"]["membersAsOf"] == "2026-06-19"
    assert reg["meta"]["counts"] == {
        "bodies": 2, "members": 1, "terms": 1, "meetings": 1,
        "documents": 2, "submissions": 2, "upcoming": 1}
    # What the committees are about to do, carried beside what they have done.
    assert reg["upcoming"][0]["committeeId"] == "c1"
    # Every body is detailed exactly once.
    assert fake.sheet_calls == ["c1", "c1a"]
    assert fake.document_calls == ["c1", "c1a"]
    assert fake.submission_calls == ["c1", "c1a"]


def test_sheet_fields_and_homepage_url():
    _, reg = _fetch()
    main, sub = reg["data"]
    assert main["type"] == "állandó"
    # The homepage is `/web/guest/<cycle>-<code>` — built only where upstream
    # says there is one.
    assert main["siteUrl"] == "https://www.parlament.hu/web/guest/43-002A"
    assert sub["type"] is None
    assert sub["siteUrl"] is None


def test_meeting_stats_are_attached_to_their_body():
    _, reg = _fetch()
    main, sub = reg["data"]
    assert main["meetingStats"]["meetings"] == 4
    # A body upstream reports no aggregate for keeps None, not a zeroed row —
    # "it never met" and "we were not told" are different statements.
    assert sub["meetingStats"] is None


def test_as_of_overrides_the_snapshot_date():
    fake = FakeFelicitas()
    reg = committees.fetch_committees(fake, 43, "2026-05-09", "2026-06-19",
                                      as_of="2026-05-20")
    assert reg["meta"]["membersAsOf"] == "2026-05-20"
    assert reg["members"][0]["asOf"] == "2026-05-20"


def test_no_detail_skips_the_per_body_requests():
    fake = FakeFelicitas()
    reg = committees.fetch_committees(fake, 43, "2026-05-09", "2026-06-19",
                                      with_detail=False)
    assert fake.sheet_calls == fake.document_calls == fake.submission_calls == []
    assert reg["documents"] == reg["submissions"] == []
    # The bodies and their membership are still there — that is the point of it.
    assert len(reg["data"]) == 2 and len(reg["members"]) == 1


def test_no_detail_carries_the_previous_detail_forward():
    """The loader replaces a cycle wholesale, so a registry that merely omits
    the detail would read as one asserting there is none: one --no-detail pass
    would blank every committee's type and delete its documents."""
    _, full = _fetch()
    fake = FakeFelicitas()
    reg = committees.fetch_committees(fake, 43, "2026-05-09", "2026-06-19",
                                      with_detail=False, previous=full)
    assert fake.sheet_calls == []                      # still no requests
    assert reg["meta"]["detailCarried"] is True
    assert reg["data"][0]["type"] == "állandó"
    assert reg["data"][0]["siteUrl"].endswith("/43-002A")
    assert len(reg["documents"]) == 2 and len(reg["submissions"]) == 2


def test_carried_detail_drops_rows_of_bodies_that_are_gone():
    """A committee upstream has removed takes its documents with it; a carried
    row would point at a body the registry no longer holds."""
    _, full = _fetch()
    fake = FakeFelicitas(bodies=[
        {"committeeId": "c1", "name": "Költségvetési Bizottság",
         "parentId": None, "parentName": None, "isSubcommittee": False,
         "standingCode": "KTB", "code": "002A", "cycle": 43,
         "dateStart": "2026-05-09", "dateEnd": None, "ord": 1}])
    reg = committees.fetch_committees(fake, 43, "2026-05-09", "2026-06-19",
                                      with_detail=False, previous=full)
    assert {d["committeeId"] for d in reg["documents"]} == {"c1"}
    assert {d["committeeId"] for d in reg["submissions"]} == {"c1"}


def test_no_detail_with_no_previous_run_writes_nothing_it_does_not_know():
    fake = FakeFelicitas()
    reg = committees.fetch_committees(fake, 43, "2026-05-09", "2026-06-19",
                                      with_detail=False, previous=None)
    assert reg["meta"]["detailCarried"] is False
    assert reg["data"][0].get("type") is None


def test_save_writes_the_registry(tmp_path):
    from parlamonitor.config import Paths
    paths = Paths(tmp_path)
    paths.ensure()
    _, reg = _fetch()
    committees.save_committees(paths, 43, reg)
    out = paths.committees_file(43)
    assert out.exists()
    assert json.loads(out.read_text())["meta"]["count"] == 2


# --- the client's own row shaping -----------------------------------------
# Payloads below are captured verbatim from the live API (2026-09).

class _FakeHttp:
    """A Felicitas transport that replays one canned select response."""

    def __init__(self, payload):
        self.payload = payload

    def polite_sleep(self):
        pass

    def post_json(self, url, body, headers=None):
        return self.payload


def _client(rows, fieldnames):
    http = _FakeHttp({"metadata": {"fieldnames": fieldnames},
                      "rows": rows,
                      "response": {"totalSize": len(rows), "pageSize": 1000}})
    return fel.FelicitasClient(http)


_LIST_FIELDS = {"bizottsagId": 0, "allandoBizottsagKod": 1, "bizottsagNev": 2,
                "albizottsagId": 3, "albizottsagNev": 4, "fobizottsagSor": 5,
                "letrahozasDatuma": 6, "megszuntetesDatuma": 7, "ciklus": 8,
                "bizottsagKod": 9, "fobizottsagSorszamField": 10,
                "alBizottsagSorszamField": 11}


def test_bodies_read_their_identity_from_the_right_columns():
    """The listing is one row per (main committee, body) pair: a subcommittee's
    own name and id are in the `albizottsag*` columns while `bizottsagNev`
    still names its parent. Reading the wrong pair silently renames every
    subcommittee after its parent."""
    rows = [
        ["2742533", "FFB", "Fenntartható Fejlődés Bizottsága", "2742533",
         "Fenntartható Fejlődés Bizottsága", True, "2022-05-02",
         "2026-05-08T21:59:59Z", 42, "A512", 3, 3],
        ["2742533", None, "Fenntartható Fejlődés Bizottsága", "2765358",
         "Erdővédelmi Albizottság", False, "2022-06-29",
         "2026-05-08T21:59:59Z", 42, "A512", 2, 2],
    ]
    bodies = _client(rows, _LIST_FIELDS).committee_bodies(42, "2022-05-02",
                                                          "2026-05-08")
    main, sub = bodies
    assert (main["committeeId"], main["name"]) == (
        "2742533", "Fenntartható Fejlődés Bizottsága")
    assert main["parentId"] is None and main["code"] == "A512"
    assert (sub["committeeId"], sub["name"]) == ("2765358", "Erdővédelmi Albizottság")
    assert sub["parentId"] == "2742533"
    assert sub["isSubcommittee"] is True
    # `bizottsagKod` on a subcommittee row is the *parent's* code, so it is not
    # kept: it would build the parent's homepage URL under the child's name.
    assert sub["code"] is None
    # The body's own dates, not the term's.
    assert sub["dateStart"] == "2022-06-29"


def test_a_body_listed_under_several_parents_appears_once():
    rows = [
        ["p1", None, "Egyik Bizottság", "s1", "Közös Albizottság", False,
         "2022-06-29", None, 42, "A512", 1, 1],
        ["p2", None, "Másik Bizottság", "s1", "Közös Albizottság", False,
         "2022-06-29", None, 42, "A513", 1, 1],
    ]
    bodies = _client(rows, _LIST_FIELDS).committee_bodies(42, "2022-05-02",
                                                          "2026-05-08")
    assert [b["committeeId"] for b in bodies] == ["s1"]


_TERM_FIELDS = {"bizottsagId": 0, "bizottsagNeve": 1, "kepviseloId": 2,
                "kepviseloNeve": 3, "tagsagKezdete": 4, "tagsagVege": 5,
                "tagsagKiHelyett": 6, "tagsagValtozasOka": 7,
                "tisztsegMegnevezese": 8, "tisztsegKezdete": 9,
                "tisztsegVege": 10, "tisztsegKiHelyett": 11,
                "tisztsegValtozasOka": 12}

_KI_HELYETT = {"metadata": {"fieldnames": {"tisztsegviseloId": 0,
                                           "tisztsegviseloNeve": 1}},
               "rows": [["h021", "Hende Csaba (Fidesz)"]]}


def test_terms_split_membership_from_office_and_unpack_the_name():
    """One upstream row can carry both a membership span and an office span;
    each becomes a term of its own. The person's faction is written into their
    name here and nowhere else, so it is split back out."""
    rows = [[
        "2742531", "Törvényalkotási Bizottság", "f002",
        "Dr. Fazekas Sándor (Fidesz)",
        "2022-05-02T13:50:00Z", "2025-06-11T08:27:00Z",
        {"metadata": {"fieldnames": {"tisztsegviseloNeve": 0}}, "rows": []},
        None,
        "elnök", "2025-06-11T08:28:00Z", "2026-05-08T21:59:59Z",
        _KI_HELYETT, "tisztségről lemondott",
    ]]
    terms = _client(rows, _TERM_FIELDS).committee_terms(42, "2022-05-02",
                                                        "2026-05-08")
    assert [t["kind"] for t in terms] == ["membership", "office"]
    membership, office = terms
    assert membership["name"] == "Dr. Fazekas Sándor"
    assert membership["factionName"] == "Fidesz"
    assert membership["role"] == "member"
    assert membership["dateEnd"] == "2025-06-11T08:27:00Z"
    assert membership["replacing"] is None
    assert office["role"] == "chair" and office["roleLabel"] == "elnök"
    assert office["reason"] == "tisztségről lemondott"
    # Whom they took over from — also carried with a faction suffix.
    assert office["replacing"] == "Hende Csaba"


def test_role_slugs_are_stable_and_unknown_titles_survive():
    assert fel.committee_role("elnök") == "chair"
    assert fel.committee_role("Alelnök") == "deputy-chair"
    assert fel.committee_role("tag") == "member"
    # Never dropped, just unranked (SCR-5).
    assert fel.committee_role("előadó") == "other"
    assert fel.committee_role(None) == "other"


_MEETING_FIELDS = {"bizottsagId": 0, "bizottsagNeve": 1, "ulesTipusa": 2,
                   "ulesId": 3, "ulesSorszam": 4, "jegyzokonyvPath": 5,
                   "evenBeluliSorszam": 6, "ulesDatuma": 7,
                   "hatarozatkepesseg": 8, "ulesHosszaMasodPercben": 9}


def test_meetings_resolve_the_minutes_path_and_keep_rows_without_one():
    rows = [
        ["2742534", "Gazdasági Bizottság", "nyilvános", "13197594", 88,
         "/biz42/bizjkv42/GAB/2603091.pdf", "4/2026", "2026-03-09T11:30:00Z",
         "Határozatképes", 1620],
        ["2742534", "Gazdasági Bizottság", "zárt", "13197567", 87, None,
         "3/2026", "2026-03-03T10:02:00Z", "Határozatképtelen", 600],
    ]
    meetings = _client(rows, _MEETING_FIELDS).committee_meetings(
        42, "2022-05-02", "2026-05-08")
    assert meetings[0]["minutesUrl"] == (
        "https://www.parlament.hu/biz42/bizjkv42/GAB/2603091.pdf")
    assert meetings[0]["durationS"] == 1620
    assert meetings[1]["minutesUrl"] is None
    assert meetings[1]["quorum"] == "Határozatképtelen"


_STAT_FIELDS = {"bizNev": 0, "bizId": 1, "osszesen": 2, "idotartam": 3,
                "osszesenHatarozatkepes": 4, "hatarozatkeptelen": 5,
                "hatarozatkeptelenneValtHatarozatkepes": 6,
                "nemFogadtaElHatarozatkepes": 7, "nemOsszesitoSor": 8}


_UPCOMING_FIELDS = {"fejlec": 0, "nap": 1, "napNev": 2, "idopont": 3,
                    "idopontOrder": 4, "adatok": 5, "helyszin": 6}

_UPCOMING_BLOCK = {
    "metadata": {"fieldnames": {"id": 0, "elmaradt": 1, "bizottsagNev": 2,
                                "bizottsagId": 3, "helyszin": 4, "hely": 5}},
    "rows": [["m-1", "", "Művelődési Bizottság", "biz-9", "Széll Kálmán terem",
              "(Országház főemelet 64.)"]],
}


def test_upcoming_asks_from_today_not_from_the_start_of_the_term():
    """`pIdoszakEleje` is a *from* date with no upper bound: asked from the
    cycle's start it returns every sitting ever put in the diary (369 for cycle
    43 in September, against the couple of dozen still ahead)."""
    http = _FakeHttp({"metadata": {"fieldnames": _UPCOMING_FIELDS},
                      "rows": [], "response": {"totalSize": 0, "pageSize": 1000}})
    sent = []
    real = http.post_json
    http.post_json = lambda url, body, headers=None: (sent.append(body),
                                                      real(url, body, headers))[1]
    client = fel.FelicitasClient(http)
    client.committee_upcoming(43, "2026-05-09", "2027-09-21")
    assert sent[0]["pIdoszakEleje"] == fel._today() != "2026-05-09"
    # …and a backfill can still ask from further back.
    client.committee_upcoming(43, "2026-05-09", "2027-09-21", from_date="2026-05-09")
    assert sent[1]["pIdoszakEleje"] == "2026-05-09"


def test_upcoming_flattens_the_day_blocks_and_reads_the_cancel_marker():
    rows = [["2026.09.21 - ", "2026.09.21", "(hétfő)", "10:30", 10.5,
             _UPCOMING_BLOCK, False]]
    items = _client(rows, _UPCOMING_FIELDS).committee_upcoming(
        43, "2026-05-09", "2027-09-21")
    assert len(items) == 1
    u = items[0]
    assert (u["committeeId"], u["date"], u["time"]) == ("biz-9", "2026-09-21", "10:30")
    assert u["venue"] == "Széll Kálmán terem (Országház főemelet 64.)"
    # Upstream marks a called-off sitting by filling `elmaradt`; "" is still on.
    assert u["cancelled"] is False


def test_upcoming_marks_a_cancelled_sitting():
    block = {**_UPCOMING_BLOCK,
             "rows": [["m-1", "elmarad", "Művelődési Bizottság", "biz-9", None, None]]}
    rows = [["2026.09.21 - ", "2026.09.21", "(hétfő)", "10:30", 10.5, block, False]]
    u = _client(rows, _UPCOMING_FIELDS).committee_upcoming(
        43, "2026-05-09", "2027-09-21")[0]
    assert u["cancelled"] is True
    assert u["venue"] is None


def test_meeting_stats_drop_the_summary_row():
    rows = [
        ["Gazdasági Bizottság", "2742534", 88, 4400, 86, 2, 0, 0, True],
        ["Összesen", None, 998, 50000, 980, 18, 0, 0, False],
    ]
    stats = _client(rows, _STAT_FIELDS).committee_meeting_stats(
        42, "2022-05-02", "2026-05-08")
    assert [s["committeeId"] for s in stats] == ["2742534"]
    assert stats[0]["meetings"] == 88 and stats[0]["totalMinutes"] == 4400


# --- the continuous sync stage --------------------------------------------

class _SyncFelicitas(FakeFelicitas):
    """The committee stage's slice of the client, plus the cycle-range lookup
    `sync` resolves its date bounds from."""

    def cycle_ranges(self):
        return {43: {"start": "2026-05-09", "end": None}}


def _sync(tmp_path, fel, **kw):
    from parlamonitor import sync
    from parlamonitor.config import Paths
    paths = Paths(tmp_path)
    paths.ensure()
    state = {}
    changed = sync._sync_committees(fel, paths, 43, state,
                                    force=kw.get("force", False),
                                    with_detail=kw.get("with_detail", True))
    return paths, state, changed


def test_sync_writes_the_registry_on_a_first_pass(tmp_path):
    paths, state, changed = _sync(tmp_path, _SyncFelicitas())
    assert changed is True
    assert paths.committees_file(43).exists()
    assert state["committees"]["count"] == 2


def test_sync_rewrites_nothing_when_the_registry_is_unchanged(tmp_path):
    from parlamonitor import sync
    from parlamonitor.config import Paths
    paths = Paths(tmp_path)
    paths.ensure()
    fel = _SyncFelicitas()
    state = {}
    assert sync._sync_committees(fel, paths, 43, state,
                                 force=False, with_detail=True) is True
    before = paths.committees_file(43).stat().st_mtime_ns
    # A second pass over an unchanged upstream must leave the file alone, so the
    # DB loader's mtime-keyed incremental update has nothing to redo.
    assert sync._sync_committees(fel, paths, 43, state,
                                 force=False, with_detail=True) is False
    assert paths.committees_file(43).stat().st_mtime_ns == before


def _reg_for_fp():
    return {
        "meta": {"counts": {}},
        "data": [{"committeeId": "c1", "name": "Egy Bizottság", "parentId": None,
                  "type": "állandó", "dateEnd": None}],
        "members": [{"committeeId": "c1", "personID": "a001", "role": "member",
                     "factionName": "Fidesz"}],
        "terms": [],
        "meetings": [{"meetingId": "u1", "datetime": "2026-06-10T09:00:00Z",
                      "quorum": "Határozatképes", "durationS": 600,
                      "minutesUrl": None}],
        "documents": [{"committeeId": "c1", "billId": "b1",
                       "status": "kijelölve", "referredAt": "2026-06-01"}],
        "submissions": [],
        "upcoming": [{"meetingId": "n1", "committeeId": "c1",
                      "date": "2026-06-22", "time": "10:30",
                      "venue": "Terem", "cancelled": False}],
    }


@pytest.mark.parametrize("mutate, what", [
    (lambda r: r["members"].__setitem__(0, {**r["members"][0], "personID": "b002"}),
     "a seat changing hands 1-for-1"),
    (lambda r: r["members"].__setitem__(0, {**r["members"][0], "role": "chair"}),
     "a member becoming chair"),
    (lambda r: r["data"].__setitem__(0, {**r["data"][0], "name": "Más Bizottság"}),
     "a committee renamed"),
    (lambda r: r["meetings"].__setitem__(
        0, {**r["meetings"][0], "minutesUrl": "https://www.parlament.hu/x.pdf"}),
     "minutes published for a known meeting"),
    (lambda r: r["documents"].__setitem__(
        0, {**r["documents"][0], "status": "részletes vita lezárva"}),
     "a bill's committee stage advancing"),
    (lambda r: r["upcoming"].__setitem__(
        0, {**r["upcoming"][0], "cancelled": True}),
     "a scheduled meeting called off"),
])
def test_sync_notices_changes_that_move_no_count(mutate, what):
    """Everything a pass exists to spot leaves the row counts exactly where
    they were; a counts-only fingerprint would report "nothing moved"."""
    from parlamonitor import sync
    reg = _reg_for_fp()
    before = sync._committees_fingerprint(reg)
    mutate(reg)
    assert sync._committees_fingerprint(reg) != before, what


def test_sync_fingerprint_is_stable_under_reordering():
    """Upstream is free to return the same rows in another order; that must not
    read as a change and rewrite the file."""
    from parlamonitor import sync
    reg = _reg_for_fp()
    reg["members"].append({"committeeId": "c1", "personID": "b002",
                           "role": "chair", "factionName": "TISZA"})
    before = sync._committees_fingerprint(reg)
    reg["members"].reverse()
    assert sync._committees_fingerprint(reg) == before
