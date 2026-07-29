"""Offline tests for the continuous-sync change probe (`parlamonitor.sync`).

The point of the sync watcher is to cost almost nothing when parlament.hu hasn't
changed and to re-scrape only what did. These tests drive ``run_sync`` against a
fake Felicitas client (no network), stubbing the heavy per-day scrape so we can
assert *which* sittings get re-scraped on each poll:

* first poll scrapes every day;
* an unchanged poll scrapes nothing (and only spends the cheap list + one
  speech-listing request);
* a new/longer sitting, or new speeches on the still-live latest sitting, re-scrape
  exactly that one day;
* a day first seen **video-only** (no transcript yet) keeps being cheaply
  re-checked and is re-scraped once its text is published days later — even after
  it is no longer the latest day (the text-lag fix);
* an **announced day with no speeches** is ingested (a scheduled placeholder), not
  dropped.
"""

from __future__ import annotations

import collections
import json

import pytest

from parlamonitor import sync
from parlamonitor.config import Paths


class FakeFelicitas:
    def __init__(self, days, speeches, texts=None):
        self.days = [dict(d) for d in days]
        self.speeches = speeches            # day uuid -> list of speech rows
        # speech uuid -> transcript HTML. Defaults to giving every listed speech
        # text (a fully-published day); pass ``texts={}`` for a video-only day whose
        # jegyzőkönyv has not landed yet, then fill it in to simulate publication.
        if texts is None:
            texts = {s["speech_uuid"]: "<p>szöveg</p>"
                     for sp in speeches.values() for s in sp}
        self.texts = texts
        self.calls = collections.Counter()

    def cycle_ranges(self):
        return {43: {"start": "2026-05-01", "end": None}}

    def session_days(self, cycle, start, end):
        self.calls["session_days"] += 1
        return [dict(d) for d in self.days]

    def day_speeches(self, uuid):
        self.calls["day_speeches"] += 1
        return list(self.speeches.get(uuid, []))

    def speech_text(self, uuid):
        self.calls["speech_text"] += 1
        return {"html": self.texts.get(uuid, "")}

    def close(self):
        pass


def _day(uuid, date, sitting, duration):
    return {"uuid": uuid, "date": date, "datum_felirat": f"{date.replace('-', '.')}.({sitting})",
            "ules": 1, "ulesszak": 1, "day_in_session": sitting,
            "duration_s": duration, "debate_s": duration}


def _sp(uuid, dur, sorszam):
    return {"speech_uuid": uuid, "duration": dur, "sorszam": sorszam}


@pytest.fixture
def patched(tmp_path, monkeypatch):
    """A Paths + a stub scrape so no network/transform runs; returns a list that
    records the session ids scraped on each pass."""
    scraped = []

    def fake_scrape_day(felicitas, cycle, day, *, resolve_offsets=True):
        # Mirror the real scrape_day: attach each speech's text (empty until the
        # transcript is published) so the sync's has-text bookkeeping is exercised.
        speeches = [{**s, "text_html": felicitas.texts.get(s["speech_uuid"], "")}
                    for s in felicitas.day_speeches(day["uuid"])]
        return {"session": day["uuid"], "speeches": speeches,
                "video": {"m3u8": None, "playseq": None}}

    def fake_transform_day(bundle, *, words=None):
        # session id is derived by the caller; here just carry a valid shape.
        return {"meta": {"session": None, "timingMethod": "character"}, "data": []}

    real_sitting = sync.sitting_number

    def fake_scrape_recording(felicitas, cycle, day, *, resolve_offsets=True):
        scraped.append(sync.session_id(cycle, real_sitting(day)))
        return fake_scrape_day(felicitas, cycle, day, resolve_offsets=resolve_offsets)

    monkeypatch.setattr(sync, "scrape_day", fake_scrape_recording)
    monkeypatch.setattr(sync, "transform_day", fake_transform_day)
    paths = Paths(tmp_path)
    return paths, scraped


def _run(fel, paths):
    return sync.run_sync(fel, paths, 43, skip_bills=True, skip_votes=True,
                         skip_reps=True)


def test_first_poll_scrapes_all_then_idle_polls_scrape_nothing(patched):
    paths, scraped = patched
    fel = FakeFelicitas(
        days=[_day("u1", "2026-05-09", 1, 3600), _day("u2", "2026-05-16", 2, 1800)],
        speeches={"u1": [_sp("a", 10, 1)], "u2": [_sp("b", 20, 1)]})

    s1 = _run(fel, paths)
    assert sorted(scraped) == ["43001", "43002"]
    assert sorted(s1["sessions"]) == ["43001", "43002"]
    assert s1["changed"] is True

    scraped.clear()
    s2 = _run(fel, paths)
    assert scraped == []                 # nothing re-scraped
    assert s2["sessions"] == []
    assert s2["changed"] is False


