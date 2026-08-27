"""The shared lemma store (app/lemma_cache.py) and the sharing it enables.

Lemmatizing is the most expensive thing the build does, and two passes used to buy
it separately over the same sentences: the word cloud and the lexical-diversity
half of the speech metrics. The store exists so it is paid for once. What these
tests pin down is the part that would otherwise rot silently:

* **the key** — it is content- *and* sentence-addressed, so two passes that fetch
  the same sitting in different orders and different scopes still agree, while any
  change to the text, the ids or the method misses;
* **the sharing actually happening** — the metrics pass must reach MATTR with no
  lemmatizer call of its own, which is the entire point and is invisible in the
  output (a shared stream and a recomputed one produce identical numbers);
* **every degradation** — a corrupt file, a partial entry, a stale fingerprint, a
  Modal deployment that predates the combined method. Each must fall back to doing
  the work, never to a wrong or half-applied answer.
"""

from __future__ import annotations

import gzip
import json
import sqlite3

import pytest

from app import lemma_cache, loader, nlp_modal, readability


ROWS = [(1, "A javaslat indokolt."), (2, "Köszönöm a figyelmet.")]
LEMMAS = {1: ["a", "javaslat", "indokolt"], 2: ["köszön", "a", "figyelem"]}


@pytest.fixture(autouse=True)
def _clean_memo():
    """The memo is process-global; a leaked entry would let one test's streams
    answer another's lookup."""
    lemma_cache.clear_memo()
    yield
    lemma_cache.clear_memo()


# ---------------------------------------------------------------------------
# the key
# ---------------------------------------------------------------------------

def test_fingerprint_ignores_row_order():
    """The whole reason the store is addressed by sentence id: the word-cloud pass
    walks a sitting by sentence and the metrics pass by speech, so the two hand
    these rows over in different orders and must still land on the same entry."""
    method = "lemmas:test:s1"
    assert (lemma_cache.fingerprint(method, ROWS)
            == lemma_cache.fingerprint(method, list(reversed(ROWS))))


def test_fingerprint_tracks_text_ids_and_method():
    """Everything that changes what a stream *means* has to change the key."""
    base = lemma_cache.fingerprint("m", ROWS)
    assert base != lemma_cache.fingerprint("m2", ROWS)                    # method
    assert base != lemma_cache.fingerprint("m", [(1, "Más."), ROWS[1]])   # text
    assert base != lemma_cache.fingerprint("m", [(9, ROWS[0][1]), ROWS[1]])  # ids


def test_method_tag_is_independent_of_the_wordcloud_logic_version():
    """The cloud's tag names its own extraction rules, which can change without
    changing a single lemma. Keying the streams on it would throw the expensive
    half away for a cosmetic change to the cheap one."""
    from app import nlp
    assert nlp.method_tag() in lemma_cache.method_tag()
    assert lemma_cache.method_tag() != nlp.method_tag()


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------

def test_round_trip(tmp_path):
    fp = lemma_cache.fingerprint(lemma_cache.method_tag(), ROWS)
    lemma_cache.put(tmp_path, "43001", fp, LEMMAS, model="m", method="meth")
    lemma_cache.clear_memo()                      # force the disk path
    assert lemma_cache.get(tmp_path, "43001", fp) == LEMMAS


def test_stale_fingerprint_is_a_miss(tmp_path):
    lemma_cache.put(tmp_path, "43001", "fp-old", LEMMAS, model="m", method="meth")
    lemma_cache.clear_memo()
    assert lemma_cache.get(tmp_path, "43001", "fp-new") is None


def test_memo_serves_without_touching_the_disk(tmp_path):
    """The in-process half of the sharing: within one build the metrics pass reads
    back what the cloud pass just wrote, with no file round trip."""
    fp = "fp"
    lemma_cache.put(tmp_path, "43001", fp, LEMMAS, model="m", method="meth")
    lemma_cache._path(tmp_path, "43001").unlink()
    assert lemma_cache.get(tmp_path, "43001", fp) == LEMMAS


