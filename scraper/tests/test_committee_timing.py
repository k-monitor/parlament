"""Offline tests for the committee sentence-timing stage (BIZ-30).

No audio, no GPU, no network: the Whisper words are written straight into the
per-video cache, which is exactly what a real pass would find there after the
one costly step. What is pinned is what the stage decides by itself — where a
sentence starts in the text, which sentences the alignment dares to place, which
recording goes with which sitting, and which second of which video a sentence
lands on when a sitting ran over two streams.
"""

from __future__ import annotations

import json

from parlamonitor import sync, whisper_align
from parlamonitor.committees import timing
from parlamonitor.config import Paths


def _words(text: str, start: float, step: float = 0.4) -> list[list]:
    """``text`` as Whisper would return it: one word every ``step`` seconds."""
    out = []
    t = start
    for w in text.split():
        out.append([round(t, 3), round(t + step * 0.8, 3), w])
        t += step
    return out


# --- sentences ------------------------------------------------------------------

def test_sentence_spans_are_exact_offsets_into_the_speech():
    text = ("Köszönöm szépen. A bizottság ülését megnyitom.\n\n"
            "Ismertetem a napirendet. Kérdezem, ki kíván hozzászólni?")
    spans = timing.sentence_spans(text)
    assert [s["text"] for s in spans] == [
        "Köszönöm szépen.", "A bizottság ülését megnyitom.",
        "Ismertetem a napirendet.", "Kérdezem, ki kíván hozzászólni?"]
    assert [s["para"] for s in spans] == [0, 0, 1, 1]
    for s in spans:
        a, b = s["chars"]
        assert text[a:b] == s["text"]


def test_a_stage_direction_keeps_its_opening_bracket():
    text = "Kérem, szavazzanak! (Szavazás.) Köszönöm."
    spans = timing.sentence_spans(text)
    assert [s["text"] for s in spans] == [
        "Kérem, szavazzanak!", "(Szavazás.)", "Köszönöm."]
    assert all(text[s["chars"][0]:s["chars"][1]] == s["text"] for s in spans)


def test_sentence_spans_skip_empty_paragraphs_as_the_viewer_does():
    text = "Első.\n\n   \n\nMásodik."
    assert [(s["para"], s["text"]) for s in timing.sentence_spans(text)] == [
        (0, "Első."), (1, "Második.")]


# --- the whole-sitting alignment --------------------------------------------------

SPEECHES = [
    "Jó napot kívánok mindenkinek. A bizottság ülését megnyitom.",
    "Köszönöm a szót elnök úr. A kormány támogatja a módosító javaslatot "
    "mert az a szakképzési centrumok működését segíti.",
    "Köszönjük. Kérdezem ki kíván hozzászólni. (Nincs jelentkező.)",
]


def _split(texts):
    return [[{"text": s["text"]} for s in timing.sentence_spans(t)] for t in texts]


def test_align_sitting_places_every_spoken_sentence_in_order():
    words = (_words(SPEECHES[0], 100.0)
             + _words(SPEECHES[1], 110.0)
             + _words("Köszönjük. Kérdezem ki kíván hozzászólni.", 130.0))
    spans, coverage = whisper_align.align_sitting(_split(SPEECHES), words, 200.0)
    assert coverage > 0.9
    flat = [s for sp in spans for s in sp]
    placed = [s for s in flat if s is not None]
    # Non-decreasing through the whole sitting, speech after speech.
    assert placed == sorted(placed)
    assert spans[0][0][0] >= 99.9 and spans[1][0][0] >= 109.9
    assert spans[2][1][0] >= 130.0
    # The stage direction is not spoken, so it is never placed.
    assert spans[2][-1] is None


def test_align_sitting_leaves_text_the_recording_never_caught_unplaced():
    """A long passage with no match between two anchors that sit a second apart
    was not said there: it stays untimed instead of being squeezed in."""
    passage = " ".join(["Ez a bekezdés sosem hangzott el a felvételen."] * 12)
    texts = [SPEECHES[0], passage, SPEECHES[2]]
    words = (_words(SPEECHES[0], 10.0)
             + _words("Köszönjük. Kérdezem ki kíván hozzászólni.", 20.0))
    spans, _ = whisper_align.align_sitting(_split(texts), words, 60.0)
    assert all(s is None for s in spans[1])
    assert spans[2][0] is not None


