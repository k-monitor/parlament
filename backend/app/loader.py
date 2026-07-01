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
from pathlib import Path

from . import nlp
from .config import settings
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
             rec.get("textCaption"), text_url or _BILL_PORTAL_FALLBACK,
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
    conn.execute("DELETE FROM session_word_count WHERE session_id = ?", (sid,))
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
    # Per-speech type (felszólalás típusa); a chairing type marks the speech
    # procedural so it is excluded from statistics but still stored/shown (STAT-1).
    speech_type = debug.get("felszolalasTipusa")
    procedural = 1 if settings.is_procedural_type(speech_type) else 0
    conn.execute(
        """INSERT INTO speech(uid, origin_id, speech_uuid, session_id, agenda_item_id,
               period_number, speech_index, person_id, speaker_label,
               speaker_status, felszolalas_tipus, procedural, faction_id,
               time_start, time_end, video_start,
               video_end, duration, confidence, align_method, has_text,
               source_uri, source_page)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (uid, origin_id, debug.get("speechUUID"), sid, agenda_id, period,
         speech_index, pid,
         speaker.get("label"), speaker.get("context"), speech_type, procedural,
         faction_id,
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
    """Resolve the configured word-cloud backend to a concrete one ("huspacy" or
    "regex"). "auto" prefers HuSpaCy when its model loads, else regex; an explicit
    "huspacy" that can't load is logged and degrades to regex so a build on a host
    without the model still succeeds (OPS-4)."""
    want = settings.wordcloud_backend or "auto"
    if want == "regex":
        return "regex"
    if nlp.available():
        return "huspacy"
    if want == "huspacy":
        logger.warning("wordcloud_backend=huspacy but the model is unavailable; "
                       "using the regex tokenizer instead")
    return "regex"


def _wordcloud_cache_path(data_dir: Path) -> Path:
    return Path(data_dir) / "wordcloud-cache.json"


def _session_fingerprint(method: str, texts: list[str]) -> str:
    """A stable hash of a sitting's text + the extraction method, so the cache is
    reused only when neither the transcript nor the method has changed."""
    h = hashlib.sha1(method.encode("utf-8"))
    for t in texts:
        h.update(b"\x1f")
        h.update((t or "").encode("utf-8"))
    return h.hexdigest()


def rebuild_session_word_counts(conn: sqlite3.Connection,
                                data_dir: str | Path | None = None) -> None:
    """Precompute per-sitting topical term frequencies for the word cloud (WCLOUD-2).

    For each session, lemmatize its non-procedural sentence text and extract named
    entities with HuSpaCy (or fall back to the regex tokenizer), storing the
    term→count map in ``session_word_count``. This is the expensive step, so its
    output is cached on disk (``wordcloud-cache.json`` beside the data) keyed by a
    fingerprint of the text + method: a full rebuild reprocesses only the sittings
    whose transcript actually changed (cf. the scraper's detail cache). The cache
    is optional — without ``data_dir`` (or if it can't be read/written) everything
    is simply recomputed.
    """
    backend = _wordcloud_backend()
    method = nlp.method_tag() if backend == "huspacy" else "regex:v1"
    logger.info("Word-cloud term extraction backend: %s", backend)

    cache_path = _wordcloud_cache_path(data_dir) if data_dir else None
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

    conn.execute("DELETE FROM session_word_count")
    # Latest sittings first (id desc) so the current cycle's clouds are ready
    # first when a from-scratch lemmatization pass is long.
    sids = [r[0] for r in conn.execute("SELECT id FROM session ORDER BY id DESC")]
    reused = recomputed = 0
    for i, sid in enumerate(sids, 1):
        texts = [t for (t,) in conn.execute(
            "SELECT se.text FROM sentence se "
            "JOIN speech sp ON sp.uid = se.speech_id "
            "WHERE sp.session_id = ? AND sp.procedural = 0 AND se.text IS NOT NULL",
            (sid,))]
        fp = _session_fingerprint(method, texts)
        entry = cache["sessions"].get(sid)
        if entry and entry.get("fp") == fp:
            words = entry["words"]
            reused += 1
        else:
            if backend == "huspacy":
                counts, entity_words = nlp.analyze_counts(texts)
            else:
                counts, entity_words = count_words(texts), set()
            words = {w: [c, "entity" if w in entity_words else "term"]
                     for w, c in counts.items()}
            cache["sessions"][sid] = {"fp": fp, "words": words}
            recomputed += 1
        if words:
            conn.executemany(
                "INSERT INTO session_word_count(session_id, word, count, kind) "
                "VALUES (?, ?, ?, ?)",
                [(sid, w, c, k) for w, (c, k) in words.items()])
        # Persist progress periodically so a long (HuSpaCy) pass is resumable: the
        # commit makes processed sittings queryable immediately and the cache lets
        # a re-run skip them (an interrupted run otherwise loses everything).
        if i % 10 == 0:
            conn.commit()
            _flush_cache()
            logger.info("session_word_count progress: %d/%d sittings", i, len(sids))
    conn.commit()
    _flush_cache()
    logger.info("session_word_count: %d sittings (%d processed, %d cached)",
                len(sids), recomputed, reused)


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
        # cached on disk beside the data so unchanged sittings aren't reprocessed.
        rebuild_session_word_counts(conn, data_dir)
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
    ap = argparse.ArgumentParser(description="Build the Parlamonitor SQLite DB")
    ap.add_argument("data_dir", help="scraper data directory (contains processed/)")
    ap.add_argument("db_path", help="output SQLite file")
    ap.add_argument("--session", help="load only this session id (e.g. 43003)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("parlamonitor.loader").setLevel(logging.INFO)
    build_database(args.data_dir, args.db_path, only_session=args.session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
