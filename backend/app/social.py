"""Bluesky announcements: a new sitting day, and the haikus said on it (§8.7).

Two things are worth telling people about, and this decides when:

  * **SOC-2 — a sitting day is fully processed.** Not "a day happened": the day's
    every speech has both its transcript and its per-speech video window, i.e.
    ``processing == "complete"`` in :mod:`app.publication` — exactly the state the
    site itself stops badging as "Részben feldolgozva" (SIT-2). parlament.hu
    publishes a day in instalments over days, so this is the one moment at which
    "you can now read and watch all of it" is a true statement.
  * **SOC-4 — a representative accidentally spoke a haiku.** Whole sentences that
    happen to be 5-7-5 in Hungarian syllables (:mod:`app.haiku`), from a
    mandate-holding member's substantive speech.

Design constraints that shape the whole module:

  * **It reads the DB and its own state file, nothing else.** The loader stays pure
    — no posting side effect inside a build — and this can run from the sync
    sidecar, from cron, or by hand, in any order, as often as one likes.
  * **A post is never sent twice.** The state file (JSON, beside the DB on the
    mounted volume) remembers each announced day and each posted poem. It is the
    *only* memory: the content DB is regenerable (DB-3) and gets rebuilt from
    scratch, so anything remembered inside it would re-announce the whole corpus.
  * **A first run announces nothing.** With no state file, every day in the recency
    window is recorded as already-announced and nothing is sent, so a fresh deploy
    — or a wiped volume — cannot dump a backlog into the feed. ``--backfill`` opts
    into posting that window instead.
  * **A failure loses nothing.** Whatever went out is recorded; whatever didn't
    stays unrecorded and is retried on the next run.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import bluesky
from .config import settings
from .haiku import Haiku, haikus_in_session
from .publication import hu_date, processing_state

logger = logging.getLogger(__name__)

STATE_VERSION = 1


@dataclass
class Post:
    """One post the announcer decided to make (built before anything is sent, so a
    dry run can show exactly what a real run would do)."""
    kind: str                      # "day" | "haiku"
    session_id: str
    text: str
    embed: dict | None = None
    key: str = ""                  # state key for a haiku; the session id for a day
    uid: str | None = None         # the speech a haiku came from


# --- state ------------------------------------------------------------------

def state_path() -> Path:
    """Where the "what has been announced" file lives.

    Beside the DB by default — on the standard deploy that is the mounted `/db`
    volume, so it survives container restarts and code deploys, and a DB rebuild
    (REBUILD_DB=1) does not reset the bot's memory with it."""
    configured = settings.bluesky_state
    if configured:
        return Path(configured)
    return Path(settings.db_path).resolve().parent / "bluesky-state.json"


def load_state(path: Path) -> dict | None:
    """The state file, or ``None`` when there is none yet (a first run).

    An unreadable/corrupt file is treated as a first run rather than crashing the
    sync pass — the cost is one silent seeding, not a duplicate flood."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Bluesky: unreadable state file %s (%s) — treating as a "
                       "first run, so nothing is posted this pass", path, exc)
        return None
    if not isinstance(state, dict):
        return None
    state.setdefault("version", STATE_VERSION)
    for bucket in ("days", "haikus", "scans"):
        if not isinstance(state.get(bucket), dict):
            state[bucket] = {}
    return state


def save_state(path: Path, state: dict) -> None:
    """Write the state atomically: a half-written file would read as a first run
    and silence the bot for good on the next pass."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _new_state() -> dict:
    return {"version": STATE_VERSION, "days": {}, "haikus": {}, "scans": {}}


# --- reading the sittings ---------------------------------------------------

def _has_session_status(conn: sqlite3.Connection) -> bool:
    return any(r[1] == "status" for r in conn.execute("PRAGMA table_info(session)"))


