"""Read the Országgyűlés YouTube channel for committee recordings (BIZ-16).

A committee meeting leaves two records: the jegyzőkönyv, published weeks later
as a PDF (BIZ-15), and the **live stream**, which is up the same day. The House
streams both plenary sittings and committee meetings to one channel,
``OrszággyűlésÉLŐ``, and titles each video to a fixed pattern that carries
everything needed to place it:

    2026. szeptember 21. - A Művelődési Bizottság ülése
    2026. szeptember 21. - Folytatás - A Médiatanács …jelölő eseti bizottság ülése
    2026. szeptember 21. - Az Országgyűlés ülésének élő közvetítése

— a date, an optional "Folytatás" marking the second half of a sitting that
overran, and the body it belongs to. That is the whole of the linkage: there is
no id shared with the committee registry and nothing in the video's metadata
names the meeting, so **the title is the only join** and it is parsed here into
a date and a committee label. Matching those to a body and a meeting is the
loader's, not this module's (it is the side that holds both).

**Two sources, because the cheap one is a window, not an archive.**

* The **RSS feed** (``/feeds/videos.xml?channel_id=…``) is one unauthenticated
  request and needs no API key, but YouTube serves only the **15 newest**
  videos in it. For a daily poll that is ample — the House streams a handful of
  things a week — and it is what a sync pass uses.
* **Backfill** walks the channel's whole upload history, which the feed cannot
  reach. It prefers ``yt-dlp`` (present on the box, no key, no quota) and falls
  back to the **YouTube Data API v3** when a key is configured. Measured on
  2026-09-21 the channel holds **166 videos** in total, back to 2024-02-26 —
  and that is genuinely all of it, not a paging limit: the uploads playlist is
  exactly the union of the channel's *videos* (7) and *streams* (159) tabs.

So the ceiling on backfilling committee recordings is **the channel itself**.
Of those 166 videos, 30 are committee meetings; the rest are plenary sittings
and one-off ceremonies. Nothing before 2024-02 was ever posted, so cycles 40 and
41 have no recordings to find and cycle 42 only its last two years. That is a
fact about the source, not a gap in this stage, and the registry records the
channel's own first upload so the site can say so rather than implying the
House simply did not meet.

Passes are **additive**: a plain RSS poll merges its 15 into whatever the last
run knew, so backfilled history is never lost by a cheap refresh (and a video
deleted upstream keeps its row, flagged, rather than silently vanishing).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from xml.etree import ElementTree

from ..config import Paths
from ..http_client import HttpClient

logger = logging.getLogger(__name__)

SOURCE = "youtube-orszaggyules-elo"
# OrszággyűlésÉLŐ — the House's own channel, the only one that streams committee
# meetings.
CHANNEL_ID = "UCz4RJ6wkXoc3iG3cTxl5sOQ"
FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id=%s"
# A channel's uploads playlist is its id with the "UC" prefix swapped for "UU".
# Asking for the playlist rather than the channel page is what makes one request
# return the whole history in upload order.
UPLOADS_PLAYLIST = "https://www.youtube.com/playlist?list=UU%s"
WATCH_URL = "https://www.youtube.com/watch?v=%s"
API_PLAYLIST_ITEMS = "https://www.googleapis.com/youtube/v3/playlistItems"

_ATOM = {"a": "http://www.w3.org/2005/Atom",
         "yt": "http://www.youtube.com/xml/schemas/2015",
         "media": "http://search.yahoo.com/mrss/"}

MONTHS = {
    "január": 1, "február": 2, "március": 3, "április": 4, "május": 5,
    "június": 6, "július": 7, "augusztus": 8, "szeptember": 9, "október": 10,
    "november": 11, "december": 12,
}

# "2026. szeptember 21. - <what it is>". The dot after the year is missing on a
# handful of videos ("2025 február 25. - …"), and the dash is sometimes an en or
# em dash, so neither is required as written.
_TITLE_RE = re.compile(
    r"^(\d{4})\.?\s+([a-záéíóöőúüű]+)\s+(\d{1,2})\.\s*[-–—]\s*(.+)$")
# "Folytatás" — the second video of a sitting that ran past one stream. It
# appears as its own dash-delimited segment, before or after the body's name.
_CONTINUED_RE = re.compile(r"(?:^|\s[-–—]\s)\s*Folytatás\s*(?:$|\s*[-–—]\s)")
_TRAILING_RE = re.compile(r"\s*[-–—:,.]+\s*$")
_LEADING_ART_RE = re.compile(r"^(?:A|Az)\s+", re.I)
_MEETING_SUFFIX_RE = re.compile(
    r"\s*(?:ülése|ülésének élő közvetítése|üléséről|ülés)\s*$", re.I)
_PLENARY_RE = re.compile(r"Országgyűlés\s+(?:\w+\s+)*ülés|plenáris", re.I)
_COMMITTEE_RE = re.compile(r"bizottság", re.I)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_title(title: str) -> dict:
    """One video title split into the facts that place it.

    Returns ``date`` (ISO), ``kind`` (``committee`` / ``plenary`` / ``other``),
    ``committeeLabel`` for a committee recording, and ``continued`` for the
    second half of an overrunning sitting. A title that does not match the
    House's pattern at all — a ceremony, a clip — comes back as ``other`` with
    whatever could be read, rather than being dropped: the channel is small
    enough that an unrecognised video is worth seeing in the registry.
    """
    raw = re.sub(r"\s+", " ", title or "").strip()
    out: dict = {"title": raw, "date": None, "kind": "other",
                 "committeeLabel": None, "continued": False}
    m = _TITLE_RE.match(raw)
    if not m:
        return out
    year, month, day, rest = m.groups()
    if month.lower() in MONTHS:
        out["date"] = "%s-%02d-%02d" % (year, MONTHS[month.lower()], int(day))
    if _CONTINUED_RE.search(" - " + rest):
        out["continued"] = True
        rest = _CONTINUED_RE.sub(" ", " - " + rest).strip(" -–—")
    rest = _TRAILING_RE.sub("", rest).strip()
    out["label"] = rest
    if _COMMITTEE_RE.search(rest) and not _PLENARY_RE.search(rest):
        out["kind"] = "committee"
        body = _MEETING_SUFFIX_RE.sub("", rest).strip()
        out["committeeLabel"] = _TRAILING_RE.sub("", body).strip() or None
    elif _PLENARY_RE.search(rest):
        out["kind"] = "plenary"
    return out


# --- the RSS feed (the newest 15) -------------------------------------------

def parse_feed(xml_text: str) -> list[dict]:
    """The channel's Atom feed as video records."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as e:
        logger.warning("Could not parse the YouTube feed: %s", e)
        return []
    out = []
    for entry in root.findall("a:entry", _ATOM):
        vid = entry.findtext("yt:videoId", None, _ATOM)
        if not vid:
            continue
        group = entry.find("media:group", _ATOM)
        thumb = group.find("media:thumbnail", _ATOM) if group is not None else None
        stats = group.find("media:community/media:statistics", _ATOM) \
            if group is not None else None
        out.append({
            "videoId": vid,
            "url": WATCH_URL % vid,
            "publishedAt": entry.findtext("a:published", None, _ATOM),
            "updatedAt": entry.findtext("a:updated", None, _ATOM),
            "description": (group.findtext("media:description", None, _ATOM)
                            if group is not None else None) or None,
            "thumbnail": thumb.get("url") if thumb is not None else None,
            "views": _int(stats.get("views")) if stats is not None else None,
            "durationS": None,
            **parse_title(entry.findtext("a:title", "", _ATOM)),
        })
    return out


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --- backfill: the whole upload history -------------------------------------