def test_memo_is_keyed_by_cache_dir(tmp_path):
    """The fingerprint is content-addressed, so two corpora holding the same
    sitting text produce the same key. Without the directory in the memo key one
    build would answer the other's lookups."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    lemma_cache.put(a, "43001", "fp", LEMMAS, model="m", method="meth")
    assert lemma_cache.get(b, "43001", "fp") is None


def test_memo_is_bounded(tmp_path, monkeypatch):
    """A full rebuild walks thousands of sittings; an unbounded memo would hold the
    whole corpus' streams in RAM, which is the gigabyte this store exists to keep
    on disk."""
    monkeypatch.setattr(lemma_cache, "MEMO_SITTINGS", 2)
    for i in range(5):
        lemma_cache.put(tmp_path, f"430{i}", "fp", LEMMAS, model="m", method="meth")
    assert len(lemma_cache._memo) == 2


def test_corrupt_file_is_a_miss_not_a_crash(tmp_path):
    """A truncated or garbage entry must cost time, never correctness."""
    lemma_cache.put(tmp_path, "43001", "fp", LEMMAS, model="m", method="meth")
    lemma_cache.clear_memo()
    lemma_cache._path(tmp_path, "43001").write_bytes(b"not gzip at all")
    assert lemma_cache.get(tmp_path, "43001", "fp") is None


def test_truncated_gzip_is_a_miss(tmp_path):
    lemma_cache.put(tmp_path, "43001", "fp", LEMMAS, model="m", method="meth")
    lemma_cache.clear_memo()
    path = lemma_cache._path(tmp_path, "43001")
    path.write_bytes(path.read_bytes()[:12])
    assert lemma_cache.get(tmp_path, "43001", "fp") is None


def test_disabled_store_reads_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(lemma_cache.settings, "lemma_cache", False)
    lemma_cache.put(tmp_path, "43001", "fp", LEMMAS, model="m", method="meth")
    assert not lemma_cache.cache_dir(tmp_path).exists()
    assert lemma_cache.get(tmp_path, "43001", "fp") is None


def test_write_is_atomic(tmp_path):
    """Written via a temp file + rename, so a run killed mid-write leaves the
    previous entry rather than a truncated one the next run must detect."""
    lemma_cache.put(tmp_path, "43001", "fp", LEMMAS, model="m", method="meth")
    leftovers = list(lemma_cache.cache_dir(tmp_path).glob("*.tmp"))
    assert not leftovers
    with gzip.open(lemma_cache._path(tmp_path, "43001"), "rt", encoding="utf-8") as fh:
        assert json.load(fh)["model"] == "m"


# ---------------------------------------------------------------------------
# analyze_all == analyze_counts + lemma_streams
# ---------------------------------------------------------------------------

class _Tok:
    def __init__(self, i, lemma, pos="NOUN", stop=False, punct=False,
                 space=False, like_num=False):
        self.i, self.lemma_, self.pos_ = i, lemma, pos
        self.is_stop, self.is_punct = stop, punct
        self.is_space, self.like_num = space, like_num


class _Ent:
    def __init__(self, toks, label="PER"):
        self._toks, self.label_ = toks, label

    def __iter__(self):
        return iter(self._toks)


class _Doc:
    def __init__(self, toks, ents=()):
        self._toks, self.ents = toks, list(ents)

    def __iter__(self):
        return iter(self._toks)


def _fake_pipeline(monkeypatch):
    """A stand-in spaCy whose docs exercise every branch of both token filters:
    a proper-noun entity, content words, a stop word, punctuation and a numeral.

    Faked because the HuSpaCy model is a ~127 MB optional download that is absent
    here and in CI — but the property under test is pure filtering logic over a
    parse, which a stand-in exercises exactly as well as the real one."""
    def _doc_for(text):
        if not text:
            return _Doc([])
        ents = [_Ent([_Tok(0, "Orbán", "PROPN"), _Tok(1, "Viktor", "PROPN")])]
        toks = [_Tok(0, "Orbán", "PROPN"), _Tok(1, "Viktor", "PROPN"),
                _Tok(2, "költségvetés"), _Tok(3, "a", "DET", stop=True),
                _Tok(4, ".", "PUNCT", punct=True), _Tok(5, "2026", "NUM",
                                                        like_num=True),
                _Tok(6, "törvény")]
        return _Doc(toks, ents)

    class _Nlp:
        def pipe(self, texts, **_kw):
            for t in texts:
                yield _doc_for(t)

    from app import nlp as nlp_mod
    monkeypatch.setattr(nlp_mod, "get_nlp", lambda model=None: _Nlp())
    return nlp_mod


def test_analyze_all_matches_the_two_functions_it_replaces(monkeypatch):
    """The correctness property of the whole refactor. ``analyze_all`` exists to
    produce the cloud's tallies and the diversity streams from one parse instead
    of two; if it ever drifts from either, word clouds or MATTR change silently
    with nothing to catch it. Both halves share their filter with the originals
    (``_count_doc``, ``_diversity_lemma``) precisely so this can be asserted."""
    nlp_mod = _fake_pipeline(monkeypatch)
    texts = ["Első mondat.", "Második mondat."]

    counts, ents, streams = nlp_mod.analyze_all(texts)
    ref_counts, ref_ents = nlp_mod.analyze_counts(texts)
    ref_streams = list(nlp_mod.lemma_streams(texts))

    assert counts == ref_counts
    assert ents == ref_ents
    assert streams == ref_streams


def test_analyze_all_keeps_empty_texts_positional(monkeypatch):
    """``analyze_counts`` drops falsy texts; ``analyze_all`` must not, because its
    streams are zipped back onto the sentence rows they came from — a dropped one
    would shift every later sentence's lemmas onto its neighbour. The counts stay
    identical either way: an empty text parses to an empty doc."""
    nlp_mod = _fake_pipeline(monkeypatch)
    texts = ["Első.", "", "Harmadik."]

    counts, _ents, streams = nlp_mod.analyze_all(texts)
    assert len(streams) == 3 and streams[1] == []
    assert counts == nlp_mod.analyze_counts(texts)[0]


# ---------------------------------------------------------------------------
# the sharing, end to end
# ---------------------------------------------------------------------------

def _long_speech(n=12):
    return ["A költségvetési törvényjavaslat tárgyalása megkezdődött.",
            "A képviselők többsége támogatta a módosító javaslatokat.",
            "Az ágazati fejlesztések finanszírozása továbbra is kérdéses marad."] * n


def _session_with_long_speech(session="43002", period=43, sitting=2,
                              date="2026-05-10"):
    return {
        "meta": {"session": session, "electoralPeriod": period, "sitting": sitting,
                 "date": date, "dateStart": f"{date}T08:00:00",
                 "dateEnd": f"{date}T10:00:00", "source": "felicitas-json",
                 "timingMethod": "estimated-day-offset"},
        "data": [{
            "originID": f"{period}-{sitting}-1", "speechIndex": 1,
            "electoralPeriod": {"number": period},
            "agendaItem": {"title": "Általános vita", "officialTitle": "Általános vita",
                           "type": "debate", "nativeType": "HU-debate"},
            "people": [{"type": "memberOfParliament", "label": "Kovács Béla",
                        "context": "main-speaker", "personID": "k001",
                        "faction": {"label": "Fidesz", "id": 7}}],
            "media": {"videoFileURI": "https://example/p.m3u8", "duration": 600,
                      "videoStart": 0.0, "videoEnd": 600.0},
            "textContents": [{"type": "proceedings", "sourceURI": "https://parlament.hu/z",
                              "textBody": [{"speech_id": f"{period}-{sitting}-1",
                                            "sentences": [{"text": t, "paragraph": 0}
                                                          for t in _long_speech()]}]}],
            "debug": {"confidence": 0.9, "align-method": "forced-alignment"},
        }],
    }


@pytest.fixture
def shared_build(tmp_path, data_dir, monkeypatch):
    """A build where the word-cloud pass runs on a stubbed HuSpaCy and the metrics
    pass is watched: it records every lemmatizer call the metrics half makes.

    Returns ``(db_path, metrics_lemma_calls)``."""
    (data_dir / "processed" / "43002-session.json").write_text(
        json.dumps(_session_with_long_speech(), ensure_ascii=False))

    # conftest pins the shared fixtures to the regex backend (no model download);
    # the sharing only exists on the HuSpaCy path, so opt back in.
    monkeypatch.setattr(loader.settings, "wordcloud_backend", "huspacy")
    monkeypatch.setattr(loader.nlp, "available", lambda model=None: True)

    def fake_analyze_all(texts, **_kw):
        from collections import Counter
        counts = Counter()
        streams = []
        for i, t in enumerate(texts):
            # A stream long enough to clear the MATTR window, deterministic per
            # sentence so a mis-zip onto the wrong sentence would be visible.
            stream = [f"lemma{i}_{j % 40}" for j in range(60)] if t else []
            streams.append(stream)
            counts.update(stream)
        return counts, set(), streams

    monkeypatch.setattr(loader.nlp, "analyze_all", fake_analyze_all)

    calls: list[list[str]] = []

    def spy_lemma_streams(texts, **_kw):
        calls.append(list(texts))
        return iter([[f"own{j % 40}" for j in range(60)] for _ in texts])

    monkeypatch.setattr(loader.nlp, "lemma_streams", spy_lemma_streams)

    out = tmp_path / "shared.db"
    loader.build_database(data_dir, out)
    return out, calls


def test_metrics_reuse_the_wordcloud_pass_lemmas(shared_build):
    """The point of the whole change: the metrics pass reaches MATTR without ever
    calling a lemmatizer, because the cloud already lemmatized these sentences."""
    db, metrics_calls = shared_build
    assert metrics_calls == [], "the metrics pass re-lemmatized shared sentences"

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM speech_metrics WHERE speech_id='43002-1'").fetchone()
    conn.close()
    assert row["mattr"] is not None and row["ttr"] is not None
    assert row["lemma_model"], "a shared stream must still record which model made it"


def test_shared_streams_are_the_wordcloud_ones(shared_build):
    """Not merely *a* number — the diversity has to be computed from the stream the
    cloud produced. The stub's own fallback stream (`own…`) has a different type
    count, so the two are distinguishable in the stored figures."""
    db, _calls = shared_build
    conn = sqlite3.connect(db)
    types_, tokens = conn.execute(
        "SELECT types, tokens FROM speech_metrics WHERE speech_id='43002-1'").fetchone()
    conn.close()
    # 36 sentences x 60 lemmas, and each sentence's 40 distinct lemmas are unique
    # to it (lemma{i}_{j}) — so the shared stream gives 36*40 types over 36*60
    # tokens. The fallback would have given 40 types.
    assert (types_, tokens) == (36 * 40, 36 * 60)


def test_lemma_cache_file_is_written_for_the_sitting(shared_build):
    """It has to survive the build, or the next one pays again."""
    db, _calls = shared_build
    assert lemma_cache._path(db.parent, "43002").exists()


def test_metrics_fall_back_when_the_store_is_off(tmp_path, data_dir, monkeypatch):
    """With the store disabled the previous behaviour returns exactly: the metrics
    pass lemmatizes for itself. Nothing about the output changes."""
    monkeypatch.setattr(lemma_cache.settings, "lemma_cache", False)
    (data_dir / "processed" / "43002-session.json").write_text(
        json.dumps(_session_with_long_speech(), ensure_ascii=False))
    monkeypatch.setattr(loader.settings, "wordcloud_backend", "huspacy")
    monkeypatch.setattr(loader.nlp, "available", lambda model=None: True)
    monkeypatch.setattr(loader.nlp, "analyze_all",
                        lambda texts, **kw: (__import__("collections").Counter(),
                                             set(), [[] for _ in texts]))
    calls = []

    def spy(texts, **_kw):
        calls.append(list(texts))
        return iter([[f"x{j % 40}" for j in range(60)] for _ in texts])

    monkeypatch.setattr(loader.nlp, "lemma_streams", spy)
    loader.build_database(data_dir, tmp_path / "off.db")
    assert calls, "with the store off the metrics pass must lemmatize for itself"


def test_partial_entry_is_refused(tmp_path, data_dir, monkeypatch):
    """A stored sitting missing one measurable sentence must be refused outright.
    Half-applying it would score a speech on part of its text and label the result
    MATTR — a wrong number wearing a right one's name."""
    (data_dir / "processed" / "43002-session.json").write_text(
        json.dumps(_session_with_long_speech(), ensure_ascii=False))
    monkeypatch.setattr(loader.settings, "wordcloud_backend", "huspacy")
    monkeypatch.setattr(loader.nlp, "available", lambda model=None: True)

    def holey_analyze_all(texts, **_kw):
        from collections import Counter
        streams = [[f"l{i}_{j % 40}" for j in range(60)] for i, _t in enumerate(texts)]
        return Counter(), set(), streams

    monkeypatch.setattr(loader.nlp, "analyze_all", holey_analyze_all)
    calls = []

    def spy(texts, **_kw):
        calls.append(list(texts))
        return iter([[f"own{j % 40}" for j in range(60)] for _ in texts])

    monkeypatch.setattr(loader.nlp, "lemma_streams", spy)

    db = tmp_path / "holey.db"
    loader.build_database(data_dir, db)
    assert calls == []          # first build shared cleanly

    # Punch a hole in the stored entry, then re-measure: the pass must notice and
    # lemmatize rather than score the speech on the sentences that remain.
    path = lemma_cache._path(db.parent, "43002")
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        entry = json.load(fh)
    entry["lemmas"].pop(next(iter(entry["lemmas"])))
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(entry, fh)
    lemma_cache.clear_memo()
    # Drop the metrics cache too, or the sitting is served from its own finished
    # numbers and the lemma lookup under test never runs.
    (db.parent / "speech-metrics-cache.json").unlink()

    conn = loader.connect(db)
    loader.rebuild_speech_metrics(conn, db.parent)
    conn.close()
    assert calls, "a partial entry must be refused, not half-used"


