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
* a day whose video is only **partly segmented** (text complete, but the tail of
  its speeches untimed) is chased the same way until upstream finishes it, and
  given up on after ``MEDIA_CHASE_DAYS`` (the media-lag fix);
* an **announced day with no speeches** is ingested (a scheduled placeholder), not
  dropped.
"""

from __future__ import annotations

import collections
from datetime import datetime, timedelta, timezone
import json

import pytest

from parlamonitor import config, sync
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

    def day_speech_roster(self, uuid):
        # What the change probe asks for: the flat listing, one request, without
        # the per-act fan-out a full day listing now costs.
        self.calls["day_speech_roster"] += 1
        return [{k: v for k, v in s.items() if k not in ("aktus", "aktus_id")}
                for s in self.speeches.get(uuid, [])]

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

    def fake_scrape_day(felicitas, cycle, day, *, resolve_offsets=True,
                        titles=None):
        # Mirror the real scrape_day: attach each speech's text (empty until the
        # transcript is published) so the sync's has-text bookkeeping is exercised,
        # and carry the day's identity (date + upstream uuid) the raw file is keyed
        # and cross-checked on (renumbering guard / cancelled-sitting prune).
        speeches = [{**s, "text_html": felicitas.texts.get(s["speech_uuid"], "")}
                    for s in felicitas.day_speeches(day["uuid"])]
        return {"session": day["uuid"], "date": day.get("date"),
                "day_uuid": day["uuid"], "speeches": speeches,
                "video": {"m3u8": None, "playseq": None}}

    def fake_transform_day(bundle, *, words=None):
        # session id is derived by the caller; here just carry a valid shape.
        return {"meta": {"session": None, "timingMethod": "character"}, "data": []}

    real_sitting = sync.sitting_number

    def fake_scrape_recording(felicitas, cycle, day, *, resolve_offsets=True,
                              titles=None):
        scraped.append(sync.session_id(cycle, real_sitting(day)))
        return fake_scrape_day(felicitas, cycle, day,
                               resolve_offsets=resolve_offsets, titles=titles)

    monkeypatch.setattr(sync, "scrape_day", fake_scrape_recording)
    monkeypatch.setattr(sync, "transform_day", fake_transform_day)
    paths = Paths(tmp_path)
    return paths, scraped


def _run(fel, paths):
    return sync.run_sync(fel, paths, 43, skip_bills=True, skip_votes=True,
                         skip_committees=True,
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
    assert fel.calls["day_speech_roster"] == 1
    assert fel.calls["day_speeches"] == 0
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
    assert fel.calls["day_speech_roster"] == 2   # u1 (incomplete) + u2 (latest)
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


# --- cancelled sittings -----------------------------------------------------
#
# parlament.hu announces sittings ahead of time and sometimes calls one off: the
# day disappears from the day list and its ülésnap number is handed to the day
# announced in its place (2026-08: the announced 22./23. ülésnap of 08-03/08-04
# went away, 08-10/08-11 became 22./23.). Both halves have to work — drop the day
# that is gone, and scrape the one that inherited its number.

def _announced_pair(paths, scraped):
    """A poll where 43001 is held and 43002/43003 are announced upcoming days."""
    fel = FakeFelicitas(
        days=[_day("u1", "2026-05-09", 1, 3600),
              _day("cancel-a", "2026-05-25", 2, None),
              _day("cancel-b", "2026-05-26", 3, None)],
        speeches={"u1": [_sp("a", 10, 1)], "cancel-a": [], "cancel-b": []},
        texts={"a": "<p>kész</p>"})
    _run(fel, paths)
    assert sorted(scraped) == ["43001", "43002", "43003"]
    scraped.clear()
    return fel


def test_cancelled_sitting_is_pruned_and_its_number_reused(patched):
    paths, scraped = patched
    fel = _announced_pair(paths, scraped)

    # Both announced days are called off; two new ones are announced a week later
    # and take over ülésnap 2 and 3.
    fel.days = [_day("u1", "2026-05-09", 1, 3600),
                _day("moved-a", "2026-06-01", 2, None),
                _day("moved-b", "2026-06-02", 3, None)]
    fel.speeches.update({"moved-a": [], "moved-b": []})

    s = _run(fel, paths)

    assert sorted(s["removedSessions"]) == ["43002", "43003"]
    # The freed numbers are scraped in the SAME pass — the renumbering guard no
    # longer sees a foreign date holding the key.
    assert sorted(scraped) == ["43002", "43003"]
    assert sorted(s["sessions"]) == ["43002", "43003"]
    assert s["changed"] is True
    held = {json.loads(paths.raw_day(sid).read_text())["date"]
            for sid in ("43002", "43003")}
    assert held == {"2026-06-01", "2026-06-02"}
    # The cancelled days are no longer remembered as scraped either.
    assert set(sync.load_state(paths.sync_state)["proceedings"]) == {
        "43001", "43002", "43003"}


def test_cancelled_sitting_with_no_replacement_leaves_no_files(patched):
    """The plain cancellation: nothing takes the number over, so the files stay
    deleted and the loader's next update drops the row (the site stops showing it)."""
    paths, scraped = patched
    fel = _announced_pair(paths, scraped)

    fel.days = [_day("u1", "2026-05-09", 1, 3600)]

    s = _run(fel, paths)

    assert sorted(s["removedSessions"]) == ["43002", "43003"]
    assert scraped == []
    assert not paths.raw_day("43002").exists()
    assert not paths.session_file("43002").exists()
    assert paths.raw_day("43001").exists()


