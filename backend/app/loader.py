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

    python -m app.loader ../data backend/parlamonitor.db          # full rebuild
    python -m app.loader ../data backend/parlamonitor.db --session 43003
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import kmonitor, nlp, nlp_modal, wikidata
from .config import settings
from .parlament_links import bill_page_url
from .wordfreq import count_words

logger = logging.getLogger("parlamonitor.loader")

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
                               wikidata_id, wikipedia_url,
                               photo_uri, photo_file, constituency, seat, email,
                               website, highest_education, active, is_mp,
                               education_json, committees_json, offices_json,
                               faction_history_json, election_history_json,
                               external_stats_json)
            VALUES (:pid, :label, :label_full, :firstname, :lastname,
                    :wikidata_id, :wikipedia_url, :photo_uri,
                    :photo_file, :constituency, :seat, :email, :website,
                    :highest_education, :active, 1, :education, :committees,
                    :offices, :faction_history, :election_history, :external_stats)
            ON CONFLICT(person_id) DO UPDATE SET
                label=excluded.label, label_full=excluded.label_full,
                firstname=excluded.firstname, lastname=excluded.lastname,
                wikidata_id=excluded.wikidata_id, wikipedia_url=excluded.wikipedia_url,
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
                "wikidata_id": rec.get("wikidataId"),
                "wikipedia_url": rec.get("wikipediaUrl"),
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

        # Rebuild this MP's membership row FOR THIS REGISTRY'S CYCLE only. The
        # registry's factionHistory `cycle` is a date-range *string* ("2026-"),
        # not a period number, so it can't drive integer period filters. We
        # therefore store ONE canonical membership row per cycle, keyed on the
        # registry's electoral period (the MP's faction in that cycle); the
        # richer, display-only history lives in faction_history_json (surfaced by
        # the profile endpoint). We still register every historical faction in
        # the faction table so its colour is known (REP-4).
        #
        # The delete MUST be period-scoped: an MP serving in several cycles has a
        # membership row per cycle (loaded from each cycle's registry), and a
        # blanket delete-by-person would wipe the other cycles' rows when this
        # registry is loaded — leaving the per-cycle list/faction scope (§4A)
        # showing only MPs unique to the last-loaded cycle.
        conn.execute("DELETE FROM membership WHERE person_id = ? AND period_number IS ?",
                     (pid, period))
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
# Bills (irományok) registry
# ---------------------------------------------------------------------------

def load_bills(conn: sqlite3.Connection, registry: dict) -> int:
    """Load a cycle's bills + sponsors (Bills module). Re-ingesting a cycle
    replaces its bills (idempotent, ING-4). Sponsors join to the shared person
    and faction tables (EXT-2); a sponsor whose kepviseloId isn't a known MP
    (or a government/committee submitter) keeps its display label but no link."""
    meta = registry.get("meta", {})
    period = meta.get("cycle")
    data = registry.get("data", [])

    if period is not None:
        conn.execute("INSERT INTO electoral_period(number) VALUES (?) "
                     "ON CONFLICT(number) DO NOTHING", (period,))

    # Replace this cycle's bills (delete children first for the FK). Every
    # per-bill child table is cleared so a re-ingest is fully idempotent.
    # bill_motion_sponsor keys on motion_id (not bill_id), so clear it first
    # via its parent motions before the bill-id-keyed tables are wiped.
    conn.execute(
        "DELETE FROM bill_motion_sponsor WHERE motion_id IN "
        "(SELECT id FROM bill_motion WHERE bill_id IN "
        "(SELECT id FROM bill WHERE period_number IS ?))", (period,))
    child_tables = ("bill_sponsor", "bill_event", "bill_committee_event",
                    "bill_vote", "bill_deadline", "bill_committee",
                    "bill_document", "bill_motion_summary", "bill_motion")
    for tbl in child_tables:
        conn.execute(
            f"DELETE FROM {tbl} WHERE bill_id IN "
            "(SELECT id FROM bill WHERE period_number IS ?)", (period,))
    conn.execute("DELETE FROM bill WHERE period_number IS ?", (period,))

    def _person(pid):
        """A kepviseloId, but only if it's a known MP (so the FK holds)."""
        if pid and conn.execute(
                "SELECT 1 FROM person WHERE person_id=?", (pid,)).fetchone():
            return pid
        return None

    for rec in data:
        bid = rec.get("billId")
        if not bid:
            continue
        text_url = rec.get("textUrl")
        h = (rec.get("detail") or {}).get("header") or {}
        conn.execute(
            """INSERT INTO bill(id, bill_number, number_sort, period_number,
                   title, type, main_type, status, submitted_date, text_url,
                   text_caption, source_url, no_text, stages_json,
                   subtype, character, negotiation_mode, status_type,
                   current_event, promulgation_number, mk_number,
                   promulgation_date, remark, last_modifier,
                   kozlony_url, kozlony_doc_url)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   bill_number=excluded.bill_number, number_sort=excluded.number_sort,
                   period_number=excluded.period_number, title=excluded.title,
                   type=excluded.type, main_type=excluded.main_type,
                   status=excluded.status, submitted_date=excluded.submitted_date,
                   text_url=excluded.text_url, text_caption=excluded.text_caption,
                   source_url=excluded.source_url, no_text=excluded.no_text,
                   stages_json=excluded.stages_json,
                   subtype=excluded.subtype, character=excluded.character,
                   negotiation_mode=excluded.negotiation_mode,
                   status_type=excluded.status_type, current_event=excluded.current_event,
                   promulgation_number=excluded.promulgation_number,
                   mk_number=excluded.mk_number, promulgation_date=excluded.promulgation_date,
                   remark=excluded.remark, last_modifier=excluded.last_modifier,
                   kozlony_url=excluded.kozlony_url,
                   kozlony_doc_url=excluded.kozlony_doc_url""",
            (bid, rec.get("billNumber"), rec.get("billNumberSort"), period,
             rec.get("title"), rec.get("type"), rec.get("mainType"),
             rec.get("status"), rec.get("submittedDate"), text_url,
             rec.get("textCaption"),
             bill_page_url(bid) or text_url or _BILL_PORTAL_FALLBACK,
             1 if rec.get("noText") else 0, _json_or_none(rec.get("stages")),
             h.get("subtype"), h.get("character"), h.get("negotiationMode"),
             h.get("statusType"), h.get("currentEvent"), h.get("promulgationNumber"),
             h.get("mkNumber"), h.get("promulgationDate"), h.get("remark"),
             h.get("lastModifier"), h.get("kozlonyUrl"), h.get("kozlonyDocUrl")))

        for i, sp in enumerate(rec.get("sponsors") or []):
            pid = _person(sp.get("personID"))
            faction_id = None
            ext = sp.get("factionId")
            if ext is not None:
                frow = conn.execute("SELECT id FROM faction WHERE ext_id=?",
                                    (ext,)).fetchone()
                faction_id = frow["id"] if frow else None
            conn.execute(
                "INSERT INTO bill_sponsor(bill_id, person_id, faction_id, label, ord) "
                "VALUES (?,?,?,?,?)",
                (bid, pid, faction_id, sp.get("label"), i))

        _load_bill_detail(conn, bid, rec.get("detail") or {}, _person)
    conn.commit()
    logger.info("Loaded %d bills (cycle %s)", len(data), period)
    return len(data)


