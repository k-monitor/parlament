"""Per-speech readability + lexical diversity (READ-1..7).

Three groups of properties, all of which are things that would otherwise go wrong
*silently* — no error, no NaN, just a plausible number that means something other
than the label on it:

* **the calibration** — LIX at Björnsson's Swedish threshold saturates in
  Hungarian, so the measurement is pinned to saphes' calibrated threshold and the
  test fails loudly if a package upgrade moves it;
* **the two token streams** — LIX must see surface forms and diversity must see
  lemmas, and neither the stenographer's stage directions nor the speaker
  attribution may be counted as the speaker's words;
* **the degradation** — a build with no lemmatizer must ship readability with the
  diversity half absent, never approximated, and must not overwrite complete
  measurements it can no longer reproduce.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from saphes import lexical_diversity, recommended_threshold

from app import loader, readability


# ---------------------------------------------------------------------------
# calibration
# ---------------------------------------------------------------------------

def test_threshold_is_the_calibrated_hungarian_one():
    """The long-word threshold is 8, from saphes' equipercentile calibration —
    never Björnsson's Swedish 6, at which ~42% of running Hungarian tokens count
    as "long" and the index stops discriminating.

    Read from the package rather than hard-coded, so this test is what catches a
    recalibration arriving with an upgrade: it fails, somebody re-verifies the
    corpus share, and the stored scores are rebuilt with --remeasure-speeches."""
    assert readability.HU_THRESHOLD == 8
    assert readability.LONG_WORD_THRESHOLD == recommended_threshold("hu").threshold


def test_calibration_matches_the_germanic_reference_share():
    """The calibrated threshold exists to preserve what the second LIX term
    *means* — "the longest X% of running words" — across languages. saphes' own
    record says Hungarian at 8 selects ~27.3% against the Swedish reference's
    ~25.7% at 6; the plenary corpus itself lands at 26.2% (see
    app/readability.py). This pins the relationship rather than the exact
    figures."""
    rec = recommended_threshold("hu")
    assert abs(rec.matched_share - rec.reference_share) < 0.05


def test_bjornsson_bands_are_refused_off_their_scale():
    """saphes returns no band at a recalibrated threshold, because the labels were
    fitted to Swedish prose at 6. That refusal is *why* this project bands against
    its own corpus instead — if saphes ever started handing out a label here, the
    UI would be showing a Swedish word for a Hungarian number."""
    from saphes import lix
    text = "A kutyák megálltak a kertben és vártak. Aztán hazamentek."
    assert lix(text).band is not None                       # threshold 6: on scale
    assert lix(text, long_word_threshold=8).band is None    # threshold 8: off it


# ---------------------------------------------------------------------------
# what counts as the speaker's words
# ---------------------------------------------------------------------------

def test_strips_the_leading_speaker_attribution():
    assert readability.strip_speaker_label(
        "TUZSON BENCE igazságügyi miniszter: Tisztelt Elnök úr!") \
        == "Tisztelt Elnök úr!"
    assert readability.strip_speaker_label("ELNÖK: Köszönöm.") == "Köszönöm."


def test_keeps_a_sentence_that_merely_contains_a_colon():
    """The label is recognised structurally, so an ordinary sentence with a colon
    — or an acronym-led one — is never mutilated."""
    for text in ("EU-csúcs volt: erről szólt a vita.",
                 "MSZP frakcióvezetője mondta ezt: nem értünk egyet.",
                 "Mit gondolnak erről: semmit."):
        assert readability.strip_speaker_label(text) == text


def test_drops_stage_directions_including_across_sentences():
    """The stenographer's parentheticals are not the speaker's words. An unclosed
    "(" carries into the next transcript sentence — a stage direction routinely
    spans two — and without the carry its text would be counted as speech and its
    full stops as the speaker's sentences."""
    spoken = readability.spoken_sentences([
        "A javaslat indokolt. (Taps a kormánypárti",
        "oldalon. Közbeszólás: Nem igaz!) Köszönöm a figyelmet.",
    ])
    assert spoken == ["A javaslat indokolt.", "Köszönöm a figyelmet."]


def test_keeps_dictated_references_inline():
    """"(2)" and "(V. 9.)" are part of the sentence the speaker dictated, not
    heckles — dropping them would silently shorten real speech."""
    spoken = readability.spoken_sentences(
        ["A Házszabály 9. § (2) bekezdése és a 17/2026. (V. 9.) OGY-határozat."])
    assert spoken == ["A Házszabály 9. § (2) bekezdése és a 17/2026. (V. 9.) OGY-határozat."]


# ---------------------------------------------------------------------------
# the metrics themselves
# ---------------------------------------------------------------------------

