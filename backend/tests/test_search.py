"""Search correctness: Hungarian accent/case folding, prefix morphology, exact
phrase, and query construction safety (SEA-1/SEA-2, OPS-3)."""

from __future__ import annotations

import pytest

from app.search import build_match


@pytest.mark.parametrize("query,expected", [
    ("költségvetés", '"költségvetés"*'),
    # Two characters is the default floor, so "eu" keeps its suffixes …
    ("eu b", '"eu"* "b"'),
    # … while a single character is matched exactly: `a*` is a scan of two thirds
    # of the corpus, not a search (see settings.min_prefix_len).
    ("a b", '"a" "b"'),
    ('"tisztelt ház"', '"tisztelt ház"'),
    ('ágazat "nemzeti ügy"', '"ágazat"* "nemzeti ügy"'),
])
def test_build_match(query, expected):
    assert build_match(query) == expected


def test_prefix_floor_is_configurable(monkeypatch):
    """The floor is a knob, not a constant: a deployment that would rather pay for
    short prefixes can lower it, and one on slower storage can raise it."""
    # Patched on the Settings object `app.search` itself holds, not on
    # `app.config.settings`: modules bind `settings` at import, and another test in
    # the suite `importlib.reload`s app.config, rebinding that name to a fresh
    # object — the hazard test_og.py and test_compare.py both document.
    from app import search as search_module

    monkeypatch.setattr(search_module.settings, "min_prefix_len", 1)
    assert build_match("a b") == '"a"* "b"*'
    monkeypatch.setattr(search_module.settings, "min_prefix_len", 5)
    assert build_match("ágazat ügy") == '"ágazat"* "ügy"'


def test_build_match_empty():
    assert build_match("") is None
    assert build_match('"" ') is None


def test_injection_neutralized():
    # FTS operators inside terms are quoted away, not executed.
    assert build_match("foo OR bar") == '"foo"* "OR"* "bar"*'


def test_accent_and_case_folding(client):
    # "koltsegvetes" (no accents, lowercase) must match "költségvetés".
    r = client.get("/api/v1/proceedings/search", params={"q": "koltsegvetes"})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    assert "<mark>" in data["results"][0]["highlighted"]


def test_uppercase_query_matches_lowercase_text(client):
    r = client.get("/api/v1/proceedings/search", params={"q": "AGAZATI"})
    assert r.json()["total"] >= 1


def test_prefix_morphology(client):
    # "koltsegvet" prefix should still hit "költségvetés".
    r = client.get("/api/v1/proceedings/search", params={"q": "koltsegvet"})
    assert r.json()["total"] >= 1


def test_exact_phrase(client):
    hit = client.get("/api/v1/proceedings/search", params={"q": '"fontos kérdés"'})
    assert hit.json()["total"] >= 1
    miss = client.get("/api/v1/proceedings/search", params={"q": '"kérdés fontos"'})
    assert miss.json()["total"] == 0


def test_search_result_carries_seek_and_provenance(client):
    r = client.get("/api/v1/proceedings/search", params={"q": "koltsegvetes"})
    res = r.json()["results"][0]
    assert res["time_start"] == 10.0          # seekable into the day stream
    assert res["speech_uid"] == "43001-1"
    assert res["timing"]["estimated"] is True  # VIE-6 disclosure
    assert res["speaker"]["label"] == "Kovács Béla"


def test_search_speaker_filter_narrows_the_whole_result_set(client):
    # SEA-3: the speaker filter is combinable like every other filter, and the
    # trend (SEA-8) and breakdown (SEA-9) describe the same narrowed set — they
    # share one WHERE with the result list, so a filter that reaches one of them
    # and not the others would be a silent lie in the charts.
    base = client.get("/api/v1/proceedings/search", params={"q": "koltsegvetes"})
    assert base.json()["total"] >= 1
    # Kovács Béla (k001) is the one who said it in the fixture corpus …
    mine = client.get("/api/v1/proceedings/search",
                      params={"q": "koltsegvetes", "person_id": "k001"}).json()
    assert mine["total"] == base.json()["total"]
    assert all(r["speaker"]["person_id"] == "k001" for r in mine["results"])
    # … and Nagy Anna (n002) did not.
    other = client.get("/api/v1/proceedings/search",
                       params={"q": "koltsegvetes", "person_id": "n002"}).json()
    assert other["total"] == 0 and other["results"] == []

    trend = client.get("/api/v1/proceedings/search/trend",
                       params={"q": "koltsegvetes", "person_id": "n002"}).json()
    assert trend["buckets"] == []
    bd = client.get("/api/v1/proceedings/search/breakdown",
                    params={"q": "koltsegvetes", "person_id": "k001"}).json()
    assert [s["person_id"] for s in bd["speakers"]] == ["k001"]