def _load_bill_detail(conn: sqlite3.Connection, bill_id: str, detail: dict,
                      resolve_person) -> None:
    """Insert one bill's adatlap detail (events, votes, committees, deadlines,
    documents, motion summary, and the individual non-self-standing motions with
    their submitters) into the child tables. ``resolve_person`` maps a
    kepviseloId to a known person_id or None (EXT-2)."""
    for i, e in enumerate(detail.get("events") or []):
        conn.execute(
            """INSERT INTO bill_event(bill_id, ord, event_date, name, person_id,
                   related_label, committee_id, speech_number, speech_id,
                   vote_id, remark)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (bill_id, i, e.get("date"), e.get("name"),
             resolve_person(e.get("personID")), e.get("relatedLabel"),
             e.get("committeeId"), e.get("speechNumber"), e.get("speechId"),
             e.get("voteId"), e.get("remark")))

    for i, e in enumerate(detail.get("committeeEvents") or []):
        conn.execute(
            """INSERT INTO bill_committee_event(bill_id, ord, event_date, name,
                   committee, committee_id, person_id, person_label, amendment,
                   overreaching_amendment, report)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (bill_id, i, e.get("date"), e.get("name"), e.get("committee"),
             e.get("committeeId"), resolve_person(e.get("personID")),
             e.get("personLabel"), e.get("amendment"),
             e.get("overreachingAmendment"), e.get("report")))

    for i, v in enumerate(detail.get("votes") or []):
        conn.execute(
            """INSERT INTO bill_vote(bill_id, ord, vote_id, vote_date, subject,
                   yes, no, abstain, result)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (bill_id, i, v.get("voteId"), v.get("date"), v.get("subject"),
             v.get("yes"), v.get("no"), v.get("abstain"), v.get("result")))

    for i, d in enumerate(detail.get("deadlines") or []):
        conn.execute(
            "INSERT INTO bill_deadline(bill_id, ord, name, deadline, reference, remark) "
            "VALUES (?,?,?,?,?,?)",
            (bill_id, i, d.get("name"), d.get("deadline"), d.get("reference"),
             d.get("remark")))

    for i, c in enumerate(detail.get("committees") or []):
        conn.execute(
            "INSERT INTO bill_committee(bill_id, ord, committee, committee_id, "
            "role, reference, parts) VALUES (?,?,?,?,?,?,?)",
            (bill_id, i, c.get("committee"), c.get("committeeId"), c.get("role"),
             c.get("reference"), c.get("parts")))

    for i, d in enumerate(detail.get("documents") or []):
        conn.execute(
            "INSERT INTO bill_document(bill_id, ord, kind, title, url, doc_date, "
            "published) VALUES (?,?,?,?,?,?,?)",
            (bill_id, i, d.get("kind"), d.get("title"), d.get("url"),
             d.get("date"), d.get("published")))

    for i, m in enumerate(detail.get("motionSummary") or []):
        conn.execute(
            "INSERT INTO bill_motion_summary(bill_id, ord, type, valid, withdrawn, "
            "total) VALUES (?,?,?,?,?,?)",
            (bill_id, i, m.get("type"), m.get("valid"), m.get("withdrawn"),
             m.get("total")))

    def _faction(ext):
        if ext is None:
            return None
        frow = conn.execute("SELECT id FROM faction WHERE ext_id=?", (ext,)).fetchone()
        return frow["id"] if frow else None

    # The individual non-self-standing motions (each its own iromány with a PDF);
    # their submitters link to an MP profile where the kepviseloId is a known MP.
    for i, m in enumerate(detail.get("motions") or []):
        cur = conn.execute(
            """INSERT INTO bill_motion(bill_id, ord, iromany_id, bill_number,
                   number_sort, main_type, type, submitted_date, text_url,
                   text_caption, no_text, has_vote, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (bill_id, i, m.get("iromanyId"), m.get("billNumber"),
             m.get("billNumberSort"), m.get("mainType"), m.get("type"),
             m.get("submittedDate"), m.get("textUrl"), m.get("textCaption"),
             1 if m.get("noText") else 0, 1 if m.get("hasVote") else 0,
             m.get("note")))
        motion_id = cur.lastrowid
        for j, sp in enumerate(m.get("sponsors") or []):
            conn.execute(
                "INSERT INTO bill_motion_sponsor(motion_id, person_id, faction_id, "
                "label, ord) VALUES (?,?,?,?,?)",
                (motion_id, resolve_person(sp.get("personID")),
                 _faction(sp.get("factionId")), sp.get("label"), j))


# ---------------------------------------------------------------------------
# Votes (szavazások) registry
# ---------------------------------------------------------------------------

# Normalize the raw Hungarian vote type to a stable code for counting/colouring.
_VOTE_CODES = {
    "Igen": "yes",
    "Nem": "no",
    "Tartózkodás": "abstain",
    "Nem szavazott": "novote",
    "Jelen, nem szavazott": "novote",
    "Előre bejelentett hiányzó": "absent",
}


def _vote_code(value: str | None) -> str | None:
    if not value:
        return None
    return _VOTE_CODES.get(value.strip(), "other")


def load_votes(conn: sqlite3.Connection, registry: dict) -> int:
    """Load a cycle's roll-call votes (Votes module). Re-ingesting a cycle
    replaces its votes (idempotent, ING-4). The per-MP roll call joins to the
    shared person entity, each vote subject to a held bill, and each faction
    breakdown to the shared faction entity (EXT-2). A vote's id is the upstream
    szavazasId, matching ``bill_vote.vote_id`` — the link is by that shared key.

    Must run after representatives and bills so the person/bill/faction links
    resolve against already-loaded core entities."""
    meta = registry.get("meta", {})
    period = meta.get("cycle")
    data = registry.get("data", [])

    if period is not None:
        conn.execute("INSERT INTO electoral_period(number) VALUES (?) "
                     "ON CONFLICT(number) DO NOTHING", (period,))

    # Replace this cycle's votes (children first for the FK).
    for tbl in ("vote_record", "vote_faction_stat", "vote_subject"):
        conn.execute(
            f"DELETE FROM {tbl} WHERE vote_id IN "
            "(SELECT id FROM vote WHERE period_number IS ?)", (period,))
    conn.execute("DELETE FROM vote WHERE period_number IS ?", (period,))

    def _person(pid):
        if pid and conn.execute(
                "SELECT 1 FROM person WHERE person_id=?", (pid,)).fetchone():
            return pid
        return None

    def _faction(name):
        if not name:
            return None
        frow = conn.execute("SELECT id FROM faction WHERE label=?",
                            (name.strip(),)).fetchone()
        return frow["id"] if frow else None

    for rec in data:
        vid = rec.get("voteId")
        if not vid:
            continue
        detail = rec.get("detail") or {}
        h = detail.get("header") or {}
        # The upstream `hasKepviselo` flag is unreliable (false even when a roll
        # call exists), so derive has_per_mp from whether records were actually
        # returned — that is what the UI keys the roll-call section on.
        has_per_mp = 1 if (detail.get("records")) else 0
        conn.execute(
            """INSERT INTO vote(id, period_number, vote_datetime, voting_mode,
                   subject, result, yes, no, abstain, total_votes, has_per_mp, remark)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (vid, period, rec.get("datetime"), rec.get("votingMode"),
             rec.get("subject"), rec.get("result"), rec.get("yes"),
             rec.get("no"), rec.get("abstain"), h.get("totalVotes"),
             has_per_mp, h.get("remark")))

        for i, s in enumerate(rec.get("subjects") or []):
            # Store the raw iromanyId; the bill link is resolved at query time so
            # re-ingesting bills can't break votes (EXT-1).
            conn.execute(
                "INSERT INTO vote_subject(vote_id, ord, iromany_id, "
                "bill_number, title) VALUES (?,?,?,?,?)",
                (vid, i, s.get("billId"), s.get("billNumber"), s.get("title")))

        for r in detail.get("records") or []:
            conn.execute(
                "INSERT INTO vote_record(vote_id, person_id, name, faction_name, "
                "value, value_code) VALUES (?,?,?,?,?,?)",
                (vid, _person(r.get("personID")), r.get("name"),
                 r.get("factionName"), r.get("voteValue"),
                 _vote_code(r.get("voteValue"))))

        for i, fs in enumerate(detail.get("factionStats") or []):
            conn.execute(
                """INSERT INTO vote_faction_stat(vote_id, ord, faction_id,
                       faction_name, total, yes, no, abstain, absent, not_voting,
                       against_faction)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (vid, i, _faction(fs.get("factionName")), fs.get("factionName"),
                 fs.get("total"), fs.get("yes"), fs.get("no"), fs.get("abstain"),
                 fs.get("absent"), fs.get("notVoting"), fs.get("againstFaction")))

    conn.commit()
    logger.info("Loaded %d votes (cycle %s)", len(data), period)
    return len(data)


# Bills have no clean per-bill permalink on the modern portal; the text PDF is
# the most specific resolvable original (LEGAL-1). This generic search page is
# the fallback when a bill has no text.
_BILL_PORTAL_FALLBACK = "https://www.parlament.hu/web/guest/iromanyok-lekerdezese"


# ---------------------------------------------------------------------------
# Session record
# ---------------------------------------------------------------------------

def _ensure_session_status(conn: sqlite3.Connection) -> None:
    """Add ``session.status`` to a pre-existing DB, so the upcoming/scheduled-day
    feature also lands via the incremental ``--update`` path (which snapshots the
    live DB rather than re-running the schema), not only a full rebuild. A no-op on
    a freshly-built DB whose schema already has the column."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(session)")]
    if cols and "status" not in cols:
        conn.execute("ALTER TABLE session ADD COLUMN status TEXT DEFAULT 'published'")


def _ensure_speaker_office(conn: sqlite3.Connection) -> None:
    """Add ``speech.speaker_office`` to a pre-existing DB, so the speaker-office
    feature lands via the incremental ``--update`` path too (which snapshots the
    live DB rather than re-running the schema). Without this, the first
    ``_load_speech`` INSERT after a code deploy would fail on the missing column
    on a DB built by the old schema. A no-op on a freshly-built DB. NB this only
    adds the column; existing speeches are back-filled by ``migrate_speaker_office``
    (a re-scrape/re-transform repopulates it from the raw ``role`` on its own)."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(speech)")]
    if cols and "speaker_office" not in cols:
        conn.execute("ALTER TABLE speech ADD COLUMN speaker_office TEXT")


