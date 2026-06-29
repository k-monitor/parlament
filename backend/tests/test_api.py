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


def test_session_wordcloud(client):
    """WCLOUD-1/2: the sitting word cloud returns frequency-ranked topical words,
    stop-words and short tokens dropped."""
    d = client.get("/api/v1/proceedings/sessions/43001/wordcloud").json()
    assert d["session_id"] == "43001" and d["date"] == "2026-05-09"
    words = {w["text"]: w["count"] for w in d["words"]}
    # content words survive (folded to lowercase)
    assert "költségvetés" in words and "ágazati" in words and "fejlesztés" in words
    # stop-word ("kérdés") and short token ("ügye", < 4 chars is "az"/"a") dropped
    assert "kérdés" not in words
    assert all(len(w) >= 4 for w in words)


def test_session_wordcloud_missing_session(client):
    assert client.get("/api/v1/proceedings/sessions/99999/wordcloud").status_code == 404


def test_session_top_speakers(client):
    """TOPSPK-1/2: the sitting toplist ranks known representatives by total
    speaking time, excluding procedural speeches and unattributed speakers."""
    d = client.get("/api/v1/proceedings/sessions/43001/top-speakers").json()
    assert d["session_id"] == "43001" and d["date"] == "2026-05-09"
    speakers = d["speakers"]
    # Both speeches are non-procedural and attributed (k001, n002); ranked by time.
    assert [s["person_id"] for s in speakers] == ["k001", "n002"]
    top = speakers[0]
    assert top["label"] == "Kovács Béla" and top["speeches"] == 1
    assert top["seconds"] == 30 and top["faction"]["label"] == "Fidesz"
    # Sorted by speaking time descending.
    assert speakers[0]["seconds"] >= speakers[1]["seconds"]


def test_session_top_speakers_missing_session(client):
    assert client.get("/api/v1/proceedings/sessions/99999/top-speakers").status_code == 404


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


def test_representative_statistics_shows_bills_when_module_enabled(client):
    """REP-3: with the Bills module live, the bills-submitted metric is shown."""
    d = client.get("/api/v1/representatives/k001/statistics").json()
    assert d["totals"]["speech_count"] == 1
    assert d["totals"]["speaking_seconds"] == 30.0
    assert d["totals"]["bills_available"] is True
    assert d["totals"]["bills_submitted"] == 3   # from upstream per-cycle counts
    assert d["methodology"]                       # REP-5 methodology note present
    assert d["over_time"]                          # trend data present


def test_representative_statistics_hides_bills_when_module_disabled(client, monkeypatch):
    """REP-3/EXT-6: with the Bills module disabled, the metric is hidden, not faked."""
    from app.config import settings
    monkeypatch.setattr(settings, "enabled_modules", ["proceedings", "representatives"])
    d = client.get("/api/v1/representatives/k001/statistics").json()
    assert d["totals"]["bills_available"] is False
    assert d["totals"]["bills_submitted"] is None


def test_representative_speeches_list(client):
    d = client.get("/api/v1/representatives/k001/speeches").json()
    assert d["total"] == 1
    assert d["speeches"][0]["uid"] == "43001-1"
    assert d["speeches"][0]["excerpt"]


def test_representative_statistics_scoped_to_selected_cycle(client):
    """§4A: stats are scoped to `period`. The test DB only has cycle 43, so the
    cycle-43 totals match the all-cycles totals, while cycle 42 (no data) is
    zeroed — proving previous-cycle data never leaks into another cycle."""
    cyc43 = client.get("/api/v1/representatives/k001/statistics",
                       params={"period": 43}).json()
    assert cyc43["scope"]["period"] == 43
    assert "43" in cyc43["scope"]["description"]
    assert cyc43["totals"]["speech_count"] == 1
    assert cyc43["totals"]["bills_submitted"] == 3      # own bills in cycle 43

    cyc42 = client.get("/api/v1/representatives/k001/statistics",
                       params={"period": 42}).json()
    assert cyc42["totals"]["speech_count"] == 0
    assert cyc42["totals"]["speaking_seconds"] == 0
    assert cyc42["over_time"] == []
    assert cyc42["scope"]["sessions_covered"] == 0
    assert cyc42["totals"]["bills_submitted"] is None   # no cycle-42 bills


def test_representative_speeches_scoped_to_selected_cycle(client):
    """§4A: an MP's speech list only covers the selected cycle's sittings."""
    assert client.get("/api/v1/representatives/k001/speeches",
                      params={"period": 43}).json()["total"] == 1
    assert client.get("/api/v1/representatives/k001/speeches",
                      params={"period": 42}).json()["total"] == 0


def test_representatives_list_scoped_to_selected_cycle(client):
    """§4A: the list shows only MPs serving in the cycle, with that cycle's stats."""
    cyc43 = client.get("/api/v1/representatives", params={"period": 43}).json()
    assert cyc43["total"] == 2
    kovacs = next(r for r in cyc43["representatives"] if r["person_id"] == "k001")
    assert kovacs["speech_count"] == 1
    # No MPs served in the (empty) cycle 42, so the list is empty there.
    assert client.get("/api/v1/representatives", params={"period": 42}).json()["total"] == 0


def test_representatives_list_filters_and_sorts(client):
    everyone = client.get("/api/v1/representatives").json()
    assert everyone["total"] == 2
    by_time = client.get("/api/v1/representatives",
                         params={"sort": "speaking_time"}).json()
    # Kovács (30s) ranks above Nagy (0s, no transcript).
    assert by_time["representatives"][0]["person_id"] == "k001"


def test_representatives_name_search_is_accent_insensitive(client):
    """§4B FOLD-1: an unaccented query matches accented names (`kovacs` → Kovács)."""
    # Accent-stripped query matches.
    r = client.get("/api/v1/representatives", params={"q": "kovacs"}).json()
    assert [x["person_id"] for x in r["representatives"]] == ["k001"]
    # Case-insensitive too, and the accented form still matches itself.
    assert client.get("/api/v1/representatives",
                      params={"q": "KOVÁCS"}).json()["total"] == 1
    # Display text keeps its original accents (FOLD-5).
    assert r["representatives"][0]["label"] == "Kovács Béla"


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
    monkeypatch.setenv("PARLAMONITOR_MODULES", "proceedings")
    monkeypatch.setenv("PARLAMONITOR_DB", str(db_path))
    importlib.reload(config_module)
    import app.main as main_module
    importlib.reload(main_module)
    from fastapi.testclient import TestClient
    c = TestClient(main_module.app)
    assert c.get("/api/v1/proceedings/sessions").status_code == 200
    assert c.get("/api/v1/representatives").status_code == 404
    # restore for other tests
    monkeypatch.delenv("PARLAMONITOR_MODULES")
    importlib.reload(config_module)
    importlib.reload(main_module)