def test_a_held_sitting_is_never_pruned(patched):
    """A day that already has speeches vanishing from the listing is an upstream
    glitch, not a cancellation: the archive is kept."""
    paths, scraped = patched
    fel = FakeFelicitas(
        days=[_day("u1", "2026-05-09", 1, 3600), _day("u2", "2026-05-16", 2, 1800)],
        speeches={"u1": [_sp("a", 10, 1)], "u2": [_sp("b", 20, 1)]})
    _run(fel, paths)
    scraped.clear()

    fel.days = [_day("u2", "2026-05-16", 2, 1800)]     # u1 drops out of the list

    s = _run(fel, paths)

    assert s["removedSessions"] == []
    assert paths.raw_day("43001").exists()


def test_rescheduled_day_keeps_its_number(patched):
    """An announced day moved in place (same upstream uuid, new date) is the same
    sitting — it is re-scraped under its own key, not refused as a renumbering."""
    paths, scraped = patched
    fel = FakeFelicitas(
        days=[_day("u1", "2026-05-09", 1, 3600), _day("u2", "2026-05-25", 2, None)],
        speeches={"u1": [_sp("a", 10, 1)], "u2": []},
        texts={"a": "<p>kész</p>"})
    _run(fel, paths)
    scraped.clear()

    fel.days[1] = _day("u2", "2026-06-01", 2, None)    # same uuid, later date

    s = _run(fel, paths)

    assert s["removedSessions"] == []
    assert scraped == ["43002"]
    assert json.loads(paths.raw_day("43002").read_text())["date"] == "2026-06-01"


# --- partly-segmented video (media lag) ------------------------------------
#
# The mirror image of the text lag: parlament.hu also publishes the PER-SPEECH
# video timings in instalments. 2026-07-27 arrived with its transcript complete but
# only the first 110 of 166 speeches timed — and because "done" was judged on text
# alone, that day was frozen half-timed the moment a newer sitting appeared.

def _recent(days_ago: int) -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=days_ago)).isoformat()


def _partly_timed_pair(untimed_dur=None, old=False):
    """A two-day fake where the OLDER day (u1, never the latest) has all its text
    but an untimed tail, and u2 is complete. ``old`` dates u1 past MEDIA_CHASE_DAYS."""
    d1 = _recent(sync.MEDIA_CHASE_DAYS + 5) if old else _recent(3)
    return FakeFelicitas(
        days=[_day("u1", d1, 1, 3600), _day("u2", _recent(2), 2, 1800)],
        speeches={"u1": [_sp("a", 10, 1), _sp("a2", untimed_dur, 2)],
                  "u2": [_sp("b", 20, 1)]})