def _long_speech(n=12):
    """A speech comfortably over the length floor, as transcript sentences."""
    return ["A költségvetési törvényjavaslat tárgyalása megkezdődött.",
            "A képviselők többsége támogatta a módosító javaslatokat.",
            "Az ágazati fejlesztések finanszírozása továbbra is kérdéses marad."] * n


def test_sentence_count_comes_from_the_transcript_not_a_splitter():
    """B is the transcript's own segmentation — the more trustworthy count, and
    the one the reader sees — so a speech of N sentences reports exactly N."""
    sentences = _long_speech()
    assert readability.readability(sentences)["sentences"] == len(sentences)


def test_short_speech_is_not_scored():
    """Below the floor there is no row at all: a LIX built from two sentences is
    noise, and the UI must show nothing rather than a number that looks like a
    finding."""
    assert readability.readability(["Köszönöm.", "Egyetértek."]) is None
    assert readability.readability([]) is None


def test_readability_ignores_stage_directions_in_its_counts():
    """The applause line adds words and a sentence to the raw text; neither may
    reach the score."""
    clean = _long_speech()
    noisy = clean + ["(Taps a kormánypárti oldalon. Szórványos derültség.)"]
    assert readability.readability(noisy) == readability.readability(clean)


def test_diversity_needs_lemmas_and_says_so():
    """The asymmetry the whole design exists around: the same four Hungarian words
    score 1.0 as surface forms and 0.25 as lemmas. The first number is not a richer
    vocabulary, it is one word inflected four ways — so the diversity half is fed
    lemmas or nothing at all."""
    surface = ["ház", "házak", "házban", "házakat"]
    assert lexical_diversity(surface, unit="surface").ttr == 1.0
    assert readability.diversity(["ház"] * 4)["ttr"] == 0.25


def test_no_mattr_below_the_window():
    """saphes silently degrades MATTR to the whole-text TTR on a short stream, and
    TTR is not comparable across lengths — so a short speech reports no MATTR
    rather than an incomparable one wearing MATTR's name."""
    short = readability.diversity(["szó%d" % i for i in range(10)])
    assert short["ttr"] == 1.0 and short["mattr"] is None
    long_ = readability.diversity(["szó%d" % (i % 50) for i in
                                   range(readability.MATTR_WINDOW * 2)])
    assert long_["mattr"] is not None


# ---------------------------------------------------------------------------
# corpus-relative bands
# ---------------------------------------------------------------------------

def test_bands_place_a_score_in_its_corpus_quintile():
    cuts = readability.quantiles(list(range(1, 101)))
    assert readability.band_for(5, cuts) == "very-easy"
    assert readability.band_for(50, cuts) == "average"
    assert readability.band_for(99, cuts) == "very-hard"


def test_no_band_without_a_distribution():
    """A DB with no corpus distribution yet gets no label — the chip shows the
    bare number instead of inventing a comparison."""
    assert readability.band_for(42.0, {}) is None
    assert readability.band_for(None, {20: 1.0}) is None


# ---------------------------------------------------------------------------
# the loader pass
# ---------------------------------------------------------------------------

def _session_with_long_speech(session="43002", period=43, sitting=2,
                              date="2026-05-10"):
    """A sitting whose one substantive speech clears the length floor, so the pass
    actually produces a row (the shared fixture's speeches are deliberately tiny)."""
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
def metrics_db(tmp_path, data_dir, monkeypatch):
    """A DB containing one sitting with a measurable speech, built with NO
    lemmatizer reachable — the bare-host case, and the deterministic one (the
    HuSpaCy model is optional and absent in CI)."""
    monkeypatch.setattr(loader.nlp, "available", lambda model=None: False)
    (data_dir / "processed" / "43002-session.json").write_text(
        json.dumps(_session_with_long_speech(), ensure_ascii=False))
    out = tmp_path / "metrics.db"
    loader.build_database(data_dir, out)
    return out