def test_new_speeches_on_live_day_rescrape_only_that_day(patched):
    paths, scraped = patched
    fel = FakeFelicitas(
        days=[_day("u1", "2026-05-09", 1, 3600), _day("u2", "2026-05-16", 2, 1800)],
        speeches={"u1": [_sp("a", 10, 1)], "u2": [_sp("b", 20, 1)]})
    _run(fel, paths)

    # A speech is added to the latest (still-live) day u2 — duration unchanged.
    fel.speeches["u2"].append(_sp("c", 15, 2))
    fel.texts["c"] = "<p>új</p>"
    scraped.clear()
    s = _run(fel, paths)
    assert scraped == ["43002"]
    assert s["sessions"] == ["43002"]


def test_changed_duration_on_past_day_rescrapes_it(patched):
    paths, scraped = patched
    fel = FakeFelicitas(
        days=[_day("u1", "2026-05-09", 1, 3600), _day("u2", "2026-05-16", 2, 1800)],
        speeches={"u1": [_sp("a", 10, 1)], "u2": [_sp("b", 20, 1)]})
    _run(fel, paths)

    # An upstream correction lengthens the older sitting u1.
    fel.days[0]["duration_s"] = 4000
    scraped.clear()
    s = _run(fel, paths)
    assert scraped == ["43001"]
    assert s["sessions"] == ["43001"]


def test_idle_poll_only_makes_cheap_requests(patched):
    paths, scraped = patched
    fel = FakeFelicitas(
        days=[_day("u1", "2026-05-09", 1, 3600), _day("u2", "2026-05-16", 2, 1800)],
        speeches={"u1": [_sp("a", 10, 1)], "u2": [_sp("b", 20, 1)]})
    _run(fel, paths)

    fel.calls.clear()
    _run(fel, paths)
    # An idle poll over fully-published days: one list query + one speech-listing
    # fingerprint for the single live day — nothing per-speech, no text probe.
    assert fel.calls["session_days"] == 1
    assert fel.calls["day_speeches"] == 1
    assert fel.calls["speech_text"] == 0


def test_text_published_later_rescrapes_past_video_only_day(patched):
    """The text-lag fix: a past day scraped video-only is re-scraped when its
    transcript is published days later, though it is no longer the latest day."""
    paths, scraped = patched
    # u1 (older) has speeches but NO text yet; u2 (latest) is fully published.
    fel = FakeFelicitas(
        days=[_day("u1", "2026-05-09", 1, 3600), _day("u2", "2026-05-16", 2, 1800)],
        speeches={"u1": [_sp("a", 10, 1), _sp("a2", 10, 2)], "u2": [_sp("b", 20, 1)]},
        texts={"b": "<p>kész</p>"})     # only u2 has text
    _run(fel, paths)

    # An idle poll while u1 still has no text: u1 is re-checked cheaply (listing +
    # a single text probe) but not re-scraped, and u2 (latest) gets its listing.
    fel.calls.clear()
    scraped.clear()
    s_idle = _run(fel, paths)
    assert scraped == []
    assert s_idle["sessions"] == []
    assert fel.calls["day_speeches"] == 2        # u1 (incomplete) + u2 (latest)
    # u1 has 2 speeches; the probe samples distinct positions (first, last) and,
    # finding no text, gives up — a couple of cheap requests, not the whole day.
    assert fel.calls["speech_text"] == 2

    # The jegyzőkönyv is published for u1. Next poll re-scrapes exactly u1.
    fel.texts["a"] = "<p>a szöveg</p>"
    fel.texts["a2"] = "<p>a2 szöveg</p>"
    scraped.clear()
    s = _run(fel, paths)
    assert scraped == ["43001"]
    assert s["sessions"] == ["43001"]

    # Now that u1's text is in, it is complete: a further idle poll re-scrapes
    # nothing and no longer probes u1's text.
    fel.calls.clear()
    scraped.clear()
    _run(fel, paths)
    assert scraped == []
    assert fel.calls["speech_text"] == 0


def test_pre_textlag_state_backfills_has_text_no_mass_rescrape(patched):
    """Upgrade safety: a sync-state written before the text-lag fix has no
    ``has_text`` field; the first poll must backfill it from the raw files on disk
    (no network) rather than re-scraping every already-complete day."""
    paths, scraped = patched
    fel = FakeFelicitas(
        days=[_day("u1", "2026-05-09", 1, 3600), _day("u2", "2026-05-16", 2, 1800)],
        speeches={"u1": [_sp("a", 10, 1)], "u2": [_sp("b", 20, 1)]})
    _run(fel, paths)                      # both scraped; raw files carry text

    # Simulate an old sync-state: drop the new completeness fields.
    st = sync.load_state(paths.sync_state)
    for s in st["proceedings"].values():
        s.pop("has_text", None)
        s.pop("speech_count", None)
    sync.save_state(paths.sync_state, st)

    scraped.clear()
    fel.calls.clear()
    out = _run(fel, paths)
    assert scraped == []                  # nothing needlessly re-scraped
    assert out["sessions"] == []
    assert fel.calls["speech_text"] == 0  # complete days recognised, not probed