def _sessions(conn: sqlite3.Connection, since: str) -> list[sqlite3.Row]:
    """Every sitting day held since ``since`` (ISO date), oldest first, with the
    counts SIT-2 completeness is judged from and the two numbers a day post
    reports. The same correlated-subquery shape the sittings list uses — a page of
    days costs ~1.5 ms."""
    status_col = ("COALESCE(s.status, 'published')" if _has_session_status(conn)
                  else "'published'")
    return conn.execute(
        f"""SELECT s.id, s.period_number, s.sitting, s.date, {status_col} AS status,
                   (SELECT COUNT(*) FROM speech sp
                     WHERE sp.session_id = s.id) AS speeches,
                   (SELECT COUNT(*) FROM speech sp
                     WHERE sp.session_id = s.id AND sp.has_text = 1) AS with_text,
                   (SELECT COUNT(*) FROM speech sp
                     WHERE sp.session_id = s.id
                       AND sp.video_start IS NOT NULL) AS with_video,
                   (SELECT COUNT(DISTINCT sp.person_id) FROM speech sp
                     WHERE sp.session_id = s.id AND sp.person_id IS NOT NULL
                       AND COALESCE(sp.procedural, 0) = 0) AS speakers,
                   (SELECT COALESCE(SUM(sp.duration), 0) FROM speech sp
                     WHERE sp.session_id = s.id AND sp.person_id IS NOT NULL
                       AND COALESCE(sp.procedural, 0) = 0) AS speaking_seconds,
                   (SELECT COUNT(*) FROM agenda_item ai
                     WHERE ai.session_id = s.id) AS agenda_items
            FROM session s
            WHERE s.date >= ?
            ORDER BY s.date, s.sitting""", (since,)).fetchall()


def _scan_fingerprint(row) -> str:
    """What a haiku scan of this day covered.

    A day's transcript arrives in instalments, so the same day is re-scanned each
    time more of it lands — and skipped entirely when nothing changed, which is
    what keeps a poll cheap."""
    return f"{row['speeches']}:{row['with_text']}"


# --- post text -------------------------------------------------------------

def _hu_duration(seconds: float | None) -> str | None:
    """`33120` → `9 óra 12 perc` (None when there is no usable length)."""
    if not seconds or seconds <= 0:
        return None
    minutes = int(round(seconds / 60))
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours} óra {mins} perc"
    if hours:
        return f"{hours} óra"
    return f"{mins} perc"


def _speaking_time(row) -> str | None:
    """Total time spoken on the day — the sum the day's own speaker toplist adds up
    (§5.4: statistics-eligible speeches only, STAT-1).

    Deliberately NOT the day's wall-clock length: `session.video_duration` holds a
    per-speech clip length upstream, and `date_start` is midnight on days whose
    speeches carry whole-day offsets — either would put a made-up number in a post.
    A value beyond a plausible sitting day is dropped rather than published, since
    it can only come from timing this bot has no way to sanity-check."""
    seconds = row["speaking_seconds"] or 0
    return _hu_duration(seconds) if 0 < seconds <= 24 * 3600 else None


def day_post(row, base_url: str) -> Post:
    """The "this sitting day is now fully readable" post (SOC-3).

    It says what the day *was* (when, which sitting of which cycle) and what is now
    available (how many speeches, by how many speakers, over how many agenda items
    and how much speaking time) — the facts a reader needs to decide whether to open
    it, each one a number the site itself shows on the page being linked to."""
    url = f"{base_url}/sessions/{row['id']}"
    # The same figures the day's card and its toplist carry, so a reader who
    # follows the link finds exactly what the post promised.
    facts = [f"{row['speeches']} felszólalás"]
    if row["speakers"]:
        facts.append(f"{row['speakers']} felszólaló")
    if row["agenda_items"]:
        facts.append(f"{row['agenda_items']} napirendi pont")
    spoken = _speaking_time(row)
    if spoken:
        facts.append(f"{spoken} beszédidő")
    when = hu_date(row["date"]) or row["date"]
    headline = (f"✅ Feldolgozva a {row['period_number']}. ciklus "
                f"{row['sitting']}. ülésnapja ({when})")
    head = f"{headline}\n{' · '.join(facts)}"
    text = f"{head}\nMinden felszólalás szövege és videója kereshető:\n{url}"
    if bluesky.graphemes(text) > bluesky.MAX_GRAPHEMES:
        # Only reachable with an unusually long origin or fact list. Give up the
        # invitation line, then trim the facts — never the link, which is the point
        # of the post.
        text = f"{head}\n{url}"
        if bluesky.graphemes(text) > bluesky.MAX_GRAPHEMES:
            text = (bluesky.clip(head, bluesky.MAX_GRAPHEMES
                                 - bluesky.graphemes(f"\n{url}")) + f"\n{url}")
    return Post(kind="day", session_id=row["id"], key=row["id"], text=text,
                embed=bluesky.external_embed(
                    url, f"Országgyűlési ülésnap – {when}",
                    " · ".join(facts)))