def test_align_sitting_spreads_a_run_the_gap_can_hold():
    """Speech Whisper garbled, in a gap about as long as it takes to say it,
    is interpolated as the plenary does it."""
    texts = [SPEECHES[0], "Ezt a mondatot a gép félreértette teljesen.", SPEECHES[2]]
    words = (_words(SPEECHES[0], 10.0)
             + [[16.0, 16.4, "xyz"], [16.5, 16.9, "qqq"]]
             + _words("Köszönjük. Kérdezem ki kíván hozzászólni.", 19.0))
    spans, _ = whisper_align.align_sitting(_split(texts), words, 60.0)
    s = spans[1][0]
    assert s is not None and spans[0][-1][1] <= s[0] <= s[1] <= spans[2][0][0]


def test_align_sitting_places_an_unmatched_opening_just_before_the_first_anchor():
    """Minutes of pre-roll before anything matches: the opening line goes just
    before the first match, not at 0:00 of the stream."""
    texts = ["Tisztelt hölgyeim és uraim.", SPEECHES[1]]
    words = _words(SPEECHES[1], 600.0)
    spans, _ = whisper_align.align_sitting(_split(texts), words, 900.0)
    start, end = spans[0][0]
    assert 590.0 <= start < end <= spans[1][0][0]


def test_align_sitting_refuses_an_unrelated_recording():
    words = _words("teljesen más szöveg ami semmiben sem egyezik " * 20, 0.0)
    assert whisper_align.align_sitting(_split(SPEECHES), words, 300.0) is None


def test_align_sitting_does_not_let_one_open_paren_silence_the_sitting():
    texts = ["Ez egy nyitott zárójel ( amit senki nem zárt be.", SPEECHES[1]]
    words = _words("Ez egy nyitott zárójel", 5.0) + _words(SPEECHES[1], 20.0)
    spans, _ = whisper_align.align_sitting(_split(texts), words, 80.0)
    assert all(s is not None for s in spans[1])


# --- pairing ----------------------------------------------------------------------

def _video(vid, date, label="A Költségvetési Bizottság", continued=False,
           published="2026-06-10T09:00:00+00:00"):
    return {"videoId": vid, "date": date, "kind": "committee",
            "committeeLabel": label, "continued": continued,
            "publishedAt": published, "url": f"https://youtu.be/{vid}"}


def test_a_continuation_joins_the_video_it_continues():
    groups = timing.recording_groups([
        _video("b", "2026-06-10", continued=True,
               published="2026-06-10T11:00:00+00:00"),
        _video("a", "2026-06-10", published="2026-06-10T09:00:00+00:00"),
        _video("c", "2026-06-10", label="A Mentelmi Bizottság"),
        {"videoId": "p", "date": "2026-06-10", "kind": "plenary"},
    ])
    assert [[v["videoId"] for v in g] for g in groups] == [["a", "b"], ["c"]]


def test_candidates_prefer_the_exact_day_and_fall_back_a_day_either_side():
    recs = {("koltsegvetesi bizottsag", "2026-06-10"): [{"meetingId": "m1"}],
            ("koltsegvetesi bizottsag", "2026-06-12"): [{"meetingId": "m2"}]}
    assert timing.candidates([_video("a", "2026-06-10")], recs) == [{"meetingId": "m1"}]
    assert timing.candidates([_video("a", "2026-06-11")], recs) == [
        {"meetingId": "m1"}, {"meetingId": "m2"}]
    assert timing.candidates([_video("a", "2026-06-20")], recs) == []


def test_fold_agrees_with_the_loader_on_article_case_and_accents():
    assert timing.fold_committee("A Költségvetési  Bizottság") == \
        timing.fold_committee("költségvetési bizottság") == "koltsegvetesi bizottsag"


# --- two videos, one sitting --------------------------------------------------------

def test_a_sitting_over_two_streams_is_timed_in_each_videos_own_seconds():
    rec = {"speeches": [{"ord": 0, "text": SPEECHES[0]},
                        {"ord": 1, "text": SPEECHES[1]}]}
    first = {"words": _words(SPEECHES[0], 30.0), "durationS": 60.0}
    second = {"words": _words(SPEECHES[1], 5.0), "durationS": 120.0}
    out = timing.align_record(rec, [("v1", first), ("v2", second)])
    s0 = out["speeches"][0]["sentences"][0]
    s1 = out["speeches"][1]["sentences"][0]
    assert (s0["videoId"], s1["videoId"]) == ("v1", "v2")
    assert 29.0 <= s0["timeStart"] <= 31.0
    assert 4.0 <= s1["timeStart"] <= 6.0          # part 2's clock, not the sitting's
    assert out["timed"] == out["sentences"]


