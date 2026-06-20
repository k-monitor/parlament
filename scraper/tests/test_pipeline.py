"""Unit tests for the pure-logic pieces of the scraper.

These cover the parts the requirements call out for automated testing (OPS-3):
the JSON→record transform, sentence↔time mapping, and the helper logic — all
offline, no network. Run with ``pytest`` from the ``scraper/`` directory.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parlamonitor import agenda, magyarkozlony
from parlamonitor.http_client import HttpError
from parlamonitor.names import build_person, split_name, split_speaker
from parlamonitor.segment import html_to_text, split_sentences
from parlamonitor.timing import apply_timing, smil_span_seconds
from parlamonitor.proceedings.transform import transform_day
from parlamonitor.proceedings.scrape import sitting_number


# --- names -----------------------------------------------------------------

def test_split_speaker_faction():
    assert split_speaker("Kövér László (Fidesz)") == ("Kövér László", "Fidesz", None)


def test_split_speaker_president_office():
    label, faction, role = split_speaker("DR. KÖVÉR LÁSZLÓ, az Országgyűlés elnöke")
    assert label == "Kövér László"
    assert role == "president"


def test_split_speaker_minister():
    label, faction, role = split_speaker("Varga Mihály pénzügyminiszter (Fidesz)")
    assert label == "Varga Mihály"
    assert faction == "Fidesz"
    assert role == "miniszter"


def test_split_name_surname_first():
    assert split_name("Kövér László") == ("László", "Kövér")


def test_build_person_links_id():
    p = build_person("Ágh Péter (Fidesz)", person_id="a011")
    assert p["personID"] == "a011"
    assert p["faction"]["label"] == "Fidesz"
    assert p["type"] == "memberOfParliament"


# --- magyarkozlony (promulgated-bill gazette link) -------------------------

# The schema.org meta tag on a real magyarkozlony.hu issue-listing page.
_MK_HTML = (
    '<div class="row" itemscope itemtype="http://schema.org/Newspaper">'
    '<meta itemprop="url" '
    'content="https://magyarkozlony.hu/dokumentumok/7f841dfae11a76776f555462bc09e8741289997a/megtekintes">'
    '<a href="https://magyarkozlony.hu/dokumentumok/7f841dfae11a76776f555462bc09e8741289997a/megtekintes">'
    '<b>Magyar Közlöny 2026. évi 44. szám</b></a></div>'
)


class _FakeHttp:
    def __init__(self, text=None, fail=False):
        self._text, self._fail, self.calls = text, fail, []

    def get_text(self, url, **kw):
        self.calls.append(url)
        if self._fail:
            raise HttpError("boom")
        return self._text


def test_kozlony_resolves_direct_link():
    http = _FakeHttp(text=_MK_HTML)
    out = magyarkozlony.resolve(http, 44, "2026-05-09")
    assert out["url"] == "https://magyarkozlony.hu/?year=2026&month=&serial=44"
    assert out["docUrl"] == (
        "https://magyarkozlony.hu/dokumentumok/"
        "7f841dfae11a76776f555462bc09e8741289997a/megtekintes")
    assert http.calls == [out["url"]]


def test_kozlony_degrades_when_fetch_fails():
    """A failed fetch still yields the listing URL (SCR-5), docUrl is None."""
    out = magyarkozlony.resolve(_FakeHttp(fail=True), 44, "2026-05-09")
    assert out["url"].endswith("serial=44")
    assert out["docUrl"] is None


def test_kozlony_none_without_inputs():
    """No issue number or no date → no gazette link at all (not promulgated)."""
    http = _FakeHttp(text=_MK_HTML)
    assert magyarkozlony.resolve(http, None, "2026-05-09") is None
    assert magyarkozlony.resolve(http, 44, None) is None
    assert http.calls == []  # never fetched


# --- agenda ----------------------------------------------------------------

def test_agenda_classify_interpellation():
    native, core = agenda.classify("Interpellációk")
    assert core == agenda.CORE_GOVERNMENT_QUESTIONING


def test_agenda_bills_split():
    topic, bills = agenda.split_topic_and_bills("Általános vita T/360 a költségvetésről")
    assert "T/360" in bills


# --- segmentation ----------------------------------------------------------

def test_html_to_text_strips_tags():
    html = '<div class="felszolalas-szoveg"><p><span>Jó napot. Köszönöm.</span></p></div>'
    assert html_to_text(html) == "Jó napot. Köszönöm."


def test_split_sentences_basic():
    sents = split_sentences("Jó napot kívánok. Köszönöm a szót! Valóban?")
    assert len(sents) == 3


# --- timing ----------------------------------------------------------------

def test_smil_span():
    uri = "https://sgis.parlament.hu:446/vod/smil:20260601.124141.1332144.30513190.smil/playlist.m3u8"
    span = smil_span_seconds(uri)
    assert span is not None and span > 0


def test_apply_timing_uses_real_per_speech_offsets():
    """With Felicitas offsets present, each speech's sentences are anchored to
    its exact [videoStart, videoEnd] window (felicitas-speech-offset), so timing
    cannot drift across the day."""
    speeches = [
        {"speechIndex": 1, "media": {"videoStart": 100.0, "videoEnd": 130.0},
         "textContents": [{"textBody": [{"type": "speech", "sentences": [
             {"text": "Hosszabb első mondat itt."}, {"text": "Rövid."}]}]}],
         "debug": {}},
        {"speechIndex": 2, "media": {"videoStart": 500.0, "videoEnd": 560.0},
         "textContents": [{"textBody": [{"type": "speech", "sentences": [
             {"text": "Második beszéd egyetlen mondata."}]}]}],
         "debug": {}},
    ]
    apply_timing(speeches, force=True)
    s1 = speeches[0]["textContents"][0]["textBody"][0]["sentences"]
    s2 = speeches[1]["textContents"][0]["textBody"][0]["sentences"]
    # Speech 1 anchored to its own window; sentences split by char length within.
    assert s1[0]["timeStart"] == 100.0
    assert s1[-1]["timeEnd"] == 130.0
    assert 100.0 < s1[0]["timeEnd"] < 130.0
    # Speech 2 jumps to its real offset (no end-to-end accumulation from speech 1).
    assert s2[0]["timeStart"] == 500.0
    assert s2[0]["timeEnd"] == 560.0
    for sp in speeches:
        assert sp["debug"]["align-method"] == "felicitas-speech-offset"
        assert sp["debug"]["confidence"] == 0.9


def test_apply_timing_is_monotonic_and_day_absolute():
    speeches = [
        {"speechIndex": 1, "media": {"videoFileURI":
            "https://x/vod/smil:20260601.120000.0.600000.smil/playlist.m3u8",
            "duration": 100},
         "textContents": [{"textBody": [{"type": "speech", "sentences": [
             {"text": "Első mondat hosszabb."}, {"text": "Rövid."}]}]}],
         "debug": {}},
        {"speechIndex": 2, "media": {"duration": 50},
         "textContents": [{"textBody": [{"type": "speech", "sentences": [
             {"text": "Másik beszéd mondata."}]}]}],
         "debug": {}},
    ]
    apply_timing(speeches, force=True)
    sents = [s for sp in speeches for c in sp["textContents"]
             for b in c["textBody"] for s in b["sentences"]]
    times = [s["timeStart"] for s in sents] + [sents[-1]["timeEnd"]]
    assert times == sorted(times)          # monotonic across the whole day
    assert sents[0]["timeStart"] == 0.0     # anchored at day start
    # span = (600000-0)/1000 = 600s; last sentence ends within the day stream.
    assert sents[-1]["timeEnd"] <= 600.0
    for sp in speeches:
        assert sp["debug"]["align-method"] == "estimated-day-offset"
        assert sp["debug"]["confidence"] <= 0.7


# --- transform end-to-end (offline) ---------------------------------------

def _raw_bundle():
    return {
        "cycle": 43, "sitting": 7, "session": "43007", "date": "2026-06-09",
        "datum_felirat": "2026.06.09.(7)",
        "video": {"m3u8":
            "https://sgis.parlament.hu:446/vod/smil:20260609.090000.0.1200000.smil/playlist.m3u8",
            "day_off1": 0.0},
        "scraped_at": "2026-06-18T00:00:00+00:00", "source": "felicitas-json",
        "speeches": [
            {"sorszam": 1, "speech_uuid": "u1", "person_id": "s001",
             "speaker": "Sulyok Tamás (Fidesz)", "type": "ülésvezetés",
             "aktus": "Ülésnap megnyitása", "kezdete": "09:00:00", "duration": 120,
             "text_html": "<div><p><span>Megnyitom az ülést. Üdvözlök mindenkit.</span></p></div>",
             "video_off_start": 0.0, "video_off_end": 120.0},
            {"sorszam": 2, "speech_uuid": "u2", "person_id": "g053",
             "speaker": "Gulyás Gergely (Fidesz)", "type": "napirend előtti felszólalás",
             "aktus": "Napirend előtti felszólalások", "kezdete": "09:02:00",
             "duration": 300,
             "text_html": "<div><p><span>Tisztelt Ház! Fontos kérdésről beszélek. Köszönöm.</span></p></div>"},
            {"sorszam": 3, "speech_uuid": "u3", "person_id": None,
             "speaker": "Kis Pál (független)", "type": "felszólalás",
             "aktus": "Általános vita", "kezdete": "09:10:00", "duration": 60,
             "text_html": ""},  # video-only, no transcript
        ],
    }


def test_transform_day_shape():
    rec = transform_day(_raw_bundle())
    assert rec["meta"]["session"] == "43007"
    assert rec["meta"]["counts"]["speeches"] == 3
    assert rec["meta"]["counts"]["withText"] == 2
    data = rec["data"]
    # originID format and ordering by speechIndex.
    assert data[0]["originID"] == "43-7-1"
    assert [d["speechIndex"] for d in data] == [1, 2, 3]
    # cross-module link preserved.
    assert data[1]["people"][0]["personID"] == "g053"
    # whole-day stream is the video URI for every speech (VIE-2).
    assert "playlist.m3u8" in data[0]["media"]["videoFileURI"]
    # provenance offsets carried through (kept for §10 swap).
    assert data[0]["media"]["videoStart"] == 0.0
    # Speech 1 has real Felicitas offsets (0–120s): sentences are anchored to
    # that exact window, so timing uses the precise per-speech method.
    sents = data[0]["textContents"][0]["textBody"][0]["sentences"]
    assert sents[0]["timeStart"] == 0.0
    assert sents[-1]["timeEnd"] == 120.0          # last sentence ends at videoEnd
    assert data[0]["debug"]["align-method"] == "felicitas-speech-offset"
    # Speech 2 has no per-speech offsets -> whole-day positional fallback.
    assert data[1]["debug"]["align-method"] == "estimated-day-offset"


def test_transform_degraded_speech_flagged():
    rec = transform_day(_raw_bundle())
    no_text = rec["data"][2]
    assert no_text["textContents"] == []
    assert no_text["debug"]["confidence"] == 0.5
    assert no_text["debug"]["confidence_reason"] == "no-proceedings-text"


def test_sitting_number_from_felirat():
    assert sitting_number({"datum_felirat": "2026.06.23.(11)"}) == 11


# --- bills / irományok -----------------------------------------------------

def test_bills_main_type_from_number_prefix():
    """Fetching every iromány type at once still tags each row with the correct
    ``mainType``, derived from the iromány-number prefix (BILL-9) rather than the
    query param (which is empty when all types are fetched)."""
    from parlamonitor.felicitas import FelicitasClient

    rows = [
        {"iromanyId": "a", "iromanyszam": "T/253", "cim": "bill",
         "iromanytipus": "törvényjavaslat"},
        {"iromanyId": "b", "iromanyszam": "I/12", "cim": "interpelláció",
         "iromanytipus": "interpelláció"},
        {"iromanyId": "c", "iromanyszam": "H/9", "cim": "határozat",
         "iromanytipus": "határozati javaslat"},
    ]
    fc = FelicitasClient.__new__(FelicitasClient)
    fc.select_all = lambda *a, **k: rows  # no network
    out = fc.bills(43)  # main_type="" → all types
    assert [b["mainType"] for b in out] == ["T", "I", "H"]
    assert out[0]["type"] == "törvényjavaslat"