def yt_dlp_available() -> bool:
    return shutil.which("yt-dlp") is not None


def backfill_yt_dlp(channel: str, *, timeout: float = 900.0) -> list[dict]:
    """Every video on the channel, via ``yt-dlp``'s flat playlist dump.

    ``--flat-playlist`` asks for the listing only and never touches a video
    page, so the whole history is a handful of requests rather than one per
    video. It gives no reliable upload timestamp — which costs nothing here,
    because the date this module needs is the one in the title, and that is the
    date the sitting was *held* rather than the date the file was posted.
    """
    cmd = ["yt-dlp", "--flat-playlist", "--dump-json", "--no-warnings",
           "--ignore-errors", UPLOADS_PLAYLIST % channel[2:]]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("yt-dlp backfill failed: %s", e)
        return []
    out = []
    for line in p.stdout.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        vid = rec.get("id")
        if not vid:
            continue
        thumbs = rec.get("thumbnails") or []
        out.append({
            "videoId": vid,
            "url": rec.get("webpage_url") or rec.get("url") or WATCH_URL % vid,
            "publishedAt": _ts_iso(rec.get("timestamp")),
            "updatedAt": None,
            "description": rec.get("description") or None,
            "thumbnail": (thumbs[-1].get("url") if thumbs else None),
            "views": _int(rec.get("view_count")),
            "durationS": _int(rec.get("duration")),
            **parse_title(rec.get("title") or ""),
        })
    if not out and p.returncode != 0:
        logger.warning("yt-dlp backfill returned nothing (exit %s): %s",
                       p.returncode,
                       p.stderr.decode("utf-8", "replace").strip()[:300])
    return out


