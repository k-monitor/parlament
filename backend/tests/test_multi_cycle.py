"""Multi-cycle scope (§4A / CYC-1a): the repeatable `period` param.

The header's cycle chooser lets the reader put several electoral cycles in scope
at once, so every period-aware endpoint takes `period` as a repeatable query
param and must return the **union** of those cycles — never one of them, never
everything. The fixture DB holds cycle 43 only, so these tests seed a second,
smaller cycle (42) and then assert that `?period=42&period=43` equals 42 + 43 for
each shape of period filter in the API: a plain column filter, the precomputed
per-cycle aggregate rows, and the distinct-count aggregate that cannot simply be
summed across cycles.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def two_cycles(db_path):
    """Seed a cycle-42 sitting: one k001 speech (Fidesz, 100 s, one sentence),
    one bill and one vote — deliberately smaller than cycle 43's so a union is
    distinguishable from either cycle alone."""
    c = sqlite3.connect(db_path)
    fidesz = c.execute("SELECT id FROM faction WHERE label='Fidesz'").fetchone()[0]
    c.execute("INSERT INTO electoral_period (number, label, date_start, date_end) "
              "VALUES (42, '42. ciklus', '2022-05-02', '2026-04-30')")
    c.execute("INSERT INTO session (id, period_number, sitting, date) "
              "VALUES ('42001', 42, 1, '2024-03-05')")
    c.execute(
        """INSERT INTO speech (uid, origin_id, session_id, period_number,
               speech_index, person_id, speaker_label, faction_id, has_text,
               duration, procedural)
           VALUES ('42001-1','42-1-1','42001',42,1,'k001','Kovács Béla',?,1,100.0,0)""",
              (fidesz,))
    c.execute("INSERT INTO sentence (speech_id, ord, text) VALUES ('42001-1',0,?)",
              ("A költségvetés régi vitája.",))
    c.execute("INSERT INTO membership (person_id, faction_id, period_number) "
              "VALUES ('k001', ?, 42)", (fidesz,))
    # The aggregates the loader precomputes per cycle (REP-7).
    c.execute("INSERT INTO person_stats (person_id, period_number, speech_count, "
              "speaking_seconds, sentence_count) VALUES ('k001', 42, 1, 100.0, 1)")
    c.execute("INSERT INTO person_session_stats (person_id, session_id, date, "
              "speech_count, speaking_seconds) "
              "VALUES ('k001', '42001', '2024-03-05', 1, 100.0)")
    c.execute("INSERT INTO faction_stats (faction_id, period_number, speech_count, "
              "speaking_seconds, mp_count) VALUES (?, 42, 1, 100.0, 1)", (fidesz,))
    c.execute("INSERT INTO bill (id, bill_number, number_sort, period_number, title, "
              "type, main_type, status, submitted_date) "
              "VALUES ('bill-42','T/1',1,42,'Régi törvényjavaslat','törvényjavaslat',"
              "'T','kihirdetve','2024-03-01T09:00:00Z')")
    c.execute("INSERT INTO vote (id, period_number, vote_datetime, voting_mode, subject, "
              "result, yes, no, abstain, has_per_mp) "
              "VALUES ('v-42',42,'2024-03-05T10:00:00Z','Gépi szavazás','régi szavazás',"
              "'Elfogadva',1,0,0,1)")
    c.commit()
    c.close()
    return db_path


def _get(client, path, periods, **params):
    """GET `path` with `period` repeated once per cycle in scope."""
    return client.get(path, params=[("period", p) for p in periods]
                      + list(params.items())).json()


def test_sessions_union(client, two_cycles):
    """A plain column filter: the sittings list unions the selected cycles."""
    assert _get(client, "/api/v1/proceedings/sessions", [42])["total"] == 1
    assert _get(client, "/api/v1/proceedings/sessions", [43])["total"] == 1
    both = _get(client, "/api/v1/proceedings/sessions", [42, 43])
    assert both["total"] == 2
    assert {s["period_number"] for s in both["sessions"]} == {42, 43}
    # …and is still narrower than the unscoped list only when a cycle is left out.
    assert client.get("/api/v1/proceedings/sessions").json()["total"] == 2


def test_search_union(client, two_cycles):
    """The FTS filter: both cycles' költségvetés hits come back together."""
    one = _get(client, "/api/v1/proceedings/search", [43], q="koltsegvetes")
    other = _get(client, "/api/v1/proceedings/search", [42], q="koltsegvetes")
    both = _get(client, "/api/v1/proceedings/search", [42, 43], q="koltsegvetes")
    assert one["total"] == other["total"] == 1
    assert both["total"] == 2
    assert {r["period"] for r in both["results"]} == {42, 43}


