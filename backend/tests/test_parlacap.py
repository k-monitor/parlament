"""CAP policy-topic classification of speeches (TOPIC-1..7).

Four groups of properties, chosen because each one fails *silently* — no error, no
missing column, just a plausible-looking topic that means something other than what
the UI says it means:

* **block assembly** — the corpus marks paragraphs three different ways across its
  five cycles, so the unit fed to the model must come out the same size whichever
  cycle it came from, or the annotation's quality quietly depends on the era;
* **the threshold is a read-time policy** — it must change what the site shows
  without changing a stored row, and must *not* enter the method tag, or retuning
  it would silently invalidate a corpus-sized cache and demand a GPU;
* **aggregation semantics** — weighting by words rather than blocks, and "Other"
  being reportable but never winning, are both invisible in the output when wrong;
* **degradation** — a host with no model must ship what the cache covers and leave
  the rest unlabelled, never fail the build and never write a guess.

No test here loads the real model: the classifier is injected.
"""

from __future__ import annotations

import json

import pytest

from app import loader, parlacap
from app.config import settings


# ---------------------------------------------------------------------------
# block assembly
# ---------------------------------------------------------------------------

def _sentences(n, words, para_every=None):
    """`n` sentences of `words` words each. `para_every` starts a new paragraph
    index every that many sentences; None puts everything in paragraph 0, which is
    what cycle 41 (NULL paragraph column) looks like after the loader's fallback."""
    out = []
    for i in range(n):
        para = 0 if para_every is None else i // para_every
        out.append((para, " ".join(f"szó{i}-{j}" for j in range(words))))
    return out


def test_blocks_follow_real_paragraphs_when_they_are_real():
    """Cycles 42-43 mark genuine paragraphs of ordinary length; those must survive
    as blocks 1:1, because they are also the unit the reader sees in the viewer."""
    # 4 paragraphs of 5 sentences x 15 words = 75 words each, over the 60 target.
    blocks = parlacap.build_blocks(_sentences(20, 15, para_every=5))
    assert len(blocks) == 4
    assert [b[1] for b in blocks] == [0, 1, 2, 3]     # first paragraph of each


def test_blocks_split_a_speech_with_no_paragraph_marks():
    """Cycle 41 carries no paragraph marks at all, so the whole speech arrives as
    paragraph 0. Left alone it would be four times the model's token limit; the word
    budget has to break it up."""
    blocks = parlacap.build_blocks(_sentences(60, 20, para_every=None))  # 1200 words
    assert len(blocks) > 1
    assert all(parlacap.word_count(b[2]) <= settings.parlacap_max_words
               for b in blocks)


def test_blocks_merge_line_fragments():
    """Cycles 39-40 mark source *line* breaks, so their 'paragraphs' are fragments
    of a few words. Trusting them would feed the model shreds; they must be merged
    up to something that can carry a topic."""
    blocks = parlacap.build_blocks(_sentences(40, 4, para_every=1))  # 160 words
    assert len(blocks) < 40
    # Every block except possibly the last reaches the target.
    assert all(parlacap.word_count(b[2]) >= settings.parlacap_target_words
               for b in blocks[:-1])


def test_block_never_exceeds_the_word_budget():
    """The cap has to be an upper bound, not a threshold the final sentence
    overshoots — a block that closed only once already over ran to `maximum` plus a
    whole sentence, which is how a cycle got past the token limit it was sized for."""
    blocks = parlacap.build_blocks(_sentences(100, 37, para_every=None))
    assert all(parlacap.word_count(b[2]) <= settings.parlacap_max_words
               for b in blocks)


def test_trailing_runt_is_merged_not_emitted():
    """A short tail must join the previous block: on its own it would fall under the
    minimum and never be classified, so emitting it would silently drop a speech's
    closing sentences out of the vote."""
    sentences = _sentences(8, 20, para_every=4) + [(2, "Köszönöm szépen.")]
    blocks = parlacap.build_blocks(sentences)
    assert all(parlacap.word_count(b[2]) >= settings.parlacap_min_words
               for b in blocks)
    assert blocks[-1][2].endswith("Köszönöm szépen.")


def test_block_ordinals_are_contiguous():
    """The ordinal is the table's primary key alongside speech_id, so a gap or a
    repeat is a lost or overwritten row rather than a cosmetic flaw."""
    blocks = parlacap.build_blocks(_sentences(30, 20, para_every=3))
    assert [b[0] for b in blocks] == list(range(len(blocks)))


# ---------------------------------------------------------------------------
# the threshold is applied on read, never on write
# ---------------------------------------------------------------------------

def _rows(*specs):
    """`(label, score, words)` triples as stored `speech_topic` rows."""
    return [(i, label, score, None, None, words)
            for i, (label, score, words) in enumerate(specs)]


def test_threshold_changes_the_answer_without_changing_the_data():
    """The whole point of storing raw predictions: the same rows must yield
    different topics at different thresholds, with nothing reclassified."""
    rows = _rows(("Health", 0.95, 100), ("Labor", 0.70, 300))
    assert parlacap.aggregate(rows, threshold=0.9)["label"] == "Health"
    # Lower the bar and the longer, less certain block outweighs it.
    assert parlacap.aggregate(rows, threshold=0.6)["label"] == "Labor"


def test_threshold_is_not_in_the_method_tag():
    """If it were, retuning a presentation setting would invalidate every cached
    sitting and demand a GPU to re-earn predictions that had not changed."""
    before = parlacap.method_tag()
    old = settings.parlacap_threshold
    try:
        settings.parlacap_threshold = 0.5
        assert parlacap.method_tag() == before
    finally:
        settings.parlacap_threshold = old


def test_method_tag_covers_what_does_change_predictions():
    """The converse: block sizing and truncation length change what the model saw,
    so they must invalidate the cache."""
    before = parlacap.method_tag()
    old = settings.parlacap_max_words
    try:
        settings.parlacap_max_words = old + 50
        assert parlacap.method_tag() != before
    finally:
        settings.parlacap_max_words = old


# ---------------------------------------------------------------------------
# aggregation semantics
# ---------------------------------------------------------------------------

def test_weighted_by_words_not_by_block_count():
    """A speech is about what it spends its words on. Counting blocks would let
    three short asides outvote one long argument."""
    rows = _rows(("Labor", 0.99, 10), ("Labor", 0.99, 10), ("Labor", 0.99, 10),
                 ("Health", 0.99, 200))
    assert parlacap.aggregate(rows, threshold=0.9)["label"] == "Health"


def test_other_is_reported_but_cannot_win():
    """CAP's "Other" means "no policy content here" — a real prediction, but not a
    subject. A speech that opens with greetings and then argues about hospitals is
    about health care."""
    rows = _rows(("Other", 0.99, 300), ("Health", 0.95, 100))
    agg = parlacap.aggregate(rows, threshold=0.9)
    assert agg["label"] == "Health"
    assert agg["other_share"] == pytest.approx(0.75)


def test_speech_that_is_only_procedural_gets_no_topic():
    rows = _rows(("Other", 0.99, 300))
    assert parlacap.aggregate(rows, threshold=0.9) is None


def test_nothing_confident_enough_gets_no_topic():
    """Silence is the intended answer, not a "misc" bucket."""
    rows = _rows(("Health", 0.55, 300), ("Labor", 0.40, 200))
    assert parlacap.aggregate(rows, threshold=0.9) is None


def test_coverage_reports_the_share_that_cleared_the_bar():
    """The reader's cue that a label rests on part of a speech rather than all of
    it; measured against everything classified, including the sub-threshold rows."""
    rows = _rows(("Health", 0.99, 60), ("Labor", 0.10, 40))
    agg = parlacap.aggregate(rows, threshold=0.9)
    assert agg["coverage"] == pytest.approx(0.6)


def test_breakdown_is_ordered_and_deterministic():
    rows = _rows(("Health", 0.99, 50), ("Labor", 0.99, 90), ("Energy", 0.99, 90))
    agg = parlacap.aggregate(rows, threshold=0.9)
    shares = [b["share"] for b in agg["breakdown"]]
    assert shares == sorted(shares, reverse=True)
    # Ties break on the label so two servers agree on the same speech.
    assert [b["label"] for b in agg["breakdown"][:2]] == ["Energy", "Labor"]


