"""Entity extraction when the current cycle's model is unreachable (NEL, §10).

The current electoral period is routed to `settings.huspacy_model` — in production
the transformer, which runs ONLY on Modal. On a host with no Modal backend that
model resolves to nothing, and every sitting of the LIVE cycle is skipped. These
tests pin the two properties that make such a host degrade honestly instead of
silently stripping the newest sitting days of all their inline links:

* a skip is non-destructive — mentions already in the DB survive it (the pass used
  to clear the table before finding out it could not refill it);
* a skip that hits the current cycle is logged loudly, naming the sittings.

Plus the escape hatch that puts the mentions back once a model IS reachable:
`--reextract-entities --period N` (`loader.reextract_entities`), which `--update`
cannot do because it only ever revisits sittings whose source file changed.
"""

from __future__ import annotations

import logging
import sqlite3

import pytest

from app import loader, nlp


# A model name nothing can load — stands in for "trf, on a host without Modal".
_ABSENT = "hu_core_news_definitely_absent"


@pytest.fixture
def entity_env(monkeypatch, tmp_path):
    """Entity extraction on, local HuSpaCy backend, and the CURRENT cycle routed to
    an unloadable model. Also stubs the network side of link resolution."""
    monkeypatch.setattr(loader.settings, "entity_links", True)
    monkeypatch.setattr(loader.settings, "wordcloud_backend", "huspacy")
    monkeypatch.setattr(loader.settings, "huspacy_model", _ABSENT)
    monkeypatch.setattr(loader.settings, "huspacy_model_archive", _ABSENT)
    # nlp caches load attempts per model name; drop ours so each test re-resolves.
    monkeypatch.setattr(loader.wikidata, "resolve_candidates",
                        lambda *a, **k: {})
    yield
    nlp._pipelines.pop(_ABSENT, None)


def _seed_mention(conn, session_id, key="Orbán Viktor"):
    """Put one mention on a sentence of `session_id`, as an earlier build would have."""
    sid = conn.execute(
        "SELECT se.id FROM sentence se JOIN speech sp ON sp.uid = se.speech_id "
        "WHERE sp.session_id = ? AND sp.procedural = 0 AND se.text IS NOT NULL "
        "ORDER BY se.id LIMIT 1", (session_id,)).fetchone()
    assert sid, "fixture sitting has no non-procedural sentence to hang a mention on"
    conn.execute(
        "INSERT INTO entity(sentence_id, entity_key, surface, char_start, char_end, kind) "
        "VALUES (?,?,?,?,?,?)", (sid[0], key, key, 0, len(key), "PER"))
    conn.commit()


def test_skip_keeps_existing_mentions(conn, tmp_path, entity_env):
    """A sitting whose model is unavailable keeps the mentions it already has.

    The regression: the pass cleared `entity` up front and only then discovered it
    had no model, leaving the newest sitting days with zero mentions — so their
    transcripts rendered with no MP-profile and no K-Monitor badges at all."""
    loader._ensure_entity_tables(conn)
    _seed_mention(conn, "43001")

    loader.rebuild_entity_mentions(conn, tmp_path)

    assert conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0] == 1


def test_scoped_skip_keeps_existing_mentions(conn, tmp_path, entity_env):
    """Same guarantee on the incremental (`--update`) path, which scopes the pass
    to the sittings that were just reloaded."""
    loader._ensure_entity_tables(conn)
    _seed_mention(conn, "43001")

    loader.rebuild_entity_mentions(conn, tmp_path, only_sessions={"43001"})

    assert conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0] == 1


def test_current_cycle_skip_warns_and_names_sittings(conn, tmp_path, entity_env, caplog):
    """Skipping the live cycle is a WARNING that names the affected sittings and the
    model — silence here is what let the live site sit link-less for days."""
    loader._ensure_entity_tables(conn)
    with caplog.at_level(logging.WARNING, logger="parlamonitor.loader"):
        loader.rebuild_entity_mentions(conn, tmp_path)

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("43001" in m and "CURRENT cycle" in m for m in warnings), warnings
    assert any(_ABSENT in m for m in warnings), warnings


@pytest.mark.skipif(not nlp.available("hu_core_news_md"),
                    reason="HuSpaCy model hu_core_news_md not installed")
def test_reextract_entities_refills_one_cycle(db_path, monkeypatch):
    """`reextract_entities(period=…)` re-runs NER over an already-built DB.

    The recovery path for a cycle that was skipped: no JSON reload, no full rebuild
    — which is what `--update` would need, since it only revisits sittings whose
    source file changed and a model becoming available changes no file."""
    monkeypatch.setattr(loader.settings, "entity_links", True)
    monkeypatch.setattr(loader.settings, "wordcloud_backend", "huspacy")
    monkeypatch.setattr(loader.settings, "huspacy_model", "hu_core_news_md")
    monkeypatch.setattr(loader.wikidata, "resolve_candidates", lambda *a, **k: {})

    # The fixture transcript names nobody, so give the NER something to find.
    c = sqlite3.connect(db_path)
    speech = c.execute("SELECT uid FROM speech WHERE session_id='43001' AND procedural=0 "
                       "AND has_text=1 LIMIT 1").fetchone()[0]
    c.execute("INSERT INTO sentence(speech_id, ord, text) VALUES (?, 99, ?)",
              (speech, "A javaslatot Orbán Viktor terjesztette elő."))
    c.commit()
    c.close()

    assert loader.reextract_entities(db_path, period=43) is True

    c = sqlite3.connect(db_path)
    try:
        n_43 = c.execute(
            "SELECT COUNT(*) FROM entity e JOIN sentence se ON se.id = e.sentence_id "
            "JOIN speech sp ON sp.uid = se.speech_id WHERE sp.session_id = '43001'"
        ).fetchone()[0]
    finally:
        c.close()
    assert n_43 > 0, "cycle-43 sitting still has no mentions after re-extraction"


def test_reextract_unknown_period_is_a_noop(db_path, monkeypatch):
    """An empty cycle changes nothing — no swap, no half-written DB."""
    monkeypatch.setattr(loader.settings, "entity_links", True)
    before = db_path.stat().st_mtime_ns
    assert loader.reextract_entities(db_path, period=99) is False
    assert db_path.stat().st_mtime_ns == before
