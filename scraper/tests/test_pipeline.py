"""Unit tests for the pure-logic pieces of the scraper.

These cover the parts the requirements call out for automated testing (OPS-3):
the JSON→record transform, sentence↔time mapping, and the helper logic — all
offline, no network. Run with ``pytest`` from the ``scraper/`` directory.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parlamonitor import agenda, magyarkozlony, wikidata
from parlamonitor.http_client import HttpError
from parlamonitor.names import build_person, split_name, split_speaker
from parlamonitor.segment import html_to_text, split_sentences
from parlamonitor.timing import apply_timing, smil_span_seconds
from parlamonitor.proceedings.transform import transform_day
from parlamonitor.proceedings.scrape import (_awaiting_content, cycle_days,
                                             number_days, renumbered, scrape_day,
                                             sitting_number)


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


def test_build_person_office_from_tisztseg():
    # A non-MP minister: the speaker string carries no faction, but the per-speech
    # tisztség (Felicitas `tisztseg`) names the office. It rides through as `office`
    # so the profile can identify a speaker who holds no mandate.
    p = build_person("Dr. Törőcsikné Dr. Görög Márta ()", person_id="0052",
                     office="igazságügyi miniszter")
    assert p["office"] == "igazságügyi miniszter"
    assert "faction" not in p


def test_build_person_no_office_when_absent():
    p = build_person("Ágh Péter (Fidesz)", person_id="a011", office=None)
    assert "office" not in p
    # An empty/whitespace tisztség is dropped, not stored as a blank office.
    assert "office" not in build_person("Ágh Péter (Fidesz)", office="  ")


# --- non-roster speaker portraits ------------------------------------------

class _PhotoFelicitas:
    """A fake whose ``photo`` returns bytes for known ids, None (404) for
    ``missing`` ids, and raises for ``flaky`` ids (a transient error)."""
    def __init__(self, have, missing, flaky=()):
        self.have, self.missing, self.flaky = set(have), set(missing), set(flaky)
        self.calls = []

    def photo(self, pid):
        self.calls.append(pid)
        if pid in self.flaky:
            raise HttpError("boom")
        if pid in self.have:
            return b"jpegbytes"
        return None  # 404


def test_fetch_missing_photos_downloads_and_negative_caches(tmp_path):
    from parlamonitor.representatives.scrape import (fetch_missing_photos,
                                                     load_photo_negcache)
    photos = tmp_path / "photos"
    f = _PhotoFelicitas(have={"004L"}, missing={"0052"})
    res = fetch_missing_photos(f, photos, ["004L", "0052"])
    assert res["fetched"] == 1
    assert (photos / "004L.jpg").read_bytes() == b"jpegbytes"    # advocate portrait saved
    assert not (photos / "0052.jpg").exists()                    # minister: no portrait
    assert load_photo_negcache(photos) == {"0052"}               # 404 remembered

    # A second run skips the already-downloaded id AND the negative-cached 404.
    f2 = _PhotoFelicitas(have={"004L"}, missing={"0052"})
    fetch_missing_photos(f2, photos, ["004L", "0052"])
    assert f2.calls == []


def test_fetch_missing_photos_retries_transient_errors(tmp_path):
    from parlamonitor.representatives.scrape import (fetch_missing_photos,
                                                     load_photo_negcache)
    photos = tmp_path / "photos"
    f = _PhotoFelicitas(have=set(), missing=set(), flaky={"x1"})
    fetch_missing_photos(f, photos, ["x1"])
    # A transient failure is NOT negative-cached, so it will be retried next run.
    assert load_photo_negcache(photos) == set()


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


# --- wikidata (MP -> Wikidata/Wikipedia via P4966) -------------------------

# A trimmed Wikidata Query Service JSON response: P4966 is the parlament.hu
# kepviseloId, so a row's value is exactly our personID. Row 1 has both a
# Hungarian and English article, row 2 only English, row 3 no article at all.
_WD_JSON = {
    "results": {"bindings": [
        {"p4966": {"value": "g056"},
         "item": {"value": "http://www.wikidata.org/entity/Q172301"},
         "huArticle": {"value": "https://hu.wikipedia.org/wiki/Gy%C5%91ngy%C3%B6si_M%C3%A1rton"},
         "enArticle": {"value": "https://en.wikipedia.org/wiki/M%C3%A1rton_Gy%C5%91ngy%C3%B6si"}},
        {"p4966": {"value": "n026"},
         "item": {"value": "http://www.wikidata.org/entity/Q832221"},
         "enArticle": {"value": "https://en.wikipedia.org/wiki/Tibor_Navracsics"}},
        {"p4966": {"value": "x999"},
         "item": {"value": "http://www.wikidata.org/entity/Q1"}},
    ]}
}


class _FakeJsonHttp:
    def __init__(self, data=None, fail=False):
        self._data, self._fail = data, fail
        self.calls, self.slept = [], 0

    def get_json(self, url, **kw):
        self.calls.append((url, kw))
        if self._fail:
            raise HttpError("boom")
        return self._data

    def polite_sleep(self):
        self.slept += 1


def test_wikidata_links_keyed_by_p4966_and_prefer_hu():
    http = _FakeJsonHttp(data=_WD_JSON)
    links = wikidata.fetch_mp_links(http)
    # keyed by the parlament.hu id (== our personID), QID lifted from the URI
    assert links["g056"]["wikidataId"] == "Q172301"
    # the Hungarian article wins over the English one for a Hungarian site
    assert links["g056"]["wikipediaUrl"].startswith("https://hu.wikipedia.org/")
    # only English available → use it
    assert links["n026"]["wikipediaUrl"].startswith("https://en.wikipedia.org/")
    # linked on Wikidata but no article anywhere → id kept, url is None
    assert links["x999"] == {"wikidataId": "Q1", "wikipediaUrl": None}
    # one query for the whole roster, preceded by a politeness sleep
    assert len(http.calls) == 1 and http.slept == 1
    assert http.calls[0][0] == wikidata.ENDPOINT


def test_wikidata_links_degrade_to_empty_on_failure():
    """A failed query never raises — the roster still loads, just without links."""
    assert wikidata.fetch_mp_links(_FakeJsonHttp(fail=True)) == {}


# --- agenda ----------------------------------------------------------------

def test_agenda_classify_interpellation():
    native, core = agenda.classify("Interpellációk")
    assert core == agenda.CORE_GOVERNMENT_QUESTIONING


def test_agenda_bills_split():
    topic, bills = agenda.split_topic_and_bills("Általános vita T/360 a költségvetésről")
    assert "T/360" in bills


def test_agenda_title_keeps_text_after_mid_title_code():
    """A code inside parentheses mid-title must not truncate the topic: the old
    behaviour cut at the first code, leaving "Interpelláció megtárgyalása (" and
    dropping the actual subject."""
    topic, bills = agenda.split_topic_and_bills(
        "Interpelláció megtárgyalása (I/112) Folytatódik-e a panelprogram?")
    assert bills == ["I/112"]
    assert topic == "Interpelláció megtárgyalása Folytatódik-e a panelprogram?"
    assert "(" not in topic


def test_agenda_title_trailing_code_and_empty_parens():
    topic, bills = agenda.split_topic_and_bills(
        "A honvédelemről szóló törvényjavaslat általános vitája (T/360)")
    assert bills == ["T/360"]
    assert topic == "A honvédelemről szóló törvényjavaslat általános vitája"


def test_agenda_item_grouping_key_independent_of_speech_type():
    """Speeches of one act must share the grouping key (title + officialTitle)
    regardless of their per-speech type, so they land in one section — the
    loader groups on that key, NOT on the classified type."""
    from parlamonitor.proceedings.transform import _agenda_item
    chair = _agenda_item({"aktus": "Személyes érintettség", "type": "ülésvezetés"})
    speaker = _agenda_item({"aktus": "Személyes érintettség",
                            "type": "személyes érintettség miatti felszólalás"})
    group_key = lambda i: (i.get("title"), i.get("officialTitle"))
    assert group_key(chair) == group_key(speaker)


def test_agenda_type_from_act_name_with_speech_fallback():
    """A named act classifies from its own name (an interpelláció stays
    questioning-of-the-government even for an immediate-question-typed speech);
    an act whose name carries no signal falls back to the speech type so
    structural sections aren't mislabelled regular."""
    from parlamonitor.proceedings.transform import _agenda_item
    interp = _agenda_item({"aktus": "Interpelláció megtárgyalása (I/94) Ki védi meg?",
                           "type": "elhangzik az interpelláció/kérdés/azonnali kérdés"})
    assert interp["type"] == agenda.CORE_GOVERNMENT_QUESTIONING
    chair = _agenda_item({"aktus": "Az ülés napirendjének megállapítása",
                          "type": "ülésvezetés"})
    assert chair["type"] == agenda.CORE_PROCEDURAL