def test_search_without_a_query_lists_the_speakers_speeches(client, db_path):
    # SEA-3: a speaker is filter enough to search with. There is no keyword to
    # match, so the result unit becomes the speech — one row, shown by its
    # opening — and the ordering falls back to the sitting date, bm25 having
    # nothing to rank.
    _seed_prior_speech(db_path)   # a speech by somebody else, right before his
    r = client.get("/api/v1/proceedings/search", params={"person_id": "k001"})
    assert r.status_code == 200
    data = r.json()
    assert data["query"] == "" and data["match"] is None
    assert data["sort"] == "date_desc"       # relevance needs a term to rank by
    assert data["total"] == 1                # his one speech, not its sentences
    hit = data["results"][0]
    assert hit["speech_uid"] == "43001-1" and hit["sentence_ord"] == 0
    assert hit["highlighted"] == "A költségvetés fontos kérdés."   # nothing marked
    # The preview is the speech's own opening: it neither reaches back into the
    # speech before it nor runs on into the one after.
    assert hit["context"]["before"] == []
    assert [c["text"] for c in hit["context"]["after"]] == ["Az ÁGAZATI fejlesztés ügye sürgős!"]
    # …and it is still just a filter, combinable with the rest (his speech is
    # `procedural`, so an agenda filter for the vote excludes it).
    narrowed = client.get("/api/v1/proceedings/search",
                          params={"person_id": "k001", "agenda_type": "voting"}).json()
    assert narrowed["total"] == 0
    # Nagy Anna has two speeches in the corpus here — the seeded one above and
    # the fixture's vote, which carries no transcript — and only the one with
    # text can be listed: the page searches the transcript, not the day's agenda.
    other = client.get("/api/v1/proceedings/search",
                       params={"person_id": "n002"}).json()
    assert [r["speech_uid"] for r in other["results"]] == ["43001-0"]


def test_search_without_a_query_or_a_speaker_is_refused(client):
    # The corpus itself is not a result set: with neither a term nor a speaker
    # there is nothing to bound the scan, so it is a 400 rather than a read of
    # every sentence the House has ever spoken.
    assert client.get("/api/v1/proceedings/search").status_code == 400
    assert client.get("/api/v1/proceedings/search",
                      params={"faction_id": 7}).status_code == 400
    # A term that holds nothing searchable is the same answer …
    assert client.get("/api/v1/proceedings/search",
                      params={"q": '""'}).status_code == 400
    # … unless a speaker carries the search on its own.
    assert client.get("/api/v1/proceedings/search",
                      params={"q": '""', "person_id": "k001"}).status_code == 200


def test_search_aggregates_follow_a_query_less_search(client):
    # SEA-8/SEA-9 describe the same result set as the list, in both modes: the
    # trend counts the speeches, and the breakdown attributes them.
    trend = client.get("/api/v1/proceedings/search/trend",
                       params={"person_id": "k001"}).json()
    assert trend["query"] == ""
    assert sum(b["hits"] for b in trend["buckets"]) == 1
    bd = client.get("/api/v1/proceedings/search/breakdown",
                    params={"person_id": "k001"}).json()
    assert [(s["label"], s["hits"]) for s in bd["speakers"]] == [("Kovács Béla", 1)]
    assert [(f["label"], f["hits"]) for f in bd["factions"]] == [("Fidesz", 1)]