def test_cap_codes_match_the_labels():
    """The codes are what comparative research joins on; a wrong one is invisible
    in the UI (which shows the name) but corrupts any export."""
    assert parlacap.CAP_CODES["Health"] == 3
    assert parlacap.CAP_CODES["Law and Crime"] == 12       # CAP has no major 11
    assert parlacap.CAP_CODES["Culture"] == 23             # nor 22
    assert set(parlacap.CAP_CODES) == set(parlacap.LABELS)


def test_label_order_is_pinned():
    """The cache stores label *indices*, so reordering LABELS would silently
    relabel the whole corpus on the next read."""
    assert len(parlacap.LABELS) == 22
    assert parlacap.LABELS[0] == "Education"
    assert parlacap.LABELS[9] == "Other"
    assert parlacap.LABELS[-1] == "Energy"


# ---------------------------------------------------------------------------
# the loader pass: caching, degradation, procedural exclusion
# ---------------------------------------------------------------------------

class _FakeClassifier:
    """Stands in for the model: labels every block Health, and counts calls so a
    test can prove the cache spared the GPU."""

    def __init__(self, label="Health", score=0.97):
        self.calls = 0
        self.blocks = 0
        self.label = parlacap.LABELS.index(label)
        self.score = score

    def __call__(self, texts):
        self.calls += 1
        self.blocks += len(texts)
        return [(self.label, self.score, 9, 0.01)
                if parlacap.word_count(t) >= settings.parlacap_min_words else None
                for t in texts]


def _live_settings():
    """Every distinct ``Settings`` instance currently reachable.

    Normally there is exactly one. But ``test_api.py`` reloads ``app.config`` and
    ``app.main`` to exercise a re-read of the environment, and a reload rebinds
    ``config.settings`` to a *new* object while every module that did
    ``from .config import settings`` earlier keeps the old one. So which instance
    ``app.main`` consults depends on test *order*, and patching only the one this
    module imported makes these tests pass alone and fail in the suite. Patch all
    of them and the order stops mattering."""
    import app.config
    import app.main
    from app.modules.proceedings import router as proceedings_router
    seen = {}
    for module in (app.config, app.main, loader, parlacap, proceedings_router):
        current = getattr(module, "settings", None)
        if current is not None:
            seen[id(current)] = current
    return list(seen.values())


@pytest.fixture
def topics_on(monkeypatch, tmp_path):
    """Enable the pass with an injected classifier, restoring the settings after.

    The shared fixture's speeches are two sentences long — a realistic *procedural*
    length, but far under the production minimum, so at the real floor this pass
    would correctly classify nothing and every test below would pass vacuously. The
    floor is lowered here so the plumbing is exercised; the floor's own behaviour is
    covered by the block-assembly tests, which do not need a DB."""
    for instance in _live_settings():
        monkeypatch.setattr(instance, "parlacap", True)
        monkeypatch.setattr(instance, "parlacap_min_words", 2)
        monkeypatch.setattr(instance, "parlacap_target_words", 4)
    fake = _FakeClassifier()
    monkeypatch.setattr(parlacap, "classify", fake)
    monkeypatch.setattr(parlacap, "backend", lambda **kw: "local")
    return fake


def test_pass_writes_rows_and_reuses_its_cache(conn, tmp_path, topics_on):
    loader.rebuild_speech_topics(conn, tmp_path)
    first = conn.execute("SELECT COUNT(*) FROM speech_topic").fetchone()[0]
    assert first > 0
    assert topics_on.calls > 0

    calls = topics_on.calls
    loader.rebuild_speech_topics(conn, tmp_path)
    # Second run is served entirely from disk — no further model calls, same rows.
    assert topics_on.calls == calls
    assert conn.execute("SELECT COUNT(*) FROM speech_topic").fetchone()[0] == first


def test_cache_is_portable_without_a_model(conn, tmp_path, topics_on, monkeypatch):
    """The shipping story: classify on a GPU box, copy the JSON, rebuild anywhere.
    A host that cannot classify must still write every row the cache covers."""
    loader.rebuild_speech_topics(conn, tmp_path)
    expected = conn.execute("SELECT COUNT(*) FROM speech_topic").fetchone()[0]
    assert expected > 0

    conn.execute("DELETE FROM speech_topic")
    monkeypatch.setattr(parlacap, "backend", lambda **kw: None)
    loader.rebuild_speech_topics(conn, tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM speech_topic").fetchone()[0] == expected


def test_missing_model_and_missing_cache_leaves_rows_unlabelled(conn, tmp_path,
                                                                monkeypatch):
    """A missing topic hides a badge; a failed build takes the site down. This must
    be the first kind of failure, not the second."""
    for instance in _live_settings():
        monkeypatch.setattr(instance, "parlacap", True)
    monkeypatch.setattr(parlacap, "backend", lambda **kw: None)
    loader.rebuild_speech_topics(conn, tmp_path)          # must not raise
    assert conn.execute("SELECT COUNT(*) FROM speech_topic").fetchone()[0] == 0


def test_procedural_speeches_are_never_classified(conn, tmp_path, topics_on):
    """The model reads a chairing announcement as being about whatever bill it
    names, so those speeches are excluded structurally rather than by confidence."""
    loader.rebuild_speech_topics(conn, tmp_path)
    leaked = conn.execute(
        "SELECT COUNT(*) FROM speech_topic t JOIN speech sp ON sp.uid = t.speech_id "
        "WHERE sp.procedural = 1").fetchone()[0]
    assert leaked == 0


def test_disabled_pass_writes_nothing(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "parlacap", False)
    loader.rebuild_speech_topics(conn, tmp_path)
    tables = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='speech_topic'"
    ).fetchone()[0]
    # The table may pre-exist from the schema; what matters is that nothing ran.
    if tables:
        assert conn.execute("SELECT COUNT(*) FROM speech_topic").fetchone()[0] == 0


def test_cache_stores_label_indices_not_names(conn, tmp_path, topics_on):
    """Indices are what keep a corpus-sized cache at tens of megabytes rather than
    hundreds; this pins the on-disk shape so a refactor cannot quietly inflate it."""
    loader.rebuild_speech_topics(conn, tmp_path)
    cache = json.loads((tmp_path / "parlacap-cache.json").read_text())
    rows = next(iter(cache["sessions"].values()))["rows"]
    assert rows, "expected at least one classified block"
    uid, block, para, label, score, runner, runner_score, words = rows[0]
    assert isinstance(label, int) and 0 <= label < len(parlacap.LABELS)
    assert isinstance(block, int) and isinstance(words, int)


# ---------------------------------------------------------------------------
# the API surface
# ---------------------------------------------------------------------------

def test_speech_carries_its_topic(client, conn, tmp_path, topics_on):
    loader.rebuild_speech_topics(conn, tmp_path)
    conn.commit()
    uid = conn.execute("SELECT speech_id FROM speech_topic LIMIT 1").fetchone()[0]
    body = client.get(f"/api/v1/proceedings/speeches/{uid}").json()
    topic = body["speech"]["topic"]
    assert topic["label"] == "Health"
    assert 0 < topic["share"] <= 1
    assert topic["threshold"] == settings.parlacap_threshold


def test_meta_publishes_the_methodology(client, conn, tmp_path, topics_on):
    """Every derived figure on the site has to be able to say what produced it
    (TRUST-1 / REP-5) — including the threshold, which changes what is shown."""
    loader.rebuild_speech_topics(conn, tmp_path)
    conn.commit()
    meta = client.get("/api/v1/meta").json()
    assert meta["features"]["speech_topics"] is True
    assert meta["speech_topics"]["threshold"] == settings.parlacap_threshold
    assert meta["speech_topics"]["unit"] == "paragraph"
    assert len(meta["speech_topics"]["labels"]) == 22