def test_partly_timed_day_is_chased_until_the_rest_is_timed(patched):
    """A past day whose tail has no per-speech timing keeps being re-listed, and is
    re-scraped as soon as upstream segments the rest — even though its text (the
    old completeness signal) has been in all along."""
    paths, scraped = patched
    fel = _partly_timed_pair()
    _run(fel, paths)

    # Idle poll: u1 is still short of its timings, so it is re-listed (one cheap
    # request) — and NOT text-probed, since its transcript is already in.
    fel.calls.clear()
    scraped.clear()
    assert _run(fel, paths)["sessions"] == []
    assert scraped == []
    assert fel.calls["day_speech_roster"] == 2   # u1 (untimed tail) + u2 (latest)
    assert fel.calls["speech_text"] == 0

    # Upstream finishes segmenting u1's recording → exactly u1 is re-scraped.
    fel.speeches["u1"][1]["duration"] = 12
    scraped.clear()
    assert _run(fel, paths)["sessions"] == ["43001"]
    assert scraped == ["43001"]

    # Complete on both counts now: the next poll leaves it alone entirely.
    fel.calls.clear()
    scraped.clear()
    _run(fel, paths)
    assert scraped == []
    assert fel.calls["day_speech_roster"] == 1   # only u2, the latest day


def test_fully_timed_day_is_not_re_listed(patched):
    """The control: when every speech is timed, a past day costs no request at all
    — the chase must not turn every finished day into a per-poll re-listing."""
    paths, scraped = patched
    fel = _partly_timed_pair(untimed_dur=10)
    _run(fel, paths)

    fel.calls.clear()
    _run(fel, paths)
    assert fel.calls["day_speech_roster"] == 1   # only u2, the latest day


def test_long_unfinished_day_stops_being_chased(patched):
    """A day upstream never finished segmenting is given up on after
    MEDIA_CHASE_DAYS, so it doesn't cost a request on every poll forever (SCR-4)."""
    paths, scraped = patched
    fel = _partly_timed_pair(old=True)
    _run(fel, paths)

    fel.calls.clear()
    _run(fel, paths)
    assert fel.calls["day_speech_roster"] == 1   # only u2; u1 aged out of the chase


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
                         skip_committees=True,
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


# --- the document mirror in a sync pass (DOC-1) -----------------------------
# The live server runs `sync`, and mirroring the iromány files would add ~870 MB
# per cycle to its disk. So the one thing that must hold unconditionally is that
# a sync configured no differently than before writes no documents at all.

def test_sync_mirrors_no_documents_by_default(patched, monkeypatch):
    paths, _ = patched
    monkeypatch.delenv("PARLAMONITOR_DOCUMENTS", raising=False)
    called = []
    monkeypatch.setattr(sync, "fetch_documents",
                        lambda *a, **kw: called.append(a) or {})
    fel = FakeFelicitas(days=[_day("u1", "2026-05-09", 1, 3600)],
                        speeches={"u1": [_sp("a", 10, 1)]})
    summary = _run(fel, paths)
    assert called == []
    assert summary["documents"] is False
    assert not (paths.data / "documents").exists()


def test_sync_runs_the_mirror_once_asked(patched, monkeypatch):
    paths, _ = patched
    monkeypatch.setattr(sync, "load_registry", lambda p, c: {"data": []})
    seen = {}
    monkeypatch.setattr(sync, "fetch_documents",
                        lambda http, p, c, reg, **kw: seen.update(kw) or {"fetched": 0})
    fel = FakeFelicitas(days=[_day("u1", "2026-05-09", 1, 3600)],
                        speeches={"u1": [_sp("a", 10, 1)]})
    fel.http = object()          # the mirror fetches over the client's transport
    summary = sync.run_sync(fel, paths, 43, skip_bills=True, skip_votes=True,
                         skip_committees=True,
                            skip_reps=True, documents="text")
    assert summary["documents"] == {"fetched": 0}
    assert seen["retention"] == "text" and seen["compression"] == "xz"


