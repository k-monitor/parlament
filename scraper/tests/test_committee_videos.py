"""Offline tests for the committee recordings stage (BIZ-16).

The title is the *whole* of the join from a video to a committee meeting —
nothing in a video's metadata names the body or the sitting — so the title
parser is the part worth pinning, and the fixtures are the shapes the House's
own channel actually publishes.
"""

from __future__ import annotations

import json

from parlamonitor.committees import videos


FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
 <title>OrszággyűlésÉLŐ</title>
 <entry>
  <id>yt:video:aaa</id><yt:videoId>aaa</yt:videoId>
  <title>2026. szeptember 21. - A Művelődési Bizottság ülése</title>
  <published>2026-09-21T12:14:08+00:00</published>
  <updated>2026-09-21T13:54:29+00:00</updated>
  <media:group>
   <media:title>2026. szeptember 21. - A Művelődési Bizottság ülése</media:title>
   <media:thumbnail url="https://i3.ytimg.com/vi/aaa/hqdefault.jpg"/>
   <media:description>Meghallgatás</media:description>
   <media:community><media:statistics views="29916"/></media:community>
  </media:group>
 </entry>
 <entry>
  <id>yt:video:bbb</id><yt:videoId>bbb</yt:videoId>
  <title>2026. szeptember 21. - Az Országgyűlés ülésének élő közvetítése</title>
  <published>2026-09-18T10:04:52+00:00</published>
  <media:group>
   <media:title>2026. szeptember 21. - Az Országgyűlés ülésének élő közvetítése</media:title>
  </media:group>
 </entry>
