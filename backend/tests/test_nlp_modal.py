"""Offline tests for the Modal word-cloud offload (WCLOUD-6).

No network / no Modal account: the deployed service is stubbed so we verify the
*client* batching + ordering and the *loader* integration — including that the
Modal backend is cache-compatible with local HuSpaCy (same method tag) and that
an unchanged second pass reuses the cache instead of re-dispatching.
"""

from __future__ import annotations

import sqlite3

from app import loader, nlp, nlp_modal
from app.config import parse_modal_cycles


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

    assert [sid for sid, _, _, _ in out] == ["43001", "43002"]
    _, _, words, streams = out[0]
    assert words["törvény"] == [2, "term"]
    assert words["orbán viktor"] == [1, "entity"]
    # Lemmas were not asked for, so none come back — never a fabricated empty.
    assert streams is None


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


def test_wordcloud_backend_skips_modal_outside_the_cycle_scope(monkeypatch):
    """A cycle outside the Modal budget scope degrades exactly like a missing
    Modal client — even with the backend configured and available."""
    monkeypatch.setattr(loader.settings, "wordcloud_backend", "modal")
    monkeypatch.setattr(nlp_modal, "available", lambda: True)
    monkeypatch.setattr(nlp, "available", lambda model=None: False)  # no local model
    assert loader._nlp_backend("hu_core_news_md", modal_ok=False) == "regex"


# --- config: which cycles may spend Modal credit ---------------------------

def test_modal_cycles_default_is_the_latest_cycle_only():
    s = loader.settings
    assert parse_modal_cycles("latest") == "latest"
    assert s.modal_cycle_allowed(43, 43) is True
    assert s.modal_cycle_allowed(42, 43) is False
    # No period recorded → the sitting is one of the newest days; an unknown
    # latest (empty corpus) can't exclude anything.
    assert s.modal_cycle_allowed(None, 43) is True
    assert s.modal_cycle_allowed(42, None) is True


def test_modal_cycles_accepts_all_and_explicit_lists(monkeypatch):
    s = loader.settings
    monkeypatch.setattr(s, "modal_cycles", "all")
    assert s.modal_cycle_allowed(41, 43) is True
    monkeypatch.setattr(s, "modal_cycles", "42,43")
    assert s.modal_cycle_allowed(42, 43) is True
    assert s.modal_cycle_allowed(41, 43) is False
    # A typo must not silently open the offload up to the whole archive.
    monkeypatch.setattr(s, "modal_cycles", "yes please")
    assert s.modal_cycle_allowed(41, 43) is False


# --- loader: end-to-end rebuild via the (stubbed) Modal backend ------------

def test_rebuild_uses_modal_and_caches(monkeypatch, conn, db_path):
    calls = {"batches": 0, "sittings": 0}

    def fake_extract(misses, **_kw):
        calls["batches"] += 1
        for sid, fp, _texts in misses:
            calls["sittings"] += 1
            yield (sid, fp, {"törvény": [3, "term"], "orbán viktor": [2, "entity"]},
                   None)

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

    # The cache holds the sitting keyed by a HuSpaCy-compatible fingerprint,
    # and records which model produced it (self-describing, no fp recompute).
    import json
    cache = json.loads((cache_dir / "wordcloud-cache.json").read_text())
    assert "43001" in cache["sessions"]
    entry = cache["sessions"]["43001"]
    assert entry["model"] == loader.settings.huspacy_model
    assert entry["method"] == nlp.method_tag(loader.settings.huspacy_model)

    # …and a second identical pass reuses it — nothing is re-dispatched to Modal.
    calls["sittings"] = 0
    loader.rebuild_session_word_counts(conn, cache_dir)
    assert calls["sittings"] == 0


# --- loader: per-cycle model split (current cycle vs archive) ---------------

def _two_cycle_corpus(tmp_path):
    """A data dir with one sitting in the newest period (43001) and one in an
    older, frozen one (42100)."""
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
    return data