def haiku_post(h: Haiku, row, base_url: str) -> Post:
    """The "somebody accidentally spoke a haiku" post (SOC-4).

    Framed as *accidental* on purpose: the speaker wrote no poem, the syllables
    just fell that way, and saying so is the difference between a joke the reader
    is in on and a claim about somebody's intent. The poem is quoted verbatim, with
    a link to the sentence in the transcript so anyone can check it."""
    url = f"{base_url}{h.link}" if h.link.startswith("/") else h.link
    who = f"{h.speaker} ({h.faction})" if h.faction else h.speaker
    when = hu_date(row["date"]) or row["date"]
    header = "🌸 Véletlen haiku az Országgyűlésben:"
    # Bounded before it is used as a budget, so an unusually long name/faction
    # cannot squeeze the poem itself out of the post.
    credit = bluesky.clip(f"— {who}, {when}", 90)
    # Size the poem against what is left of the 300-grapheme limit once the fixed
    # parts (header, credit, link, newlines) have taken their share, so the link at
    # the end is never what gets cut.
    fixed = f"{header}\n\n\n\n{credit}\n{url}"
    poem = bluesky.clip("\n".join(h.lines),
                        bluesky.MAX_GRAPHEMES - bluesky.graphemes(fixed))
    text = f"{header}\n\n{poem}\n\n{credit}\n{url}"
    return Post(kind="haiku", session_id=row["id"], key=h.key, uid=h.uid, text=text,
                embed=bluesky.external_embed(
                    url, f"{who} felszólalása – {when}",
                    "Véletlen haiku a felszólalás jegyzőkönyvében."))


# --- the pass --------------------------------------------------------------

@dataclass
class Result:
    """What one pass did — returned for the CLI's summary and the tests."""
    posts: list[Post] = field(default_factory=list)
    seeded: int = 0            # days recorded as already-announced on a first run
    skipped: int = 0           # candidates left for the next run by the per-run cap
    failed: bool = False       # a post could not be sent (retried next run)