</feed>
"""


class FakeHttp:
    """Answers the feed and counts the requests; never touches the network."""

    def __init__(self, text=FEED):
        self.text = text
        self.calls = []

    def get_text(self, url, **kw):
        self.calls.append(url)
        return self.text

    def polite_sleep(self):
        pass


def test_title_carries_the_date_the_body_and_the_continuation():
    p = videos.parse_title("2026. szeptember 21. - A Művelődési Bizottság ülése")
    assert (p["date"], p["kind"]) == ("2026-09-21", "committee")
    assert p["committeeLabel"] == "A Művelődési Bizottság"
    assert p["continued"] is False

    # "Folytatás" is its own dash-delimited segment, before the body's name.
    c = videos.parse_title("2026. szeptember 21. - Folytatás - "
                           "A Médiatanács elnökét és tagjait jelölő eseti bizottság ülése")
    assert c["continued"] is True
    assert c["committeeLabel"] == \
        "A Médiatanács elnökét és tagjait jelölő eseti bizottság"
    assert c["date"] == "2026-09-21"


def test_title_variants_the_channel_actually_publishes():
    # A trailing dash left dangling by a truncated title.
    t = videos.parse_title(
        "2026. augusztus 27. - Az Igazságügyi és Alkotmányügyi Bizottság ülése -")
    assert t["committeeLabel"] == "Az Igazságügyi és Alkotmányügyi Bizottság"
    # The dot after the year is sometimes missing.
    assert videos.parse_title(
        "2025 február 25. - Az Országgyűlés plenáris ülésének élő közvetítése"
    )["date"] == "2025-02-25"


def test_a_plenary_broadcast_is_not_a_committee():
    for title in ("2026. szeptember 21. - Az Országgyűlés ülésének élő közvetítése",
                  "2026. március 3. - Az Országgyűlés rendkívüli plenáris "
                  "ülésének élő közvetítése"):
        p = videos.parse_title(title)
        assert p["kind"] == "plenary"
        assert p["committeeLabel"] is None


def test_a_title_off_the_pattern_is_kept_rather_than_dropped():
    """The channel is small enough that an unrecognised video is worth seeing
    in the registry (SCR-5)."""
    p = videos.parse_title("EU pályázati díjkiosztó ünnepség az Országházban")
    assert (p["kind"], p["date"], p["committeeLabel"]) == ("other", None, None)
    assert p["title"].startswith("EU pályázati")


def test_feed_is_read_into_video_rows():
    rows = videos.parse_feed(FEED)
    assert [r["videoId"] for r in rows] == ["aaa", "bbb"]
    assert rows[0]["url"] == "https://www.youtube.com/watch?v=aaa"
    assert rows[0]["views"] == 29916
    assert rows[0]["thumbnail"].endswith("hqdefault.jpg")
    assert rows[0]["kind"] == "committee"
    assert rows[1]["kind"] == "plenary"


def test_a_broken_feed_yields_nothing_rather_than_raising():
    assert videos.parse_feed("<not xml") == []


def test_an_rss_pass_never_loses_what_a_backfill_found():
    """A cheap poll sees the newest 15; the history a backfill paid for has to
    survive it, and the fields RSS cannot supply must not be blanked."""
    previous = {"data": [
        {"videoId": "old", "title": "2024. március 4. - A Mentelmi Bizottság ülése",
         "date": "2024-03-04", "kind": "committee", "durationS": 900,
         "committeeLabel": "A Mentelmi Bizottság", "continued": False},
        {"videoId": "aaa", "title": "2026. szeptember 21. - A Művelődési Bizottság ülése",
         "date": "2026-09-21", "kind": "committee", "durationS": 10910,
         "committeeLabel": "A Művelődési Bizottság", "continued": False},
    ]}
    http = FakeHttp()
    out = videos.fetch_videos(http, previous=previous)
    held = {v["videoId"]: v for v in out["data"]}
    assert set(held) == {"old", "aaa", "bbb"}
    # The feed carries no duration; the backfilled one stands.
    assert held["aaa"]["durationS"] == 10910
    assert held["aaa"]["views"] == 29916          # and the fresher view count wins
    assert held["old"]["durationS"] == 900
    # Newest first, so the registry reads the way the channel does.
    assert [v["videoId"] for v in out["data"]][0] in ("aaa", "bbb")
    assert out["meta"]["counts"]["committee"] == 2
    assert out["meta"]["earliestVideo"] == "2024-03-04"
    assert http.calls == [videos.FEED_URL % videos.CHANNEL_ID]


def test_backfill_without_yt_dlp_or_a_key_says_so(monkeypatch):
    """SCR-6: a backfill that silently did nothing would look like a channel
    with no history."""
    monkeypatch.setattr(videos, "yt_dlp_available", lambda: False)
    monkeypatch.setattr(videos, "api_key", lambda: None)
    out = videos.fetch_videos(FakeHttp(), backfill=True)
    assert out["meta"]["backfilled"] is False
    assert any("yt-dlp" in e for e in out["meta"]["errors"])
    # The feed still landed — the cheap half of the pass is unaffected.
    assert out["meta"]["count"] == 2


def test_a_failed_feed_does_not_fail_the_pass(monkeypatch):
    class Broken(FakeHttp):
        def get_text(self, url, **kw):
            raise OSError("connection reset")
    out = videos.fetch_videos(Broken(), previous={"data": [
        {"videoId": "old", "date": "2024-03-04", "kind": "committee",
         "committeeLabel": "A Mentelmi Bizottság", "continued": False}]})
    assert out["meta"]["errors"] and "rss" in out["meta"]["errors"][0]
    # What was already held is still held.
    assert [v["videoId"] for v in out["data"]] == ["old"]


def test_backfill_reads_yt_dlp_s_flat_playlist(monkeypatch):
    lines = "\n".join(json.dumps(r) for r in [
        {"id": "zzz", "title": "2024. február 26. - A Mentelmi Bizottság ülése",
         "webpage_url": "https://www.youtube.com/watch?v=zzz",
         "duration": 1800, "view_count": 12, "timestamp": 1708900000,
         "thumbnails": [{"url": "https://i.ytimg.com/vi/zzz/default.jpg"}]},
        {"title": "no id at all"},
    ])

    class P:
        stdout = lines.encode("utf-8")
        stderr = b""
        returncode = 0

    monkeypatch.setattr(videos, "yt_dlp_available", lambda: True)
    monkeypatch.setattr(videos.subprocess, "run", lambda *a, **kw: P())
    rows = videos.backfill_yt_dlp(videos.CHANNEL_ID)
    assert [r["videoId"] for r in rows] == ["zzz"]
    assert rows[0]["durationS"] == 1800
    assert rows[0]["date"] == "2024-02-26"
    assert rows[0]["publishedAt"].startswith("2024-")
