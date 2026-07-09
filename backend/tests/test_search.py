"""Search correctness: Hungarian accent/case folding, prefix morphology, exact
phrase, and query construction safety (SEA-1/SEA-2, OPS-3)."""

from __future__ import annotations

import pytest

from app.search import build_match


@pytest.mark.parametrize("query,expected", [
    ("költségvetés", '"költségvetés"*'),
    ("a b", '"a"* "b"*'),
    ('"tisztelt ház"', '"tisztelt ház"'),
    ('ágazat "nemzeti ügy"', '"ágazat"* "nemzeti ügy"'),
])
def test_build_match(query, expected):
    assert build_match(query) == expected


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