# --- segmentation ----------------------------------------------------------

def test_html_to_text_strips_tags():
    html = '<div class="felszolalas-szoveg"><p><span>Jó napot. Köszönöm.</span></p></div>'
    assert html_to_text(html) == "Jó napot. Köszönöm."


def test_split_sentences_basic():
    sents = split_sentences("Jó napot kívánok. Köszönöm a szót! Valóban?")
    assert len(sents) == 3
    # A single paragraph → every sentence carries paragraph index 0.
    assert all(s["paragraph"] == 0 for s in sents)


def test_split_sentences_tracks_paragraphs():
    """Sentences carry the index of their source paragraph (newline boundary),
    so the reader can reconstruct the original multi-paragraph formatting."""
    text = "Első bekezdés első mondata. Ugyanaz második mondata.\nMásodik bekezdés."
    sents = split_sentences(text)
    assert [s["paragraph"] for s in sents] == [0, 0, 1]


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
        "day_uuid": "623075ee-d900-4f17-8944-da6465a86766",
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


def test_plenary_day_page_url_round_trips():
    import base64 as _b64
    import gzip as _gzip
    import json as _json
    from parlamonitor.proceedings.transform import plenary_day_page_url

    uid = "623075ee-d900-4f17-8944-da6465a86766"
    url = plenary_day_page_url(uid)
    assert url.startswith("https://www.parlament.hu/ulesnapok-ulesidok#page=cv1gzb-")
    # Reverse the encoding the same way parlament.hu's client does and confirm
    # the decoded state points at this exact sitting day's speech listing.
    token = url.split("#page=cv1gzb-", 1)[1].translate(str.maketrans("_-", "/+"))
    token += "=" * (-len(token) % 4)
    state = _json.loads(_gzip.decompress(_b64.b64decode(token)).decode("utf-8"))
    assert "ulesnap-felszolalasai-with-contract" in state["page"]
    assert state["binding"]["felszolalasDao.parameter"]["content"]["pUlesnapId"] == uid