# ---------------------------------------------------------------------------
# the Modal backend (TOPIC-7): cycle gating, tag safety, failure containment
# ---------------------------------------------------------------------------

def test_backend_off_refuses_both(monkeypatch):
    for instance in _live_settings():
        monkeypatch.setattr(instance, "parlacap", True)
        monkeypatch.setattr(instance, "parlacap_backend", "off")
    assert parlacap.backend() is None


def test_auto_never_selects_modal(monkeypatch):
    """The spend rule, and the one that makes an unconfigured GPU box safe: it has
    a card *and* very likely Modal credentials (for the HuSpaCy/Whisper offloads),
    so an `auto` that preferred Modal would quietly bill a full archive backfill
    to a service that may not even be deployed. Modal is always opt-in."""
    from app import parlacap_modal
    for instance in _live_settings():
        monkeypatch.setattr(instance, "parlacap", True)
        monkeypatch.setattr(instance, "parlacap_backend", "auto")
    monkeypatch.setattr(parlacap_modal, "available", lambda: True)
    assert parlacap.backend() != "modal"


def test_backend_local_never_dispatches_to_modal(monkeypatch):
    """An explicitly local host must not reach for a metered GPU because one
    happens to be configured."""
    from app import parlacap_modal
    for instance in _live_settings():
        monkeypatch.setattr(instance, "parlacap", True)
        monkeypatch.setattr(instance, "parlacap_backend", "local")
    monkeypatch.setattr(parlacap_modal, "available", lambda: True)
    assert parlacap.backend() != "modal"


def test_modal_is_refused_for_out_of_scope_cycles(monkeypatch):
    """The spend guard. `modal_ok=False` means this sitting's cycle is outside
    PARLAMONITOR_MODAL_CYCLES, and no configuration may override that — it is the
    only thing standing between a routine rebuild and buying 600k blocks of
    archive on a metered GPU."""
    from app import parlacap_modal
    for instance in _live_settings():
        monkeypatch.setattr(instance, "parlacap", True)
        monkeypatch.setattr(instance, "parlacap_backend", "modal")
    monkeypatch.setattr(parlacap_modal, "available", lambda: True)
    assert parlacap.backend(modal_ok=True) == "modal"
    # It may still fall back to a local torch install (this venv has one); what
    # must never happen is the metered path being taken.
    assert parlacap.backend(modal_ok=False) != "modal"


def test_only_the_latest_cycle_reaches_modal(conn, tmp_path, monkeypatch, topics_on):
    """End to end through the loader: with the default `latest` scope, a sitting
    from an older cycle must not be dispatched even when Modal is the backend."""
    from app import parlacap_modal
    for instance in _live_settings():
        monkeypatch.setattr(instance, "modal_cycles", "latest")
    sent: list[str] = []

    def _fake_classify(misses, **kw):
        for sid, fp, texts in misses:
            sent.append(sid)
            yield sid, fp, [(2, 0.97, 9, 0.01)] * len(texts)

    monkeypatch.setattr(parlacap_modal, "classify", _fake_classify)
    monkeypatch.setattr(parlacap, "backend",
                        lambda **kw: "modal" if kw.get("modal_ok", True) else None)

    periods = {sid: p for sid, p in conn.execute(
        "SELECT id, period_number FROM session")}
    latest = max(p for p in periods.values() if p is not None)
    loader.rebuild_speech_topics(conn, tmp_path)
    assert sent, "expected the newest cycle to be dispatched"
    assert all(periods[sid] in (None, latest) for sid in sent)


def test_modal_method_tag_mismatch_refuses_to_cache(monkeypatch):
    """A service on a different model would file its predictions under this
    host's method tag — after which they would never be recomputed, because the
    fingerprint would match forever. Refusing is the only recoverable option."""
    from app import parlacap_modal

    class _Svc:
        class method_tag:
            @staticmethod
            def remote():
                return "parlacap:some-other-model:len512:min12:blk60-220:v1"

    monkeypatch.setattr(parlacap_modal, "_service", lambda *a, **k: _Svc())
    with pytest.raises(RuntimeError, match="method"):
        list(parlacap_modal.classify([("43001", "fp", ["a block of text"])]))


def test_modal_failure_leaves_the_build_standing(conn, tmp_path, monkeypatch,
                                                 topics_on):
    """A Modal outage, an expired token or a stale deployment must cost this run
    its new topics — not the build, and not the topics already stored."""
    from app import parlacap_modal

    def _boom(misses, **kw):
        raise RuntimeError("modal is down")
        yield  # pragma: no cover - generator marker

    monkeypatch.setattr(parlacap_modal, "classify", _boom)
    monkeypatch.setattr(parlacap, "backend", lambda **kw: "modal")
    loader.rebuild_speech_topics(conn, tmp_path)          # must not raise
    assert conn.execute("SELECT COUNT(*) FROM speech_topic").fetchone()[0] == 0


def test_modal_and_local_share_one_cache(monkeypatch):
    """The backend is not part of the method tag: a sitting classified on Modal
    and one classified locally must land in the same cache under the same key, or
    switching hosts would silently re-buy the corpus."""
    before = parlacap.method_tag()
    for instance in _live_settings():
        monkeypatch.setattr(instance, "parlacap_backend", "modal")
    assert parlacap.method_tag() == before


# ---------------------------------------------------------------------------
# irományok: the same classifier over document text (TOPIC-8)
# ---------------------------------------------------------------------------
# The properties that matter here are the ones the speech pass does not already
# cover: paragraphs have to be *recovered* from extracted PDF layout rather than
# read off a column, and the input can be absent entirely — a production server
# mirrors no documents at all and must still replay what was shipped to it.

import lzma  # noqa: E402
from pathlib import Path  # noqa: E402


def _mirror(data_dir, docs, *, cycle=43, compress=True):
    """Write a synthetic scraper document mirror (DOC-1) for ``{url: text}``."""
    root = Path(data_dir) / "documents" / str(cycle)
    (root / "text").mkdir(parents=True, exist_ok=True)
    entries = []
    for i, (url, text) in enumerate(docs.items()):
        name = f"doc{i}.txt" + (".xz" if compress else "")
        blob = text.encode("utf-8")
        (root / "text" / name).write_bytes(lzma.compress(blob) if compress else blob)
        entries.append({"id": f"doc{i}", "url": url, "kind": "main",
                        "status": "ok", "textFile": f"text/{name}",
                        "textChars": len(text)})
    (root / "index.json").write_text(
        json.dumps({"meta": {"cycle": cycle}, "documents": entries},
                   ensure_ascii=False))
    return root


# The URL the shared bills fixture gives T/100, and a body long enough that the
# lowered test floor classifies it.
_T100 = "https://www.parlament.hu/irom43/00100/00100.pdf"
_DOC_TEXT = ("Országgyűlés Hivatala\nIrományszám: T/100\nÉrkezett:\n\n"
             "2026 MÁJ 03.\n\n"
             "Tisztelt Elnök Úr! Az Alaptörvény alapján a mellékelt\n"
             "törvényjavaslatot kívánom benyújtani.\n\n"
             "1. § A költségvetés fő összegei a következők szerint alakulnak.\n"
             "A bevételi főösszeg és a kiadási főösszeg egyaránt emelkedik.\n")


# --- recovering the unit from PDF layout -----------------------------------

def test_document_paragraphs_rejoin_hard_wrapped_lines():
    """pdftotext wraps to the PDF's own line breaks; a paragraph is the run of
    lines between blank ones, or every sentence would arrive as a fragment."""
    paras = parlacap.document_paragraphs(
        "Első sor\nugyanannak a bekezdésnek a folytatása.\n\nMásik bekezdés.\n")
    assert paras == ["Első sor ugyanannak a bekezdésnek a folytatása.",
                     "Másik bekezdés."]


def test_document_paragraphs_keep_the_cover_sheet():
    """Measured over all 356 cycle-43 documents, dropping the opening block
    silences 32 irományok to change 3. The stamp fragments stay; they are short,
    so the minimum-length floor already declines to classify them alone."""
    paras = parlacap.document_paragraphs(_DOC_TEXT)
    assert paras[0].startswith("Országgyűlés Hivatala")
    assert any("költségvetés" in p for p in paras)


