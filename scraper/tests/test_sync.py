"""Offline tests for the continuous-sync change probe (`parlamonitor.sync`).

The point of the sync watcher is to cost almost nothing when parlament.hu hasn't
changed and to re-scrape only what did. These tests drive ``run_sync`` against a
fake Felicitas client (no network), stubbing the heavy per-day scrape so we can
assert *which* sittings get re-scraped on each poll:

* first poll scrapes every day;
* an unchanged poll scrapes nothing (and only spends the cheap list + one
  speech-listing request);
* a new/longer sitting, or new speeches on the still-live latest sitting, re-scrape
  exactly that one day.
"""

from __future__ import annotations

import collections

import pytest

from parlamonitor import sync
from parlamonitor.config import Paths


class FakeFelicitas:
    def __init__(self, days, speeches):
        self.days = [dict(d) for d in days]
        self.speeches = speeches            # day uuid -> list of speech rows
        self.calls = collections.Counter()

    def cycle_ranges(self):
        return {43: {"start": "2026-05-01", "end": None}}

    def session_days(self, cycle, start, end):
        self.calls["session_days"] += 1
        return [dict(d) for d in self.days]

    def day_speeches(self, uuid):
        self.calls["day_speeches"] += 1
        return list(self.speeches.get(uuid, []))

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
        return {"session": day["uuid"], "speeches": felicitas.day_speeches(day["uuid"])}

    def fake_transform_day(bundle, *, words=None):
        # session id is derived by the caller; here just carry a valid shape.
        return {"meta": {"session": None, "timingMethod": "character"}, "data": []}

    # Record which sitting the caller actually wrote by wrapping _write_json's
    # processed target — simplest is to intercept scrape_day per (cycle, sitting).
    real_sitting = sync.sitting_number

    def fake_scrape_recording(felicitas, cycle, day, *, resolve_offsets=True):
        scraped.append(sync.session_id(cycle, real_sitting(day)))
        return fake_scrape_day(felicitas, cycle, day, resolve_offsets=resolve_offsets)

    monkeypatch.setattr(sync, "scrape_day", fake_scrape_recording)
    monkeypatch.setattr(sync, "transform_day", fake_transform_day)
    paths = Paths(tmp_path)
    return paths, scraped


def test_first_poll_scrapes_all_then_idle_polls_scrape_nothing(patched):
    paths, scraped = patched
    fel = FakeFelicitas(
        days=[_day("u1", "2026-05-09", 1, 3600), _day("u2", "2026-05-16", 2, 1800)],
        speeches={"u1": [_sp("a", 10, 1)], "u2": [_sp("b", 20, 1)]})

    s1 = sync.run_sync(fel, paths, 43, skip_bills=True, skip_votes=True, skip_reps=True)
    assert sorted(scraped) == ["43001", "43002"]
    assert sorted(s1["sessions"]) == ["43001", "43002"]
    assert s1["changed"] is True

    scraped.clear()
    s2 = sync.run_sync(fel, paths, 43, skip_bills=True, skip_votes=True, skip_reps=True)
    assert scraped == []                 # nothing re-scraped
    assert s2["sessions"] == []
    assert s2["changed"] is False


def test_new_speeches_on_live_day_rescrape_only_that_day(patched):
    paths, scraped = patched
    fel = FakeFelicitas(
        days=[_day("u1", "2026-05-09", 1, 3600), _day("u2", "2026-05-16", 2, 1800)],
        speeches={"u1": [_sp("a", 10, 1)], "u2": [_sp("b", 20, 1)]})
    sync.run_sync(fel, paths, 43, skip_bills=True, skip_votes=True, skip_reps=True)

    # A speech is added to the latest (still-live) day u2 — duration unchanged.
    fel.speeches["u2"].append(_sp("c", 15, 2))
    scraped.clear()
    s = sync.run_sync(fel, paths, 43, skip_bills=True, skip_votes=True, skip_reps=True)
    assert scraped == ["43002"]
    assert s["sessions"] == ["43002"]


def test_changed_duration_on_past_day_rescrapes_it(patched):
    paths, scraped = patched
    fel = FakeFelicitas(
        days=[_day("u1", "2026-05-09", 1, 3600), _day("u2", "2026-05-16", 2, 1800)],
        speeches={"u1": [_sp("a", 10, 1)], "u2": [_sp("b", 20, 1)]})
    sync.run_sync(fel, paths, 43, skip_bills=True, skip_votes=True, skip_reps=True)

    # An upstream correction lengthens the older sitting u1.
    fel.days[0]["duration_s"] = 4000
    scraped.clear()
    s = sync.run_sync(fel, paths, 43, skip_bills=True, skip_votes=True, skip_reps=True)
    assert scraped == ["43001"]
    assert s["sessions"] == ["43001"]


def test_idle_poll_only_makes_cheap_requests(patched):
    paths, scraped = patched
    fel = FakeFelicitas(
        days=[_day("u1", "2026-05-09", 1, 3600), _day("u2", "2026-05-16", 2, 1800)],
        speeches={"u1": [_sp("a", 10, 1)], "u2": [_sp("b", 20, 1)]})
    sync.run_sync(fel, paths, 43, skip_bills=True, skip_votes=True, skip_reps=True)

    fel.calls.clear()
    sync.run_sync(fel, paths, 43, skip_bills=True, skip_votes=True, skip_reps=True)
    # An idle proceedings poll: one list query + one speech-listing fingerprint
    # for the single live day — nothing per-speech.
    assert fel.calls["session_days"] == 1
    assert fel.calls["day_speeches"] == 1