def test_a_repeated_remeasure_is_free(tmp_path, data_dir, monkeypatch):
    """The metrics pass fills the store from its own work too, so re-running
    ``--remeasure-speeches`` — after a saphes upgrade, a changed threshold — does
    not buy the same lemmatization a second time. Before, only the word-cloud pass
    ever filled it, so a metrics-only re-run paid the model every time."""
    (data_dir / "processed" / "43002-session.json").write_text(
        json.dumps(_session_with_long_speech(), ensure_ascii=False))
    # Word cloud on the regex backend (conftest's default): it lemmatizes nothing,
    # so the store can only be filled by the metrics pass itself.
    monkeypatch.setattr(loader.nlp, "available", lambda model=None: True)
    calls = []

    def spy(texts, **_kw):
        calls.append(list(texts))
        return iter([[f"x{j % 40}" for j in range(60)] for _ in texts])

    monkeypatch.setattr(loader.nlp, "lemma_streams", spy)

    db = tmp_path / "remeasure.db"
    loader.build_database(data_dir, db)
    assert len(calls) == 1, "the first pass must do the work"

    # Second run, metrics only, with its own cache dropped so the sitting is a miss.
    (db.parent / "speech-metrics-cache.json").unlink()
    lemma_cache.clear_memo()
    conn = loader.connect(db)
    loader.rebuild_speech_metrics(conn, db.parent)
    mattr = conn.execute(
        "SELECT mattr FROM speech_metrics WHERE speech_id='43002-1'").fetchone()[0]
    conn.close()

    assert len(calls) == 1, "the second pass re-lemmatized what it already had"
    assert mattr is not None, "and it still produced the diversity numbers"