def test_oversized_paragraph_is_split_rather_than_truncated():
    """A document's paragraph routinely runs past the model's token limit, and
    letting it stand whole would silently drop its second half."""
    long_para = " ".join(f"szó{i}" for i in range(500)) + "."
    blocks = parlacap.build_text_blocks(long_para, target=60, maximum=100)
    assert len(blocks) >= 5
    assert all(parlacap.word_count(t) <= 100 for (_o, _p, t) in blocks)
    # nothing was lost on the way
    assert sum(parlacap.word_count(t) for (_o, _p, t) in blocks) >= 500


def test_long_paragraph_splits_on_sentences_before_word_count():
    two = ("Ez az első mondat, amely elég hosszú ahhoz, hogy önmagában is "
           "kitöltse a keretet. Ez pedig a második mondat, ugyanilyen hosszú "
           "és ugyanilyen önálló.")
    pieces = parlacap._split_oversized(two, maximum=14)
    assert pieces[0].endswith("keretet.")


def test_document_method_tag_differs_from_the_speech_one():
    """The two passes share one cache file; an entry from one must never be
    mistaken for the other's, even at identical model settings."""
    assert parlacap.document_method_tag() != parlacap.method_tag()
    assert parlacap.document_method_tag().startswith(parlacap.method_tag())


# --- the loader pass --------------------------------------------------------

@pytest.fixture
def bill_topics_on(topics_on, monkeypatch):
    """``topics_on`` plus the iromány half enabled."""
    for instance in _live_settings():
        monkeypatch.setattr(instance, "bill_topics", True)
    return topics_on


def test_bill_pass_writes_rows_and_reuses_its_cache(conn, tmp_path, data_dir,
                                                    bill_topics_on):
    _mirror(data_dir, {_T100: _DOC_TEXT})
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=data_dir)
    rows = conn.execute("SELECT COUNT(*) FROM bill_topic").fetchone()[0]
    assert rows > 0
    assert conn.execute(
        "SELECT DISTINCT bill_id FROM bill_topic").fetchone()[0] == "bill-uuid-1"

    calls = bill_topics_on.calls
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=data_dir)
    assert bill_topics_on.calls == calls           # served from disk
    assert conn.execute("SELECT COUNT(*) FROM bill_topic").fetchone()[0] == rows


def test_bill_rows_carry_the_cycle_for_scoped_rebuilds(conn, tmp_path, data_dir,
                                                       bill_topics_on):
    _mirror(data_dir, {_T100: _DOC_TEXT})
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=data_dir)
    assert conn.execute(
        "SELECT DISTINCT period_number FROM bill_topic").fetchone()[0] == 43


def test_cache_replays_on_a_host_with_no_document_mirror(conn, tmp_path, data_dir,
                                                         bill_topics_on, monkeypatch):
    """The delivery story, and the one that differs from the speech pass: the
    production server has no documents to fingerprint against, so a cached entry
    is replayed as it stands rather than re-derived."""
    _mirror(data_dir, {_T100: _DOC_TEXT})
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=data_dir)
    expected = conn.execute("SELECT COUNT(*) FROM bill_topic").fetchone()[0]
    assert expected > 0

    conn.execute("DELETE FROM bill_topic")
    monkeypatch.setattr(parlacap, "backend", lambda **kw: None)
    # No mirror anywhere, and no model — exactly a web server after a `docker
    # compose run init`, with only parlacap-cache.json copied across.
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=tmp_path / "nowhere")
    assert conn.execute("SELECT COUNT(*) FROM bill_topic").fetchone()[0] == expected


def test_no_mirror_and_no_cache_leaves_irományok_unlabelled(conn, tmp_path,
                                                            bill_topics_on, monkeypatch):
    monkeypatch.setattr(parlacap, "backend", lambda **kw: None)
    loader.rebuild_bill_topics(conn, tmp_path / "empty",
                               data_dir=tmp_path / "nowhere")
    assert conn.execute("SELECT COUNT(*) FROM bill_topic").fetchone()[0] == 0


def test_the_two_passes_share_one_cache_without_clobbering_it(conn, tmp_path,
                                                              data_dir, bill_topics_on):
    """Both write ``parlacap-cache.json``; whichever runs second must keep the
    first's half, or shipping the file would deliver only one of the two."""
    _mirror(data_dir, {_T100: _DOC_TEXT})
    loader.rebuild_speech_topics(conn, tmp_path)
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=data_dir)
    cache = json.loads((tmp_path / "parlacap-cache.json").read_text())
    assert cache["sessions"] and cache["bills"]

    # …and the speech pass running again does not drop the bills half.
    loader.rebuild_speech_topics(conn, tmp_path)
    cache = json.loads((tmp_path / "parlacap-cache.json").read_text())
    assert cache["bills"]


def test_bill_cache_stores_label_indices_not_names(conn, tmp_path, data_dir,
                                                   bill_topics_on):
    """Same size argument as the speech cache: indices against the pinned LABELS
    tuple, which is what keeps a corpus-sized file shippable."""
    _mirror(data_dir, {_T100: _DOC_TEXT})
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=data_dir)
    entry = json.loads((tmp_path / "parlacap-cache.json").read_text())["bills"]
    rows = entry["bill-uuid-1"]["rows"]
    assert rows and all(isinstance(r[2], int) for r in rows)
    assert entry["bill-uuid-1"]["method"] == parlacap.document_method_tag()


def test_uncompressed_and_gzipped_document_text_are_both_read(conn, tmp_path,
                                                              data_dir, bill_topics_on):
    """The mirror's codec is an operator setting (xz / gzip / none); the loader
    must not care which was chosen."""
    _mirror(data_dir, {_T100: _DOC_TEXT}, compress=False)
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=data_dir)
    assert conn.execute("SELECT COUNT(*) FROM bill_topic").fetchone()[0] > 0


def test_changed_document_text_busts_the_cache(conn, tmp_path, data_dir,
                                               bill_topics_on):
    _mirror(data_dir, {_T100: _DOC_TEXT})
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=data_dir)
    calls = bill_topics_on.calls
    _mirror(data_dir, {_T100: _DOC_TEXT + "\n\nÚj bekezdés a módosított iromány végén.\n"})
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=data_dir)
    assert bill_topics_on.calls > calls


def test_disabled_bill_pass_writes_nothing(conn, tmp_path, data_dir, topics_on,
                                           monkeypatch):
    for instance in _live_settings():
        monkeypatch.setattr(instance, "bill_topics", False)
    _mirror(data_dir, {_T100: _DOC_TEXT})
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=data_dir)
    # The table itself is part of schema.sql, so it exists on any fresh build;
    # what the switch must guarantee is that nothing was classified into it.
    assert conn.execute("SELECT COUNT(*) FROM bill_topic").fetchone()[0] == 0
    assert not (tmp_path / "parlacap-cache.json").exists()


def test_only_the_latest_cycle_reaches_modal_for_irományok(conn, tmp_path, data_dir,
                                                           bill_topics_on, monkeypatch):
    """The metered-spend guard applies here too: the archive is far larger than
    the newest cycle, and a backfill must never be bought by accident."""
    seen = []
    monkeypatch.setattr(parlacap, "backend",
                        lambda **kw: seen.append(kw.get("modal_ok")) or None)
    conn.execute("UPDATE bill SET period_number = 39 WHERE id = 'bill-uuid-1'")
    _mirror(data_dir, {_T100: _DOC_TEXT})
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=data_dir)
    assert seen and all(ok is False for ok in seen)


# --- the API ----------------------------------------------------------------

def test_bill_carries_its_topic(client, conn, tmp_path, data_dir, bill_topics_on):
    _mirror(data_dir, {_T100: _DOC_TEXT})
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=data_dir)
    conn.commit()

    detail = client.get("/api/v1/bills/bill-uuid-1").json()
    assert detail["topic"]["label"] == "Health"
    assert detail["topic"]["code"] == parlacap.CAP_CODES["Health"]

    listing = client.get("/api/v1/bills?period=43").json()
    topics = {b["bill_number"]: b["topic"] for b in listing["bills"]}
    assert topics["T/100"]["label"] == "Health"
    # An iromány whose document was never mirrored says nothing rather than
    # borrowing a neighbour's label.
    assert topics["T/101"] is None