def load_session(conn: sqlite3.Connection, record: dict) -> str:
    """Load one sitting-day session record atomically (ING-3). Re-ingesting a
    session replaces all of its derived rows (ING-4)."""
    meta = record.get("meta", {})
    sid = meta.get("session")
    period = meta.get("electoralPeriod")
    try:
        _ensure_session_status(conn)
        _ensure_speaker_office(conn)
        # delete-then-insert keyed on session id => idempotent replace (ING-4).
        _delete_session(conn, sid)

        if period is not None:
            conn.execute(
                "INSERT INTO electoral_period(number) VALUES (?) "
                "ON CONFLICT(number) DO NOTHING", (period,))

        conn.execute(
            """INSERT INTO session(id, period_number, sitting, date, date_start,
                   date_end, status, source, source_page, scraped_at, timing_method,
                   video_uri, video_playseq, video_duration, video_license,
                   video_creator)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, period, meta.get("sitting"), meta.get("date"),
             meta.get("dateStart"), meta.get("dateEnd"),
             meta.get("status") or "published", meta.get("source"),
             meta.get("sourcePage") or _first_source_page(record),
             meta.get("sourceScrapedAt"),
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
    conn.execute("DELETE FROM session_word_count WHERE session_id = ?", (sid,))
    conn.execute("DELETE FROM session WHERE id = ?", (sid,))


def _load_speech(conn, sid, period, sp, agenda_cache) -> None:
    ag = sp.get("agendaItem") or {}
    # Group by ACT IDENTITY (title + official title), NOT by type: one act's
    # speeches can carry different per-speech types, and including type here
    # split a single act into several sections (e.g. each interpelláció, and
    # "Személyes érintettség"). The first speech of the act fixes its type.
    ag_key = (ag.get("title"), ag.get("officialTitle"))
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
    # Per-speech type (felszólalás típusa); a chairing type marks the speech
    # procedural so it is excluded from statistics but still stored/shown (STAT-1).
    speech_type = debug.get("felszolalasTipusa")
    procedural = 1 if settings.is_procedural_type(speech_type) else 0
    conn.execute(
        """INSERT INTO speech(uid, origin_id, speech_uuid, session_id, agenda_item_id,
               period_number, speech_index, person_id, speaker_label,
               speaker_status, speaker_office, felszolalas_tipus, procedural, faction_id,
               time_start, time_end, video_start,
               video_end, duration, confidence, align_method, has_text,
               source_uri, source_page)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (uid, origin_id, debug.get("speechUUID"), sid, agenda_id, period,
         speech_index, pid,
         speaker.get("label"), speaker.get("context"), speaker.get("office"),
         speech_type, procedural,
         faction_id,
         time_start, time_end, media.get("videoStart"), media.get("videoEnd"),
         duration, debug.get("confidence"), debug.get("align-method"),
         has_text, source_uri, media.get("sourcePage")))

    for i, s in enumerate(sentences):
        conn.execute(
            "INSERT INTO sentence(speech_id, ord, text, time_start, time_end, paragraph) "
            "VALUES (?,?,?,?,?,?)",
            (uid, i, s["text"], s.get("timeStart"), s.get("timeEnd"),
             s.get("paragraph")))