def test_plenary_day_page_url_none_without_uuid():
    from parlamonitor.proceedings.transform import plenary_day_page_url
    assert plenary_day_page_url(None) is None
    # Legacy cycle-42 numeric day ids don't resolve on the new SPA page.
    assert plenary_day_page_url("2741490") is None


def test_transform_day_carries_day_page_url():
    rec = transform_day(_raw_bundle())
    assert "ulesnapok-ulesidok#page=cv1gzb-" in rec["meta"]["sourcePage"]


def test_transform_degraded_speech_flagged():
    rec = transform_day(_raw_bundle())
    no_text = rec["data"][2]
    assert no_text["textContents"] == []
    assert no_text["debug"]["confidence"] == 0.5
    assert no_text["debug"]["confidence_reason"] == "no-proceedings-text"


def test_transform_dedups_speech_listed_under_several_agenda_items():
    # The Felicitas source emits the same physical speech once per agenda item it
    # is linked to (same speech_uuid + video offsets + text, differing aktus). The
    # transform must collapse those to one speech, keeping the first agenda item
    # and recording the others — not show the chair's remarks 2-3× in a row.
    raw = _raw_bundle()
    opener = dict(raw["speeches"][0])
    dup1 = dict(opener, aktus="Az ülés napirendjének megállapítása")
    dup2 = dict(opener, aktus="Bejelentések")
    raw["speeches"] = [opener, dup1, dup2] + raw["speeches"][1:]

    rec = transform_day(raw)
    data = rec["data"]
    # Three physical speeches survive (u1 once, u2, u3), not five.
    assert rec["meta"]["counts"]["speeches"] == 3
    assert [d["debug"]["speechUUID"] for d in data] == ["u1", "u2", "u3"]
    # speechIndex is contiguous over the deduped list (drives the uid).
    assert [d["speechIndex"] for d in data] == [1, 2, 3]
    # Kept under its FIRST agenda item; the others are recorded for provenance.
    assert data[0]["agendaItem"]["officialTitle"] == "Ülésnap megnyitása"
    assert data[0]["debug"]["mergedAgendaItems"] == [
        "Az ülés napirendjének megállapítása", "Bejelentések"]


def test_transform_keeps_distinct_speeches_sharing_a_speaker():
    # Same speaker, different speeches (distinct uuid) must NOT be merged.
    raw = _raw_bundle()
    again = dict(raw["speeches"][0], speech_uuid="u1b", sorszam=4,
                 aktus="Napirend utáni felszólalások", kezdete="18:00:00",
                 video_off_start=6000.0, video_off_end=6120.0)
    raw["speeches"].append(again)
    rec = transform_day(raw)
    assert rec["meta"]["counts"]["speeches"] == 4
    assert [d["debug"]["speechUUID"] for d in rec["data"]] == ["u1", "u2", "u3", "u1b"]


# --- upcoming / scheduled sittings (announced days, no recording yet) -------

def test_transform_marks_normal_day_published():
    rec = transform_day(_raw_bundle())
    assert rec["meta"]["status"] == "published"


def test_transform_marks_speechless_day_scheduled():
    # An announced sitting parlament.hu lists before any speeches exist.
    raw = _raw_bundle()
    raw["speeches"] = []
    raw["video"] = None
    rec = transform_day(raw)
    assert rec["meta"]["status"] == "scheduled"
    assert rec["meta"]["counts"] == {"speeches": 0, "withText": 0}
    assert rec["data"] == []
    # Still carries a usable day range and a source link, so the placeholder is
    # renderable and links back to parlament.hu (VIE-7).
    assert rec["meta"]["date"] == "2026-06-09"
    assert "ulesnapok-ulesidok#page=" in rec["meta"]["sourcePage"]