# --- the stage, end to end ---------------------------------------------------------

def _data_dir(tmp_path, *, speeches=None, videos=None):
    paths = Paths(tmp_path / "data")
    paths.ensure()
    (paths.processed / "committees-43.json").write_text(json.dumps(
        {"meta": {"cycle": 43, "dateFrom": "2026-05-09", "dateTo": "2030-05-01"},
         "data": []}))
    speeches = speeches or [{"ord": i, "text": t} for i, t in enumerate(SPEECHES)]
    (paths.processed / "committee-minutes-43.json").write_text(json.dumps(
        {"meta": {"cycle": 43}, "data": [{
            "meetingId": "m1", "committeeId": "c1",
            "committeeName": "Költségvetési Bizottság",
            "datetime": "2026-06-10T07:00:00Z", "cover": {"date": "2026-06-10"},
            "speeches": speeches}]}, ensure_ascii=False))
    paths.committee_videos_file().write_text(json.dumps(
        {"meta": {}, "data": videos or [_video("v1", "2026-06-10")]},
        ensure_ascii=False))
    return paths


def _cache(paths, vid, words, duration):
    timing.save_words(paths, vid, whisper_align.method_tag("large-v3-turbo"),
                      {"words": words, "durationS": duration})


def _all_words():
    return (_words(SPEECHES[0], 100.0) + _words(SPEECHES[1], 110.0)
            + _words("Köszönjük. Kérdezem ki kíván hozzászólni.", 130.0))


def test_build_timing_times_a_sitting_from_cached_words(tmp_path):
    paths = _data_dir(tmp_path)
    _cache(paths, "v1", _all_words(), 300.0)
    reg = timing.build_timing(paths, backend="character", model="large-v3-turbo")
    assert reg["meta"]["counts"]["aligned"] == 1
    [row] = reg["data"]
    assert row["meetingId"] == "m1" and row["cycle"] == 43
    assert row["videos"] == [{"videoId": "v1", "durationS": 300.0}]
    sent = row["speeches"][1]["sentences"][0]
    text = SPEECHES[1]
    assert text[sent["chars"][0]:sent["chars"][1]] == sent["text"]
    assert sent["videoId"] == "v1" and sent["timeStart"] >= 109.9

    # Same inputs again: carried over, not re-aligned.
    again = timing.build_timing(paths, backend="character",
                                model="large-v3-turbo", previous=reg)
    assert again["meta"]["counts"]["reused"] == 1
    assert again["data"][0]["alignedAt"] == row["alignedAt"]


def test_build_timing_realigns_when_the_minutes_text_changes(tmp_path):
    paths = _data_dir(tmp_path)
    _cache(paths, "v1", _all_words(), 300.0)
    first = timing.build_timing(paths, backend="character", model="large-v3-turbo")
    speeches = [{"ord": i, "text": t} for i, t in enumerate(SPEECHES)]
    speeches[0]["text"] += " Üdvözlöm a vendégeket."
    paths = _data_dir(tmp_path, speeches=speeches)
    second = timing.build_timing(paths, backend="character",
                                 model="large-v3-turbo", previous=first)
    assert second["meta"]["counts"]["aligned"] == 1
    assert second["data"][0]["fingerprint"] != first["data"][0]["fingerprint"]


def test_a_recording_without_words_keeps_its_previous_timing(tmp_path):
    """A failed download must not erase a sitting that was timed before."""
    paths = _data_dir(tmp_path)
    previous = {"data": [{"meetingId": "m1", "fingerprint": "old",
                          "heldOn": "2026-06-10", "sentences": 3, "timed": 3}]}
    reg = timing.build_timing(paths, backend="character",
                              model="large-v3-turbo", previous=previous)
    assert [r["meetingId"] for r in reg["data"]] == ["m1"]
    assert reg["data"][0]["fingerprint"] == "old"