def test_a_sync_pass_is_budgeted_so_the_first_one_cannot_run_away(patched,
                                                                  monkeypatch):
    """Switching the mirror on faces the whole cycle's backlog, not the two
    irományok that arrived since the last poll. A pass takes a slice of it and
    leaves the rest to the next one, so it never holds the sync lock (or the
    politeness budget) for a 15-minute download run."""
    paths, _ = patched
    monkeypatch.delenv("PARLAMONITOR_DOCUMENTS_PER_SYNC", raising=False)
    monkeypatch.setattr(sync, "load_registry", lambda p, c: {"data": []})
    seen = {}
    monkeypatch.setattr(sync, "fetch_documents",
                        lambda http, p, c, reg, **kw: seen.update(kw) or {"fetched": 0})
    fel = FakeFelicitas(days=[], speeches={})
    fel.http = object()

    sync.run_sync(fel, paths, 43, skip_bills=True, skip_votes=True,
                         skip_committees=True,
                  skip_reps=True, documents="text")
    assert seen["limit"] == config.DEFAULT_DOCUMENTS_PER_SYNC

    monkeypatch.setenv("PARLAMONITOR_DOCUMENTS_PER_SYNC", "7")
    sync.run_sync(fel, paths, 43, skip_bills=True, skip_votes=True,
                         skip_committees=True,
                  skip_reps=True, documents="text")
    assert seen["limit"] == 7

    # An explicit argument wins over the environment, and 0 means "no cap" —
    # which reaches the stage as None, its own word for unlimited.
    sync.run_sync(fel, paths, 43, skip_bills=True, skip_votes=True,
                         skip_committees=True,
                  skip_reps=True, documents="text", documents_limit=0)
    assert seen["limit"] is None


def test_a_failing_mirror_never_sinks_the_sync(patched, monkeypatch):
    """SCR-5: the document stage is the newest and least essential thing in the
    pass; a missing registry or a broken fetch must cost the run a log line, not
    the sittings it just scraped."""
    paths, scraped = patched
    def boom(*a, **kw):
        raise FileNotFoundError("bills-43.json not found")
    monkeypatch.setattr(sync, "load_registry", boom)
    fel = FakeFelicitas(days=[_day("u1", "2026-05-09", 1, 3600)],
                        speeches={"u1": [_sp("a", 10, 1)]})
    summary = sync.run_sync(fel, paths, 43, skip_bills=True, skip_votes=True,
                         skip_committees=True,
                            skip_reps=True, documents="text")
    assert summary["sessions"] == ["43001"]
    assert any("documents:" in e for e in summary["errors"])


# --- the committee minutes / recordings stages in a sync pass (BIZ-15/16) ---

class _FakeSyncHttp:
    """The HTTP half of a Felicitas client: serves the YouTube feed and the
    minutes PDFs, and counts what was asked for."""

    def __init__(self, feed="<feed/>", pdfs=None):
        self.feed = feed
        self.pdfs = pdfs or {}
        self.calls = collections.Counter()

    def get_text(self, url, **kw):
        self.calls["feed"] += 1
        return self.feed

    def get_capped(self, url, **kw):
        self.calls["pdf"] += 1
        body = self.pdfs.get(url)

        class F:
            over_cap = False
            data = body
            status = 200 if body else 404
            content_type = "application/pdf"
        return F()

    def polite_sleep(self):
        pass


def _committees_file(paths, meetings):
    import json
    paths.processed.mkdir(parents=True, exist_ok=True)
    paths.committees_file(43).write_text(json.dumps(
        {"meta": {"cycle": 43}, "data": [], "meetings": meetings}),
        encoding="utf-8")


def test_minutes_sync_skips_a_cycle_with_no_registry(tmp_path):
    """The minutes hang off the committee registry; without one there is
    nothing to fetch and the stage must not invent work."""
    paths = Paths(tmp_path)
    http = _FakeSyncHttp()
    assert sync._sync_committee_minutes(http, paths, 43, {}, force=False,
                                        limit=10) is False
    assert http.calls["pdf"] == 0


