"""API contract tests: shapes the SPA relies on, module manifest, deep-linking,
sentence↔time mapping, and graceful degradation (OPS-3, EXT-3/EXT-6)."""

from __future__ import annotations


def test_meta_lists_modules_and_attribution(client):
    m = client.get("/api/v1/meta").json()
    names = {mod["name"] for mod in m["modules"]}
    assert {"proceedings", "representatives"} <= names
    assert m["source_attribution"]["url"] == "https://www.parlament.hu"  # LEGAL-1
    assert m["counts"]["sessions"] == 1


def test_health_ok(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"


def test_speech_viewer_payload(client):
    r = client.get("/api/v1/proceedings/speeches/43001-1")
    assert r.status_code == 200
    d = r.json()
    assert d["speech"]["speaker"]["label"] == "Kovács Béla"
    assert len(d["sentences"]) == 2
    # Sentences carry day-absolute timing for click-to-seek (VIE-3).
    assert d["sentences"][0]["time_start"] == 10.0
    assert d["session"]["video_uri"].endswith(".m3u8")     # VIE-2 HLS source
    # The VOD activation endpoint is carried through so the player can ping it.
    assert "playseq.php" in d["session"]["video_playseq"]
    assert d["speech"]["source_page"]                       # VIE-7 source link
    assert d["neighbours"]["next"] == "43001-2"             # prev/next nav


def test_speech_not_found(client):
    assert client.get("/api/v1/proceedings/speeches/99999-1").status_code == 404


def test_session_browse_groups_by_agenda(client):
    d = client.get("/api/v1/proceedings/sessions/43001").json()
    titles = [a["title"] for a in d["agenda"]]
    assert "Napirend előtt" in titles and "Szavazás" in titles
    # Each agenda item carries its speeches in order (use case 2).
    first = d["agenda"][0]
    assert first["speeches"][0]["uid"] == "43001-1"


def test_degraded_speech_renders(client):
    """VIE-8: video-only speech still returns with metadata + no-text flag."""
    d = client.get("/api/v1/proceedings/speeches/43001-2").json()
    assert d["speech"]["has_text"] is False
    assert d["sentences"] == []
    assert d["speech"]["speaker"]["label"] == "Nagy Anna"


def test_representative_profile(client):
    d = client.get("/api/v1/representatives/k001").json()
    assert d["label"] == "Kovács Béla"
    assert d["constituency"] == "Budapest 1."
    assert d["current_faction"]["label"] == "Fidesz"
    assert d["current_faction"]["color"] == "#FF6A13"
    assert d["education"]


def test_representative_statistics_hides_bills(client):
    """REP-3: bills metric hidden (not faked) until the Bills module."""
    d = client.get("/api/v1/representatives/k001/statistics").json()
    assert d["totals"]["speech_count"] == 1
    assert d["totals"]["speaking_seconds"] == 30.0
    assert d["totals"]["bills_available"] is False
    assert d["totals"]["bills_submitted"] is None
    assert d["methodology"]                       # REP-5 methodology note present
    assert d["over_time"]                          # trend data present


def test_representative_speeches_list(client):
    d = client.get("/api/v1/representatives/k001/speeches").json()
    assert d["total"] == 1
    assert d["speeches"][0]["uid"] == "43001-1"
    assert d["speeches"][0]["excerpt"]


def test_representatives_list_filters_and_sorts(client):
    everyone = client.get("/api/v1/representatives").json()
    assert everyone["total"] == 2
    by_time = client.get("/api/v1/representatives",
                         params={"sort": "speaking_time"}).json()
    # Kovács (30s) ranks above Nagy (0s, no transcript).
    assert by_time["representatives"][0]["person_id"] == "k001"


def test_factions_endpoint_has_averages(client):
    d = client.get("/api/v1/representatives/factions").json()
    fidesz = next(f for f in d["factions"] if f["label"] == "Fidesz")
    assert fidesz["mp_count"] == 1
    assert fidesz["avg_speaking_seconds"] == 30.0
    assert fidesz["color"] == "#FF6A13"


def test_disabled_module_not_mounted(monkeypatch, db_path):
    """EXT-6: a module absent from config is never mounted (routes 404)."""
    import importlib
    from app import config as config_module
    monkeypatch.setenv("OGYWATCH_MODULES", "proceedings")
    monkeypatch.setenv("OGYWATCH_DB", str(db_path))
    importlib.reload(config_module)
    import app.main as main_module
    importlib.reload(main_module)
    from fastapi.testclient import TestClient
    c = TestClient(main_module.app)
    assert c.get("/api/v1/proceedings/sessions").status_code == 200
    assert c.get("/api/v1/representatives").status_code == 404
    # restore for other tests
    monkeypatch.delenv("OGYWATCH_MODULES")
    importlib.reload(config_module)
    importlib.reload(main_module)