def test_the_order_paper_carries_the_topic_of_what_it_schedules(
        client, conn, tmp_path, data_dir, bill_topics_on):
    """The coming sitting's items say what they are *about* (NR-5 + TOPIC-8).

    An order paper is where an iromány's title is least useful — it is printed as
    a citation of the law being amended — so this is the surface the chip was
    needed on most. It is resolved from the iromány the item links to, which means
    an item naming a number we do not hold carries no topic rather than a guess.
    """
    _mirror(data_dir, {_T100: _DOC_TEXT})
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=data_dir)
    conn.commit()

    days = client.get("/api/v1/proceedings/upcoming").json()["agenda"]["days"]
    topics = {i["billCode"]: i["topic"] for d in days for i in d["items"]}
    assert topics["T/100"]["label"] == "Health"
    assert topics["T/999"] is None


def test_meta_publishes_the_iromány_methodology(client, conn, tmp_path, data_dir,
                                                bill_topics_on):
    _mirror(data_dir, {_T100: _DOC_TEXT})
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=data_dir)
    conn.commit()

    meta = client.get("/api/v1/meta").json()
    assert meta["features"]["bill_topics"] is True
    assert meta["bill_topics"]["bills"] >= 1
    assert meta["bill_topics"]["unit"] == "document block"
    assert meta["bill_topics"]["threshold"] == settings.parlacap_threshold


def test_meta_hides_the_badge_when_nothing_is_classified(client):
    """A DB with no iromány topics must switch the chip off rather than render a
    column of blanks — the same contract as the speech flag."""
    meta = client.get("/api/v1/meta").json()
    assert meta["features"]["bill_topics"] is False


def test_replay_refuses_a_cache_from_a_different_method(conn, tmp_path, data_dir,
                                                        bill_topics_on, monkeypatch):
    """The replay path cannot fingerprint (there is no text on that host), so the
    method tag is the only guard left — and it matters, because stored rows are
    label *indices*: replaying a different model's predictions would not error
    anywhere, it would silently relabel every iromány."""
    _mirror(data_dir, {_T100: _DOC_TEXT})
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=data_dir)
    assert conn.execute("SELECT COUNT(*) FROM bill_topic").fetchone()[0] > 0

    cache_file = tmp_path / "parlacap-cache.json"
    cache = json.loads(cache_file.read_text())
    for entry in cache["bills"].values():
        entry["method"] = "parlacap:some/other-model:len512:min12:blk60-220:v1:docv1"
    cache_file.write_text(json.dumps(cache))

    conn.execute("DELETE FROM bill_topic")
    monkeypatch.setattr(parlacap, "backend", lambda **kw: None)
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=tmp_path / "nowhere")
    assert conn.execute("SELECT COUNT(*) FROM bill_topic").fetchone()[0] == 0


def test_replay_accepts_a_matching_method(conn, tmp_path, data_dir,
                                          bill_topics_on, monkeypatch):
    """…while the ordinary shipped cache, made by this same method, still lands."""
    _mirror(data_dir, {_T100: _DOC_TEXT})
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=data_dir)
    expected = conn.execute("SELECT COUNT(*) FROM bill_topic").fetchone()[0]

    conn.execute("DELETE FROM bill_topic")
    monkeypatch.setattr(parlacap, "backend", lambda **kw: None)
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=tmp_path / "nowhere")
    assert conn.execute("SELECT COUNT(*) FROM bill_topic").fetchone()[0] == expected


# ---------------------------------------------------------------------------
# filtering irományok by topic (TOPIC-8)
# ---------------------------------------------------------------------------
# The one property that matters, and the one that is easy to get wrong: a filter
# sitting next to a visible label must select exactly the rows carrying that
# label. "Has a block labelled X" would be far cheaper and would quietly return
# documents whose chip says something else — so the filter resolves the *dominant*
# topic by the same rule the chip does, at the same read-time threshold.