def test_minutes_sync_repairs_the_url_and_reuses_the_stored_text(tmp_path,
                                                                 monkeypatch):
    """Two things at once: the missing-slash URL from an older registry is
    fetched correctly, and a second pass re-parses from disk without asking
    upstream for anything (SCR-2)."""
    paths = Paths(tmp_path)
    paths.ensure()
    _committees_file(paths, [
        {"meetingId": "m1", "committeeId": "c1", "committeeName": "X Bizottság",
         "datetime": "2026-06-10T09:00:00Z",
         # As cycle 40 and part of 41 come back: no slash after the host.
         "minutesUrl": "https://www.parlament.hubiz40/bizjkv40/VFB/1.pdf"},
        {"meetingId": "m2", "committeeId": "c1", "committeeName": "X Bizottság",
         "datetime": "2026-06-11T09:00:00Z", "minutesUrl": None}])
    fixed = "https://www.parlament.hu/biz40/bizjkv40/VFB/1.pdf"
    http = _FakeSyncHttp(pdfs={fixed: b"%PDF-1.7 ..."})
    monkeypatch.setattr(sync, "fetch_minutes", sync.fetch_minutes)
    from parlamonitor.committees import minutes_scrape
    monkeypatch.setattr(minutes_scrape, "pdftotext_available", lambda: True)
    monkeypatch.setattr(minutes_scrape, "extract_layout_text",
                        lambda pdf, **kw: "       ELNÖK: Megnyitom az ülést.\n")

    state = {}
    first = sync._sync_committee_minutes(http, paths, 43, state, force=False,
                                         limit=10)
    assert first["fetched"] == 1 and first["speeches"] == 1
    # The meeting with no published minutes is not fetched at all (BIZ-9).
    assert http.calls["pdf"] == 1
    assert paths.committee_minutes_file(43).exists()

    # Second pass: nothing new upstream, nothing re-fetched, nothing rewritten.
    assert sync._sync_committee_minutes(http, paths, 43, state, force=False,
                                        limit=10) is False
    assert http.calls["pdf"] == 1


def test_minutes_sync_caps_how_much_one_pass_fetches(tmp_path, monkeypatch):
    """SCR-4: the first pass after a cycle is added must not become a
    thousand-request scrape. What is left over is counted, not forgotten."""
    paths = Paths(tmp_path)
    paths.ensure()
    urls = {}
    meetings = []
    for i in range(5):
        u = f"https://www.parlament.hu/biz43/x/{i}.pdf"
        urls[u] = b"%PDF-1.7 ..."
        meetings.append({"meetingId": f"m{i}", "committeeId": "c1",
                         "committeeName": "X", "datetime": "2026-06-10T09:00:00Z",
                         "minutesUrl": u})
    _committees_file(paths, meetings)
    http = _FakeSyncHttp(pdfs=urls)
    from parlamonitor.committees import minutes_scrape
    monkeypatch.setattr(minutes_scrape, "pdftotext_available", lambda: True)
    monkeypatch.setattr(minutes_scrape, "extract_layout_text",
                        lambda pdf, **kw: "       ELNÖK: Megnyitom.\n")
    out = sync._sync_committee_minutes(http, paths, 43, {}, force=False, limit=2)
    assert (out["fetched"], out["pending"]) == (2, 3)
    assert http.calls["pdf"] == 2


def test_video_sync_is_one_request_and_rewrites_only_on_a_change(tmp_path):
    paths = Paths(tmp_path)
    paths.ensure()
    feed = ("<?xml version='1.0' encoding='UTF-8'?>"
            "<feed xmlns='http://www.w3.org/2005/Atom' "
            "xmlns:yt='http://www.youtube.com/xml/schemas/2015'>"
            "<entry><yt:videoId>aaa</yt:videoId>"
            "<title>2026. június 10. - A Költségvetési Bizottság ülése</title>"
            "<published>2026-06-10T09:00:00+00:00</published></entry></feed>")
    http = _FakeSyncHttp(feed=feed)
    state = {}
    out = sync._sync_committee_videos(http, paths, state, force=False)
    assert out["committee"] == 1
    assert paths.committee_videos_file().exists()
    assert http.calls["feed"] == 1
    # Unchanged channel: read again (it is one request), rewritten never.
    mtime = paths.committee_videos_file().stat().st_mtime_ns
    assert sync._sync_committee_videos(http, paths, state, force=False) is False
    assert paths.committee_videos_file().stat().st_mtime_ns == mtime
    assert http.calls["feed"] == 2