def backfill_api(http: HttpClient, channel: str, api_key: str) -> list[dict]:
    """Every video on the channel, via the YouTube Data API v3.

    The fallback for a box without ``yt-dlp``. It needs a key
    (``PARLAMONITOR_YOUTUBE_API_KEY``) and spends quota — one unit per 50
    videos — which is why it is not the first choice for a channel this small.
    """
    out: list[dict] = []
    token = None
    for _ in range(40):                      # 2 000 videos; the channel has 166
        params = {"part": "snippet,contentDetails", "maxResults": 50,
                  "playlistId": "UU" + channel[2:], "key": api_key}
        if token:
            params["pageToken"] = token
        try:
            page = http.get_json(API_PLAYLIST_ITEMS, params=params)
        except Exception as e:               # noqa: BLE001 (SCR-5)
            logger.warning("YouTube API backfill failed: %s", e)
            break
        for item in page.get("items") or []:
            snippet = item.get("snippet") or {}
            vid = ((item.get("contentDetails") or {}).get("videoId")
                   or (snippet.get("resourceId") or {}).get("videoId"))
            if not vid:
                continue
            thumbs = (snippet.get("thumbnails") or {})
            best = thumbs.get("high") or thumbs.get("medium") or thumbs.get("default")
            out.append({
                "videoId": vid,
                "url": WATCH_URL % vid,
                "publishedAt": ((item.get("contentDetails") or {})
                                .get("videoPublishedAt")
                                or snippet.get("publishedAt")),
                "updatedAt": None,
                "description": snippet.get("description") or None,
                "thumbnail": (best or {}).get("url"),
                "views": None,
                "durationS": None,
                **parse_title(snippet.get("title") or ""),
            })
        token = page.get("nextPageToken")
        http.polite_sleep()
        if not token:
            break
    return out


