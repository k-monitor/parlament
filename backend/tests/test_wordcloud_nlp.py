"""HuSpaCy-backed word cloud (WCLOUD-2): lemmatization collapses inflected forms
and named entities are kept as single multi-word terms.

These tests need the HuSpaCy model (`hu_core_news_md`); they skip cleanly when it
is not installed, so the suite still runs on a minimal (regex-only) environment.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import loader, nlp
from tests.conftest import _registry, _session_record

pytestmark = pytest.mark.skipif(
    not nlp.available(), reason="HuSpaCy model hu_core_news_md not installed")


def _wc_record(session, sitting, date, sentences):
    rec = _session_record(session=session, sitting=sitting, date=date)
    rec["data"][0]["textContents"][0]["textBody"][0]["sentences"] = [
        {"text": t, "timeStart": 0.0, "timeEnd": 1.0} for t in sentences]
    rec["data"] = rec["data"][:1]
    return rec


@pytest.fixture
def nlp_client(tmp_path, monkeypatch):
    # Patch the settings object the loader/nlp modules actually hold (bound at
    # import). Another test reloads app.config, rebinding app.config.settings to a
    # fresh object, so patching that import here would miss the one loader reads.
    from app import db as db_module
    monkeypatch.setattr(loader.settings, "wordcloud_backend", "huspacy")

    data = tmp_path / "data"
    (data / "processed").mkdir(parents=True)
    (data / "processed" / "representatives-43.json").write_text(
        json.dumps(_registry(), ensure_ascii=False))
    (data / "processed" / "43001-session.json").write_text(json.dumps(_wc_record(
        "43001", 1, "2026-05-09",
        ["Az Országgyűlés új törvényeket fogadott el.",
         "A törvényt és a törvények sokaságát Orbán Viktor terjesztette elő.",
         "Magyarország és az Európai Unió a klímavédelemről tárgyalt."]),
        ensure_ascii=False))
    out = tmp_path / "wc.db"
    loader.build_database(data, out)

    monkeypatch.setattr(loader.settings, "db_path", str(out))
    monkeypatch.setattr(db_module.settings, "db_path", str(out))
    from app.main import app
    return TestClient(app), out


def test_inflected_forms_collapse_to_one_lemma(nlp_client):
    """"törvényeket", "törvényt" and "törvények" count as one lemma "törvény"."""
    _, out = nlp_client
    c = sqlite3.connect(out); c.row_factory = sqlite3.Row
    rows = {r["word"]: r for r in c.execute(
        "SELECT word, count, kind FROM session_word_count WHERE session_id='43001'")}
    c.close()
    assert "törvény" in rows and rows["törvény"]["count"] == 3
    # the surface forms are gone — only the lemma remains
    assert not any(w in rows for w in ("törvényeket", "törvényt", "törvények"))


def test_named_entities_kept_whole_and_tagged(nlp_client):
    """Multi-word named entities stay single terms and are flagged kind=entity."""
    client, _ = nlp_client
    d = client.get("/api/v1/proceedings/sessions/43001/wordcloud").json()
    words = {w["text"]: w for w in d["words"]}
    assert "Orbán Viktor" in words and words["Orbán Viktor"]["kind"] == "entity"
    assert "Európai Unió" in words and words["Európai Unió"]["kind"] == "entity"
    # a plain lemma is not flagged as an entity
    assert words["törvény"]["kind"] == "term"
    # the entity's parts are not also counted on their own
    assert "viktor" not in words and "unió" not in words


def test_wordcloud_cache_records_model_name(nlp_client):
    """The word-cloud cache entry records the HuSpaCy model + exact method that
    produced it, so which model ran is inspectable without recomputing the
    fingerprint against each candidate model."""
    _, out = nlp_client
    cache = json.loads((out.parent / "wordcloud-cache.json").read_text())
    entry = cache["sessions"]["43001"]
    assert entry["model"] == loader.settings.huspacy_model  # hu_core_news_md
    assert entry["method"] == nlp.method_tag(loader.settings.huspacy_model)


def test_entity_cache_records_model_name(tmp_path, monkeypatch):
    """The entity cache likewise records the model + method that produced each
    sitting's mentions. Built with entity extraction off, then run directly so
    the test needs no network (K-Monitor / Wikidata resolution)."""
    data = tmp_path / "data"
    (data / "processed").mkdir(parents=True)
    (data / "processed" / "representatives-43.json").write_text(
        json.dumps(_registry(), ensure_ascii=False))
    (data / "processed" / "43001-session.json").write_text(json.dumps(_wc_record(
        "43001", 1, "2026-05-09",
        ["A törvényt Orbán Viktor terjesztette elő."]), ensure_ascii=False))
    out = tmp_path / "e.db"
    loader.build_database(data, out)          # entity_links off by default → no NEL

    monkeypatch.setattr(loader.settings, "entity_links", True)
    conn = sqlite3.connect(out)
    loader.rebuild_entity_mentions(conn, tmp_path)
    conn.close()

    cache = json.loads((tmp_path / "entity-cache.json").read_text())
    entry = cache["sessions"]["43001"]
    assert entry["model"] == loader.settings.huspacy_model
    assert entry["method"] == (
        nlp.method_tag(loader.settings.huspacy_model) + ":" + loader._ENTITY_LOGIC)
