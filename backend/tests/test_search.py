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


def test_filter_by_faction_and_agenda(client):
    # Filter to a faction with no costing-related hit.
    fac = client.get("/api/v1/representatives/factions").json()["factions"]
    tisza = next(f for f in fac if f["label"] == "TISZA")["id"]
    r = client.get("/api/v1/proceedings/search",
                   params={"q": "koltsegvetes", "faction_id": tisza})
    assert r.json()["total"] == 0   # Kovács (Fidesz) said it, not TISZA