class _FakeFelicitasDay:
    """Minimal Felicitas stand-in for scrape_day: an announced day has no speeches
    and no resolvable recording yet."""
    def __init__(self, speeches, video=None, texts=None, offsets=None,
                 details=None):
        self._speeches = speeches
        self._video = video
        self._texts = texts or {}
        self._offsets = offsets or {}
        self._details = details or {}

    def day_speeches(self, uuid):
        return list(self._speeches)

    def day_video(self, uuid):
        return self._video

    def speech_text(self, uuid):
        return {"html": self._texts.get(uuid, ""), **self._details.get(uuid, {})}

    def speech_offsets(self, uuid):
        return self._offsets.get(uuid)


def _felicitas_day():
    return {"uuid": "623075ee-d900-4f17-8944-da6465a86766",
            "date": "2026-07-15", "datum_felirat": "2026.07.15.(16)",
            "duration_s": None, "debate_s": None}


def test_scrape_day_returns_placeholder_for_announced_day():
    # Previously scrape_day returned None here and the day was dropped; now it must
    # return a placeholder bundle so the upcoming sitting can be shown.
    bundle = scrape_day(_FakeFelicitasDay(speeches=[]), 43, _felicitas_day())
    assert bundle is not None
    assert bundle["speeches"] == []
    assert bundle["session"] == "43016"
    assert transform_day(bundle)["meta"]["status"] == "scheduled"


def test_scrape_day_skips_whole_day_offsets_for_unsegmented_sitting():
    # Regression (sitting 43015, 2026-07-07): a recently-held day whose recording
    # is published but not yet cut per speech makes speech_offsets echo the
    # WHOLE-DAY window [day_off1, day_off2] for every speech. Accepting it stamped
    # videoStart=0 / videoEnd=day-span on each, so the loader derived a ≈15 h
    # duration per speech and the speaker toplist summed to 152 h. Such offsets
    # must be rejected → no per-speech offsets written.
    speeches = [
        {"speech_uuid": "u1", "sorszam": 1, "speaker": "A"},
        {"speech_uuid": "u2", "sorszam": 2, "speaker": "B"},
    ]
    fake = _FakeFelicitasDay(
        speeches=speeches,
        video={"m3u8": "x", "day_off1": 1200.0, "day_off2": 56251.0},
        offsets={"u1": (1200.0, 56251.0), "u2": (1200.0, 56251.0)},
    )
    bundle = scrape_day(fake, 43, _felicitas_day())
    for sp in bundle["speeches"]:
        assert "video_off_start" not in sp
        assert "video_off_end" not in sp


def test_transform_marks_unsegmented_day_awaiting_media():
    # A held sitting parlament.hu lists but has not segmented per speech (whole-day
    # offsets echoed, no transcript) has no timings/clips/text to show, so the
    # transform marks it `awaiting_media` — the UI shows it not-yet-ready/disabled.
    speeches = [
        {"speech_uuid": "u1", "sorszam": 1, "speaker": "A"},
        {"speech_uuid": "u2", "sorszam": 2, "speaker": "B"},
    ]
    fake = _FakeFelicitasDay(
        speeches=speeches,
        video={"m3u8": "x", "day_off1": 1200.0, "day_off2": 56251.0},
        offsets={"u1": (1200.0, 56251.0), "u2": (1200.0, 56251.0)},
    )
    rec = transform_day(scrape_day(fake, 43, _felicitas_day()))
    assert rec["meta"]["status"] == "awaiting_media"


def test_transform_marks_segmented_day_published():
    # Real per-speech windows → per-speech media exists → normal published day.
    speeches = [{"speech_uuid": "u1", "sorszam": 1, "speaker": "A"}]
    fake = _FakeFelicitasDay(
        speeches=speeches,
        video={"m3u8": "x", "day_off1": 1200.0, "day_off2": 56251.0},
        offsets={"u1": (1200.0, 1320.0)},
    )
    rec = transform_day(scrape_day(fake, 43, _felicitas_day()))
    assert rec["meta"]["status"] == "published"


def test_scrape_day_keeps_real_per_speech_offsets():
    # A genuinely segmented day: each speech's window is a small slice inside the
    # recording, so offsets ARE kept (converted to day-stream-relative seconds).
    speeches = [
        {"speech_uuid": "u1", "sorszam": 1, "speaker": "A"},
        {"speech_uuid": "u2", "sorszam": 2, "speaker": "B"},
    ]
    fake = _FakeFelicitasDay(
        speeches=speeches,
        video={"m3u8": "x", "day_off1": 1200.0, "day_off2": 56251.0},
        offsets={"u1": (1200.0, 1320.0), "u2": (1320.0, 1500.0)},
    )
    bundle = scrape_day(fake, 43, _felicitas_day())
    by_uuid = {sp["speech_uuid"]: sp for sp in bundle["speeches"]}
    assert by_uuid["u1"]["video_off_start"] == 0.0
    assert by_uuid["u1"]["video_off_end"] == 120.0
    assert by_uuid["u2"]["video_off_start"] == 120.0
    assert by_uuid["u2"]["video_off_end"] == 300.0