def wire_nonmp_photos(conn: sqlite3.Connection, photos_dir) -> int:
    """Point non-MP speakers' profiles at their downloaded portrait (REP-2).

    Ministers and nationality advocates (nemzetiségi szószólók) who aren't in the
    MP roster are created as bare stubs (``_ensure_person``) with no photo. The
    scraper's ``speaker-photos`` step drops ``<pid>.jpg`` into the photos dir for
    those that have a portrait upstream (an advocate like Gallai Gergely does; a
    portrait-less minister does not). Here we set ``photo_uri``/``photo_file`` to
    that file wherever it exists on disk. Idempotent and global (not per-session),
    so it wires every downloaded portrait whenever any load runs — only touches
    non-MP rows that don't already have a photo, and never overrides an MP's
    roster portrait."""
    photos = Path(photos_dir)
    if not photos.is_dir():
        return 0
    rows = conn.execute(
        "SELECT person_id FROM person WHERE COALESCE(is_mp, 0) = 0 "
        "AND (photo_uri IS NULL OR photo_uri = '')").fetchall()
    wired = 0
    for r in rows:
        pid = r[0]
        if (photos / f"{pid}.jpg").exists():
            conn.execute(
                "UPDATE person SET photo_uri = ?, photo_file = ? WHERE person_id = ?",
                (f"/media/photos/{pid}.jpg", f"{pid}.jpg", pid))
            wired += 1
    return wired


# ---------------------------------------------------------------------------
# Aggregates (rebuilt every run — REP-7)
# ---------------------------------------------------------------------------