def announce(db_path: str | Path | None = None, *, client=None,
             dry_run: bool | None = None, state_file: str | Path | None = None,
             today: date | None = None, backfill: bool = False) -> Result:
    """One announcement pass over the DB (SOC-2/SOC-4). Safe to run repeatedly.

    ``client`` overrides the environment-built Bluesky client (the tests inject a
    recorder); ``dry_run`` decides what to post, prints it and touches neither the
    network nor the state file. ``today`` pins the recency window for tests."""
    dry_run = settings.bluesky_dry_run if dry_run is None else dry_run
    result = Result()

    if not settings.bluesky_announce:
        logger.debug("Bluesky: announcements disabled (PARLAMONITOR_BLUESKY_ANNOUNCE=0)")
        return result

    poster = client if client is not None else bluesky.client_from_env()
    if poster is None and not dry_run:
        logger.info("Bluesky: no credentials configured — set "
                    "PARLAMONITOR_BLUESKY_AUTH=handle:app-password to post")
        return result

    base_url = (settings.bluesky_base_url or settings.site_url or "").rstrip("/")
    if not base_url:
        logger.error("Bluesky: no public base URL — set PARLAMONITOR_SITE_URL (or "
                     "PARLAMONITOR_BLUESKY_BASE_URL) so the posts can link to the "
                     "site; nothing posted")
        return result

    db_path = Path(db_path or settings.db_path)
    if not db_path.exists():
        logger.warning("Bluesky: no DB at %s — nothing to announce", db_path)
        return result

    path = Path(state_file) if state_file else state_path()
    stored = load_state(path)
    state = stored if stored is not None else _new_state()
    today = today or date.today()
    since = (today - timedelta(days=settings.bluesky_max_age_days)).isoformat()

    # Read-only: a serving color may be reading the same file, and the loader may be
    # mid-swap. Nothing here writes to the content DB.
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = _sessions(conn, since)

        if stored is None and not backfill:
            # First run: adopt the current state of the world as "already said".
            for row in rows:
                if _complete(row, today):
                    state["days"][row["id"]] = {"seeded": True}
                    result.seeded += 1
                state["scans"][row["id"]] = _scan_fingerprint(row)
            state["seeded_at"] = _now()
            if dry_run:
                logger.info("Bluesky (dry run): first run — would record %d recent "
                            "sitting day(s) as already announced", result.seeded)
            else:
                save_state(path, state)
                logger.info("Bluesky: first run — recorded %d recent sitting day(s) "
                            "as already announced; nothing posted (use --backfill "
                            "to announce them instead)", result.seeded)
            return result

        posts, scans, result.skipped = _candidates(conn, rows, state, today, base_url)
    finally:
        conn.close()

    if not posts:
        logger.info("Bluesky: nothing new to announce")
        return result

    # Send in order (a day, then its poems), recording each as it goes: an
    # interrupted run has posted a prefix, and the rest is still pending.
    # `unsent` collects the days whose posts did NOT all go out, so their haiku scan
    # is not marked settled and the next pass retries them.
    unsent: set[str] = set()
    for i, post in enumerate(posts):
        if bluesky.graphemes(post.text) > bluesky.MAX_GRAPHEMES:
            # A builder bug, not an upstream problem: the server would reject it. Left
            # unrecorded and unsettled so a fix makes it postable, but WITHOUT
            # stopping the pass — one malformed poem must not block anything else.
            logger.error("Bluesky: skipping an oversized %s post for %s (%d > %d "
                         "graphemes) — post builder needs fixing", post.kind,
                         post.session_id, bluesky.graphemes(post.text),
                         bluesky.MAX_GRAPHEMES)
            unsent.add(post.session_id)
            continue
        if dry_run:
            logger.info("Bluesky (dry run) would post [%s]:\n%s\n", post.kind, post.text)
            result.posts.append(post)
            continue
        try:
            uri = poster.post(post.text, embed=post.embed)
        except bluesky.BlueskyError as exc:
            # Auth, rate limit or an outage — all of them will hit the next post
            # too, so stop and leave the remainder for the next pass.
            logger.error("Bluesky: %s — stopping this pass; %d post(s) stay pending",
                         exc, len(posts) - i)
            result.failed = True
            unsent.update(p.session_id for p in posts[i:])
            break
        _record(state, post, uri)
        result.posts.append(post)

    # A day's scan is only settled when everything it produced actually went out.
    for sid, fingerprint in scans.items():
        if sid not in unsent:
            state["scans"][sid] = fingerprint

    if not dry_run:
        save_state(path, state)
    return result


def _complete(row, today: date) -> bool:
    """Whether this day is fully processed — the site's own SIT-2 verdict."""
    return processing_state(row["status"], row["date"], row["speeches"],
                            row["with_text"], row["with_video"],
                            today=today) == "complete"