def test_awaiting_content(tmp_path):
    import json as _json
    def _write(name, obj):
        p = tmp_path / name
        p.write_text(_json.dumps(obj))
        return p
    # No speeches yet (announced placeholder) → still awaiting.
    assert _awaiting_content(_write("a.json", {"speeches": []})) is True
    # Speeches but none with text (video-only, transcript not published) → awaiting.
    assert _awaiting_content(_write("b.json", {"speeches": [{"text_html": ""}]})) is True
    # At least one speech has text → done.
    assert _awaiting_content(_write("c.json", {"speeches": [{"text_html": "<p>x</p>"}]})) is False
    # A malformed/missing file self-heals by re-scraping.
    assert _awaiting_content(tmp_path / "missing.json") is True
    # A text-less day within the publication-lag window is still awaiting.
    from datetime import datetime, timedelta, timezone
    recent = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
    assert _awaiting_content(
        _write("d.json", {"date": recent, "speeches": [{"text_html": ""}]})) is True
    # A text-less day past the window is officially missing text → done (not
    # re-downloaded every run). This is the cycle-39 2011-autumn case.
    assert _awaiting_content(
        _write("e.json", {"date": "2011-09-12", "speeches": [{"text_html": ""}]})) is False
    # An old announced-but-empty placeholder likewise will not fill in → done.
    assert _awaiting_content(
        _write("f.json", {"date": "2011-09-12", "speeches": []})) is False


def test_sitting_number_from_felirat():
    assert sitting_number({"datum_felirat": "2026.06.23.(11)"}) == 11


# --- cycle-wide ülésnap numbering (session-key stability) -------------------

def _cycle43_days():
    """The shape parlament.hu returns for cycle 43: two ülésszaks, and per-day
    ordinals (``day_in_session``/``day_in_ules``) that restart with each of them."""
    spring = ["2026-05-09", "2026-05-12", "2026-05-26", "2026-05-27",
              "2026-06-01", "2026-06-08", "2026-06-09", "2026-06-15"]
    summer = ["2026-06-16", "2026-06-22", "2026-06-23", "2026-06-29", "2026-06-30"]
    days = []
    for name, dates in (("2026. tavaszi", spring), ("2026. nyári", summer)):
        for i, date in enumerate(dates, 1):
            days.append({"uuid": f"u-{date}", "date": date, "datum_felirat": None,
                         "ulesszak": name, "day_in_session": i, "day_in_ules": 1})
    return days


def test_number_days_is_cycle_wide_when_the_source_ordinal_is_missing():
    # The 2026-07 outage: datumFelirat came back empty and day_in_session (which
    # restarts at 1 each ülésszak) was used as the cycle key, so the summer days
    # were renumbered 1..N and overwrote the spring ones.
    numbered = number_days(_cycle43_days())
    assert [d["sitting"] for d in numbered] == list(range(1, 14))
    by_date = {d["date"]: d["sitting"] for d in numbered}
    assert by_date["2026-06-16"] == 9      # NOT 1, its day_in_session
    assert by_date["2026-06-30"] == 13     # NOT 5


def test_number_days_prefers_the_published_ordinal():
    days = [{"uuid": "b", "date": "2026-05-12", "datum_felirat": "2026.05.12.(2)"},
            {"uuid": "a", "date": "2026-05-09", "datum_felirat": "2026.05.09.(1)"}]
    assert [d["sitting"] for d in number_days(days)] == [1, 2]


def test_number_days_ignores_a_partial_published_ordinal():
    # A day lacking a label sorts BEFORE a labelled one here — a synthesized
    # number could only slot in above every published ordinal, so 'b' getting
    # slotted after 'a' but before a lower-numbered later day would go
    # backwards. Ambiguous; date order wins for the whole list instead.
    days = [{"uuid": "a", "date": "2026-05-09", "datum_felirat": "2026.05.09.(5)"},
            {"uuid": "b", "date": "2026-05-12", "datum_felirat": None},
            {"uuid": "c", "date": "2026-05-26", "datum_felirat": "2026.05.26.(1)"}]
    assert [d["sitting"] for d in number_days(days)] == [1, 2, 3]


