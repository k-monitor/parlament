"""Offline tests for the Modal word-cloud offload (WCLOUD-6).

No network / no Modal account: the deployed service is stubbed so we verify the
*client* batching + ordering and the *loader* integration — including that the
Modal backend is cache-compatible with local HuSpaCy (same method tag) and that
an unchanged second pass reuses the cache instead of re-dispatching.
"""

from __future__ import annotations

import sqlite3

from app import loader, nlp, nlp_modal


# --- client: batching + result mapping -------------------------------------

def test_chunks_group_by_sentence_count():
    misses = [("s0", "f0", ["a", "b", "c"]),
              ("s1", "f1", ["d", "e", "f"]),
              ("s2", "f2", ["g"])]
    chunks = list(nlp_modal._chunks(misses, batch_sentences=5))
    # 3 sentences hits the threshold only after the 2nd sitting; the 3rd trails.
    assert [[m[0] for m in c] for c in chunks] == [["s0", "s1"], ["s2"]]


def test_extract_maps_results_in_order_with_entity_kinds(monkeypatch):
    class _Method:
        def map(self, payloads):
            # One result list per chunk, one dict per sitting in the chunk.
            for payload in payloads:
                yield [{"counts": {"törvény": 2, "orbán viktor": 1},
                        "entities": ["orbán viktor"]} for _ in payload]

    class _Svc:
        analyze_sessions = _Method()

    monkeypatch.setattr(nlp_modal, "_service", lambda app_name=None: _Svc())

    misses = [("43001", "fp1", ["m1", "m2"]), ("43002", "fp2", ["m3"])]
    out = list(nlp_modal.extract(misses, batch_sentences=100))

    assert [sid for sid, _, _ in out] == ["43001", "43002"]
    _, _, words = out[0]
    assert words["törvény"] == [2, "term"]
    assert words["orbán viktor"] == [1, "entity"]


def test_method_tag_matches_local_huspacy():
    # Cache/DB built with local huspacy and updated via Modal must interoperate.
    assert nlp_modal.method_tag() == nlp.method_tag()


# --- loader: backend resolution --------------------------------------------

def test_wordcloud_backend_selects_modal_when_available(monkeypatch):
    monkeypatch.setattr(loader.settings, "wordcloud_backend", "modal")
    monkeypatch.setattr(nlp_modal, "available", lambda: True)
    assert loader._nlp_backend("hu_core_news_md") == "modal"


def test_wordcloud_backend_falls_back_when_modal_unavailable(monkeypatch):
    monkeypatch.setattr(loader.settings, "wordcloud_backend", "modal")
    monkeypatch.setattr(nlp_modal, "available", lambda: False)
    monkeypatch.setattr(nlp, "available", lambda model=None: False)  # no local model
    assert loader._nlp_backend("hu_core_news_md") == "regex"


# --- loader: end-to-end rebuild via the (stubbed) Modal backend ------------

def test_rebuild_uses_modal_and_caches(monkeypatch, conn, db_path):
    calls = {"batches": 0, "sittings": 0}

    def fake_extract(misses, **_kw):
        calls["batches"] += 1
        for sid, fp, _texts in misses:
            calls["sittings"] += 1
            yield sid, fp, {"törvény": [3, "term"], "orbán viktor": [2, "entity"]}

    monkeypatch.setattr(loader.settings, "wordcloud_backend", "modal")
    monkeypatch.setattr(nlp_modal, "available", lambda: True)
    monkeypatch.setattr(nlp_modal, "extract", fake_extract)

    cache_dir = db_path.parent
    loader.rebuild_session_word_counts(conn, cache_dir)

    rows = dict(conn.execute(
        "SELECT word, kind FROM session_word_count WHERE session_id='43001'"))
    assert rows.get("törvény") == "term"
    assert rows.get("orbán viktor") == "entity"
    assert calls["sittings"] == 1          # the one sitting was dispatched once

    # The cache holds the sitting keyed by a HuSpaCy-compatible fingerprint…
    import json
    cache = json.loads((cache_dir / "wordcloud-cache.json").read_text())
    assert "43001" in cache["sessions"]

    # …and a second identical pass reuses it — nothing is re-dispatched to Modal.
    calls["sittings"] = 0
    loader.rebuild_session_word_counts(conn, cache_dir)
    assert calls["sittings"] == 0


# --- loader: per-cycle model split (current cycle vs archive) ---------------

def test_rebuild_routes_archive_cycle_to_archive_app(monkeypatch, tmp_path):
    """Two sittings in different electoral periods: the newest period's sitting is
    dispatched to the primary Modal app (transformer), the older one to the
    cheaper archive app — with per-model method tags in the fingerprints."""
    import json
    from tests.conftest import _registry, _session_record

    data = tmp_path / "data"
    (data / "processed").mkdir(parents=True)
    (data / "processed" / "representatives-43.json").write_text(
        json.dumps(_registry(), ensure_ascii=False))
    (data / "processed" / "42100-session.json").write_text(json.dumps(
        _session_record(session="42100", period=42, sitting=100, date="2025-11-03"),
        ensure_ascii=False))
    (data / "processed" / "43001-session.json").write_text(
        json.dumps(_session_record(), ensure_ascii=False))

    dispatched = []  # (app_name, [sids])

    def fake_extract(misses, app_name=None, **_kw):
        dispatched.append((app_name, sorted(sid for sid, _fp, _t in misses)))
        for sid, fp, _texts in misses:
            yield sid, fp, {"törvény": [1, "term"]}

    monkeypatch.setattr(loader.settings, "wordcloud_backend", "modal")
    monkeypatch.setattr(loader.settings, "huspacy_model", "hu_core_news_trf")
    monkeypatch.setattr(loader.settings, "huspacy_model_archive", "hu_core_news_md")
    monkeypatch.setattr(nlp_modal, "available", lambda: True)
    monkeypatch.setattr(nlp_modal, "extract", fake_extract)

    db = tmp_path / "split.db"
    loader.build_database(data, db)

    by_app = dict(dispatched)
    assert by_app[loader.settings.modal_app_name] == ["43001"]
    assert by_app[loader.settings.modal_app_name_archive] == ["42100"]

    # Both sittings got word rows despite the different models/apps.
    conn = sqlite3.connect(db)
    try:
        counts = dict(conn.execute(
            "SELECT session_id, COUNT(*) FROM session_word_count GROUP BY session_id"))
        assert counts.get("42100") and counts.get("43001")
    finally:
        conn.close()