def test_an_unpaired_recording_times_nothing(tmp_path):
    paths = _data_dir(tmp_path, videos=[_video("v9", "2026-07-01")])
    reg = timing.build_timing(paths, backend="character", model="large-v3-turbo")
    assert reg["data"] == [] and reg["meta"]["counts"]["sittings"] == 0


def test_misses_are_downloaded_transcribed_cached_and_the_audio_deleted(
        tmp_path, monkeypatch):
    paths = _data_dir(tmp_path)
    audio = paths.committee_audio_dir()

    def fake_download(video_id, dest, **_kw):
        dest.mkdir(parents=True, exist_ok=True)
        f = dest / f"{video_id}.webm"
        f.write_bytes(b"opus")
        return f

    def fake_transcribe(_resolved, batch, **_kw):
        for video_id, path in batch:
            assert path.exists()
            yield video_id, {"words": _all_words(), "durationS": 300.0}

    monkeypatch.setattr(timing, "download_audio", fake_download)
    monkeypatch.setattr(timing, "_transcribe", fake_transcribe)
    monkeypatch.setattr(whisper_align, "resolve_backend", lambda _b: "whisper-local")
    reg = timing.build_timing(paths, backend="whisper-local", model="large-v3-turbo")
    assert reg["meta"]["counts"]["aligned"] == 1 and reg["meta"]["failed"] == []
    assert timing.load_words(paths, "v1", whisper_align.method_tag("large-v3-turbo"))
    assert not list(audio.glob("*"))


def test_a_failed_download_is_reported_so_sync_tries_again(tmp_path, monkeypatch):
    paths = _data_dir(tmp_path)
    monkeypatch.setattr(timing, "download_audio", lambda *_a, **_k: None)
    monkeypatch.setattr(whisper_align, "resolve_backend", lambda _b: "whisper-local")
    state: dict = {}
    out = sync._sync_committee_timing(paths, state, force=False,
                                      backend="whisper-local")
    assert out["failed"] == 1
    assert "committeeTiming" not in state          # not marked done: retried

    _cache(paths, "v1", _all_words(), 300.0)
    out = sync._sync_committee_timing(paths, state, force=False,
                                      backend="whisper-local")
    assert out["aligned"] == 1 and state["committeeTiming"]["fp"]
    # Nothing moved since: the stage does not even open the minutes.
    assert sync._sync_committee_timing(paths, state, force=False,
                                       backend="whisper-local") is False


def test_modal_scope_keeps_older_cycles_off_the_gpu(tmp_path, monkeypatch):
    paths = _data_dir(tmp_path)
    (paths.processed / "committee-minutes-44.json").write_text('{"data": []}')
    monkeypatch.setattr(whisper_align, "resolve_backend", lambda _b: "whisper-modal")
    monkeypatch.delenv("PARLAMONITOR_MODAL_CYCLES", raising=False)
    called = []
    monkeypatch.setattr(timing, "download_audio",
                        lambda *a, **k: called.append(a) or None)
    got, failed = timing.ensure_words(paths, [("v1", 43)], backend="whisper-modal",
                                      model="large-v3-turbo", language="hu")
    assert (got, failed, called) == ({}, [], [])


def test_a_backend_that_fails_outright_fails_the_batch_not_the_stage(
        tmp_path, monkeypatch):
    """A Whisper app deployed before `transcribe_audio` existed raises on the
    first call. The recordings count as failed (so sync tries again) and the
    stage still writes what it has."""
    paths = _data_dir(tmp_path)

    def fake_download(video_id, dest, **_kw):
        dest.mkdir(parents=True, exist_ok=True)
        f = dest / f"{video_id}.webm"
        f.write_bytes(b"opus")
        return f

    def broken(_resolved, _batch, **_kw):
        raise AttributeError("transcribe_audio")
        yield  # pragma: no cover

    monkeypatch.setattr(timing, "download_audio", fake_download)
    monkeypatch.setattr(timing, "_transcribe", broken)
    monkeypatch.setattr(whisper_align, "resolve_backend", lambda _b: "whisper-modal")
    monkeypatch.setenv("PARLAMONITOR_MODAL_CYCLES", "all")
    reg = timing.build_timing(paths, backend="whisper-modal", model="large-v3-turbo")
    assert reg["meta"]["failed"] == ["v1"] and reg["data"] == []
    assert not list(paths.committee_audio_dir().glob("*"))