def test_number_days_fills_an_unlabelled_day_at_the_live_edge():
    # The 2026-08 recurrence: parlament.hu lists an upcoming/just-held sitting
    # before assigning its datumFelirat label. That must NOT drag every already
    # -labelled day's real ordinal down to fill the gap (see the module-level
    # 2026-07 outage note) — the unlabelled day just gets the next free number.
    days = [{"uuid": "a", "date": "2026-06-16", "datum_felirat": "2026.06.16.(9)"},
            {"uuid": "b", "date": "2026-06-22", "datum_felirat": "2026.06.22.(10)"},
            {"uuid": "c", "date": "2026-06-23", "datum_felirat": "2026.06.23.(11)"},
            {"uuid": "d", "date": "2026-06-29", "datum_felirat": None}]
    numbered = number_days(days)
    assert [d["sitting"] for d in numbered] == [9, 10, 11, 12]
    assert {d["date"]: d["sitting"] for d in numbered}["2026-06-29"] == 12


class _DaysStub:
    """Felicitas stub exposing only what :func:`cycle_days` needs."""

    def __init__(self, days, start="2026-05-01", end=None):
        self.days = days
        self.ranges = {43: {"start": start, "end": end}}
        self.asked = None

    def cycle_ranges(self):
        return self.ranges

    def session_days(self, cycle, date_from, date_to):
        self.asked = (date_from, date_to)
        return [d for d in self.days if date_from <= d["date"] <= date_to]


def test_cycle_days_numbers_over_the_whole_cycle_not_the_window():
    """Scraping a narrow window must not renumber it from 1 — the ordinals are
    positions in the cycle, so the query widens to the cycle before numbering."""
    stub = _DaysStub(_cycle43_days())
    days = cycle_days(stub, 43, "2026-06-16", "2026-06-30")
    assert stub.asked == ("2026-05-01", "2026-06-30")
    assert [(d["date"], d["sitting"]) for d in days] == [
        ("2026-06-16", 9), ("2026-06-22", 10), ("2026-06-23", 11),
        ("2026-06-29", 12), ("2026-06-30", 13)]


def test_download_period_refuses_to_renumber_over_an_existing_day(tmp_path,
                                                                  monkeypatch):
    """The archive is never silently overwritten: if the cycle's day list has
    shifted (here the earlier days vanished from it, so 2026-06-16 is numbered 1),
    the day whose key is already taken is skipped until --allow-renumber."""
    from parlamonitor import config as pm_config
    from parlamonitor.proceedings import scrape as pm_scrape

    monkeypatch.setattr(pm_scrape, "scrape_day",
                        lambda felicitas, cycle, day, **kw: {
                            "date": day["date"], "speeches": [{"text_html": "x"}],
                            "video": {}})
    paths = pm_config.Paths(tmp_path)
    paths.ensure()
    paths.raw_day("43001").write_text(
        json.dumps({"date": "2026-05-09", "speeches": [{"text_html": "spring"}]}))

    stub = _DaysStub([{"uuid": "u", "date": "2026-06-16", "datum_felirat": None}],
                     start="2026-06-16")
    assert pm_scrape.download_period(stub, paths, 43, "2026-06-16", "2026-06-16",
                                     force=True) == []
    assert json.loads(paths.raw_day("43001").read_text())["date"] == "2026-05-09"

    assert pm_scrape.download_period(stub, paths, 43, "2026-06-16", "2026-06-16",
                                     force=True, allow_renumber=True) == ["43001"]
    assert json.loads(paths.raw_day("43001").read_text())["date"] == "2026-06-16"


def test_renumbered_flags_a_key_held_by_another_date(tmp_path):
    raw = tmp_path / "raw-43001-day.json"
    raw.write_text(json.dumps({"date": "2026-05-09", "speeches": []}))
    assert renumbered(raw, "2026-06-16") == "2026-05-09"
    assert renumbered(raw, "2026-05-09") is None
    assert renumbered(tmp_path / "missing.json", "2026-05-09") is None


# --- complete day speech listing (agenda listing + flat roster) -------------

def _listed(sorszam, uuid, aktus, **kw):
    return {"sorszam": sorszam, "speech_uuid": uuid, "speaker": f"A ({uuid})",
            "type": "felszólalás", "aktus": aktus, "aktus_id": aktus,
            "bills": [], "committee_id": None, "is_committee": False, **kw}


def _roster(sorszam, uuid, stype=None):
    return {"sorszam": sorszam, "speech_uuid": uuid, "speaker": "Árvay Nikolett",
            "person_id": "0032", "type": stype, "kezdete": None, "duration": 66}