def test_search_trend_axis_spans_every_selected_cycle(client, two_cycles):
    """The trend axis is anchored to the scope, so a two-cycle scope runs from the
    earlier cycle's start to the later one's (synthesised, ongoing) end."""
    t = _get(client, "/api/v1/proceedings/search/trend", [42, 43], q="koltsegvetes")
    assert t["start"] <= "2022-05-02"
    assert t["end"] >= "2026-05-09"
    assert sum(b["hits"] for b in t["buckets"]) == 2


def test_representative_statistics_sums_the_selected_cycles(client, two_cycles):
    """Per-cycle aggregate rows are summed, not picked: the two-cycle totals equal
    cycle 42's plus cycle 43's, and the scope is reported back in full."""
    c42 = _get(client, "/api/v1/representatives/k001/statistics", [42])["totals"]
    c43 = _get(client, "/api/v1/representatives/k001/statistics", [43])["totals"]
    both = _get(client, "/api/v1/representatives/k001/statistics", [42, 43])
    assert both["totals"]["speech_count"] == c42["speech_count"] + c43["speech_count"]
    assert (both["totals"]["speaking_seconds"]
            == c42["speaking_seconds"] + c43["speaking_seconds"])
    assert both["scope"]["periods"] == [42, 43]
    assert both["scope"]["period"] is None       # scalar only for a single cycle
    assert both["scope"]["sessions_covered"] == 2
    assert len(both["over_time"]) == 2


def test_representatives_list_sums_stats_across_cycles(client, two_cycles):
    """The MP list's speech counts likewise sum over the cycles in scope."""
    both = _get(client, "/api/v1/representatives", [42, 43])
    kovacs = next(r for r in both["representatives"] if r["person_id"] == "k001")
    assert kovacs["speech_count"] == 2
    # n002 only served in cycle 43, so a 42-only scope drops them entirely.
    assert {r["person_id"] for r in _get(client, "/api/v1/representatives",
                                         [42])["representatives"]} == {"k001"}


def test_factions_count_each_mp_once_across_cycles(client, two_cycles):
    """`mp_count` is a DISTINCT speaker count, so a multi-cycle scope must derive
    it over the whole scope rather than summing the per-cycle rows — k001 spoke
    for Fidesz in both cycles and must still count once."""
    fidesz = next(f for f in _get(client, "/api/v1/representatives/factions",
                                  [42, 43])["factions"] if f["label"] == "Fidesz")
    assert fidesz["mp_count"] == 1                    # not 1 + 1
    assert fidesz["speech_count"] == 2                # speeches DO add up
    assert fidesz["avg_speeches"] == 2


def test_bills_and_votes_union(client, two_cycles):
    """The module lists and their facet endpoints honour the same scope."""
    assert _get(client, "/api/v1/bills", [42])["total"] == 1
    both = _get(client, "/api/v1/bills", [42, 43])
    assert both["total"] == 4                          # 3 in cycle 43 + 1 in 42
    assert "kihirdetve" in _get(client, "/api/v1/bills/facets", [42])["statuses"]

    assert _get(client, "/api/v1/votes", [42])["total"] == 1
    assert _get(client, "/api/v1/votes", [42, 43])["total"] == 3


def test_unknown_and_duplicate_periods_are_harmless(client, two_cycles):
    """A repeated or unknown cycle number changes nothing: the scope is
    de-duplicated, and a cycle with no rows simply contributes none."""
    assert (_get(client, "/api/v1/proceedings/sessions", [43, 43])["total"]
            == _get(client, "/api/v1/proceedings/sessions", [43])["total"] == 1)
    assert _get(client, "/api/v1/proceedings/sessions", [43, 99])["total"] == 1