def _ts_iso(ts) -> str | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), timezone.utc).isoformat(
            timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None


def api_key() -> str | None:
    key = os.environ.get("PARLAMONITOR_YOUTUBE_API_KEY", "").strip()
    return key or None


# --- the pass ---------------------------------------------------------------

def fetch_videos(http: HttpClient, *, channel: str | None = None,
                 backfill: bool = False, previous: dict | None = None) -> dict:
    """Read the channel into a video registry, merging with what is already held.

    The RSS feed is always read: it is one request and it is the freshest view
    of the channel. ``backfill`` additionally walks the whole upload history,
    which is what a first run (or a run after the channel has posted more than
    15 videos since the last one) needs.

    **Additive by design.** Previous rows are kept and updated in place rather
    than replaced, so a cheap RSS poll can never throw away a history a backfill
    paid for. A row the channel no longer serves keeps its place with
    ``seenAt`` unchanged, which is how a video taken down stays visible as
    something that existed — the site links it and never mirrors it (LEGAL-1),
    so a dead link is the only honest thing it can show.
    """
    cid = channel or CHANNEL_ID
    found: list[dict] = []
    errors: list[str] = []
    method = []

    try:
        found.extend(parse_feed(http.get_text(FEED_URL % cid)))
        method.append("rss")
    except Exception as e:                                   # noqa: BLE001 (SCR-5)
        logger.warning("Could not read the YouTube feed: %s", e)
        errors.append("rss: %s" % e)
    http.polite_sleep()
    feed_count = len(found)

    if backfill:
        rows: list[dict] = []
        if yt_dlp_available():
            rows = backfill_yt_dlp(cid)
            if rows:
                method.append("yt-dlp")
            else:
                errors.append("yt-dlp: returned nothing")
        key = api_key()
        if not rows and key:
            rows = backfill_api(http, cid, key)
            if rows:
                method.append("youtube-api")
            else:
                errors.append("youtube-api: returned nothing")
        if not rows and not yt_dlp_available() and not key:
            # Neither route available: say so rather than reporting a backfill
            # that silently did nothing (SCR-6).
            errors.append("backfill needs yt-dlp on PATH or "
                          "PARLAMONITOR_YOUTUBE_API_KEY")
            logger.warning("Backfill asked for but neither yt-dlp nor a "
                           "YouTube API key is available")
        found.extend(rows)

    merged = _merge(previous, found)
    kinds: dict[str, int] = {}
    for v in merged:
        kinds[v["kind"]] = kinds.get(v["kind"], 0) + 1
    dates = sorted(v["date"] for v in merged if v.get("date"))
    logger.info("Videos: %d held (%d from the feed, %d seen this pass) — "
                "%d committee, %d plenary", len(merged), feed_count, len(found),
                kinds.get("committee", 0), kinds.get("plenary", 0))
    return {
        "meta": {
            "source": SOURCE,
            "channelId": cid,
            "channelUrl": "https://www.youtube.com/channel/%s" % cid,
            "scrapedAt": _now_iso(),
            "method": method,
            "backfilled": backfill and len(method) > 1,
            "errors": errors,
            # The channel's own first upload. The site needs it to say why a
            # 2016 meeting has no recording: nothing was posted before this,
            # so the absence is the channel's, not the scraper's.
            "earliestVideo": dates[0] if dates else None,
            "latestVideo": dates[-1] if dates else None,
            "count": len(merged),
            "counts": {"videos": len(merged), "feed": feed_count,
                       "seen": len(found), **kinds},
        },
        "data": merged,
    }


def _merge(previous: dict | None, found: list[dict]) -> list[dict]:
    """Fold this pass's videos into what was already held, newest date first.

    A video seen again keeps the fields the cheaper source cannot supply — the
    RSS feed carries no duration, so an RSS-only poll must not blank the
    duration a backfill established. Merging per field rather than per row is
    what makes the two sources combine instead of overwrite.
    """
    held: dict[str, dict] = {}
    for v in (previous or {}).get("data") or []:
        if v.get("videoId"):
            held[v["videoId"]] = dict(v)
    now = _now_iso()
    for v in found:
        row = held.get(v["videoId"])
        if row is None:
            held[v["videoId"]] = {**v, "firstSeenAt": now, "seenAt": now}
            continue
        for key, value in v.items():
            if value is not None and value != "":
                row[key] = value
        row["seenAt"] = now
    return sorted(held.values(),
                  key=lambda v: (v.get("date") or "", v.get("publishedAt") or "",
                                 v.get("videoId") or ""),
                  reverse=True)


def load_previous(paths: Paths) -> dict | None:
    f = paths.committee_videos_file()
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Could not read %s (%s) — treating as a first run", f, e)
        return None


def save_videos(paths: Paths, registry: dict) -> None:
    out = paths.committee_videos_file()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(out)
    logger.info("Wrote %s (%d videos, %d committee)", out,
                registry["meta"]["count"],
                registry["meta"]["counts"].get("committee", 0))
