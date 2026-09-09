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