def rebuild_aggregates(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM person_stats")
    conn.execute("DELETE FROM faction_stats")
    conn.execute("DELETE FROM person_session_stats")

    # All aggregates count only statistics-eligible speeches: procedural/chairing
    # speeches (s.procedural = 1) are stored and shown in the viewer but never
    # counted toward a representative's or faction's totals (STAT-1).

    # Per person, per period + an all-periods row (period_number IS NULL).
    conn.execute(
        """INSERT INTO person_stats(person_id, period_number, speech_count,
               speaking_seconds, sentence_count)
           SELECT s.person_id, s.period_number, COUNT(*),
                  COALESCE(SUM(s.duration), 0),
                  COALESCE(SUM((SELECT COUNT(*) FROM sentence se
                               WHERE se.speech_id = s.uid)), 0)
           FROM speech s WHERE s.person_id IS NOT NULL AND s.procedural = 0
           GROUP BY s.person_id, s.period_number""")
    conn.execute(
        """INSERT INTO person_stats(person_id, period_number, speech_count,
               speaking_seconds, sentence_count)
           SELECT s.person_id, NULL, COUNT(*), COALESCE(SUM(s.duration), 0),
                  COALESCE(SUM((SELECT COUNT(*) FROM sentence se
                               WHERE se.speech_id = s.uid)), 0)
           FROM speech s WHERE s.person_id IS NOT NULL AND s.procedural = 0
           GROUP BY s.person_id""")

    conn.execute(
        """INSERT INTO faction_stats(faction_id, period_number, speech_count,
               speaking_seconds, mp_count)
           SELECT s.faction_id, s.period_number, COUNT(*),
                  COALESCE(SUM(s.duration), 0), COUNT(DISTINCT s.person_id)
           FROM speech s WHERE s.faction_id IS NOT NULL AND s.procedural = 0
           GROUP BY s.faction_id, s.period_number""")
    # All-periods row (period_number IS NULL) consumed by the factions endpoint.
    conn.execute(
        """INSERT INTO faction_stats(faction_id, period_number, speech_count,
               speaking_seconds, mp_count)
           SELECT s.faction_id, NULL, COUNT(*), COALESCE(SUM(s.duration), 0),
                  COUNT(DISTINCT s.person_id)
           FROM speech s WHERE s.faction_id IS NOT NULL AND s.procedural = 0
           GROUP BY s.faction_id""")

    conn.execute(
        """INSERT INTO person_session_stats(person_id, session_id, date,
               speech_count, speaking_seconds)
           SELECT s.person_id, s.session_id, ss.date, COUNT(*),
                  COALESCE(SUM(s.duration), 0)
           FROM speech s JOIN session ss ON ss.id = s.session_id
           WHERE s.person_id IS NOT NULL AND s.procedural = 0
           GROUP BY s.person_id, s.session_id""")
    conn.commit()
    rebuild_word_doc_freq(conn)
    rebuild_word_first_seen(conn)
    logger.info("Rebuilt aggregate tables")


def _wordcloud_backend() -> str:
    """Resolve the configured word-cloud backend to a concrete one ("modal",
    "huspacy" or "regex").

    "modal" (opt-in) offloads the HuSpaCy pipeline to Modal; if the client isn't
    installed / not authenticated it degrades to local HuSpaCy, then regex.
    "auto" prefers local HuSpaCy when its model loads, else regex (it never
    auto-selects Modal). An explicit backend that can't be used is logged and
    degrades so a build on a bare host still succeeds (OPS-4)."""
    want = settings.wordcloud_backend or "auto"
    if want == "regex":
        return "regex"
    if want == "modal":
        if nlp_modal.available():
            return "modal"
        logger.warning("wordcloud_backend=modal unavailable; trying local HuSpaCy, "
                       "then the regex tokenizer")
    if nlp.available():
        return "huspacy"
    if want == "huspacy":
        logger.warning("wordcloud_backend=huspacy but the model is unavailable; "
                       "using the regex tokenizer instead")
    return "regex"


def _words_map(counts, entity_words) -> dict:
    """Term→[count, kind] map stored in ``session_word_count`` (kind flags named
    entities so the cloud can style them)."""
    return {w: [c, "entity" if w in entity_words else "term"]
            for w, c in counts.items()}


def _wordcloud_cache_path(cache_dir: Path) -> Path:
    return Path(cache_dir) / "wordcloud-cache.json"


def _session_fingerprint(method: str, texts: list[str]) -> str:
    """A stable hash of a sitting's text + the extraction method, so the cache is
    reused only when neither the transcript nor the method has changed."""
    h = hashlib.sha1(method.encode("utf-8"))
    for t in texts:
        h.update(b"\x1f")
        h.update((t or "").encode("utf-8"))
    return h.hexdigest()


def rebuild_session_word_counts(conn: sqlite3.Connection,
                                cache_dir: str | Path | None = None,
                                only_sessions: set[str] | None = None) -> None:
    """Precompute per-sitting topical term frequencies for the word cloud (WCLOUD-2).

    For each session, lemmatize its non-procedural sentence text and extract named
    entities with HuSpaCy (or fall back to the regex tokenizer), storing the
    term→count map in ``session_word_count``. This is the expensive step, so its
    output is cached on disk (``wordcloud-cache.json`` in ``cache_dir``) keyed by a
    fingerprint of the text + method: a full rebuild reprocesses only the sittings
    whose transcript actually changed (cf. the scraper's detail cache). The cache
    is optional — without ``cache_dir`` (or if it can't be read/written) everything
    is simply recomputed, so it must point at a writable location (the source data
    dir is mounted read-only in the container; use the DB's dir instead).

    ``only_sessions`` scopes the pass to just those session ids (the incremental
    ``--update`` path): only their ``session_word_count`` rows are cleared and
    recomputed, leaving every other sitting's rows intact — so an update never
    re-reads the whole corpus's sentences just to refresh one changed day.

    With ``wordcloud_backend="modal"`` the HuSpaCy analysis runs on Modal workers
    (WCLOUD-6); only cache-miss sittings are sent, packed into batches, so credit
    tracks actual new work. Output is identical to the local ``huspacy`` backend,
    so the disk cache is shared between them.
    """
    backend = _wordcloud_backend()
    if backend == "modal":
        method = nlp_modal.method_tag()
    elif backend == "huspacy":
        method = nlp.method_tag()
    else:
        method = "regex:v1"
    logger.info("Word-cloud term extraction backend: %s", backend)

    cache_path = _wordcloud_cache_path(cache_dir) if cache_dir else None
    cache: dict = {"method": method, "sessions": {}}
    if cache_path and cache_path.exists():
        try:
            loaded = json.loads(cache_path.read_text())
            if loaded.get("method") == method:        # else stale → start fresh
                cache = loaded
        except (OSError, ValueError):
            logger.warning("Could not read word-cloud cache %s; recomputing", cache_path)

    def _flush_cache():
        if not cache_path:
            return
        try:
            cache_path.write_text(json.dumps(cache, ensure_ascii=False))
        except OSError as exc:
            logger.warning("Could not write word-cloud cache %s (%s)", cache_path, exc)

    if only_sessions is None:
        conn.execute("DELETE FROM session_word_count")
        # Latest sittings first (id desc) so the current cycle's clouds are ready
        # first when a from-scratch lemmatization pass is long.
        sids = [r[0] for r in conn.execute("SELECT id FROM session ORDER BY id DESC")]
    else:
        sids = [r[0] for r in conn.execute("SELECT id FROM session ORDER BY id DESC")
                if r[0] in only_sessions]
        conn.executemany("DELETE FROM session_word_count WHERE session_id = ?",
                         [(s,) for s in sids])

    def _fetch(sid):
        return [t for (t,) in conn.execute(
            "SELECT se.text FROM sentence se "
            "JOIN speech sp ON sp.uid = se.speech_id "
            "WHERE sp.session_id = ? AND sp.procedural = 0 AND se.text IS NOT NULL",
            (sid,))]

    def _write(sid, words):
        if words:
            conn.executemany(
                "INSERT INTO session_word_count(session_id, word, count, kind) "
                "VALUES (?, ?, ?, ?)",
                [(sid, w, c, k) for w, (c, k) in words.items()])

    reused = recomputed = 0

    def _emit(sid, fp, words):
        # Store a freshly-computed sitting: cache it, write its rows, and commit +
        # flush the cache periodically so a long pass is resumable (a re-run skips
        # what's already done rather than losing everything).
        nonlocal recomputed
        cache["sessions"][sid] = {"fp": fp, "words": words}
        _write(sid, words)
        recomputed += 1
        if recomputed % 10 == 0:
            conn.commit()
            _flush_cache()
            logger.info("session_word_count progress: %d recomputed", recomputed)

    if backend == "modal":
        # Split cache hits (written now) from misses; ship the misses to Modal in
        # batches and write each result as it returns.
        misses = []  # (sid, fp, texts)
        for sid in sids:
            texts = _fetch(sid)
            fp = _session_fingerprint(method, texts)
            entry = cache["sessions"].get(sid)
            if entry and entry.get("fp") == fp:
                _write(sid, entry["words"])
                reused += 1
            else:
                misses.append((sid, fp, texts))
        conn.commit()
        for sid, fp, words in nlp_modal.extract(misses):
            _emit(sid, fp, words)
    else:
        # Local: process one sitting at a time (low memory) — HuSpaCy or regex.
        for sid in sids:
            texts = _fetch(sid)
            fp = _session_fingerprint(method, texts)
            entry = cache["sessions"].get(sid)
            if entry and entry.get("fp") == fp:
                _write(sid, entry["words"])
                reused += 1
                continue
            if backend == "huspacy":
                counts, entity_words = nlp.analyze_counts(texts)
            else:
                counts, entity_words = count_words(texts), set()
            _emit(sid, fp, _words_map(counts, entity_words))

    conn.commit()
    _flush_cache()
    logger.info("session_word_count: %d sittings (%d processed, %d cached)",
                len(sids), recomputed, reused)


# Bump when the entity-span extraction logic changes in a way that should
# invalidate the on-disk entity cache even if the model/text are unchanged.
# ent-v2: extract ORG (institutions) alongside PER, and carry the span `kind`.
_ENTITY_LOGIC = "ent-v2"


def _entity_cache_path(cache_dir: Path) -> Path:
    return Path(cache_dir) / "entity-cache.json"


def _ensure_entity_tables(conn: sqlite3.Connection) -> None:
    """Make the NEL tables match the current schema, in place — so the feature
    (and its later extensions) also land on an existing DB via the incremental
    ``--update`` path, not only a full rebuild. The old reserved ``entity`` shape is
    replaced; a ``kind`` column is added to ``entity``; ``entity_link`` is (re)created
    in its current multi-destination shape; and ``person.kmonitor_url`` is added."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(entity)")]
    if cols and "entity_key" not in cols:
        conn.execute("DROP TABLE entity")
        cols = []
    if not cols:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS entity (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                sentence_id INTEGER NOT NULL REFERENCES sentence(id),
                entity_key  TEXT NOT NULL,
                surface     TEXT NOT NULL,
                char_start  INTEGER,
                char_end    INTEGER,
                kind        TEXT NOT NULL DEFAULT 'PER'
            );
            CREATE INDEX IF NOT EXISTS idx_entity_sentence ON entity(sentence_id);
            CREATE INDEX IF NOT EXISTS idx_entity_key ON entity(entity_key);
        """)
    elif "kind" not in cols:
        conn.execute("ALTER TABLE entity ADD COLUMN kind TEXT NOT NULL DEFAULT 'PER'")

    # entity_link is rebuilt wholesale on every resolve, so if its columns don't
    # match the current (multi-destination) shape just drop and recreate it — no
    # data is lost that the next resolve wouldn't repopulate.
    el_cols = [r[1] for r in conn.execute("PRAGMA table_info(entity_link)")]
    if el_cols and "links_json" not in el_cols:
        conn.execute("DROP TABLE entity_link")
        el_cols = []
    if not el_cols:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS entity_link (
                entity_key      TEXT PRIMARY KEY,
                kind            TEXT,
                ambiguous       INTEGER DEFAULT 0,
                links_json      TEXT,
                resolved_at     TEXT
            );
        """)

    person_cols = [r[1] for r in conn.execute("PRAGMA table_info(person)")]
    if person_cols and "kmonitor_url" not in person_cols:
        conn.execute("ALTER TABLE person ADD COLUMN kmonitor_url TEXT")


def _delete_session_entities(conn: sqlite3.Connection, sid: str) -> None:
    conn.execute(
        "DELETE FROM entity WHERE sentence_id IN (SELECT se.id FROM sentence se "
        "JOIN speech sp ON sp.uid = se.speech_id WHERE sp.session_id = ?)", (sid,))


def rebuild_entity_mentions(conn: sqlite3.Connection,
                            cache_dir: str | Path | None = None,
                            only_sessions: set[str] | None = None) -> None:
    """Extract PERSON + ORGANISATION mentions from transcript sentences into the
    ``entity`` table (NEL, §10) so the transcript can link names inline.

    Uses the same HuSpaCy backend as the word cloud (local model, or Modal when
    the host has no model); when neither is available the pass is skipped so a
    bare-host build still succeeds (OPS-4). Cached on disk like the word cloud
    (``entity-cache.json``), keyed by a fingerprint of the text + method, so a
    rebuild re-runs NER only for the sittings whose transcript actually changed.
    ``only_sessions`` scopes the pass to just those ids (the ``--update`` path).

    This only fills ``entity`` (the mention spans); resolving each distinct name
    to a Wikidata item/Wikipedia article is a separate, network-side step
    (``app.wikidata.resolve_entities`` → ``entity_link``)."""
    if not settings.entity_links:
        return
    _ensure_entity_tables(conn)
    # Person spans need the neural NER; the regex tokenizer can't produce them.
    if nlp.available():
        method, use_modal = nlp.method_tag() + ":" + _ENTITY_LOGIC, False
    elif _wordcloud_backend() == "modal" and nlp_modal.available():
        method, use_modal = nlp_modal.method_tag() + ":" + _ENTITY_LOGIC, True
    else:
        logger.info("entity extraction skipped (no HuSpaCy model available)")
        return

    cache_path = _entity_cache_path(cache_dir) if cache_dir else None
    cache: dict = {"method": method, "sessions": {}}
    if cache_path and cache_path.exists():
        try:
            loaded = json.loads(cache_path.read_text())
            if loaded.get("method") == method:
                cache = loaded
        except (OSError, ValueError):
            logger.warning("Could not read entity cache %s; recomputing", cache_path)

    def _flush():
        if cache_path:
            try:
                cache_path.write_text(json.dumps(cache, ensure_ascii=False))
            except OSError as exc:
                logger.warning("Could not write entity cache %s (%s)", cache_path, exc)

    if only_sessions is None:
        sids = [r[0] for r in conn.execute("SELECT id FROM session ORDER BY id DESC")]
        conn.execute("DELETE FROM entity")
    else:
        sids = [r[0] for r in conn.execute("SELECT id FROM session ORDER BY id DESC")
                if r[0] in only_sessions]
        for sid in sids:
            _delete_session_entities(conn, sid)

    def _fetch(sid):
        # Deterministic order (by sentence PK = insertion order) so cached spans
        # re-associate to the right sentence rows on reuse. Non-procedural only,
        # matching the word-cloud entity source.
        return conn.execute(
            "SELECT se.id, se.text FROM sentence se "
            "JOIN speech sp ON sp.uid = se.speech_id "
            "WHERE sp.session_id = ? AND sp.procedural = 0 AND se.text IS NOT NULL "
            "ORDER BY se.id", (sid,)).fetchall()

    def _write(sent_rows, per_sentence_spans) -> int:
        rows = []
        for (sentence_id, _text), spans in zip(sent_rows, per_sentence_spans):
            for span in spans:
                # ent-v2 spans are (surface, start, end, key, kind); tolerate a
                # legacy 4-tuple from an older cache by defaulting kind to PER.
                surface, start, end, key = span[0], span[1], span[2], span[3]
                kind = span[4] if len(span) > 4 else "PER"
                rows.append((sentence_id, key, surface, start, end, kind))
        if rows:
            conn.executemany(
                "INSERT INTO entity(sentence_id, entity_key, surface, char_start, char_end, kind) "
                "VALUES (?,?,?,?,?,?)", rows)
        return len(rows)

    processed = reused = mentions = 0
    misses = []  # (sid, fp, sent_rows)
    for sid in sids:
        sent_rows = _fetch(sid)
        fp = _session_fingerprint(method, [t for (_i, t) in sent_rows])
        entry = cache["sessions"].get(sid)
        if entry and entry.get("fp") == fp:
            mentions += _write(sent_rows, entry["spans"])
            reused += 1
        else:
            misses.append((sid, fp, sent_rows))

    def _emit(sid, fp, sent_rows, per_sentence_spans):
        nonlocal processed, mentions
        cache["sessions"][sid] = {"fp": fp, "spans": per_sentence_spans}
        mentions += _write(sent_rows, per_sentence_spans)
        processed += 1
        if processed % 10 == 0:
            conn.commit()
            _flush()
            logger.info("entity mentions progress: %d sittings processed", processed)

    try:
        if use_modal:
            packed = [(sid, fp, [t for (_i, t) in rows]) for (sid, fp, rows) in misses]
            by_sid = {sid: rows for (sid, _fp, rows) in misses}
            for sid, fp, spans in nlp_modal.extract_spans(packed):
                _emit(sid, fp, by_sid[sid], spans)
        else:
            for (sid, fp, sent_rows) in misses:
                spans = [[list(s) for s in per_sent]
                         for per_sent in nlp.entity_spans([t for (_i, t) in sent_rows])]
                _emit(sid, fp, sent_rows, spans)
    except Exception as exc:  # enrichment must never break the build (SCR-5)
        logger.warning("entity extraction aborted (%s); keeping what was done", exc)

    conn.commit()
    _flush()
    logger.info("entity mentions: %d sittings (%d processed, %d cached), %d PER+ORG mentions",
                len(sids), processed, reused, mentions)


def _entity_kinds(conn: sqlite3.Connection) -> dict[str, str]:
    """Each distinct ``entity_key`` → its majority ``kind`` (PER/ORG). One key is
    almost always one kind; the rare mixed key takes whichever mention kind is more
    frequent. Shared by both resolvers so they agree on how to query/match a name."""
    try:
        rows = conn.execute(
            "SELECT entity_key, kind, COUNT(*) c FROM entity GROUP BY entity_key, kind"
        ).fetchall()
    except sqlite3.OperationalError:  # pre-NEL DB
        return {}
    best: dict[str, tuple[int, str]] = {}
    for r in rows:
        key, kind, c = r[0], (r[1] or "PER"), r[2]
        if key not in best or c > best[key][0]:
            best[key] = (c, kind)
    return {k: v[1] for k, v in best.items()}


def resolve_entity_links(conn: sqlite3.Connection,
                         cache_dir: str | Path | None = None) -> None:
    """Resolve recognized names to their inline destinations and set MP K-Monitor
    links (NEL, §10). Fetches the K-Monitor tag index once, then: sets
    ``person.kmonitor_url`` for MP profiles, gathers each transcript name's Wikidata
    candidates, and writes ``entity_link`` (K-Monitor primary, Wikipedia fallback).

    Each step degrades independently (no network / disabled flag / no mentions), so
    a build never fails on enrichment (SCR-5)."""
    index = kmonitor.load_index(cache_dir)   # {} when disabled or a fetch fails

    # MP-profile K-Monitor links are independent of the transcript entities.
    if settings.kmonitor_links:
        try:
            kmonitor.resolve_representatives(conn, index)
        except Exception as exc:
            logger.warning("K-Monitor MP linking failed (%s); skipping", exc)

    if not settings.entity_links:
        return
    kinds = _entity_kinds(conn)
    if not kinds:
        return
    try:
        # Institutions already in K-Monitor need no Wikidata query (K-Monitor is the
        # primary target; Wikipedia is only a fallback, and the unfiltered ORG query
        # is the costly one). People are always queried, for the bounded Q5 MP match.
        skip = {k for k, kind in kinds.items()
                if kind == "ORG" and kmonitor._match(index, k, "ORG")}
        wd = wikidata.resolve_candidates(conn, cache_dir, kinds, skip=skip)
        kmonitor.resolve_links(conn, kinds, wd, index)
    except Exception as exc:  # enrichment must never break the build (SCR-5)
        logger.warning("entity link resolution failed (%s); leaving links as-is", exc)


def rebuild_word_doc_freq(conn: sqlite3.Connection) -> None:
    """Per-cycle word document-frequencies for the word cloud's TF·IDF (WCLOUD-2).

    The corpus side of TF·IDF, derived purely from the precomputed
    ``session_word_count``: for each period, on how many sitting days each term
    appears (once per day) and how many days contributed any term. The per-day
    term frequency is read from the same table at query time.
    """
    conn.execute("DELETE FROM word_doc_freq")
    conn.execute("DELETE FROM word_doc_total")
    conn.execute(
        """INSERT INTO word_doc_freq(period_number, word, doc_count)
           SELECT ss.period_number, swc.word, COUNT(DISTINCT swc.session_id)
           FROM session_word_count swc
           JOIN session ss ON ss.id = swc.session_id
           WHERE ss.period_number IS NOT NULL
           GROUP BY ss.period_number, swc.word""")
    conn.execute(
        """INSERT INTO word_doc_total(period_number, n_docs)
           SELECT ss.period_number, COUNT(DISTINCT swc.session_id)
           FROM session_word_count swc
           JOIN session ss ON ss.id = swc.session_id
           WHERE ss.period_number IS NOT NULL
           GROUP BY ss.period_number""")
    conn.commit()


def rebuild_word_first_seen(conn: sqlite3.Connection) -> None:
    """When each word was first ever spoken in the chamber (NEW-1).

    Derived purely from the precomputed ``session_word_count`` + session dates:
    for every distinct word, the earliest sitting day (across ALL electoral
    cycles, previous ones included) that contains it — ordered by date then id so
    a same-day tie is broken deterministically. The sitting-day page reads this
    to surface the words that *debuted* on that day (never said before).
    """
    conn.execute("DELETE FROM word_first_seen")
    conn.execute(
        """INSERT INTO word_first_seen(word, session_id, date, kind)
           SELECT word, session_id, date, kind FROM (
               SELECT swc.word, swc.session_id, ss.date, swc.kind,
                      ROW_NUMBER() OVER (
                          PARTITION BY swc.word
                          ORDER BY ss.date, ss.id) AS rn
               FROM session_word_count swc
               JOIN session ss ON ss.id = swc.session_id
           ) WHERE rn = 1""")
    conn.commit()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_database(data_dir: str | Path, db_path: str | Path, *,
                   only_session: str | None = None,
                   skip_wordcloud: bool = False) -> None:
    """Full rebuild into a fresh file, then atomic swap over the live DB (DB-4).

    ``skip_wordcloud`` skips the (expensive) per-sitting term extraction that feeds
    the word cloud / new-words features (WCLOUD-2). Those tables are left empty, so
    those views come up blank — handy for a fast dev rebuild when they aren't needed.
    """
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

        # Bills load after representatives so sponsor person/faction links
        # resolve against already-loaded core entities (EXT-2).
        for bp in sorted((data_dir / "processed").glob("bills-*.json")):
            load_bills(conn, json.loads(bp.read_text()))

        # Votes load after bills so a vote's subject links to a held bill and its
        # roll call / faction breakdown to loaded persons/factions (EXT-2).
        for vp in sorted((data_dir / "processed").glob("votes-*.json")):
            load_votes(conn, json.loads(vp.read_text()))

        sessions = sorted((data_dir / "processed").glob("*-session.json"))
        loaded = 0
        for sp in sessions:
            record = json.loads(sp.read_text())
            if only_session and record.get("meta", {}).get("session") != only_session:
                continue
            load_session(conn, record)
            loaded += 1

        # Lemmatize / entity-extract each sitting's text into session_word_count
        # before the aggregates so word_doc_freq can derive from it (WCLOUD-2);
        # cached on disk in the DB's dir (writable + persisted; the source data
        # dir is mounted read-only) so unchanged sittings aren't reprocessed.
        if skip_wordcloud:
            logger.info("Skipping word-cloud term extraction (--skip-wordcloud)")
        else:
            rebuild_session_word_counts(conn, db_path.parent)
        # Entity NEL (§10): extract PERSON + ORG mentions, then resolve names to
        # K-Monitor (primary) / Wikipedia (fallback) and set MP K-Monitor links.
        # Every step degrades gracefully (no model / no network / disabled).
        if not skip_wordcloud:
            rebuild_entity_mentions(conn, db_path.parent)
            resolve_entity_links(conn, db_path.parent)
        rebuild_aggregates(conn)
        wire_nonmp_photos(conn, Path(data_dir) / "media" / "photos")
        conn.execute("INSERT OR REPLACE INTO build_meta(key, value) VALUES (?,?)",
                     ("sessions_loaded", str(loaded)))
        conn.execute("INSERT OR REPLACE INTO build_meta(key, value) VALUES (?,?)",
                     ("source_dir", str(data_dir)))
        # When the data was last (re)built — surfaced in /meta so the UI can show
        # an "utolsó adatfrissítés" line. Timezone-aware UTC; the SPA localises it.
        conn.execute("INSERT OR REPLACE INTO build_meta(key, value) VALUES (?,?)",
                     ("data_updated_at", datetime.now(timezone.utc).isoformat()))
        # Record every processed file's (mtime, size) so a later `--update` can
        # tell which sittings/registries changed and reload only those (SCR-2).
        _seed_load_state(conn, data_dir)
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("ANALYZE")
        conn.commit()
    finally:
        conn.close()

    # Atomic swap (DB-4): rename the freshly built file over the live one.
    os.replace(tmp_path, db_path)
    _remove_db_side_files(tmp_path)
    logger.info("Built database at %s", db_path)


# ---------------------------------------------------------------------------
# Incremental update (SCR-2 / DB-4) — reload only changed files, then swap
# ---------------------------------------------------------------------------

# The processed-file globs, in the load order full builds use (reps → bills →
# votes → sessions) so cross-module person/faction/bill links resolve (EXT-2).
_PROCESSED_GLOBS = ("representatives-*.json", "bills-*.json",
                    "votes-*.json", "*-session.json")


def _file_sig(path: Path) -> tuple[float, int]:
    st = path.stat()
    return (round(st.st_mtime, 3), st.st_size)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone() is not None


def _read_load_state(conn: sqlite3.Connection) -> dict[str, tuple[float, int]]:
    return {r["name"]: (r["mtime"], r["size"])
            for r in conn.execute("SELECT name, mtime, size FROM load_state")}


def _write_load_state(conn: sqlite3.Connection, path: Path) -> None:
    mtime, size = _file_sig(path)
    conn.execute(
        "INSERT INTO load_state(name, mtime, size) VALUES (?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET mtime=excluded.mtime, size=excluded.size",
        (path.name, mtime, size))


def _seed_load_state(conn: sqlite3.Connection, data_dir: Path) -> None:
    conn.execute("DELETE FROM load_state")
    processed = Path(data_dir) / "processed"
    for pattern in _PROCESSED_GLOBS:
        for p in sorted(processed.glob(pattern)):
            _write_load_state(conn, p)


def _changed_files(processed: Path, pattern: str,
                   prev: dict[str, tuple[float, int]]) -> list[Path]:
    """Processed files matching ``pattern`` whose (mtime, size) differs from the
    last load (or that are brand new)."""
    out = []
    for p in sorted(processed.glob(pattern)):
        if prev.get(p.name) != _file_sig(p):
            out.append(p)
    return out


def _remove_db_side_files(base: Path) -> None:
    for suffix in ("-wal", "-shm"):
        side = base.with_suffix(base.suffix + suffix)
        if side.exists():
            side.unlink()


def _remove_db_files(base: Path) -> None:
    if base.exists():
        base.unlink()
    _remove_db_side_files(base)


def update_database(data_dir: str | Path, db_path: str | Path, *,
                    skip_wordcloud: bool = False) -> bool:
    """Incrementally reconcile the live DB to the scraper's processed JSON.

    Compares each ``processed/*.json`` against the ``load_state`` recorded at the
    last build/update and reloads **only the changed files** into a private
    snapshot of the current DB, rebuilds the (cheap, SQL-only) aggregates, and
    atomically swaps the result over the live file (DB-4) — so the running API
    picks it up on its next request with no restart and no downtime, having
    reprocessed only what actually changed (SCR-2).

    Degrades to a full :func:`build_database` when there is no DB yet, or when the
    existing DB predates the ``load_state`` table (one rebuild seeds the baseline).
    Returns ``True`` if the DB was changed, ``False`` if nothing was stale.
    """
    data_dir = Path(data_dir)
    db_path = Path(db_path)
    processed = data_dir / "processed"

    if not db_path.exists():
        logger.info("No DB at %s yet — doing a full build", db_path)
        build_database(data_dir, db_path, skip_wordcloud=skip_wordcloud)
        return True

    # Cheap pre-check against the LIVE DB (read-only): decide what changed before
    # paying for a snapshot copy, so an idle tick costs ~a few file stats.
    live = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    live.row_factory = sqlite3.Row
    try:
        if not _table_exists(live, "load_state"):
            logger.info("DB has no load_state table — full rebuild to seed baseline")
            live.close()
            build_database(data_dir, db_path, skip_wordcloud=skip_wordcloud)
            return True
        prev = _read_load_state(live)
    finally:
        live.close()

    changed = {pat: _changed_files(processed, pat, prev) for pat in _PROCESSED_GLOBS}
    n_changed = sum(len(v) for v in changed.values())
    if n_changed == 0:
        logger.info("No processed file changed since last load; DB is up to date")
        return False
    logger.info("Incremental update: %d changed file(s) — %s", n_changed,
                {k: len(v) for k, v in changed.items() if v})

    # Snapshot the live DB into a temp copy we mutate in place, then swap it in.
    tmp_path = db_path.with_suffix(db_path.suffix + ".building")
    _remove_db_files(tmp_path)
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn = connect(tmp_path)
    try:
        src.backup(conn)
    finally:
        src.close()

    loaded_sessions: list[str] = []
    ok = False
    try:
        conn.execute("PRAGMA journal_mode=WAL")

        for p in changed["representatives-*.json"]:
            registry = json.loads(p.read_text())
            _load_period_meta(conn, registry.get("meta", {}))
            load_representatives(conn, registry)
        for p in changed["bills-*.json"]:
            load_bills(conn, json.loads(p.read_text()))
        for p in changed["votes-*.json"]:
            load_votes(conn, json.loads(p.read_text()))

        for p in changed["*-session.json"]:
            record = json.loads(p.read_text())
            load_session(conn, record)
            sid = record.get("meta", {}).get("session")
            if sid:
                loaded_sessions.append(sid)

        # Aggregates are speech-derived, so they only need rebuilding when a
        # sitting changed (bills/votes/reps carry their own rows). Word counts are
        # scoped to just the changed sittings (cached; the rest stay intact).
        if loaded_sessions:
            if not skip_wordcloud:
                rebuild_session_word_counts(conn, db_path.parent,
                                            only_sessions=set(loaded_sessions))
                rebuild_entity_mentions(conn, db_path.parent,
                                        only_sessions=set(loaded_sessions))
            rebuild_aggregates(conn)
            # Wire any non-MP speaker portraits the scraper has downloaded since
            # the last load (global, cheap — see wire_nonmp_photos).
            wire_nonmp_photos(conn, Path(data_dir) / "media" / "photos")
        # Re-resolve entity links when the transcript OR the MP roster changed: new
        # mentions need resolving, and a reps change can flip a name from a Wikipedia
        # link to an internal profile (or set an MP's K-Monitor link). Cheap when
        # nothing new (K-Monitor index + Wikidata names are cached; entity_link is
        # just rebuilt from cache).
        if loaded_sessions or changed["representatives-*.json"]:
            resolve_entity_links(conn, db_path.parent)

        for files in changed.values():
            for p in files:
                _write_load_state(conn, p)
        conn.execute("INSERT OR REPLACE INTO build_meta(key, value) VALUES (?,?)",
                     ("last_update_sessions", ",".join(loaded_sessions)))
        # Bump the data-freshness timestamp only when an incremental update
        # actually changed something (this path returns early when nothing did).
        conn.execute("INSERT OR REPLACE INTO build_meta(key, value) VALUES (?,?)",
                     ("data_updated_at", datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("ANALYZE")
        conn.commit()
        ok = True
    finally:
        conn.close()
        if not ok:
            _remove_db_files(tmp_path)

    os.replace(tmp_path, db_path)
    _remove_db_side_files(tmp_path)
    logger.info("Updated database at %s (%d sittings, %d rep/bill/vote file(s))",
                db_path, len(loaded_sessions), n_changed - len(loaded_sessions))
    return True


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
    ap = argparse.ArgumentParser(description="Build the Parlamonitor SQLite DB")
    ap.add_argument("data_dir", help="scraper data directory (contains processed/)")
    ap.add_argument("db_path", help="output SQLite file")
    ap.add_argument("--update", action="store_true",
                    help="incrementally reload only the processed files that "
                         "changed since the last load, then atomically swap in the "
                         "result (fast, zero-downtime); full build if no DB yet")
    ap.add_argument("--session", help="load only this session id (e.g. 43003); "
                    "full-build only")
    ap.add_argument("--skip-wordcloud", action="store_true",
                    help="skip per-sitting word-cloud/new-words term extraction "
                         "(faster dev rebuild; those views come up empty)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("parlamonitor.loader").setLevel(logging.INFO)
    if args.update:
        if args.session:
            ap.error("--session is only valid for a full build, not --update")
        update_database(args.data_dir, args.db_path,
                        skip_wordcloud=args.skip_wordcloud)
    else:
        build_database(args.data_dir, args.db_path, only_session=args.session,
                       skip_wordcloud=args.skip_wordcloud)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