def test_search_trend_buckets_hits_over_time(client):
    # SEA-8: the popularity chart counts matching sentences per calendar bucket,
    # honouring the same filters as /search.
    r = client.get("/api/v1/proceedings/search/trend", params={"q": "koltsegvetes"})
    assert r.status_code == 200
    data = r.json()
    assert data["granularity"] in ("day", "week", "month")
    assert sum(b["hits"] for b in data["buckets"]) >= 1
    assert all(b["period"] and b["hits"] >= 1 for b in data["buckets"])
    # Same faction filter as the result list → same emptiness.
    fac = client.get("/api/v1/representatives/factions").json()["factions"]
    tisza = next(f for f in fac if f["label"] == "TISZA")["id"]
    empty = client.get("/api/v1/proceedings/search/trend",
                       params={"q": "koltsegvetes", "faction_id": tisza}).json()
    assert empty["buckets"] == []


def _freeze_today(monkeypatch, y, m, d):
    # Pin date.today() in the router so the ongoing-cycle window is deterministic.
    import app.modules.proceedings.router as router_mod
    from datetime import date as _date

    class _Frozen(_date):
        @classmethod
        def today(cls):
            return cls(y, m, d)

    monkeypatch.setattr(router_mod, "date", _Frozen)


def test_search_trend_ongoing_cycle_window_grows_over_time(client, monkeypatch):
    # SEA-8: a cycle-scoped chart spans the whole cycle. The ongoing cycle (43,
    # started 2026-05-09, no end date) gets a synthesised end a whole number of
    # years out — enough to cover the elapsed time, min one year, max the mandate —
    # so young data sits left-aligned with room to grow and the axis widens with age.
    _freeze_today(monkeypatch, 2026, 7, 14)  # ~2 months in → a 1-year window
    d1 = client.get("/api/v1/proceedings/search/trend",
                    params={"q": "koltsegvetes", "period": 43}).json()
    assert d1["buckets"], "fixture must have cycle-43 hits for this to test anchoring"
    assert d1["start"] == "2026-05-09"   # cycle start, not the first hit
    assert d1["end"] == "2027-05-09"     # start + 1 year (elapsed < 1y)
    assert d1["granularity"] == "week"   # a ~1-year span buckets weekly

    _freeze_today(monkeypatch, 2028, 11, 1)  # ~2.5 years in → widened to 3 years
    d2 = client.get("/api/v1/proceedings/search/trend",
                    params={"q": "koltsegvetes", "period": 43}).json()
    assert d2["end"] == "2029-05-09"     # start + ceil(elapsed) = 3 years
    assert d2["granularity"] == "month"  # a ~3-year span buckets monthly

    _freeze_today(monkeypatch, 2035, 1, 1)  # long past the mandate → capped at 4y
    d3 = client.get("/api/v1/proceedings/search/trend",
                    params={"q": "koltsegvetes", "period": 43}).json()
    assert d3["end"] == "2030-05-09"     # start + 4 years, never wider


def test_search_breakdown_groups_by_faction_and_speaker(client):
    # SEA-9: the breakdown counts matching sentences per faction and per
    # representative, honouring the same filters as /search.
    r = client.get("/api/v1/proceedings/search/breakdown", params={"q": "koltsegvetes"})
    assert r.status_code == 200
    data = r.json()
    # Kovács Béla (Fidesz) is the one who said it in the fixture corpus.
    assert any(s["label"] == "Kovács Béla" and s["hits"] >= 1 for s in data["speakers"])
    assert all(s["person_id"] for s in data["speakers"])      # only resolved MPs
    assert any(f["label"] == "Fidesz" and f["hits"] >= 1 for f in data["factions"])
    assert all(f["color"] for f in data["factions"])          # colour for charts
    # Same faction filter as the result list → same emptiness.
    fac = client.get("/api/v1/representatives/factions").json()["factions"]
    tisza = next(f for f in fac if f["label"] == "TISZA")["id"]
    empty = client.get("/api/v1/proceedings/search/breakdown",
                       params={"q": "koltsegvetes", "faction_id": tisza}).json()
    assert empty["factions"] == [] and empty["speakers"] == []