def _candidates(conn: sqlite3.Connection, rows, state: dict, today: date,
                base_url: str) -> tuple[list[Post], dict[str, str], int]:
    """The posts this pass should make, oldest day first.

    Also returns the haiku-scan fingerprints to record once they are sent, and how
    many candidates the per-run cap deferred. Generation stops at the cap rather
    than building a long list and trimming it, so an unscanned day keeps no
    fingerprint and is simply picked up next pass."""
    cap = max(1, settings.bluesky_max_posts)
    posts: list[Post] = []
    scans: dict[str, str] = {}
    skipped = 0

    for row in rows:
        sid = row["id"]
        if len(posts) >= cap:
            # Everything from here is untouched — no fingerprints, no partial work.
            skipped += 1
            continue

        if _complete(row, today) and sid not in state["days"]:
            posts.append(day_post(row, base_url))

        if not settings.bluesky_haikus or not row["with_text"]:
            continue
        fingerprint = _scan_fingerprint(row)
        if state["scans"].get(sid) == fingerprint:
            continue                      # nothing new landed since the last scan
        allowance = settings.bluesky_haiku_per_day - _posted_haikus(state, sid)
        found = haikus_in_session(conn, sid, base_url="",
                                  mps_only=settings.bluesky_haiku_mps_only)
        fresh = [h for h in found if h.key not in state["haikus"]]
        for h in fresh[: max(0, allowance)]:
            if len(posts) >= cap:
                skipped += 1
                break
            posts.append(haiku_post(h, row, base_url))
        else:
            # The scan is only settled when the cap didn't cut it short.
            scans[sid] = fingerprint

    if skipped:
        logger.info("Bluesky: per-run cap of %d reached — %d item(s) deferred to "
                    "the next pass", cap, skipped)
    return posts, scans, skipped


def _posted_haikus(state: dict, session_id: str) -> int:
    """How many poems from this sitting day have already been posted — so a day
    whose transcript arrives in instalments still yields at most
    ``bluesky_haiku_per_day``, not that many per instalment."""
    return sum(1 for v in state["haikus"].values()
               if isinstance(v, dict) and v.get("session") == session_id)


def _record(state: dict, post: Post, uri: str) -> None:
    entry = {"posted_at": _now(), "uri": uri}
    if post.kind == "day":
        state["days"][post.session_id] = entry
    else:
        state["haikus"][post.key] = {**entry, "session": post.session_id,
                                     "uid": post.uid}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- CLI -------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Announce newly processed sitting days (and the haikus said "
                    "on them) on Bluesky. Reads the DB read-only; remembers what "
                    "it posted in a state file beside it.")
    ap.add_argument("--db", help=f"DB path (default: {settings.db_path})")
    ap.add_argument("--state", help=f"state file (default: {state_path()})")
    ap.add_argument("-n", "--dry-run", action="store_true",
                    help="print what would be posted; touch neither the network "
                         "nor the state file (works without credentials)")
    ap.add_argument("--backfill", action="store_true",
                    help="on a FIRST run, post the recent window instead of "
                         "recording it as already announced (bounded by the "
                         "per-run cap, so it takes several passes)")
    ap.add_argument("--today", help="pin today's date (YYYY-MM-DD) for the "
                                    "recency window; for testing")
    ap.add_argument("-q", "--quiet", action="store_true", help="errors only")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.ERROR if args.quiet else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s",
                        stream=sys.stderr)
    logging.getLogger("app").setLevel(logging.ERROR if args.quiet else logging.INFO)

    try:
        today = date.fromisoformat(args.today) if args.today else None
    except ValueError:
        ap.error("--today must be an ISO date (YYYY-MM-DD)")

    result = announce(args.db, dry_run=args.dry_run or None, state_file=args.state,
                      today=today, backfill=args.backfill)
    days = sum(1 for p in result.posts if p.kind == "day")
    haikus = sum(1 for p in result.posts if p.kind == "haiku")
    verb = "would post" if args.dry_run else "posted"
    print(f"{verb} {days} sitting-day and {haikus} haiku announcement(s)"
          + (f"; seeded {result.seeded} day(s)" if result.seeded else "")
          + (f"; {result.skipped} deferred" if result.skipped else ""),
          file=sys.stderr)
    # A send failure is worth a non-zero exit so a cron/sync wrapper can see it;
    # "nothing to say" is a perfectly good pass.
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
