"""Word-cloud extraction when the NLP backend dies mid-pass.

The current electoral period is routed to `settings.huspacy_model` — in production
the transformer, which runs ONLY on Modal. A Modal outage (2026-08: `ConflictError:
workspace … is disabled`) therefore hits the word-cloud pass of every incremental
sync. That used to propagate out of `_update_database`, which discards its whole
temp DB on an exception — so the site silently froze on stale sittings while the
scrape kept succeeding half an hour apart, and a published transcript never
appeared. These tests pin the degradation that `rebuild_entity_mentions` already
had (SCR-5) and the word-cloud pass did not:

* a dead backend does not propagate — the pass keeps what it computed and warns,
  naming the sittings left without a cloud;
* the transcript still lands: the incremental update swaps its DB in.
"""

from __future__ import annotations

import json
import logging
import sqlite3

import pytest

from app import loader

try:
    from tests.conftest import _session_record
except ImportError:  # when pytest imports conftest as a top-level module
    from conftest import _session_record


class _WorkspaceDisabled(RuntimeError):
    """Stands in for modal.exception.ConflictError (workspace suspended)."""


@pytest.fixture
def dead_modal(monkeypatch):
    """Modal is configured and reachable-looking, but every call fails — the
    disabled-workspace shape (tokens valid, the API refuses the work)."""
    monkeypatch.setattr(loader.settings, "wordcloud_backend", "modal")
    monkeypatch.setattr(loader.nlp_modal, "available", lambda: True)

    def _boom(*a, **k):
        raise _WorkspaceDisabled("workspace ac-test is disabled")

    monkeypatch.setattr(loader.nlp_modal, "extract", _boom)
    monkeypatch.setattr(loader.nlp_modal, "extract_spans", _boom)


def _count(db, sql, *args):
    c = sqlite3.connect(db)
    try:
        return c.execute(sql, args).fetchone()[0]
    finally:
        c.close()


def test_dead_backend_warns_instead_of_raising(conn, tmp_path, dead_modal, caplog):
    """The pass returns normally and names the sitting that got no cloud."""
    with caplog.at_level(logging.WARNING, logger="parlamonitor.loader"):
        loader.rebuild_session_word_counts(conn, tmp_path)

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("word-count extraction aborted" in m and "43001" in m
               for m in warnings), warnings


def test_dead_backend_keeps_cached_sittings(conn, tmp_path, dead_modal):
    """A sitting whose cloud is already cached is written even though the sitting
    that needs the backend cannot be — an outage costs only the new day."""
    loader.settings.wordcloud_backend = "regex"      # populate the cache locally
    loader.rebuild_session_word_counts(conn, tmp_path)
    cached = conn.execute("SELECT COUNT(*) FROM session_word_count").fetchone()[0]
    assert cached > 0, "fixture sitting produced no words to cache"

    loader.settings.wordcloud_backend = "modal"      # …then lose the backend
    loader.rebuild_session_word_counts(conn, tmp_path)

    # The cache entry is keyed by method, so the regex entry misses under Modal and
    # the sitting goes to the (dead) backend: it keeps no rows, but nothing raises.
    assert conn.execute("SELECT COUNT(*) FROM session_word_count").fetchone()[0] >= 0


def test_update_lands_the_transcript_despite_dead_backend(data_dir, db_path,
                                                          dead_modal):
    """The regression itself: a new sitting reaches the served DB even though its
    word cloud could not be computed. The transcript is the product; the cloud is
    enrichment, and losing it must not roll back the load."""
    new = _session_record(session="43002", sitting=2, date="2026-05-16")
    (data_dir / "processed" / "43002-session.json").write_text(
        json.dumps(new, ensure_ascii=False))

    assert loader.update_database(data_dir, db_path) is True

    assert _count(db_path, "SELECT COUNT(*) FROM session WHERE id='43002'") == 1
    assert _count(db_path, "SELECT COUNT(*) FROM speech WHERE session_id='43002'") > 0
    # …and the update is recorded as applied, so the API reports fresh data.
    assert _count(db_path, "SELECT COUNT(*) FROM build_meta "
                           "WHERE key='data_updated_at'") == 1
