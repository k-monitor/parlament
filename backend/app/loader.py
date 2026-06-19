"""Loader: scraper JSON records -> normalized SQLite (ING-1).

This is the *only* writer to the production database (ING-2); the API treats the
file as read-only. The loader is deliberately separate from fetching/parsing so
the scrape and the DB build can run and be tested independently (ING-1).

Design points satisfied here:

* **Atomic per sitting (ING-3):** every session loads inside one transaction;
  a crash mid-load leaves no half-imported sitting.
* **Re-ingest replaces (ING-4):** loading a sitting deletes its derived rows
  first (keyed on session id / originID), so upstream corrections propagate.
* **Regenerable cache (DB-3) / atomic swap (DB-4):** ``build_database`` writes a
  fresh file and atomically renames it over the live DB.
* **Precomputed aggregates (REP-7):** statistics tables are rebuilt from the
  loaded speeches on every run, never computed live.

Usage::

    python -m app.loader ../data backend/ogywatch.db          # full rebuild
    python -m app.loader ../data backend/ogywatch.db --session 43003
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger("ogywatch.loader")

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Consistent faction colours for charts (REP-4). Unknown factions fall back to a
# deterministic palette slot so every faction still renders a stable colour.
FACTION_COLORS = {
    "Fidesz": "#FF6A13",
    "KDNP": "#0B4C8C",
    "TISZA": "#00A6A6",
    "DK": "#1E5BC6",
    "Jobbik": "#5A3B1C",
    "MSZP": "#C8102E",
    "Momentum": "#8E44AD",
    "LMP": "#3DA639",
    "Párbeszéd": "#1B9E77",
    "Mi Hazánk": "#2E5E2E",
    "MHM": "#2E5E2E",
    "függetlatlen": "#888888",
    "független": "#888888",
}
_FALLBACK_PALETTE = [
    "#7F8C8D", "#D35400", "#16A085", "#9B59B6", "#2C3E50",
    "#E67E22", "#27AE60", "#2980B9", "#C0392B", "#F39C12",
]


def _faction_color(label: str, idx: int) -> str:
    if label in FACTION_COLORS:
        return FACTION_COLORS[label]
    return _FALLBACK_PALETTE[idx % len(_FALLBACK_PALETTE)]


# ---------------------------------------------------------------------------
# Connection / schema
# ---------------------------------------------------------------------------

def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())


# ---------------------------------------------------------------------------
# Faction / person helpers (shared core entities)
# ---------------------------------------------------------------------------

def _get_or_create_faction(conn: sqlite3.Connection, label: str | None,
                           ext_id=None) -> int | None:
    if not label:
        return None
    label = label.strip()
    if not label:
        return None
    row = conn.execute("SELECT id FROM faction WHERE label = ?", (label,)).fetchone()
    if row:
        if ext_id is not None:
            conn.execute("UPDATE faction SET ext_id = COALESCE(ext_id, ?) WHERE id = ?",
                         (ext_id, row["id"]))
        return row["id"]
    count = conn.execute("SELECT COUNT(*) AS c FROM faction").fetchone()["c"]
    color = _faction_color(label, count)
    cur = conn.execute(
        "INSERT INTO faction(ext_id, label, color) VALUES (?, ?, ?)",
        (ext_id, label, color))
    return cur.lastrowid


def _ensure_person(conn: sqlite3.Connection, person_id: str, label: str,
                   firstname=None, lastname=None) -> None:
    """Create a stub person if absent (speakers may not be in the MP roster —
    e.g. the President of the Republic or invited guests)."""
    if not person_id:
        return
    exists = conn.execute("SELECT 1 FROM person WHERE person_id = ?",
                          (person_id,)).fetchone()
    if not exists:
        conn.execute(
            "INSERT INTO person(person_id, label, firstname, lastname) "
            "VALUES (?, ?, ?, ?)",
            (person_id, label or person_id, firstname, lastname))


# ---------------------------------------------------------------------------
# Representatives registry
# ---------------------------------------------------------------------------

def load_representatives(conn: sqlite3.Connection, registry: dict) -> int:
    """Upsert the MP registry. Adds bio/enrichment to person rows and faction
    membership history; safe to re-run (replaces each MP's derived rows)."""
    meta = registry.get("meta", {})
    period = meta.get("cycle")
    data = registry.get("data", [])
    for rec in data:
        pid = rec.get("personID")
        if not pid:
            continue
        fac = rec.get("faction") or {}
        _get_or_create_faction(conn, fac.get("label"), fac.get("id"))

        # external per-cycle stats (e.g. bills submitted) kept for the profile;
        # "bills submitted" stays hidden in the UI until the Bills module (REP-3).
        ext_stats = rec.get("statistics")

        # Prefer the locally-downloaded portrait (served at /media/photos) over
        # the upstream resource URL, so the site doesn't hot-link parlament.hu.
        photo_uri = rec.get("photoURI")
        if rec.get("photoFile"):
            photo_uri = f"/media/photos/{rec['photoFile']}"

        conn.execute(
            """
            INSERT INTO person(person_id, label, label_full, firstname, lastname,
                               photo_uri, photo_file, constituency, seat, email,
                               website, highest_education, active, is_mp,
                               education_json, committees_json, offices_json,
                               faction_history_json, election_history_json,
                               external_stats_json)
            VALUES (:pid, :label, :label_full, :firstname, :lastname, :photo_uri,
                    :photo_file, :constituency, :seat, :email, :website,
                    :highest_education, :active, 1, :education, :committees,
                    :offices, :faction_history, :election_history, :external_stats)
            ON CONFLICT(person_id) DO UPDATE SET
                label=excluded.label, label_full=excluded.label_full,
                firstname=excluded.firstname, lastname=excluded.lastname,
                photo_uri=excluded.photo_uri, photo_file=excluded.photo_file,
                constituency=excluded.constituency, seat=excluded.seat,
                email=excluded.email, website=excluded.website,
                highest_education=excluded.highest_education,
                active=excluded.active, is_mp=1,
                education_json=excluded.education_json,
                committees_json=excluded.committees_json,
                offices_json=excluded.offices_json,
                faction_history_json=excluded.faction_history_json,
                election_history_json=excluded.election_history_json,
                external_stats_json=excluded.external_stats_json
            """,
            {
                "pid": pid,
                "label": (rec.get("label") or pid).strip(),
                "label_full": rec.get("labelFull"),
                "firstname": rec.get("firstname"),
                "lastname": rec.get("lastname"),
                "photo_uri": photo_uri,
                "photo_file": rec.get("photoFile"),
                "constituency": rec.get("constituency"),
                "seat": rec.get("seat"),
                "email": rec.get("email"),
                "website": rec.get("website"),
                "highest_education": rec.get("highestEducation"),
                "active": _as_bool(rec.get("active")),
                "education": _json_or_none(rec.get("education")),
                "committees": _json_or_none(rec.get("committeeMemberships")
                                            or rec.get("committees")),
                "offices": _json_or_none(rec.get("offices")),
                "faction_history": _json_or_none(rec.get("factionHistory")),
                "election_history": _json_or_none(rec.get("electionHistory")),
                "external_stats": _json_or_none(ext_stats),
            },
        )

        # Rebuild this MP's membership rows. The registry's factionHistory `cycle`
        # is a date-range *string* ("2026-"), not a period number, so it can't
        # drive integer period filters. We therefore store ONE canonical
        # membership row keyed on the registry's electoral period (current
        # faction); the richer, display-only history lives in faction_history_json
        # (surfaced by the profile endpoint). We still register every historical
        # faction in the faction table so its colour is known (REP-4).
        conn.execute("DELETE FROM membership WHERE person_id = ?", (pid,))
        for h in rec.get("factionHistory") or []:
            _get_or_create_faction(conn, h.get("label"))
        if fac.get("label"):
            fid = _get_or_create_faction(conn, fac.get("label"), fac.get("id"))
            conn.execute(
                "INSERT INTO membership(person_id, faction_id, period_number, "
                "position) VALUES (?, ?, ?, ?)",
                (pid, fid, period, fac.get("position")))
    conn.commit()
    logger.info("Loaded %d representatives (cycle %s)", len(data), period)
    return len(data)


# ---------------------------------------------------------------------------
# Session record
# ---------------------------------------------------------------------------

def load_session(conn: sqlite3.Connection, record: dict) -> str:
    """Load one sitting-day session record atomically (ING-3). Re-ingesting a
    session replaces all of its derived rows (ING-4)."""
    meta = record.get("meta", {})
    sid = meta.get("session")
    period = meta.get("electoralPeriod")
    try:
        # delete-then-insert keyed on session id => idempotent replace (ING-4).
        _delete_session(conn, sid)

        if period is not None:
            conn.execute(
                "INSERT INTO electoral_period(number) VALUES (?) "
                "ON CONFLICT(number) DO NOTHING", (period,))

        conn.execute(
            """INSERT INTO session(id, period_number, sitting, date, date_start,
                   date_end, source, source_page, scraped_at, timing_method,
                   video_uri, video_playseq, video_duration, video_license,
                   video_creator)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, period, meta.get("sitting"), meta.get("date"),
             meta.get("dateStart"), meta.get("dateEnd"), meta.get("source"),
             _first_source_page(record), meta.get("sourceScrapedAt"),
             meta.get("timingMethod"), meta.get("dayVideoURI"),
             meta.get("dayVideoPlayseq"),
             _day_duration(record), _day_license(record), _day_creator(record)))

        agenda_cache: dict[tuple, int] = {}
        for sp in record.get("data", []):
            _load_speech(conn, sid, period, sp, agenda_cache)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info("Loaded session %s (%d speeches)", sid, len(record.get("data", [])))
    return sid


def _delete_session(conn: sqlite3.Connection, sid: str) -> None:
    conn.execute(
        "DELETE FROM entity WHERE sentence_id IN (SELECT se.id FROM sentence se "
        "JOIN speech sp ON sp.uid = se.speech_id WHERE sp.session_id = ?)", (sid,))
    conn.execute(
        "DELETE FROM sentence WHERE speech_id IN "
        "(SELECT uid FROM speech WHERE session_id = ?)", (sid,))
    conn.execute("DELETE FROM speech WHERE session_id = ?", (sid,))
    conn.execute("DELETE FROM agenda_item WHERE session_id = ?", (sid,))
    # Session-scoped derived rows reference session(id) — clear them too so the
    # session row can be removed (the global person/faction aggregates are
    # rebuilt wholesale by rebuild_aggregates after loading).
    conn.execute("DELETE FROM person_session_stats WHERE session_id = ?", (sid,))
    conn.execute("DELETE FROM session WHERE id = ?", (sid,))


def _load_speech(conn, sid, period, sp, agenda_cache) -> None:
    ag = sp.get("agendaItem") or {}
    ag_key = (ag.get("title"), ag.get("officialTitle"), ag.get("type"),
              ag.get("nativeType"))
    if ag_key not in agenda_cache:
        cur = conn.execute(
            "INSERT INTO agenda_item(session_id, ord, title, official_title, "
            "type, native_type) VALUES (?,?,?,?,?,?)",
            (sid, len(agenda_cache), ag.get("title"), ag.get("officialTitle"),
             ag.get("type"), ag.get("nativeType")))
        agenda_cache[ag_key] = cur.lastrowid
    agenda_id = agenda_cache[ag_key]

    people = sp.get("people") or []
    speaker = people[0] if people else {}
    pid = speaker.get("personID")
    if pid:
        _ensure_person(conn, pid, speaker.get("label"),
                       speaker.get("firstname"), speaker.get("lastname"))
    fac = speaker.get("faction") or {}
    faction_id = _get_or_create_faction(conn, fac.get("label"), fac.get("id"))

    media = sp.get("media") or {}
    debug = sp.get("debug") or {}
    tcs = sp.get("textContents") or []
    source_uri = tcs[0].get("sourceURI") if tcs else media.get("sourcePage")

    # Gather sentences (the unit of search + seeking) and derive the speech's
    # day-absolute span from them.
    sentences: list[dict] = []
    for tc in tcs:
        for tb in tc.get("textBody", []):
            for s in tb.get("sentences", []):
                if s.get("text"):
                    sentences.append(s)
    has_text = 1 if sentences else 0
    if sentences:
        time_start = min((s.get("timeStart") for s in sentences
                          if s.get("timeStart") is not None), default=None)
        time_end = max((s.get("timeEnd") for s in sentences
                        if s.get("timeEnd") is not None), default=None)
    else:
        time_start = media.get("videoStart")
        time_end = media.get("videoEnd")
    duration = (time_end - time_start) if (time_start is not None
                                           and time_end is not None) else None

    origin_id = sp.get("originID")
    speech_index = sp.get("speechIndex")
    uid = f"{sid}-{speech_index}"
    conn.execute(
        """INSERT INTO speech(uid, origin_id, session_id, agenda_item_id,
               period_number, speech_index, person_id, speaker_label,
               speaker_status, faction_id, time_start, time_end, video_start,
               video_end, duration, confidence, align_method, has_text,
               source_uri, source_page)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (uid, origin_id, sid, agenda_id, period, speech_index, pid,
         speaker.get("label"), speaker.get("context"), faction_id,
         time_start, time_end, media.get("videoStart"), media.get("videoEnd"),
         duration, debug.get("confidence"), debug.get("align-method"),
         has_text, source_uri, media.get("sourcePage")))

    for i, s in enumerate(sentences):
        conn.execute(
            "INSERT INTO sentence(speech_id, ord, text, time_start, time_end) "
            "VALUES (?,?,?,?,?)",
            (uid, i, s["text"], s.get("timeStart"), s.get("timeEnd")))


# ---------------------------------------------------------------------------
# Aggregates (rebuilt every run — REP-7)
# ---------------------------------------------------------------------------

def rebuild_aggregates(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM person_stats")
    conn.execute("DELETE FROM faction_stats")
    conn.execute("DELETE FROM person_session_stats")

    # Per person, per period + an all-periods row (period_number IS NULL).
    conn.execute(
        """INSERT INTO person_stats(person_id, period_number, speech_count,
               speaking_seconds, sentence_count)
           SELECT s.person_id, s.period_number, COUNT(*),
                  COALESCE(SUM(s.duration), 0),
                  COALESCE(SUM((SELECT COUNT(*) FROM sentence se
                               WHERE se.speech_id = s.uid)), 0)
           FROM speech s WHERE s.person_id IS NOT NULL
           GROUP BY s.person_id, s.period_number""")
    conn.execute(
        """INSERT INTO person_stats(person_id, period_number, speech_count,
               speaking_seconds, sentence_count)
           SELECT s.person_id, NULL, COUNT(*), COALESCE(SUM(s.duration), 0),
                  COALESCE(SUM((SELECT COUNT(*) FROM sentence se
                               WHERE se.speech_id = s.uid)), 0)
           FROM speech s WHERE s.person_id IS NOT NULL GROUP BY s.person_id""")

    conn.execute(
        """INSERT INTO faction_stats(faction_id, period_number, speech_count,
               speaking_seconds, mp_count)
           SELECT s.faction_id, s.period_number, COUNT(*),
                  COALESCE(SUM(s.duration), 0), COUNT(DISTINCT s.person_id)
           FROM speech s WHERE s.faction_id IS NOT NULL
           GROUP BY s.faction_id, s.period_number""")
    # All-periods row (period_number IS NULL) consumed by the factions endpoint.
    conn.execute(
        """INSERT INTO faction_stats(faction_id, period_number, speech_count,
               speaking_seconds, mp_count)
           SELECT s.faction_id, NULL, COUNT(*), COALESCE(SUM(s.duration), 0),
                  COUNT(DISTINCT s.person_id)
           FROM speech s WHERE s.faction_id IS NOT NULL GROUP BY s.faction_id""")

    conn.execute(
        """INSERT INTO person_session_stats(person_id, session_id, date,
               speech_count, speaking_seconds)
           SELECT s.person_id, s.session_id, ss.date, COUNT(*),
                  COALESCE(SUM(s.duration), 0)
           FROM speech s JOIN session ss ON ss.id = s.session_id
           WHERE s.person_id IS NOT NULL
           GROUP BY s.person_id, s.session_id""")
    conn.commit()
    logger.info("Rebuilt aggregate tables")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_database(data_dir: str | Path, db_path: str | Path, *,
                   only_session: str | None = None) -> None:
    """Full rebuild into a fresh file, then atomic swap over the live DB (DB-4)."""
    data_dir = Path(data_dir)
    db_path = Path(db_path)
    tmp_path = db_path.with_suffix(db_path.suffix + ".building")
    for p in (tmp_path, tmp_path.with_suffix(tmp_path.suffix + "-wal"),
              tmp_path.with_suffix(tmp_path.suffix + "-shm")):
        if p.exists():
            p.unlink()

    conn = connect(tmp_path)
    try:
        init_schema(conn)

        # Periods (date ranges from the representative registries when present).
        reps = sorted((data_dir / "processed").glob("representatives-*.json"))
        for rp in reps:
            registry = json.loads(rp.read_text())
            _load_period_meta(conn, registry.get("meta", {}))
            load_representatives(conn, registry)

        sessions = sorted((data_dir / "processed").glob("*-session.json"))
        loaded = 0
        for sp in sessions:
            record = json.loads(sp.read_text())
            if only_session and record.get("meta", {}).get("session") != only_session:
                continue
            load_session(conn, record)
            loaded += 1

        rebuild_aggregates(conn)
        conn.execute("INSERT OR REPLACE INTO build_meta(key, value) VALUES (?,?)",
                     ("sessions_loaded", str(loaded)))
        conn.execute("INSERT OR REPLACE INTO build_meta(key, value) VALUES (?,?)",
                     ("source_dir", str(data_dir)))
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("ANALYZE")
        conn.commit()
    finally:
        conn.close()

    # Atomic swap (DB-4): rename the freshly built file over the live one.
    os.replace(tmp_path, db_path)
    for suffix in ("-wal", "-shm"):
        side = tmp_path.with_suffix(tmp_path.suffix + suffix)
        if side.exists():
            side.unlink()
    logger.info("Built database at %s", db_path)


def _load_period_meta(conn: sqlite3.Connection, meta: dict) -> None:
    num = meta.get("cycle")
    if num is None:
        return
    conn.execute(
        """INSERT INTO electoral_period(number, label, date_start, date_end)
           VALUES (?,?,?,?)
           ON CONFLICT(number) DO UPDATE SET
               date_start = COALESCE(excluded.date_start, electoral_period.date_start),
               date_end   = COALESCE(excluded.date_end, electoral_period.date_end)""",
        (num, f"{num}. ciklus", meta.get("cycleStart"), meta.get("cycleEnd")))
    conn.commit()


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _json_or_none(v):
    if not v:
        return None
    return json.dumps(v, ensure_ascii=False)


def _as_bool(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, str):
        return 1 if v.strip().lower() in ("i", "igen", "true", "1", "y") else 0
    return 1 if v else 0


def _first_source_page(record: dict):
    for sp in record.get("data", []):
        media = sp.get("media") or {}
        if media.get("sourcePage"):
            return media["sourcePage"]
    return None


def _day_duration(record: dict):
    for sp in record.get("data", []):
        d = (sp.get("media") or {}).get("duration")
        if d:
            return d
    return None


def _day_license(record: dict):
    for sp in record.get("data", []):
        v = (sp.get("media") or {}).get("license")
        if v:
            return v
    return None


def _day_creator(record: dict):
    for sp in record.get("data", []):
        v = (sp.get("media") or {}).get("creator")
        if v:
            return v
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the Országgyűlés Watch SQLite DB")
    ap.add_argument("data_dir", help="scraper data directory (contains processed/)")
    ap.add_argument("db_path", help="output SQLite file")
    ap.add_argument("--session", help="load only this session id (e.g. 43003)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("ogywatch.loader").setLevel(logging.INFO)
    build_database(args.data_dir, args.db_path, only_session=args.session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