def test_readability_lands_without_any_model(metrics_db):
    """The readability half needs no NLP at all, so it must survive a build with
    no model — that is the difference between an annotation that always works and
    one that quietly depends on a GPU."""
    conn = sqlite3.connect(metrics_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM speech_metrics").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["lix"] and row["words"] and row["sentences"]
    # …and the diversity half is ABSENT, not guessed from surface forms.
    assert row["ttr"] is None and row["mattr"] is None and row["lemma_model"] is None
    conn.close()


def test_procedural_and_short_speeches_get_no_row(metrics_db):
    """A missing row means "not measurable" — never zero. Procedural chairing
    speeches are excluded exactly as they are from the statistics (STAT-1), and
    the fixture's two-sentence speeches fall under the floor."""
    conn = sqlite3.connect(metrics_db)
    scored = {r[0] for r in conn.execute("SELECT speech_id FROM speech_metrics")}
    assert scored == {"43002-1"}
    conn.close()


def test_distribution_is_rebuilt_from_the_scored_speeches(metrics_db):
    conn = sqlite3.connect(metrics_db)
    rows = conn.execute(
        "SELECT q, value, n FROM metric_distribution WHERE metric='lix'").fetchall()
    assert rows and all(n == 1 for (_q, _v, n) in rows)
    conn.close()


def test_cache_is_written_and_reused(tmp_path, metrics_db, monkeypatch):
    """The pass is cached to disk per sitting, keyed by the text *and* the method,
    so a rebuild only re-measures what changed."""
    cache = tmp_path / "speech-metrics-cache.json"
    assert cache.exists()
    entry = json.loads(cache.read_text())["sessions"]["43002"]
    assert entry["model"] == "none" and "43002-1" in entry["metrics"]
    assert "saphes" in entry["method"] and "lix8" in entry["method"]

    # A second pass over an unchanged sitting recomputes nothing.
    monkeypatch.setattr(loader.nlp, "available", lambda model=None: False)
    calls = []
    monkeypatch.setattr(loader.readability, "readability",
                        lambda texts: calls.append(texts) or None)
    conn = loader.connect(metrics_db)
    loader.rebuild_speech_metrics(conn, tmp_path)
    assert calls == []
    assert conn.execute("SELECT COUNT(*) FROM speech_metrics").fetchone()[0] == 1
    conn.close()


def test_method_change_invalidates_the_cache(tmp_path, metrics_db, monkeypatch):
    """Changing the long-word threshold changes what every stored score means, so
    the cached measurements must not be reused under it."""
    monkeypatch.setattr(loader.nlp, "available", lambda model=None: False)
    monkeypatch.setattr(readability, "LONG_WORD_THRESHOLD", 6)
    conn = loader.connect(metrics_db)
    loader.rebuild_speech_metrics(conn, tmp_path)
    lix6 = conn.execute("SELECT lix FROM speech_metrics").fetchone()[0]
    conn.close()
    monkeypatch.setattr(readability, "LONG_WORD_THRESHOLD", 8)
    conn = loader.connect(metrics_db)
    loader.rebuild_speech_metrics(conn, tmp_path)
    lix8 = conn.execute("SELECT lix FROM speech_metrics").fetchone()[0]
    conn.close()
    assert lix6 > lix8   # a lower threshold makes more words "long"


def test_complete_measurements_survive_a_lemmatizer_going_away(
        tmp_path, metrics_db, monkeypatch):
    """A sitting that was measured with lemmas keeps its TTR/MATTR when the model
    later becomes unreachable. Without this guard the next rebuild would null out
    the diversity half of every speech the moment Modal went down."""
    fake_lemmas = [["szó%d" % (i % 40) for i in range(60)] for _ in range(36)]
    monkeypatch.setattr(loader.nlp, "available", lambda model=None: True)
    monkeypatch.setattr(loader.nlp, "lemma_streams",
                        lambda texts, **kw: iter(fake_lemmas[:len(texts)]))
    conn = loader.connect(metrics_db)
    loader.rebuild_speech_metrics(conn, tmp_path)
    before = conn.execute(
        "SELECT mattr, lemma_model FROM speech_metrics").fetchone()
    conn.close()
    assert before[0] is not None and before[1]

    monkeypatch.setattr(loader.nlp, "available", lambda model=None: False)
    conn = loader.connect(metrics_db)
    loader.rebuild_speech_metrics(conn, tmp_path)
    after = conn.execute("SELECT mattr, lemma_model FROM speech_metrics").fetchone()
    conn.close()
    assert tuple(after) == tuple(before)


def test_only_measurable_speeches_are_lemmatized(tmp_path, metrics_db, monkeypatch):
    """The neural half is metered work (on Modal it is literally billed), so a
    speech that will not be scored must never reach the model."""
    seen: list[list[str]] = []

    def _spy(texts, **kw):
        seen.append(list(texts))
        return iter([[] for _ in texts])

    monkeypatch.setattr(loader.nlp, "available", lambda model=None: True)
    monkeypatch.setattr(loader.nlp, "lemma_streams", _spy)
    conn = loader.connect(metrics_db)
    loader.rebuild_speech_metrics(conn, tmp_path)
    conn.close()
    # Only 43002's long speech is measurable; 43001's two short ones are not, so
    # its sitting sends nothing at all.
    assert seen and all(len(t) == 36 for t in seen)


def test_modal_is_scoped_to_the_newest_cycle(tmp_path, data_dir, monkeypatch):
    """The metered half obeys the same budget guard as every other Modal offload
    (`PARLAMONITOR_MODAL_CYCLES`, default `latest`): only the newest electoral
    cycle is ever dispatched.

    This is the guard that stops a one-off backfill from draining the credit the
    live cycle depends on — thousands of frozen archive sittings all miss the cache
    at once, and measuring them is exactly the run nobody is watching. An
    out-of-scope sitting falls back to a local model, or to readability only."""
    (data_dir / "processed" / "43002-session.json").write_text(
        json.dumps(_session_with_long_speech(), ensure_ascii=False))
    (data_dir / "processed" / "42001-session.json").write_text(
        json.dumps(_session_with_long_speech(session="42001", period=42, sitting=1,
                                             date="2022-05-10"), ensure_ascii=False))

    dispatched: list[str] = []

    def _fake_modal(misses, **kw):
        for sid, fp, texts in misses:
            dispatched.append(sid)
            yield sid, fp, [[] for _ in texts]

    monkeypatch.setattr(loader.settings, "wordcloud_backend", "modal")
    monkeypatch.setattr(loader.nlp_modal, "available", lambda: True)
    monkeypatch.setattr(loader.nlp_modal, "extract_lemmas", _fake_modal)
    # The word-cloud pass shares this backend, so stub its Modal entry points too —
    # otherwise the build reaches for a real Modal service (an unauthenticated
    # lookup that hangs for over a minute before the enrichment guard swallows it).
    monkeypatch.setattr(loader.nlp_modal, "extract", lambda misses, **kw: iter(()))
    monkeypatch.setattr(loader.nlp_modal, "extract_spans", lambda misses, **kw: iter(()))
    # No local model either, so an out-of-scope sitting has nowhere else to go —
    # it must degrade to readability rather than quietly reach for Modal.
    monkeypatch.setattr(loader.nlp, "available", lambda model=None: False)

    loader.build_database(data_dir, tmp_path / "scoped.db")

    assert dispatched == ["43002"], "only the newest cycle may spend Modal credit"
    conn = sqlite3.connect(tmp_path / "scoped.db")
    scored = dict(conn.execute(
        "SELECT session_id, lemma_model IS NOT NULL FROM speech_metrics"))
    conn.close()
    # Both sittings are measured; only the in-scope one got the lemma half.
    assert scored == {"43002": 1, "42001": 0}


# ---------------------------------------------------------------------------
# the API surface
# ---------------------------------------------------------------------------

@pytest.fixture
def metrics_client(metrics_db, monkeypatch):
    from fastapi.testclient import TestClient

    from app import db as db_module
    from app.config import settings
    monkeypatch.setattr(settings, "db_path", str(metrics_db))
    monkeypatch.setattr(db_module.settings, "db_path", str(metrics_db))
    from app.main import app
    return TestClient(app)


def test_session_speeches_carry_their_metrics(metrics_client):
    body = metrics_client.get("/api/v1/proceedings/sessions/43002").json()
    speeches = [s for a in body["agenda"] for s in a["speeches"]]
    assert len(speeches) == 1
    m = speeches[0]["metrics"]
    assert m["lix"] > 0 and m["words"] > 0
    assert m["lix_band"] in {"very-easy", "easy", "average", "hard", "very-hard"}
    # No lemmatizer in this build: the diversity half is absent, not zero.
    assert m["mattr"] is None and m["mattr_band"] is None


def test_unmeasurable_speech_reports_none(metrics_client):
    body = metrics_client.get("/api/v1/proceedings/sessions/43001").json()
    speeches = [s for a in body["agenda"] for s in a["speeches"]]
    assert speeches and all(s["metrics"] is None for s in speeches)


def test_speech_detail_carries_metrics(metrics_client):
    body = metrics_client.get("/api/v1/proceedings/speeches/43002-1").json()
    assert body["speech"]["metrics"]["lix"] > 0


def test_meta_publishes_the_methodology(metrics_client):
    meta = metrics_client.get("/api/v1/meta").json()
    assert meta["features"]["speech_metrics"] is True
    sm = meta["speech_metrics"]
    assert sm["package"] == "saphes"
    assert sm["long_word_threshold"] == readability.LONG_WORD_THRESHOLD
    assert sm["mattr_window"] == readability.MATTR_WINDOW
    assert sm["scored"] == 1 and sm["with_diversity"] == 0


def test_feature_flag_is_off_on_an_unannotated_db(client):
    """The shared fixture DB has no speech long enough to score, so the SPA is told
    to hide the annotations rather than render a page of blanks."""
    meta = client.get("/api/v1/meta").json()
    assert meta["features"]["speech_metrics"] is False