def test_announced_day_with_no_speeches_is_ingested_then_filled(patched):
    """A day parlament.hu lists but has no speeches yet (an upcoming sitting) is
    scraped as a placeholder, and re-scraped once its speeches appear."""
    paths, scraped = patched
    fel = FakeFelicitas(
        days=[_day("u1", "2026-05-09", 1, 3600), _day("u2", "2026-05-16", 2, 1800)],
        speeches={"u1": [_sp("a", 10, 1)], "u2": []},   # u2 announced, empty
        texts={"a": "<p>kész</p>"})
    s1 = _run(fel, paths)
    # The empty upcoming day is NOT dropped — it is ingested alongside u1.
    assert sorted(scraped) == ["43001", "43002"]
    assert sorted(s1["sessions"]) == ["43001", "43002"]

    # The sitting is held: speeches (with text) appear on u2 → re-scraped.
    fel.speeches["u2"] = [_sp("b", 20, 1)]
    fel.texts["b"] = "<p>elhangzott</p>"
    scraped.clear()
    s2 = _run(fel, paths)
    assert scraped == ["43002"]
    assert s2["sessions"] == ["43002"]


# --- office holders (tisztségviselők) --------------------------------------

class FakeOfficeFelicitas(FakeFelicitas):
    """Adds the office-holder listing, so the sync's own cadence/fingerprint
    bookkeeping for it can be driven without touching the network."""

    def __init__(self, rows, **kw):
        super().__init__(days=[], speeches={}, **kw)
        self.rows = rows

    def office_holders(self, *, as_of, earliest=None):
        self.calls["office_holders"] += 1
        return [dict(r) for r in self.rows]


def _run_offices(fel, paths, *, force=False):
    """A poll with only the office-holder step live (the other domains need their
    own fakes, and the reps refresh would reach out to Wikidata)."""
    return sync.run_sync(fel, paths, 43, skip_bills=True, skip_votes=True,
                         skip_reps=True, skip_advocates=True, force=force)


def _expire_office_cadence(paths):
    """Age the last office-holder check so the next poll re-fetches (the registry is
    refreshed on the slow representatives cadence)."""
    state = sync.load_state(paths.sync_state)
    state["officeHolders"]["ts"] = 0
    sync.save_state(paths.sync_state, state)


def _office_row(pid, title, tol, ig=None):
    return {"kepvId": pid, "nev": f"Név {pid}", "nevElonevNelkul": f"Név {pid}",
            "tisztseg": title, "tol": tol, "ig": ig}


def test_office_holders_written_once_then_only_when_they_change(patched):
    """The registry is fetched on the reps cadence and rewritten only when its
    contents actually changed — so an idle poll costs nothing and leaves the loader
    nothing to do, while a reshuffle (a term closed) lands."""
    paths, _ = patched
    fel = FakeOfficeFelicitas([_office_row("v076", "közlekedési és beruházási "
                                           "miniszter", "2026-05-12T22:00:00Z")])
    assert _run_offices(fel, paths)["officeHolders"] is True
    written = paths.officeholders_file()
    assert written.exists()
    first_mtime = written.stat().st_mtime_ns

    # Within the cadence: not even fetched.
    assert _run_offices(fel, paths)["officeHolders"] is False
    assert fel.calls["office_holders"] == 1

    # Cadence expired, registry unchanged: fetched, but not rewritten.
    _expire_office_cadence(paths)
    assert _run_offices(fel, paths)["officeHolders"] is False
    assert fel.calls["office_holders"] == 2
    assert written.stat().st_mtime_ns == first_mtime

    # A reshuffle: the office changes hands, so the file is rewritten.
    fel.rows = [_office_row("v076", "közlekedési és beruházási miniszter",
                            "2026-05-12T22:00:00Z", "2026-08-31T21:59:59Z")]
    _expire_office_cadence(paths)
    assert _run_offices(fel, paths)["officeHolders"] is True
    reg = json.loads(written.read_text())
    assert reg["data"][0]["offices"][0]["end"] == "2026-08-31T21:59:59Z"


def test_empty_office_registry_never_overwrites_what_is_held(patched):
    """An empty upstream answer must not clobber a registry already on disk (the
    loader would then forget every office term)."""
    paths, _ = patched
    fel = FakeOfficeFelicitas([_office_row("v076", "miniszter",
                                           "2026-05-12T22:00:00Z")])
    _run_offices(fel, paths)
    before = paths.officeholders_file().read_text()

    fel.rows = []
    _expire_office_cadence(paths)
    assert _run_offices(fel, paths)["officeHolders"] is False
    assert paths.officeholders_file().read_text() == before