def test_merge_roster_fills_speeches_missing_from_agenda_listing():
    """Regression (sitting 43015, speech #300): ``ulesnapok-aktusok-query`` reports
    a speech only through the agenda act it is linked to, so speeches linked to
    none — chiefly those with no "Felszólalás oka" — were absent from the day
    entirely (16 of 303 that day). The flat day roster fills them in, each under
    the act of the speech it follows, so the sitting-day view shows it."""
    from parlamonitor.felicitas import _merge_roster

    listing = [_listed(1, "u1", "act A"), _listed(3, "u3", "act B")]
    roster = [_roster(1, "u1"), _roster(2, "u2"), _roster(3, "u3"),
              _roster(4, "u4", "ülésvezetés")]
    merged = _merge_roster(listing, roster)

    assert [s["sorszam"] for s in merged] == [1, 2, 3, 4]
    recovered = {s["speech_uuid"]: s for s in merged if s.get("from_roster")}
    assert set(recovered) == {"u2", "u4"}
    # Each inherits the act of the speech it continues, so it lands in the same
    # agenda item instead of a stray one.
    assert recovered["u2"]["aktus"] == "act A"
    assert recovered["u4"]["aktus"] == "act B"
    assert recovered["u4"]["type"] == "ülésvezetés"
    # The agenda listing's own rows keep their act/speaker (it knows more).
    assert merged[0]["speaker"] == "A (u1)"
    assert merged[0].get("from_roster") is None


def test_merge_roster_leading_gap_inherits_following_act():
    from parlamonitor.felicitas import _merge_roster

    merged = _merge_roster([_listed(2, "u2", "act A")],
                           [_roster(1, "u1"), _roster(2, "u2")])
    assert merged[0]["speech_uuid"] == "u1"
    assert merged[0]["aktus"] == "act A"


def test_merge_roster_noop_when_listing_is_complete():
    from parlamonitor.felicitas import _merge_roster

    listing = [_listed(1, "u1", "act A"), _listed(2, "u2", "act A")]
    assert _merge_roster(listing, [_roster(1, "u1"), _roster(2, "u2")]) is listing


def test_scrape_day_takes_speaker_from_detail_for_roster_speech():
    """The flat roster gives a bare name, so a recovered speech would land without
    a faction; the detail query's speaker carries the "(TISZA)" suffix."""
    speeches = [
        {"speech_uuid": "u1", "sorszam": 1, "speaker": "Kovács Anna (Fidesz)"},
        {"speech_uuid": "u2", "sorszam": 2, "speaker": "Árvay Nikolett",
         "from_roster": True},
    ]
    fake = _FakeFelicitasDay(
        speeches=speeches,
        details={"u1": {"speaker": "KOVÁCS ANNA"},
                 "u2": {"speaker": "Dr. Árvay Nikolett (TISZA)"}})
    by_uuid = {sp["speech_uuid"]: sp
               for sp in scrape_day(fake, 43, _felicitas_day())["speeches"]}
    assert by_uuid["u2"]["speaker"] == "Dr. Árvay Nikolett (TISZA)"
    # An agenda-listed speech keeps the listing's speaker (the detail query's is
    # the transcript's shouty rendering).
    assert by_uuid["u1"]["speaker"] == "Kovács Anna (Fidesz)"


def test_scrape_day_reuses_prior_text_and_fetches_only_new_speeches():
    """Backfill mode: re-listing a day to pick up its previously-missed speeches
    keeps the text/offsets already downloaded and requests only the new speech."""
    class _Counting(_FakeFelicitasDay):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.fetched = []

        def speech_text(self, uuid):
            self.fetched.append(uuid)
            return super().speech_text(uuid)

    speeches = [
        {"speech_uuid": "u1", "sorszam": 1, "speaker": "Kovács Anna (Fidesz)"},
        {"speech_uuid": "u2", "sorszam": 2, "speaker": "Árvay Nikolett",
         "from_roster": True},
    ]
    fake = _Counting(speeches=speeches, texts={"u2": "<p>Új.</p>"})
    prior = {"u1": {"speech_uuid": "u1", "text_html": "<p>Régi.</p>",
                    "role": "miniszter", "video_off_start": 10.0,
                    "video_off_end": 40.0}}
    bundle = scrape_day(fake, 43, _felicitas_day(), prior=prior)

    assert fake.fetched == ["u2"]           # the held speech cost no request
    by_uuid = {sp["speech_uuid"]: sp for sp in bundle["speeches"]}
    assert by_uuid["u1"]["text_html"] == "<p>Régi.</p>"
    assert by_uuid["u1"]["role"] == "miniszter"
    assert by_uuid["u1"]["video_off_start"] == 10.0
    assert by_uuid["u2"]["text_html"] == "<p>Új.</p>"


def test_prior_speeches_skips_textless_speeches(tmp_path):
    # A speech whose transcript has not been published yet must NOT be reused —
    # it still has to be re-requested until its text lands.
    import json as _json
    from parlamonitor.proceedings.scrape import _prior_speeches

    p = tmp_path / "raw.json"
    p.write_text(_json.dumps({"speeches": [
        {"speech_uuid": "u1", "text_html": "<p>x</p>"},
        {"speech_uuid": "u2", "text_html": ""},
        {"sorszam": 3, "text_html": "<p>y</p>"},          # no uuid → unkeyable
    ]}))
    assert set(_prior_speeches(p)) == {"u1"}
    assert _prior_speeches(tmp_path / "missing.json") == {}