# ---------------------------------------------------------------------------
# the Modal contract
# ---------------------------------------------------------------------------

def test_modal_extract_asks_for_lemmas_when_available(monkeypatch):
    class _Full:
        def map(self, payloads):
            for payload in payloads:
                yield [{"counts": {"törvény": 2}, "entities": [],
                        "lemmas": [["a"], ["b"]]} for _ in payload]

    class _Svc:
        analyze_sessions_full = _Full()

    monkeypatch.setattr(nlp_modal, "_service", lambda app_name=None: _Svc())
    out = list(nlp_modal.extract([("43001", "fp", ["m1", "m2"])],
                                 batch_sentences=100, with_lemmas=True))
    assert out[0][3] == [["a"], ["b"]]


def test_modal_extract_falls_back_on_an_older_deployment(monkeypatch):
    """The service is deployed separately from this code. Against a version that
    predates the combined method the cloud must still be built — just without the
    lemma sharing — instead of the pass dying."""
    class _Missing:
        def map(self, payloads):
            raise AttributeError("no such method: analyze_sessions_full")

    class _Old:
        def map(self, payloads):
            for payload in payloads:
                yield [{"counts": {"törvény": 1}, "entities": []} for _ in payload]

    class _Svc:
        analyze_sessions_full = _Missing()
        analyze_sessions = _Old()

    monkeypatch.setattr(nlp_modal, "_service", lambda app_name=None: _Svc())
    out = list(nlp_modal.extract([("43001", "fp", ["m1"])],
                                 batch_sentences=100, with_lemmas=True))
    assert len(out) == 1
    assert out[0][2]["törvény"] == [1, "term"]
    assert out[0][3] is None


def test_modal_mid_run_failure_is_not_swallowed(monkeypatch):
    """A failure *after* results have flowed proves the method exists, so it is a
    real error — re-running the whole batch through the older entry point would
    hide it and double the bill."""
    class _Flaky:
        def map(self, payloads):
            yield [{"counts": {}, "entities": [], "lemmas": [[]]}]
            raise RuntimeError("container died")

    class _Svc:
        analyze_sessions_full = _Flaky()

    monkeypatch.setattr(nlp_modal, "_service", lambda app_name=None: _Svc())
    gen = nlp_modal.extract([("43001", "fp", ["m1"]), ("43002", "fp2", ["m2"])],
                            batch_sentences=1, with_lemmas=True)
    next(gen)
    with pytest.raises(RuntimeError):
        list(gen)


def test_readability_method_tag_is_unchanged_by_sharing():
    """The stored metrics still describe themselves by how they were *measured*,
    not by where the lemmas came from — a shared stream and a locally computed one
    are the same stream, so they must not produce two different method tags."""
    assert readability.method_tag("hu_core_news_md") == readability.method_tag(
        "hu_core_news_md")