class _TextClassifier:
    """Labels each block by a marker word in its text, so a test can build a
    corpus with known — and deliberately competing — topics."""

    def __init__(self, marks):
        self.marks = marks          # substring -> (label, score)
        self.calls = 0

    def __call__(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            hit = next(((lab, sc) for mark, (lab, sc) in self.marks.items()
                        if mark in t), None)
            if hit is None or parlacap.word_count(t) < settings.parlacap_min_words:
                out.append(None)
                continue
            label, score = hit
            out.append((parlacap.LABELS.index(label), score,
                        parlacap.LABELS.index("Other"), 0.01))
        return out


def _words(mark, n=30):
    return mark + " " + " ".join(f"szó{i}" for i in range(n))


@pytest.fixture
def topic_corpus(conn, tmp_path, data_dir, monkeypatch):
    """Three irományok with known, different topics.

    The fixture registry gives only T/100 a document link, so the other two are
    pointed at documents here — the loader joins on `bill.text_url`, which is
    exactly what a real registry supplies."""
    for instance in _live_settings():
        monkeypatch.setattr(instance, "parlacap", True)
        monkeypatch.setattr(instance, "bill_topics", True)
        monkeypatch.setattr(instance, "parlacap_min_words", 2)
        # Small enough that each recovered paragraph closes its own block —
        # otherwise the two competing topics below merge into one block and the
        # contest this fixture exists to create never happens.
        monkeypatch.setattr(instance, "parlacap_target_words", 5)

    urls = {"bill-uuid-1": _T100,
            "bill-uuid-2": "https://www.parlament.hu/irom43/00101/00101.pdf",
            "doc-uuid-3": "https://www.parlament.hu/irom43/00005/00005.pdf"}
    for bid, url in urls.items():
        conn.execute("UPDATE bill SET text_url = ? WHERE id = ?", (url, bid))
    conn.commit()

    # T/100 is contested: more Energy words than Health ones, but the Health
    # block is the more confident of the two — which is what makes the threshold
    # able to change its answer.
    _mirror(data_dir, {
        urls["bill-uuid-1"]: _words("EGESZSEG", 20) + "\n\n" + _words("ENERGIA", 60),
        urls["bill-uuid-2"]: _words("ENERGIA", 40),
        urls["doc-uuid-3"]: _words("KOZLEKEDES", 40),
    })
    fake = _TextClassifier({"EGESZSEG": ("Health", 0.97),
                            "ENERGIA": ("Energy", 0.92),
                            "KOZLEKEDES": ("Transportation", 0.96)})
    monkeypatch.setattr(parlacap, "classify", fake)
    monkeypatch.setattr(parlacap, "backend", lambda **kw: "local")
    loader.rebuild_bill_topics(conn, tmp_path, data_dir=data_dir)
    conn.commit()
    return fake


def _listing(client, **params):
    from urllib.parse import urlencode
    return client.get("/api/v1/bills?" + urlencode(params, doseq=True)).json()


def test_topic_filter_returns_exactly_the_rows_whose_chip_matches(client, topic_corpus):
    body = _listing(client, period=43, topic="Energy", limit=50)
    assert {b["bill_number"] for b in body["bills"]} == {"T/100", "T/101"}
    # …and every one of them actually shows that label
    assert all(b["topic"]["label"] == "Energy" for b in body["bills"])
    assert body["total"] == 2


def test_topic_filter_takes_several_topics(client, topic_corpus):
    body = _listing(client, period=43, topic=["Energy", "Transportation"], limit=50)
    assert {b["bill_number"] for b in body["bills"]} == {"T/100", "T/101", "I/5"}


def test_topic_filter_never_matches_a_merely_present_topic(client, topic_corpus):
    """T/100 has a confident Health block, but Energy outweighs it — so the
    document is *about* energy and must not appear under Health. This is the
    difference between filtering on the dominant topic and filtering on any
    block, and it is invisible unless a document has two."""
    body = _listing(client, period=43, topic="Health", limit=50)
    assert body["total"] == 0


def test_topic_filter_tracks_the_read_time_threshold(client, topic_corpus,
                                                     monkeypatch):
    """Raising the threshold past Energy's confidence must move the filter and
    the chip together — they are one rule (TOPIC-6). If the filter were served
    from a stored column, retuning the threshold would silently desynchronise
    the two."""
    for instance in _live_settings():
        monkeypatch.setattr(instance, "parlacap_threshold", 0.95)
    body = _listing(client, period=43, topic="Health", limit=50)
    assert {b["bill_number"] for b in body["bills"]} == {"T/100"}
    assert body["bills"][0]["topic"]["label"] == "Health"
    # Energy no longer clears the bar anywhere, so it selects nothing at all.
    assert _listing(client, period=43, topic="Energy", limit=50)["total"] == 0


def test_topic_facet_counts_match_the_filtered_totals(client, topic_corpus):
    """A facet count that did not equal the size of the list it opens would be
    worse than no count at all."""
    facets = client.get("/api/v1/bills/facets?period=43").json()
    assert facets["topics"], "expected the facet to offer the topics in scope"
    for f in facets["topics"]:
        total = _listing(client, period=43, topic=f["label"], limit=1)["total"]
        assert total == f["count"], f["label"]
    # ordered commonest-first, and carrying the CAP code the chip shows
    counts = [f["count"] for f in facets["topics"]]
    assert counts == sorted(counts, reverse=True)
    assert all(f["code"] == parlacap.CAP_CODES[f["label"]] for f in facets["topics"])


def test_topic_facet_respects_the_sibling_filters(client, topic_corpus):
    """The bills page passes main_type=T; its picker must not offer a topic that
    only an interpelláció carries, or selecting it yields an empty list."""
    facets = client.get("/api/v1/bills/facets?period=43&main_type=T").json()
    labels = {f["label"] for f in facets["topics"]}
    assert "Transportation" not in labels          # that is I/5, a non-bill
    assert "Energy" in labels


def test_an_unhonourable_topic_filter_returns_nothing_not_everything(client, conn):
    """On a DB with no topic table the filter cannot be applied. Dropping it and
    answering with the unfiltered list is the dangerous failure — the reader
    would be looking at every iromány believing it was one topic."""
    conn.execute("DROP TABLE IF EXISTS bill_topic")
    conn.commit()
    assert _listing(client, period=43, topic="Health")["total"] == 0
    # …while an unfiltered request is unaffected
    assert _listing(client, period=43)["total"] > 0


def test_dominant_topic_sql_agrees_with_aggregate(conn, topic_corpus):
    """The SQL winner and the Python winner are two expressions of one rule
    (weight by words, blocks break a tie, then the label) and must not drift."""
    sql_winner = dict(conn.execute(
        parlacap.dominant_topic_sql(), parlacap.topic_params()).fetchall())
    rows: dict[str, list] = {}
    for r in conn.execute("SELECT bill_id, block, label, score, runner_up, "
                          "runner_score, words FROM bill_topic ORDER BY bill_id, block"):
        rows.setdefault(r["bill_id"], []).append(tuple(r)[1:])
    agg_winner = {bid: parlacap.aggregate(rs)["label"]
                  for bid, rs in rows.items() if parlacap.aggregate(rs)}
    assert sql_winner == agg_winner
    assert agg_winner            # the corpus really did classify something


# ---------------------------------------------------------------------------
# the Témák analysis (TOPIC-9)
# ---------------------------------------------------------------------------
# The page aggregates the very rows the chips are read off, so the failure it has
# to be protected from is not an exception — it is a number that looks plausible
# and means something other than the label beside it. Four ways that happens:
# counting words the threshold rejected, letting "Other" behave like a topic,
# counting a speech under a topic whose chip says something else, and leaving the
# cycle scope out of one query of the several a page costs.

def _floor_speech(index, person_id, name, faction, paragraphs, speech_type=None):
    """One speech whose paragraphs each carry a marker word, so every block gets a
    known label at a known length (`_words` yields n+1 words)."""
    video = "https://example/playlist.m3u8"
    return {
        "originID": f"43-x-{index}", "speechIndex": index,
        "electoralPeriod": {"number": 43},
        "agendaItem": {"title": "Általános vita", "officialTitle": "Általános vita",
                       "type": "debate", "nativeType": "HU-debate"},
        "people": [{"type": "memberOfParliament", "label": name,
                    "context": "main-speaker", "personID": person_id,
                    "faction": {"label": faction}}],
        "media": {"videoFileURI": video, "duration": 7200,
                  "creator": "Magyar Országgyűlés", "license": "https://lic",
                  "sourcePage": "https://parlament.hu/x"},
        "textContents": [{"type": "proceedings", "sourceURI": "https://parlament.hu/x",
                          "textBody": [{"speech_id": f"43-x-{index}", "sentences": [
                              {"text": _words(mark, n), "timeStart": float(10 * i),
                               "timeEnd": float(10 * i + 9), "paragraph": i}
                              for i, (mark, n) in enumerate(paragraphs)]}]}],
        "debug": {"confidence": 0.7, "align-method": "estimated-day-offset",
                  "felszolalasTipusa": speech_type},
    }


def _floor_session(session, sitting, date, speeches):
    video = "https://example/playlist.m3u8"
    return {
        "meta": {"session": session, "electoralPeriod": 43, "sitting": sitting,
                 "date": date, "dateStart": f"{date}T08:00:00",
                 "dateEnd": f"{date}T10:00:00", "source": "felicitas-json",
                 "dayVideoURI": video, "timingMethod": "estimated-day-offset",
                 "sourceScrapedAt": "2026-06-18T00:00:00+00:00"},
        "data": speeches,
    }


@pytest.fixture
def floor_data(data_dir):
    """Two extra sittings, a year apart, whose speeches carry known topics.

    Written into the shared data directory *before* the DB is built (hence a
    fixture of its own, ordered ahead of `conn`), so the corpus the API serves is
    the one the loader produced rather than rows poked into a table.

    What each sitting is for:

    * 2026 — a speech that contests itself (a short confident Health block against
      a long Energy one, so the dominant rule has something to decide), a plain
      Health speech, a confidently *non-policy* speech, a chairing speech, and a
      block the model is not sure about;
    * 2027 — the same two members on different topics, so the trend has two years
      and the faction split is not a single sitting's accident.
    """
    import json
    (data_dir / "processed" / "43002-session.json").write_text(json.dumps(
        _floor_session("43002", 2, "2026-05-16", [
            _floor_speech(1, "k001", "Kovács Béla", "Fidesz",
                          [("EGESZSEG", 20), ("ENERGIA", 60)]),
            _floor_speech(2, "n002", "Nagy Anna", "TISZA", [("EGESZSEG", 40)]),
            # Confident, and confidently about nothing: greetings and points of
            # order. It must be reported as such and never become a subject.
            _floor_speech(3, "n002", "Nagy Anna", "TISZA", [("UGYREND", 30)]),
            # The chair. Never classified at all (STAT-1), so its marker must not
            # appear in the mix however loud it is.
            _floor_speech(4, "k001", "Kovács Béla", "Fidesz", [("KOZLEKEDES", 80)],
                          speech_type="ülésvezetés"),
            # Classified, but under the threshold: coverage, not evidence.
            _floor_speech(5, "k001", "Kovács Béla", "Fidesz", [("BIZONYTALAN", 30)]),
        ]), ensure_ascii=False))
    (data_dir / "processed" / "43003-session.json").write_text(json.dumps(
        _floor_session("43003", 3, "2027-03-02", [
            _floor_speech(1, "n002", "Nagy Anna", "TISZA", [("EGESZSEG", 20)]),
            _floor_speech(2, "k001", "Kovács Béla", "Fidesz", [("KOZLEKEDES", 60)]),
        ]), ensure_ascii=False))
    return data_dir


@pytest.fixture
def floor(floor_data, conn, tmp_path, monkeypatch):
    """The corpus above, classified by the marker-word stand-in."""
    for instance in _live_settings():
        monkeypatch.setattr(instance, "parlacap", True)
        monkeypatch.setattr(instance, "parlacap_min_words", 2)
        # Small enough that each paragraph closes its own block, so a speech's two
        # competing topics stay two predictions rather than merging into one.
        monkeypatch.setattr(instance, "parlacap_target_words", 5)
    fake = _TextClassifier({"EGESZSEG": ("Health", 0.97),
                            "ENERGIA": ("Energy", 0.92),
                            "KOZLEKEDES": ("Transportation", 0.96),
                            "UGYREND": ("Other", 0.99),
                            "BIZONYTALAN": ("Housing", 0.55)})
    monkeypatch.setattr(parlacap, "classify", fake)
    monkeypatch.setattr(parlacap, "backend", lambda **kw: "local")
    loader.rebuild_speech_topics(conn, tmp_path)
    conn.commit()
    return fake


@pytest.fixture
def floor_client(floor, client):
    """The API bound to the classified corpus above.

    Ordering, not convenience: `client` builds the database the first time it is
    asked for, so a test that took it *before* `floor` would be served a corpus
    written after the build. Going through one fixture makes that impossible.
    """
    return client


def _mix(client, **params):
    from urllib.parse import urlencode
    body = client.get("/api/v1/proceedings/topics"
                      + ("?" + urlencode(params, doseq=True) if params else ""))
    assert body.status_code == 200, body.text
    return body.json()


def _labels(body):
    return {t["label"]: t for t in body["topics"]}


def test_the_mix_counts_the_words_the_blocks_actually_carry(floor_client, conn):
    """The chart's arithmetic against the stored rows: every topic's words are the
    confident words of that label, and the shares are of their sum."""
    body = _mix(floor_client)
    stored = dict(conn.execute(
        "SELECT label, SUM(words) FROM speech_topic WHERE score >= ? "
        "AND label <> 'Other' GROUP BY label",
        (settings.parlacap_threshold,)).fetchall())
    assert {t["label"]: t["words"] for t in body["topics"]} == stored
    total = sum(stored.values())
    assert body["coverage"]["policy_words"] == total
    assert sum(t["share"] for t in body["topics"]) == pytest.approx(1.0)
    # Commonest first, so the page can render the list as it arrives.
    assert [t["words"] for t in body["topics"]] == sorted(
        (t["words"] for t in body["topics"]), reverse=True)


def test_other_is_reported_but_is_never_a_topic(floor_client):
    """CAP's "Other" is how the model says "no policy here" — a real prediction,
    and the one thing on this page that must not be rendered as a subject."""
    body = _mix(floor_client)
    assert "Other" not in _labels(body)
    assert body["coverage"]["other_words"] > 0


def test_an_unsure_block_is_coverage_not_evidence(floor_client):
    """The Housing block sits under the threshold. It has to show up in what the
    page admits it cannot label, and nowhere else."""
    body = _mix(floor_client)
    assert "Housing" not in _labels(body)
    assert body["coverage"]["confident_blocks"] < body["coverage"]["blocks"]


def test_the_chair_is_never_in_the_mix(floor_client):
    """The chairing speech is the loudest Transportation block in 2026 and is
    excluded structurally (STAT-1) — the model reads an announcement as being
    about whatever bill it names."""
    buckets = {b["period"]: b["labels"] for b in _mix(floor_client)["trend"]["buckets"]}
    assert "Transportation" not in buckets["2026"]
    assert "Transportation" in buckets["2027"]


def test_a_topics_speech_count_is_the_speeches_whose_chip_says_so(floor_client, conn):
    """The count beside a topic must be the size of the set a reader would find by
    opening every speech under it — the dominant-topic rule, not "mentions it"."""
    body = _mix(floor_client)
    rows: dict[str, list] = {}
    for r in conn.execute("SELECT speech_id, block, label, score, runner_up, "
                          "runner_score, words FROM speech_topic "
                          "ORDER BY speech_id, block"):
        rows.setdefault(r["speech_id"], []).append(tuple(r)[1:])
    expected: dict[str, int] = {}
    for uid, blocks in rows.items():
        agg = parlacap.aggregate(blocks)
        if agg:
            expected[agg["label"]] = expected.get(agg["label"], 0) + 1
    assert {t["label"]: t["count"] for t in body["topics"] if t["count"]} == expected
    assert body["coverage"]["labelled"] == sum(expected.values())
    # The contested speech is Energy's alone: its Health block is confident but
    # short, so Health runs through the speech without being its subject.
    assert _labels(body)["Health"]["count"] == 2
    assert _labels(body)["Energy"]["count"] == 1


def test_coverage_says_what_the_picture_is_drawn_from(floor_client, conn):
    """A share of the House's speech is unreadable without the population behind
    it, and every one of these can only shrink leftwards (TRUST-1)."""
    cov = _mix(floor_client)["coverage"]
    speeches = conn.execute(
        "SELECT COUNT(*) FROM speech WHERE procedural = 0 AND has_text = 1"
    ).fetchone()[0]
    assert cov["speeches"] == speeches
    assert cov["labelled"] <= cov["classified"] <= cov["speeches"]
    # The fixture's first sitting carries speeches the classifier had no marker
    # for, so "classified" is genuinely short of the population.
    assert cov["classified"] < cov["speeches"]


def test_the_trend_is_cut_by_year_and_carries_its_own_total(floor_client):
    """Each bucket's share is taken against that year, not the corpus: the shape of
    a quiet year's agenda is the point, not that the year was quiet."""
    buckets = _mix(floor_client)["trend"]["buckets"]
    assert [b["period"] for b in buckets] == ["2026", "2027"]
    for b in buckets:
        assert b["policy_words"] == sum(b["labels"].values())
    assert sum(b["policy_words"] for b in buckets) == _mix(floor_client)["coverage"]["policy_words"]


def test_the_mix_follows_the_read_time_threshold(floor_client, monkeypatch):
    """The same rows, a different answer, nothing reclassified (TOPIC-6) — and the
    cache must not outlive the change, which is why the threshold is in its key."""
    before = _labels(_mix(floor_client))
    assert "Energy" in before
    for instance in _live_settings():
        monkeypatch.setattr(instance, "parlacap_threshold", 0.95)
    after = _mix(floor_client)
    assert "Energy" not in _labels(after)          # 0.92 no longer clears the bar
    assert after["threshold"] == 0.95
    # The contested speech changes hands rather than vanishing: at 0.95 only its
    # short Health block still counts, so what was a speech about energy is now a
    # speech about health — same rows, same speech, different subject.
    assert before["Health"]["count"] == 2
    assert _labels(after)["Health"]["count"] == 3
    assert after["coverage"]["labelled"] == 4


def test_the_cycle_scope_reaches_every_query_behind_the_page(floor_client):
    """A page costs several queries; one that forgot the scope would show the whole
    archive's numbers under a single cycle's heading."""
    assert _mix(floor_client, period=43)["topics"] == _mix(floor_client)["topics"]
    empty = _mix(floor_client, period=39)
    assert empty["topics"] == []
    assert empty["coverage"]["blocks"] == 0
    assert empty["coverage"]["speeches"] == 0
    assert empty["trend"]["buckets"] == []


def test_a_db_with_no_topics_says_so_rather_than_answering_zero(floor_client, conn):
    """An empty chart and a chart of a corpus that was never classified are
    different claims; only one of them is honest here."""
    conn.execute("DROP TABLE speech_topic")
    conn.commit()
    assert floor_client.get("/api/v1/proceedings/topics").status_code == 503


def test_topic_detail_splits_a_topic_between_its_factions(floor_client):
    """Two shares, because "who owns this topic" and "whose House is it" are
    different claims and the bigger faction wins the first by arithmetic."""
    body = floor_client.get("/api/v1/proceedings/topics/Health").json()
    factions = {f["label"]: f for f in body["factions"]}
    assert set(factions) == {"Fidesz", "TISZA"}
    assert sum(f["share_of_topic"] for f in body["factions"]) == pytest.approx(1.0)
    # TISZA said nothing else that cleared the bar, so all of its policy speech is
    # this topic — while for Fidesz it is a corner of theirs.
    assert factions["TISZA"]["share_of_own"] == pytest.approx(1.0)
    assert factions["Fidesz"]["share_of_own"] < 0.5
    assert factions["TISZA"]["words"] > factions["Fidesz"]["words"]


def test_topic_detail_names_who_speaks_about_it(floor_client):
    """The panel's way in: ranked by words on the topic, each resolvable to a
    profile."""
    body = floor_client.get("/api/v1/proceedings/topics/Transportation").json()
    assert [s["person_id"] for s in body["speakers"]] == ["k001"]
    assert body["speakers"][0]["name"] == "Kovács Béla"
    assert body["speakers"][0]["share_of_topic"] == pytest.approx(1.0)
    health = floor_client.get("/api/v1/proceedings/topics/Health").json()
    words = [s["words"] for s in health["speakers"]]
    assert words == sorted(words, reverse=True)


def test_topic_detail_is_scoped_and_refuses_a_non_topic(floor_client):
    assert floor_client.get("/api/v1/proceedings/topics/Health?period=39"
                      ).json()["factions"] == []
    # "Other" is a prediction, not a subject: there is no page for it.
    assert floor_client.get("/api/v1/proceedings/topics/Other").status_code == 404
    assert floor_client.get("/api/v1/proceedings/topics/Nonsense").status_code == 404


# --- the document half (TOPIC-8 read as an agenda) --------------------------

def test_the_document_mix_is_the_same_shape_from_another_table(client, topic_corpus):
    """The two halves are drawn as one chart, so they must be one shape and one
    arithmetic — the speech share and the document share are compared by eye."""
    body = client.get("/api/v1/bills/topics?period=43").json()
    assert set(body) == {"threshold", "coverage", "topics", "trend"}
    assert sum(t["share"] for t in body["topics"]) == pytest.approx(1.0)
    labels = {t["label"]: t for t in body["topics"]}
    # T/100 is contested: Health runs through it, Energy is what it is about. The
    # two columns of the chart are exactly this difference.
    assert labels["Health"]["words"] > 0
    assert labels["Health"]["count"] == 0
    assert labels["Energy"]["count"] == 2


def test_the_document_mixs_counts_match_the_list_they_open(client, topic_corpus):
    """Each count is a promise about a filtered list; the facet already keeps that
    promise and this page must not make a different one."""
    body = client.get("/api/v1/bills/topics?period=43").json()
    for t in body["topics"]:
        listed = _listing(client, period=43, topic=t["label"], limit=1)["total"]
        assert listed == t["count"], t["label"]


def test_the_document_coverage_names_the_population_it_could_read(client, topic_corpus):
    """Only an iromány whose document was mirrored and read as text can carry a
    topic (TOPIC-8), so a share of irományok means nothing without that count."""
    cov = client.get("/api/v1/bills/topics?period=43").json()["coverage"]
    assert cov["labelled"] <= cov["classified"] <= cov["with_text"] <= cov["bills"]
    assert cov["bills"] > 0


def test_the_document_mix_says_so_when_nothing_was_classified(client, conn,
                                                              topic_corpus):
    conn.execute("DROP TABLE bill_topic")
    conn.commit()
    assert client.get("/api/v1/bills/topics?period=43").status_code == 503


# --- one member's own agenda (TOPIC-10) -------------------------------------
# The same corpus read one speaker at a time. What has to hold is that a profile
# and the analysis page never tell different stories about the same words: the
# member's figure is their slice of the very same blocks, and the reference it is
# read against is the floor mix the Témák page itself plots.

def _rep_mix(client, person_id, **params):
    from urllib.parse import urlencode
    body = client.get(f"/api/v1/proceedings/topics/representative/{person_id}"
                      + ("?" + urlencode(params, doseq=True) if params else ""))
    assert body.status_code == 200, body.text
    return body.json()


def test_a_members_mix_is_their_own_words(floor_client, conn):
    """The shares are of what *this member* said, not of the floor."""
    body = _rep_mix(floor_client, "k001")
    stored = dict(conn.execute(
        "SELECT t.label, SUM(t.words) FROM speech_topic t "
        "JOIN speech sp ON sp.uid = t.speech_id "
        "WHERE sp.person_id = 'k001' AND t.score >= ? AND t.label <> 'Other' "
        "GROUP BY t.label", (settings.parlacap_threshold,)).fetchall())
    assert {t["label"]: t["words"] for t in body["topics"]} == stored
    assert sum(t["share"] for t in body["topics"]) == pytest.approx(1.0)
    assert body["person_id"] == "k001"


def test_the_chair_is_never_in_a_members_mix(floor_client):
    """Kovács chaired a sitting and talked about transport as a member on another.
    Only the second is his subject — the chairing turn is not classified at all
    (STAT-1), and the two must not pool."""
    body = _rep_mix(floor_client, "k001")
    transport = _labels(body)["Transportation"]
    assert transport["words"] == 61          # the member's speech, not the chair's 81


def test_a_members_topic_carries_the_houses_share_beside_it(floor_client):
    """The reference that stops the figure reading as a personality test: every
    row also carries what the whole House gave that topic in the same scope, and
    it is the number the Témák page draws — not a second computation of it."""
    body = _rep_mix(floor_client, "k001")
    floor = _labels(_mix(floor_client))
    for topic in body["topics"]:
        assert topic["house_share"] == pytest.approx(floor[topic["label"]]["share"])
    rows = _labels(body)
    # Kovács is the House's energy speaker and barely its health one, which is
    # visible only against the reference: both are shares of his own words.
    assert rows["Energy"]["share"] > rows["Energy"]["house_share"]
    assert rows["Health"]["share"] < rows["Health"]["house_share"]


def test_a_members_count_is_the_speeches_whose_chip_says_so(floor_client):
    """`count` is the dominant-topic rule, so it is the set of speeches a reader
    would find — which is why a topic can hold words and still be the subject of
    none of them."""
    rows = _labels(_rep_mix(floor_client, "k001"))
    assert rows["Energy"]["count"] == 1        # the speech it won on words
    assert rows["Transportation"]["count"] == 1
    # Health lost that same speech to Energy: real words, no speech of its own.
    assert rows["Health"]["words"] > 0
    assert rows["Health"]["count"] == 0


def test_a_members_coverage_says_what_the_picture_is_drawn_from(floor_client):
    """A profile has to state how much of the member's speech carries a label at
    all before it shows the shape of it (TRUST-1)."""
    cov = _rep_mix(floor_client, "k001")["coverage"]
    # Four speeches of his have a transcript and are not chairing turns; three of
    # them carry marker text the stand-in model recognises, and two of those came
    # out with a subject — the fourth stayed under the confidence bar. Every step
    # of that narrowing is reported, because each one loses different speech.
    assert cov["speeches"] == 4
    assert cov["classified"] == 3
    assert cov["labelled"] == 2
    assert cov["confident_blocks"] < cov["blocks"]


def test_a_members_mix_is_cycle_scoped(floor_client):
    """Same scope rule as everything else on the profile (§4A) — and the
    reference moves with it rather than staying the whole corpus's."""
    body = _rep_mix(floor_client, "k001", period=39)
    assert body["topics"] == []
    assert body["coverage"]["speeches"] == 0
    both = _rep_mix(floor_client, "n002")
    years = {b["period"] for b in both["trend"]["buckets"]}
    assert years == {"2026", "2027"}


def test_an_unknown_member_has_no_mix(floor_client):
    assert floor_client.get(
        "/api/v1/proceedings/topics/representative/nobody").status_code == 404


def test_a_db_with_no_topics_says_so_for_a_member_too(floor_client, conn):
    """Absent classification is reported as such here exactly as on the analysis
    page: a profile must not draw an empty chart and call it an agenda."""
    conn.execute("DROP TABLE speech_topic")
    conn.commit()
    assert floor_client.get(
        "/api/v1/proceedings/topics/representative/k001").status_code == 503