def test_transform_files_recovered_speech_under_neighbours_agenda_item():
    """End to end: a roster-recovered speech is published in join-number order and
    shares its neighbours' agenda item (so the loader groups it into that section
    rather than creating a one-speech stray)."""
    from parlamonitor.felicitas import _merge_roster

    listing = [_listed(1, "u1", "Általános vita lefolytatása (T/303) Valamiről"),
               _listed(3, "u3", "Általános vita lefolytatása (T/303) Valamiről")]
    speeches = _merge_roster(listing, [_roster(1, "u1"), _roster(2, "u2"),
                                       _roster(3, "u3")])
    fake = _FakeFelicitasDay(speeches=speeches, texts={"u2": "<p>Köszönöm.</p>"})
    rec = transform_day(scrape_day(fake, 43, _felicitas_day()))

    assert rec["meta"]["counts"]["speeches"] == 3
    assert [e["originID"] for e in rec["data"]] == ["43-16-1", "43-16-2", "43-16-3"]
    items = {e["agendaItem"]["officialTitle"] for e in rec["data"]}
    assert items == {"Általános vita lefolytatása (T/303) Valamiről"}
    assert rec["data"][1]["debug"]["agendaItemInferred"] is True


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


# --- incremental detail cache ----------------------------------------------

def test_fill_details_reuses_unchanged_and_fetches_new(tmp_path):
    """A second run reuses cached detail for items whose fingerprint is
    unchanged and only fetches new/changed ones (SCR-2)."""
    import json as _json
    from parlamonitor.detail_cache import fill_details, load_cache

    fp = lambda r: r.get("status")
    prior = {"data": [
        {"voteId": "v1", "status": "done", "detail": {"records": ["cached"]}},
        {"voteId": "v2", "status": "open", "detail": {"records": ["stale"]}},
    ]}
    path = tmp_path / "votes-43.json"
    path.write_text(_json.dumps(prior))
    cache = load_cache(path, "voteId", fp)

    calls = []
    def fetch(k):
        calls.append(k)
        return {"records": [f"fresh-{k}"]}

    records = [
        {"voteId": "v1", "status": "done"},   # unchanged → reuse
        {"voteId": "v2", "status": "closed"},  # fingerprint changed → fetch
        {"voteId": "v3", "status": "done"},    # new → fetch
    ]
    fetched, reused = fill_details(records, key="voteId", fingerprint=fp,
                                   fetch=fetch, cache=cache, label="Vote")
    assert (fetched, reused) == (2, 1)
    assert calls == ["v2", "v3"]
    assert records[0]["detail"] == {"records": ["cached"]}
    assert records[1]["detail"] == {"records": ["fresh-v2"]}


def test_fill_details_force_ignores_cache(tmp_path):
    from parlamonitor.detail_cache import fill_details

    cache = {"v1": ({"records": ["cached"]}, "done")}
    records = [{"voteId": "v1", "status": "done"}]
    calls = []
    fill_details(records, key="voteId", fingerprint=lambda r: r.get("status"),
                 fetch=lambda k: calls.append(k) or {"x": 1}, cache=cache,
                 force=True, label="Vote")
    assert calls == ["v1"]  # forced re-fetch despite a cache hit


def test_fill_details_fetch_failure_degrades(tmp_path):
    """A failing detail fetch degrades that item to None, never aborts (SCR-5)."""
    from parlamonitor.detail_cache import fill_details

    def boom(k):
        raise RuntimeError("network down")

    records = [{"voteId": "v1", "status": "open"}]
    fetched, reused = fill_details(records, key="voteId",
                                   fingerprint=lambda r: r.get("status"),
                                   fetch=boom, cache={}, label="Vote")
    assert (fetched, reused) == (1, 0)
    assert records[0]["detail"] is None


def test_load_cache_missing_file_is_empty(tmp_path):
    from parlamonitor.detail_cache import load_cache
    assert load_cache(tmp_path / "nope.json", "voteId", lambda r: None) == {}


def test_load_cache_skips_records_without_detail(tmp_path):
    import json as _json
    from parlamonitor.detail_cache import load_cache
    path = tmp_path / "bills-43.json"
    path.write_text(_json.dumps({"data": [
        {"billId": "b1", "detail": {"events": []}, "status": "x"},
        {"billId": "b2", "detail": None, "status": "y"},  # not cached
        {"billId": "b3", "status": "z"},                  # no detail key
    ]}))
    cache = load_cache(path, "billId", lambda r: r.get("status"))
    assert set(cache) == {"b1"}