def test_search_result_carries_surrounding_context(client):
    # SEA-4: a hit carries a few sentences of surrounding transcript context so the
    # moment can be read without opening the viewer. Speech 43001-1 has two
    # sentences — the költségvetés hit (the first) shows the ágazati sentence after
    # it and nothing before it (it is the speech's first sentence).
    r = client.get("/api/v1/proceedings/search", params={"q": "koltsegvetes"})
    res = r.json()["results"][0]
    assert res["sentence_ord"] == 0
    assert any("ÁGAZATI" in c["text"] for c in res["context"]["after"])
    assert res["context"]["before"] == []
    # …and the ágazati hit shows the költségvetés sentence before it.
    res2 = client.get("/api/v1/proceedings/search",
                      params={"q": "agazati"}).json()["results"][0]
    assert any("költségvetés" in c["text"] for c in res2["context"]["before"])


def _seed_prior_speech(db_path):
    """A speech before the sitting's first one (speech_index 0), by a different MP,
    so a hit in the first speech has a neighbouring speech to draw context from."""
    import sqlite3
    c = sqlite3.connect(db_path)
    c.execute(
        """INSERT INTO speech (uid, origin_id, session_id, period_number,
               speech_index, person_id, speaker_label, has_text)
           VALUES ('43001-0','43-1-0','43001',43,0,'n002','Nagy Anna',1)""")
    c.execute("INSERT INTO sentence (speech_id, ord, text) VALUES ('43001-0',0,?)",
              ("Előzetes megjegyzés a vitához.",))
    c.commit(); c.close()


def test_search_context_spills_into_adjacent_speech(client, db_path):
    # SEA-4: when the hit sits at a speech boundary its context is drawn from the
    # speech before/after it, not only its own speech. The költségvetés hit is the
    # first sentence of speech 43001-1, so its "before" context comes from 43001-0.
    _seed_prior_speech(db_path)
    res = client.get("/api/v1/proceedings/search",
                     params={"q": "koltsegvetes"}).json()["results"][0]
    before = res["context"]["before"]
    assert any(c["text"] == "Előzetes megjegyzés a vitához." for c in before)
    assert any(c["person_id"] == "n002" for c in before)   # a different speaker


def _seed_earlier_hit(db_path):
    """A második, korábbi ülésnap költségvetés-találata, hogy a dátum szerinti
    rendezésnek legyen mit sorba raknia."""
    import sqlite3
    c = sqlite3.connect(db_path)
    c.execute("INSERT INTO session (id, period_number, date) VALUES ('43000',43,'2026-04-01')")
    c.execute(
        """INSERT INTO speech (uid, origin_id, session_id, period_number,
               speech_index, person_id, speaker_label, has_text, time_start, time_end)
           VALUES ('43000-1','43-0-1','43000',43,1,'k001','Kovács Béla',1,0.0,5.0)""")
    c.execute("INSERT INTO sentence (speech_id, ord, text) VALUES ('43000-1',0,?)",
              ("A költségvetés régi témája.",))
    c.commit(); c.close()


def test_search_sort_by_date(client, db_path):
    # SEA-10: results can be ordered by sitting date as well as relevance; the
    # chosen ordering is echoed and an unknown value falls back to relevance
    # (the ORDER BY comes from a whitelist, never from the raw request).
    _seed_earlier_hit(db_path)   # a hit on 2026-04-01, older than 43001 (2026-05-09)
    asc = client.get("/api/v1/proceedings/search",
                     params={"q": "koltsegvetes", "sort": "date_asc"}).json()
    desc = client.get("/api/v1/proceedings/search",
                      params={"q": "koltsegvetes", "sort": "date_desc"}).json()
    assert asc["sort"] == "date_asc" and desc["sort"] == "date_desc"
    assert asc["results"][0]["date"] == "2026-04-01"    # oldest first
    assert desc["results"][0]["date"] == "2026-05-09"   # newest first
    bad = client.get("/api/v1/proceedings/search",
                     params={"q": "koltsegvetes", "sort": "date_desc; DROP TABLE speech"}).json()
    assert bad["sort"] == "relevance"                   # unknown value → relevance


def test_filter_by_faction_and_agenda(client):
    # Filter to a faction with no costing-related hit.
    fac = client.get("/api/v1/representatives/factions").json()["factions"]
    tisza = next(f for f in fac if f["label"] == "TISZA")["id"]
    r = client.get("/api/v1/proceedings/search",
                   params={"q": "koltsegvetes", "faction_id": tisza})
    assert r.json()["total"] == 0   # Kovács (Fidesz) said it, not TISZA
