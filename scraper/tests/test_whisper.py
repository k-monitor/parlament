"""Tests for Whisper forced-alignment sentence timing (TIM-1).

All offline: the alignment and cache are pure Python, and the Modal client is
stubbed, so no audio, no GPU and no network are touched (OPS-3). Run with
``pytest`` from the ``scraper/`` directory.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parlamonitor import whisper_align, whisper_modal
from parlamonitor.config import Paths
from parlamonitor.proceedings.transform import transform_day
from parlamonitor.timing import apply_timing


# --- alignment core --------------------------------------------------------

def _speech(sentences):
    return {"speechIndex": 1,
            "media": {"videoStart": 100.0, "videoEnd": 130.0},
            "textContents": [{"textBody": [{"type": "speech",
                                             "sentences": sentences}]}],
            "debug": {}}


def test_align_speech_maps_matched_word_times():
    """Each sentence takes the times of the whisper words it matches; boundaries
    fall on real spoken-word timestamps, monotonic and inside the window."""
    sentences = [{"text": "Tisztelt Ház!"},
                 {"text": "Fontos kérdésről beszélek most."}]
    # Day-absolute words spoken in the speech's [100, 130] window.
    words = [
        [101.0, 101.5, "Tisztelt"], [101.5, 102.0, "Ház"],
        [110.0, 110.5, "Fontos"], [110.5, 111.2, "kérdésről"],
        [111.2, 111.8, "beszélek"], [111.8, 112.3, "most"],
    ]
    spans, coverage = whisper_align.align_speech(sentences, words, (100.0, 130.0))
    assert coverage == 1.0
    assert spans[0] == (101.0, 102.0)       # "Tisztelt Ház"
    assert spans[1] == (110.0, 112.3)       # rest
    flat = [spans[0][0], spans[0][1], spans[1][0], spans[1][1]]
    assert flat == sorted(flat)             # monotonic
    assert 100.0 <= spans[0][0] and spans[1][1] <= 130.0


def test_align_speech_interpolates_unmatched_sentence():
    """A sentence the ASR never produced (e.g. an inaudible aside) is placed
    between its matched neighbours, not dropped."""
    sentences = [{"text": "Első mondat."},
                 {"text": "Elharapott közbeszólás."},   # no words match this
                 {"text": "Harmadik mondat vége."}]
    words = [
        [100.0, 100.5, "Első"], [100.5, 101.0, "mondat"],
        [120.0, 120.5, "Harmadik"], [120.5, 121.0, "mondat"], [121.0, 121.5, "vége"],
    ]
    spans, _ = whisper_align.align_speech(sentences, words, (100.0, 130.0))
    # Middle sentence sits strictly between the two anchored ones.
    assert spans[0][1] <= spans[1][0] <= spans[1][1] <= spans[2][0]
    assert spans[1][0] >= 101.0 and spans[1][1] <= 120.0


def test_align_speech_trusts_high_hyp_coverage_despite_stage_directions():
    """A ceremonial speech: the window holds far more transcript (many "(Taps.)"
    stage directions) than spoken words, so ref-coverage is low — but the words that
    WERE spoken align in solid runs, so the alignment is trusted and the unspoken
    lines are interpolated, not thrown away to a positional estimate."""
    sentences = [
        {"text": "Az Országgyűlés nevében eredményes munkát kívánok."},
        {"text": "(Taps.)"},
        {"text": "(Szórványos taps az ellenzéki pártok soraiban.)"},
        {"text": "(A képviselők állva tapsolnak, majd helyet foglalnak.)"},
        {"text": "Köszönöm, az ülést bezárom."},
    ]
    words = [
        [10.0, 10.5, "Az"], [10.5, 11.0, "Országgyűlés"], [11.0, 11.5, "nevében"],
        [11.5, 12.2, "eredményes"], [12.2, 12.8, "munkát"], [12.8, 13.4, "kívánok"],
        [50.0, 50.4, "Köszönöm"], [50.4, 50.9, "az"], [50.9, 51.3, "ülést"],
        [51.3, 51.9, "bezárom"],
    ]
    result = whisper_align.align_speech(sentences, words, (0.0, 60.0))
    assert result is not None                    # trusted despite low ref-coverage
    spans, _ = result
    assert spans[0] == (10.0, 13.4)              # first spoken sentence, real times
    assert spans[-1] == (50.0, 51.9)             # last spoken sentence, real times
    # The three stage directions are interpolated strictly between the two anchors.
    assert spans[0][1] <= spans[1][0] <= spans[3][1] <= spans[4][0]


def test_align_speech_low_coverage_returns_none():
    """When almost nothing matches (bad audio / heavy paraphrase) the caller must
    fall back to the positional estimate rather than trust a bogus alignment."""
    sentences = [{"text": "Ez a hivatalos jegyzőkönyv szövege teljesen."}]
    words = [[100.0, 100.5, "alma"], [100.5, 101.0, "körte"], [101.0, 101.5, "szilva"]]
    assert whisper_align.align_speech(sentences, words, (100.0, 130.0)) is None


def test_words_in_window_filters_by_midpoint():
    words = [[10.0, 12.0, "a"], [98.0, 101.0, "b"], [110.0, 111.0, "c"],
             [200.0, 201.0, "d"]]
    got = whisper_align.words_in_window(words, 100.0, 130.0)
    # "b" straddles the start but its midpoint (99.5) is before it, so it's excluded;
    # only "c" is centred inside [100, 130].
    assert [w[2] for w in got] == ["c"]


# --- cache -----------------------------------------------------------------

def test_words_cache_roundtrip_and_fingerprint(tmp_path):
    path = tmp_path / "whisper-43001.json"
    m3u8, tag = "https://x/smil:a.b.1.2.smil/playlist.m3u8", "whisper:large-v3-turbo:v1"
    words = [[1.0, 1.5, "szia"]]
    whisper_align.save_words_cache(path, m3u8=m3u8, model_tag=tag, words=words)

    assert whisper_align.load_words_cache(path, m3u8=m3u8, model_tag=tag) == words
    # A changed recording URL (re-cut) or model invalidates the cache.
    assert whisper_align.load_words_cache(path, m3u8="https://x/other.m3u8",
                                          model_tag=tag) is None
    assert whisper_align.load_words_cache(path, m3u8=m3u8,
                                          model_tag="whisper:other:v1") is None
    # Missing file → miss, not an error.
    assert whisper_align.load_words_cache(tmp_path / "nope.json") is None


# --- timing integration ----------------------------------------------------

def test_apply_timing_whisper_alignment_marks_precise():
    sp = _speech([{"text": "Tisztelt Ház!"},
                  {"text": "Fontos kérdésről beszélek most."}])
    words = [[101.0, 101.5, "Tisztelt"], [101.5, 102.0, "Ház"],
             [110.0, 110.5, "Fontos"], [111.8, 112.3, "most"]]
    apply_timing([sp], words=words, force=True)
    sents = sp["textContents"][0]["textBody"][0]["sentences"]
    assert sents[0]["timeStart"] == 101.0
    assert sp["debug"]["align-method"] == "whisper-forced-alignment"
    assert sp["debug"]["confidence"] == 0.98


def test_apply_timing_falls_back_when_no_words_in_window():
    """Words exist for the day but none in this speech's window → per-speech
    positional estimate, not a bogus whisper alignment."""
    sp = _speech([{"text": "Egyetlen mondat."}])
    words = [[500.0, 500.5, "másik"], [500.5, 501.0, "beszéd"]]  # outside [100,130]
    apply_timing([sp], words=words, force=True)
    sents = sp["textContents"][0]["textBody"][0]["sentences"]
    assert sents[0]["timeStart"] == 100.0        # anchored to the offset window
    assert sp["debug"]["align-method"] == "felicitas-speech-offset"


def test_apply_timing_without_words_is_unchanged():
    """No words → exactly the prior positional behaviour (backward compatible)."""
    sp = _speech([{"text": "Egyetlen mondat."}])
    apply_timing([sp], force=True)
    assert sp["debug"]["align-method"] == "felicitas-speech-offset"


# --- transform integration -------------------------------------------------

def _raw_with_offsets():
    return {
        "cycle": 43, "sitting": 7, "session": "43007", "date": "2026-06-09",
        "video": {"m3u8": "https://x/smil:20260609.090000.0.1200000.smil/playlist.m3u8",
                  "day_off1": 0.0},
        "source": "felicitas-json",
        "speeches": [
            {"sorszam": 1, "speech_uuid": "u1", "person_id": "g053",
             "speaker": "Gulyás Gergely (Fidesz)", "type": "felszólalás",
             "aktus": "Napirend előtti felszólalások", "kezdete": "09:00:00",
             "duration": 30,
             "text_html": "<div><p><span>Tisztelt Ház! Fontos kérdésről beszélek.</span></p></div>",
             "video_off_start": 100.0, "video_off_end": 130.0},
        ],
    }


def test_transform_day_uses_whisper_timing_when_words_given():
    words = [[101.0, 101.5, "Tisztelt"], [101.5, 102.0, "Ház"],
             [110.0, 110.5, "Fontos"], [110.5, 111.0, "kérdésről"],
             [111.0, 111.5, "beszélek"]]
    rec = transform_day(_raw_with_offsets(), words=words)
    assert rec["meta"]["timingMethod"] == "whisper-forced-alignment"
    sp = rec["data"][0]
    assert sp["debug"]["align-method"] == "whisper-forced-alignment"
    sents = sp["textContents"][0]["textBody"][0]["sentences"]
    assert sents[0]["timeStart"] == 101.0        # first spoken word


def test_transform_day_without_words_keeps_positional_timing():
    rec = transform_day(_raw_with_offsets())
    assert rec["meta"]["timingMethod"] == "felicitas-speech-offset"
    assert rec["data"][0]["debug"]["align-method"] == "felicitas-speech-offset"


# --- backend resolution / orchestration ------------------------------------

def test_ensure_words_character_backend_is_noop(tmp_path):
    paths = Paths(tmp_path)
    paths.ensure()
    out = whisper_align.ensure_words(
        paths, [("43001", "https://x/a.m3u8", None)],
        backend="character", model="large-v3-turbo", language="hu")
    assert out == {}
    assert not paths.whisper_cache("43001").exists()


def test_ensure_words_returns_cache_without_running_backend(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    paths.ensure()
    m3u8 = "https://x/a.m3u8"
    tag = whisper_align.method_tag("large-v3-turbo")
    whisper_align.save_words_cache(paths.whisper_cache("43001"), m3u8=m3u8,
                                   model_tag=tag, words=[[1.0, 1.5, "szia"]])

    def _boom(*a, **k):
        raise AssertionError("backend must not run on a cache hit")
    monkeypatch.setattr(whisper_align, "_run_backend", _boom)

    out = whisper_align.ensure_words(
        paths, [("43001", m3u8, None)],
        backend="whisper-local", model="large-v3-turbo", language="hu")
    assert out == {"43001": [[1.0, 1.5, "szia"]]}


def test_ensure_words_runs_backend_on_miss_and_caches(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    paths.ensure()
    m3u8 = "https://x/new.m3u8"

    def fake_backend(resolved, misses, *, model, language):
        assert resolved == "whisper-local"
        for session, _m3u8, _playseq in misses:
            yield session, [[2.0, 2.5, "helló"]]
    monkeypatch.setattr(whisper_align, "_run_backend", fake_backend)

    out = whisper_align.ensure_words(
        paths, [("43002", m3u8, None)],
        backend="whisper-local", model="large-v3-turbo", language="hu")
    assert out == {"43002": [[2.0, 2.5, "helló"]]}
    # Result cached, keyed to this recording + model, so a re-run is free.
    tag = whisper_align.method_tag("large-v3-turbo")
    assert whisper_align.load_words_cache(paths.whisper_cache("43002"),
                                          m3u8=m3u8, model_tag=tag) == [[2.0, 2.5, "helló"]]


# --- Modal client (stubbed) ------------------------------------------------

def test_modal_transcribe_maps_results_in_order(monkeypatch):
    class _Method:
        def map(self, jobs, **_kw):
            for i, _job in enumerate(jobs):
                yield [[float(i), float(i) + 0.5, f"w{i}"]]

    class _Svc:
        transcribe = _Method()

    monkeypatch.setattr(whisper_modal, "_service", lambda: _Svc())
    misses = [("43001", "m1", None), ("43002", "m2", "pseq")]
    out = list(whisper_modal.transcribe(misses, model="large-v3-turbo", language="hu"))
    assert [sid for sid, _ in out] == ["43001", "43002"]
    assert out[0][1] == [[0.0, 0.5, "w0"]]
    assert out[1][1] == [[1.0, 1.5, "w1"]]


def test_modal_transcribe_skips_failed_days(monkeypatch):
    """A per-day exception (return_exceptions) is skipped, not raised, so a
    backfill batch survives one bad recording."""
    class _Method:
        def map(self, jobs, **_kw):
            yield RuntimeError("decode failed")          # 43001 errors
            yield [[3.0, 3.5, "ok"]]                      # 43002 succeeds

    class _Svc:
        transcribe = _Method()

    monkeypatch.setattr(whisper_modal, "_service", lambda: _Svc())
    misses = [("43001", "m1", None), ("43002", "m2", None)]
    out = list(whisper_modal.transcribe(misses, model="large-v3-turbo", language="hu"))
    assert out == [("43002", [[3.0, 3.5, "ok"]])]