def _modal_build(monkeypatch, tmp_path, data, *, modal_cycles):
    """Build ``data`` with the Modal backend stubbed, returning
    ``(db_path, {app_name: [sids]})`` — which sittings went to which Modal app."""
    dispatched = []  # (app_name, [sids])

    def fake_extract(misses, app_name=None, **_kw):
        dispatched.append((app_name, sorted(sid for sid, _fp, _t in misses)))
        for sid, fp, _texts in misses:
            yield sid, fp, {"törvény": [1, "term"]}, None

    monkeypatch.setattr(loader.settings, "wordcloud_backend", "modal")
    monkeypatch.setattr(loader.settings, "huspacy_model", "hu_core_news_trf")
    monkeypatch.setattr(loader.settings, "huspacy_model_archive", "hu_core_news_md")
    monkeypatch.setattr(loader.settings, "modal_cycles", modal_cycles)
    monkeypatch.setattr(nlp_modal, "available", lambda: True)
    monkeypatch.setattr(nlp_modal, "extract", fake_extract)

    db = tmp_path / f"split-{modal_cycles}.db".replace(",", "_").replace(" ", "")
    loader.build_database(data, db)
    return db, dict(dispatched)


def _word_counts(db):
    conn = sqlite3.connect(db)
    try:
        return dict(conn.execute("SELECT session_id, COUNT(*) FROM session_word_count "
                                 "GROUP BY session_id"))
    finally:
        conn.close()


def test_rebuild_routes_archive_cycle_to_archive_app(monkeypatch, tmp_path):
    """With the scope opened to every cycle, the newest period's sitting is
    dispatched to the primary Modal app (transformer) and the older one to the
    cheaper archive app — with per-model method tags in the fingerprints."""
    data = _two_cycle_corpus(tmp_path)
    db, by_app = _modal_build(monkeypatch, tmp_path, data, modal_cycles="all")

    assert by_app[loader.settings.modal_app_name] == ["43001"]
    assert by_app[loader.settings.modal_app_name_archive] == ["42100"]

    # Both sittings got word rows despite the different models/apps.
    counts = _word_counts(db)
    assert counts.get("42100") and counts.get("43001")


def test_only_the_latest_cycle_is_dispatched_to_modal_by_default(monkeypatch, tmp_path):
    """The budget guard: backfilling an archive cycle must not spend Modal credit.
    The archive sitting still gets a cloud — from the local fallback, not Modal."""
    data = _two_cycle_corpus(tmp_path)
    db, by_app = _modal_build(monkeypatch, tmp_path, data, modal_cycles="latest")

    assert by_app == {loader.settings.modal_app_name: ["43001"]}
    counts = _word_counts(db)
    assert counts.get("42100") and counts.get("43001")


def test_explicit_cycle_list_scopes_the_modal_dispatch(monkeypatch, tmp_path):
    """An explicit list pins the scope regardless of which cycle is newest — the
    escape hatch for a one-off archive re-extraction."""
    data = _two_cycle_corpus(tmp_path)
    _db, by_app = _modal_build(monkeypatch, tmp_path, data, modal_cycles="42")

    assert by_app == {loader.settings.modal_app_name_archive: ["42100"]}


def test_out_of_scope_sitting_keeps_its_cached_huspacy_cloud(monkeypatch, tmp_path):
    """A rebuild after the archive falls out of the Modal scope must not overwrite
    its lemmatized cloud with a regex one: the cached result for unchanged text is
    still the best available, so it is kept."""
    data = _two_cycle_corpus(tmp_path)
    # First build with the scope open: both cycles processed "by HuSpaCy" (stub).
    db, _by_app = _modal_build(monkeypatch, tmp_path, data, modal_cycles="all")
    cache_dir = db.parent
    import json
    entry = json.loads((cache_dir / "wordcloud-cache.json").read_text())["sessions"]["42100"]
    assert entry["model"] == "hu_core_news_md"

    # Now rebuild with the default scope and no local model: the archive sitting
    # can only be recomputed by the regex tokenizer — so it isn't recomputed at all.
    db2, by_app = _modal_build(monkeypatch, tmp_path, data, modal_cycles="latest")
    # Nothing is dispatched at all: 43001's fingerprint still matches, and 42100 —
    # which the regex tokenizer WOULD otherwise have recomputed and overwritten —
    # keeps the richer entry it already has.
    assert by_app == {}
    kept = json.loads((cache_dir / "wordcloud-cache.json").read_text())["sessions"]["42100"]
    assert kept["model"] == "hu_core_news_md" and kept["fp"] == entry["fp"]
    assert kept["words"] == entry["words"]
    assert _word_counts(db2).get("42100")
