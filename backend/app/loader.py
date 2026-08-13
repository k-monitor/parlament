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
import contextlib
import glob
import hashlib
import json
import logging
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:                                    # POSIX only; the writer lock degrades to
    import fcntl                        # a no-op where it is unavailable.
except ImportError:                     # pragma: no cover - non-POSIX
    fcntl = None

from . import kmonitor, nlp, nlp_modal, portfolios, readability, wikidata
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

def _ensure_person_office(conn: sqlite3.Connection) -> None:
    """Create ``person_office`` on a pre-existing DB, so the office-term history
    (REP-2) lands on an **already-built** deployment through the incremental update
    alone (dropping in ``officeholders.json``), with no full rebuild."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS person_office (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id     TEXT NOT NULL REFERENCES person(person_id),
            title         TEXT NOT NULL,
            category      TEXT,
            date_start    TEXT,
            date_end      TEXT,
            source        TEXT NOT NULL DEFAULT 'registry'
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_person_office_person "
                 "ON person_office(person_id)")
    # `category` post-dates the table, so an already-built DB gets it added here
    # (the next registry load fills it in) rather than needing a rebuild.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(person_office)")}
    if "category" not in cols:
        conn.execute("ALTER TABLE person_office ADD COLUMN category TEXT")
    # The office listing pages by start date within a category (REP-11).
    conn.execute("CREATE INDEX IF NOT EXISTS idx_person_office_listing "
                 "ON person_office(source, category, date_start)")


def _load_person_offices(conn: sqlite3.Connection, person_id: str,
                         offices, source: str) -> int:
    """Replace ``person_id``'s office terms **from this source** (REP-2).

    ``offices`` is a list of ``{title, category, start, end}`` — the shape both the
    MP roster's ``offices`` and the office-holder registry use. The delete is
    source-scoped, so reloading one source never drops what the other supplied;
    a term with no title is skipped (there would be nothing to show).

    ``category`` is the registry's own office grouping (miniszter / államtitkár /
    parlamenti / …); the MP roster does not report it, so a roster-sourced term
    has none — which is why the office listing reads the registry rows (REP-11)."""
    conn.execute("DELETE FROM person_office WHERE person_id = ? AND source = ?",
                 (person_id, source))
    n = 0
    for o in offices or []:
        if not isinstance(o, dict):
            continue
        title = (o.get("title") or "").strip()
        if not title:
            continue
        conn.execute("INSERT INTO person_office(person_id, title, category, "
                     "date_start, date_end, source) VALUES (?,?,?,?,?,?)",
                     (person_id, title, o.get("category"), o.get("start"),
                      o.get("end"), source))
        n += 1
    return n


def _ensure_person_mandate(conn: sqlite3.Connection) -> None:
    """Create ``person_mandate`` on a pre-existing DB (REP-14).

    Same reasoning as ``_ensure_person_office``: the incremental ``--update`` path
    snapshots the live DB rather than re-running ``schema.sql``, so without this the
    first registry carrying mandates would fail on the missing table — and this is
    what lets a deployment pick up the departed MPs by dropping in a re-scraped
    ``representatives-*.json``, with no full rebuild. A no-op on a fresh DB."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS person_mandate (
            person_id      TEXT NOT NULL REFERENCES person(person_id),
            period_number  INTEGER,
            date_start     TEXT,
            date_end       TEXT,
            terminated     INTEGER NOT NULL DEFAULT 0,
            end_reason     TEXT,
            constituency   TEXT,
            predecessor_id    TEXT,
            predecessor_label TEXT,
            successor_id      TEXT,
            successor_label   TEXT,
            PRIMARY KEY (person_id, period_number)
        )""")


def _load_person_mandate(conn: sqlite3.Connection, person_id: str,
                         period: int | None, mandate) -> None:
    """Replace ``person_id``'s mandate row for ``period`` (REP-14).

    Period-scoped like the membership row beside it: an MP serving in several cycles
    holds one mandate per cycle, each loaded from that cycle's own registry, so a
    blanket delete-by-person would drop the others every time one is reloaded.
    A registry scraped before mandates existed simply carries none, and the row is
    then removed rather than left stale."""
    conn.execute("DELETE FROM person_mandate WHERE person_id = ? AND period_number IS ?",
                 (person_id, period))
    if not isinstance(mandate, dict):
        return
    pre = mandate.get("predecessor") or {}
    suc = mandate.get("successor") or {}
    conn.execute(
        """INSERT INTO person_mandate(person_id, period_number, date_start, date_end,
                                      terminated, end_reason, constituency,
                                      predecessor_id, predecessor_label,
                                      successor_id, successor_label)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (person_id, period, mandate.get("start"), mandate.get("end"),
         1 if mandate.get("terminated") else 0, mandate.get("endReason"),
         mandate.get("constituency"), pre.get("personID"), pre.get("label"),
         suc.get("personID"), suc.get("label")))


def _ensure_person_document_columns(conn: sqlite3.Connection) -> None:
    """Add ``person.cv_url`` / ``person.asset_declarations_json`` to a pre-existing
    DB (REP-13).

    Same reasoning as ``_ensure_advocate_columns``: the incremental ``--update``
    path snapshots the live DB instead of re-running ``schema.sql``, so the
    declarations land on an already-built deployment by re-loading the registry
    alone — no full rebuild. A no-op on a freshly-built DB."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(person)")]
    if not cols:
        return
    if "cv_url" not in cols:
        conn.execute("ALTER TABLE person ADD COLUMN cv_url TEXT")
    if "asset_declarations_json" not in cols:
        conn.execute("ALTER TABLE person ADD COLUMN asset_declarations_json TEXT")


def _ensure_person_birth_columns(conn: sqlite3.Connection) -> None:
    """Add ``person.date_of_birth`` / ``zodiac_sign`` / ``chinese_zodiac_sign`` to a
    pre-existing DB.

    Same reasoning as ``_ensure_person_document_columns``: the incremental
    ``--update`` path snapshots the live DB instead of re-running ``schema.sql``,
    so a freshly-scraped registry carrying the Wikidata birth date lands on an
    already-built deployment without a full rebuild. A no-op on a fresh DB."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(person)")]
    if not cols:
        return
    if "date_of_birth" not in cols:
        conn.execute("ALTER TABLE person ADD COLUMN date_of_birth TEXT")
    if "zodiac_sign" not in cols:
        conn.execute("ALTER TABLE person ADD COLUMN zodiac_sign TEXT")
    if "chinese_zodiac_sign" not in cols:
        conn.execute("ALTER TABLE person ADD COLUMN chinese_zodiac_sign TEXT")


def load_representatives(conn: sqlite3.Connection, registry: dict) -> int:
    """Upsert the MP registry. Adds bio/enrichment to person rows and faction
    membership history; safe to re-run (replaces each MP's derived rows)."""
    _ensure_person_office(conn)
    _ensure_person_mandate(conn)
    _ensure_person_document_columns(conn)
    _ensure_person_birth_columns(conn)
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
                               wikidata_id, wikipedia_url, date_of_birth, zodiac_sign,
                               chinese_zodiac_sign,
                               photo_uri, photo_file, constituency, seat, email,
                               website, highest_education, active, is_mp, cv_url,
                               education_json, committees_json, offices_json,
                               faction_history_json, election_history_json,
                               external_stats_json, asset_declarations_json)
            VALUES (:pid, :label, :label_full, :firstname, :lastname,
                    :wikidata_id, :wikipedia_url, :date_of_birth, :zodiac_sign,
                    :chinese_zodiac_sign, :photo_uri,
                    :photo_file, :constituency, :seat, :email, :website,
                    :highest_education, :active, 1, :cv_url, :education, :committees,
                    :offices, :faction_history, :election_history, :external_stats,
                    :asset_declarations)
            ON CONFLICT(person_id) DO UPDATE SET
                label=excluded.label, label_full=excluded.label_full,
                firstname=excluded.firstname, lastname=excluded.lastname,
                wikidata_id=excluded.wikidata_id, wikipedia_url=excluded.wikipedia_url,
                date_of_birth=excluded.date_of_birth, zodiac_sign=excluded.zodiac_sign,
                chinese_zodiac_sign=excluded.chinese_zodiac_sign,
                photo_uri=excluded.photo_uri, photo_file=excluded.photo_file,
                constituency=excluded.constituency, seat=excluded.seat,
                email=excluded.email, website=excluded.website,
                highest_education=excluded.highest_education,
                active=excluded.active, is_mp=1, cv_url=excluded.cv_url,
                education_json=excluded.education_json,
                committees_json=excluded.committees_json,
                offices_json=excluded.offices_json,
                faction_history_json=excluded.faction_history_json,
                election_history_json=excluded.election_history_json,
                external_stats_json=excluded.external_stats_json,
                asset_declarations_json=excluded.asset_declarations_json
            """,
            {
                "pid": pid,
                "label": (rec.get("label") or pid).strip(),
                "label_full": rec.get("labelFull"),
                "firstname": rec.get("firstname"),
                "lastname": rec.get("lastname"),
                "wikidata_id": rec.get("wikidataId"),
                "wikipedia_url": rec.get("wikipediaUrl"),
                "date_of_birth": rec.get("dateOfBirth"),
                "zodiac_sign": rec.get("zodiacSign"),
                "chinese_zodiac_sign": rec.get("chineseZodiacSign"),
                "photo_uri": photo_uri,
                "photo_file": rec.get("photoFile"),
                "constituency": rec.get("constituency"),
                "seat": rec.get("seat"),
                "email": rec.get("email"),
                "website": rec.get("website"),
                "highest_education": rec.get("highestEducation"),
                "active": _as_bool(rec.get("active")),
                "cv_url": rec.get("cvUrl"),
                "education": _json_or_none(rec.get("education")),
                "committees": _json_or_none(rec.get("committeeMemberships")
                                            or rec.get("committees")),
                "offices": _json_or_none(rec.get("offices")),
                "faction_history": _json_or_none(rec.get("factionHistory")),
                "election_history": _json_or_none(rec.get("electionHistory")),
                "external_stats": _json_or_none(ext_stats),
                "asset_declarations": _json_or_none(rec.get("assetDeclarations")),
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
        # The MP's own office (tisztség) terms, as term rows the profile can list
        # (REP-2). `offices_json` keeps the same data for older readers.
        _load_person_offices(conn, pid, rec.get("offices"), "roster")
        # The seat they held in this cycle — its term, whether it ended early and
        # why, and who stood on either side of the handover (REP-14).
        _load_person_mandate(conn, pid, period, rec.get("mandate"))

        conn.execute("DELETE FROM membership WHERE person_id = ? AND period_number IS ?",
                     (pid, period))
        for h in rec.get("factionHistory") or []:
            _get_or_create_faction(conn, h.get("label"))
        # The membership row is what puts this person in the cycle's scope (§4A), so
        # it is written whether or not the registry knows their faction — an MP with
        # no faction on record must still appear in the cycle they served in, not
        # vanish from it. `faction_id` is then simply NULL.
        fid = (_get_or_create_faction(conn, fac.get("label"), fac.get("id"))
               if fac.get("label") else None)
        conn.execute(
            "INSERT INTO membership(person_id, faction_id, period_number, "
            "position) VALUES (?, ?, ?, ?)",
            (pid, fid, period, fac.get("position")))
    conn.commit()
    logger.info("Loaded %d representatives (cycle %s)", len(data), period)
    return len(data)


# ---------------------------------------------------------------------------
# Nationality advocates (nemzetiségi szószólók)
# ---------------------------------------------------------------------------

def _ensure_advocate_columns(conn: sqlite3.Connection) -> None:
    """Add ``person.is_advocate`` / ``person.nationality`` to a pre-existing DB.

    Same reasoning as ``_ensure_session_status``: the incremental ``--update`` path
    snapshots the live DB instead of re-running ``schema.sql``, so without this the
    first advocate registry loaded after a code deploy would fail on the missing
    columns. This is what lets the whole feature land on an **already-scraped,
    already-built** deployment by just dropping in the new ``advocates-*.json`` —
    no full rebuild. A no-op on a freshly-built DB."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(person)")]
    if not cols:
        return
    if "is_advocate" not in cols:
        conn.execute("ALTER TABLE person ADD COLUMN is_advocate INTEGER DEFAULT 0")
    if "nationality" not in cols:
        conn.execute("ALTER TABLE person ADD COLUMN nationality TEXT")


# The upstream per-cycle counters that come as a list of one row per cycle.
_CYCLE_STAT_KEYS = ("speeches", "billsSubmitted")


def _merge_external_stats(existing: dict | None, incoming: dict | None) -> dict | None:
    """Merge upstream per-cycle counters, the incoming rows winning per cycle.

    An advocate registry is scraped **per cycle**, so a ``--no-details`` run knows
    only its own cycle's counts; a blind overwrite would drop the other cycles an
    earlier file (or the MP roster, for someone who has been both) contributed.
    Non-cycle-keyed entries are replaced wholesale when the incoming one is
    non-empty."""
    if not incoming:
        return existing
    if not existing:
        return incoming
    out = dict(existing)
    for key, rows in incoming.items():
        if key in _CYCLE_STAT_KEYS and isinstance(rows, list):
            by_cycle = {r.get("cycle"): r
                        for r in (existing.get(key) or []) if isinstance(r, dict)}
            by_cycle.update({r.get("cycle"): r for r in rows if isinstance(r, dict)})
            out[key] = sorted(by_cycle.values(),
                              key=lambda r: (r.get("cycle") is None,
                                             -(r.get("cycle") or 0)))
        elif rows:
            out[key] = rows
    return out


def load_advocates(conn: sqlite3.Connection, registry: dict) -> int:
    """Upsert a cycle's nationality-advocate registry (nemzetiségi szószólók).

    Advocates share the ``person`` table with MPs — their upstream id is the very
    id the transcripts already carry, so this **enriches the speaker stubs**
    (``_ensure_person``) the proceedings loader created rather than adding a
    parallel entity (EXT-2). They are marked ``is_advocate`` and carry the
    ``nationality`` they speak for; ``is_mp`` is left untouched, so someone who has
    been both (an advocate in one cycle, an MP in another) keeps both marks, and
    every optional field is COALESCE-merged so an advocate load never nulls out
    what the richer MP roster supplied.

    A membership row per cycle (with no faction — an advocate belongs to none)
    keeps them inside the cycle scope every period-aware view filters on (§4A).
    Safe to re-run: it replaces only this cycle's advocate rows (ING-4)."""
    _ensure_advocate_columns(conn)
    _ensure_person_office(conn)
    _ensure_person_document_columns(conn)
    _ensure_person_birth_columns(conn)
    meta = registry.get("meta", {})
    period = meta.get("cycle")
    data = registry.get("data", [])

    if period is not None:
        conn.execute("INSERT INTO electoral_period(number) VALUES (?) "
                     "ON CONFLICT(number) DO NOTHING", (period,))

    for rec in data:
        pid = rec.get("personID")
        if not pid:
            continue

        # Only the locally-downloaded portrait is used: hot-linking parlament.hu's
        # image resource is avoided, and leaving it NULL lets `wire_nonmp_photos`
        # fill it in once the scraper's photo step has fetched the file.
        photo_file = rec.get("photoFile")
        photo_uri = f"/media/photos/{photo_file}" if photo_file else None

        prev = conn.execute("SELECT external_stats_json FROM person WHERE person_id=?",
                            (pid,)).fetchone()
        stats = _merge_external_stats(
            _loads_json(prev["external_stats_json"]) if prev else None,
            rec.get("statistics"))

        conn.execute(
            """
            INSERT INTO person(person_id, label, label_full, firstname, lastname,
                               wikidata_id, wikipedia_url, date_of_birth, zodiac_sign,
                               chinese_zodiac_sign, photo_uri, photo_file,
                               seat, email, website, highest_education, active,
                               is_advocate, nationality, cv_url,
                               education_json, committees_json, offices_json,
                               external_stats_json, asset_declarations_json)
            VALUES (:pid, :label, :label_full, :firstname, :lastname,
                    :wikidata_id, :wikipedia_url, :date_of_birth, :zodiac_sign,
                    :chinese_zodiac_sign, :photo_uri, :photo_file,
                    :seat, :email, :website, :highest_education, :active,
                    1, :nationality, :cv_url, :education, :committees, :offices,
                    :external_stats, :asset_declarations)
            ON CONFLICT(person_id) DO UPDATE SET
                label=excluded.label,
                label_full=COALESCE(excluded.label_full, person.label_full),
                firstname=COALESCE(excluded.firstname, person.firstname),
                lastname=COALESCE(excluded.lastname, person.lastname),
                wikidata_id=COALESCE(excluded.wikidata_id, person.wikidata_id),
                wikipedia_url=COALESCE(excluded.wikipedia_url, person.wikipedia_url),
                date_of_birth=COALESCE(excluded.date_of_birth, person.date_of_birth),
                zodiac_sign=COALESCE(excluded.zodiac_sign, person.zodiac_sign),
                chinese_zodiac_sign=COALESCE(excluded.chinese_zodiac_sign,
                                             person.chinese_zodiac_sign),
                photo_uri=COALESCE(excluded.photo_uri, person.photo_uri),
                photo_file=COALESCE(excluded.photo_file, person.photo_file),
                seat=COALESCE(excluded.seat, person.seat),
                email=COALESCE(excluded.email, person.email),
                website=COALESCE(excluded.website, person.website),
                highest_education=COALESCE(excluded.highest_education,
                                           person.highest_education),
                active=COALESCE(excluded.active, person.active),
                is_advocate=1,
                nationality=COALESCE(excluded.nationality, person.nationality),
                cv_url=COALESCE(excluded.cv_url, person.cv_url),
                education_json=COALESCE(excluded.education_json, person.education_json),
                committees_json=COALESCE(excluded.committees_json, person.committees_json),
                offices_json=COALESCE(excluded.offices_json, person.offices_json),
                external_stats_json=excluded.external_stats_json,
                asset_declarations_json=COALESCE(excluded.asset_declarations_json,
                                                 person.asset_declarations_json)
            """,
            {
                "pid": pid,
                "label": (rec.get("label") or pid).strip(),
                "label_full": rec.get("labelFull") or None,
                "firstname": rec.get("firstname"),
                "lastname": rec.get("lastname"),
                "wikidata_id": rec.get("wikidataId"),
                "wikipedia_url": rec.get("wikipediaUrl"),
                "date_of_birth": rec.get("dateOfBirth"),
                "zodiac_sign": rec.get("zodiacSign"),
                "chinese_zodiac_sign": rec.get("chineseZodiacSign"),
                "photo_uri": photo_uri,
                "photo_file": photo_file,
                "seat": rec.get("seat"),
                "email": rec.get("email"),
                "website": rec.get("website"),
                "highest_education": rec.get("highestEducation"),
                "active": _as_bool(rec.get("active")),
                "nationality": rec.get("nationality"),
                "cv_url": rec.get("cvUrl"),
                "education": _json_or_none(rec.get("education")),
                "committees": _json_or_none(rec.get("committeeMemberships")
                                            or rec.get("committees")),
                "offices": _json_or_none(rec.get("offices")),
                "external_stats": _json_or_none(stats),
                "asset_declarations": _json_or_none(rec.get("assetDeclarations")),
            },
        )

        _load_person_offices(conn, pid, rec.get("offices"), "roster")

        # The advocate's factionless membership row for this cycle. Scoped to
        # `faction_id IS NULL` on delete so it can never wipe a faction membership
        # the MP roster loaded for the same person and cycle.
        conn.execute("DELETE FROM membership WHERE person_id = ? "
                     "AND period_number IS ? AND faction_id IS NULL", (pid, period))
        conn.execute("INSERT INTO membership(person_id, faction_id, period_number, "
                     "position) VALUES (?, NULL, ?, ?)",
                     (pid, period, rec.get("mandate")))
    conn.commit()
    logger.info("Loaded %d nationality advocates (cycle %s)", len(data), period)
    return len(data)


# ---------------------------------------------------------------------------
# Office holders (tisztségviselők)
# ---------------------------------------------------------------------------

def _load_office_holders_file(conn: sqlite3.Connection, data_dir: Path) -> int:
    """Load ``processed/officeholders.json`` if the scrape has produced one.

    Absent on a corpus scraped before this stage existed, which is not an error —
    profiles then fall back to dating an office from its speeches."""
    path = Path(data_dir) / "processed" / "officeholders.json"
    if not path.exists():
        logger.info("No officeholders.json in %s — skipping office terms",
                    path.parent)
        return 0
    return load_office_holders(conn, json.loads(path.read_text()))


def load_office_holders(conn: sqlite3.Connection, registry: dict) -> int:
    """Load the office-holder registry: every office (*tisztség*) term with its
    upstream start/end dates, for MPs and non-MPs alike (REP-2).

    This is what dates the office of a **non-MP** minister or state secretary: they
    are in no roster, so before this their profile could only bound the office by
    the speeches carrying it — never saying when it really began, nor that they
    still hold it.

    A registry entry for someone **not yet** in ``person`` is inserted as a stub
    (name + the split name, no mandate): over half of this registry never spoke in
    the House — MNB and Közbeszerzési Hatóság members, ministers who only ever
    appeared in writing — and the office listing (REP-11) is the parliament's own
    all-time listing, so leaving them out would silently halve it. Their profile is
    an office history and nothing else, which is what the source says about them.

    Safe to re-run — it replaces the registry-sourced terms of every person it names
    (ING-4), leaving the roster-sourced ones alone."""
    _ensure_person_office(conn)
    data = registry.get("data", [])
    known = {r[0] for r in conn.execute("SELECT person_id FROM person")}
    people = terms = added = 0
    for rec in data:
        pid = rec.get("personID")
        if not pid:
            continue
        if pid not in known:
            _ensure_person(conn, pid, rec.get("label") or rec.get("labelFull"),
                           firstname=rec.get("firstname"),
                           lastname=rec.get("lastname"))
            # The registry is the only source of a non-MP's full (honorific) name.
            conn.execute("UPDATE person SET label_full = COALESCE(label_full, ?) "
                         "WHERE person_id = ?", (rec.get("labelFull"), pid))
            known.add(pid)
            added += 1
        n = _load_person_offices(conn, pid, rec.get("offices"), "registry")
        if n:
            people += 1
            terms += n
    conn.commit()
    logger.info("Loaded %d office term(s) for %d people (%d new to the corpus)",
                terms, people, added)
    return terms


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
    # The derived §6C links reference these bills; clear them so the delete below
    # isn't blocked by a foreign key (rebuild_portfolios re-derives them).
    _drop_portfolio_links(
        conn, "portfolio_bill",
        "bill_id IN (SELECT id FROM bill WHERE period_number IS ?)", (period,))
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
    # Derived §6C links point at the speeches about to go; drop them first so the
    # delete isn't blocked by a foreign key. rebuild_portfolios re-derives them
    # wholesale after the load, so nothing is lost.
    _drop_portfolio_links(
        conn, "portfolio_speech",
        "speech_uid IN (SELECT uid FROM speech WHERE session_id = ?)", (sid,))
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
# Portfolios (tárcák) — §6C
# ---------------------------------------------------------------------------

# The bill events that name a **responding tárca**. Enumerated rather than
# pattern-matched, for the same reason as STAT-1's procedural types: the list is
# auditable and can never silently swallow an event kind whose `related_label`
# means something else. `azonnali kérdésre adott képviselői viszonválasz` is
# deliberately absent — that one is the *asking MP's* counter-reply, and upstream
# leaves its related label empty on all 4 066 of them.
_PORTFOLIO_ANSWER_EVENTS = (
    "kérdés megválaszolva",                        # answered orally (K and A)
    "kérdés írásban megválaszolva",                # answered in writing (K)
    "interpelláció szóban megválaszolva",          # answered in plenary (I)
    "azonnali kérdésre adott miniszteri viszonválasz",   # the minister's reply (A)
)


def _drop_portfolio_links(conn: sqlite3.Connection, table: str, where: str,
                          params: tuple) -> None:
    """Delete the §6C link rows pointing at records a loader is about to replace.

    The portfolio tables are derived, so they are always rebuilt after a load —
    but they hold foreign keys into `bill` and `speech`, and SQLite refuses to
    delete a parent while a stale link still points at it. A DB loaded before this
    module existed has no such table, which is not an error: there is nothing to
    clear."""
    try:
        conn.execute(f"DELETE FROM {table} WHERE {where}", params)
    except sqlite3.OperationalError:
        pass


def _ensure_portfolio_tables(conn: sqlite3.Connection) -> None:
    """Create the §6C tables on a pre-existing DB, so the portfolios land on an
    already-built deployment through the incremental update alone — they are
    derived from rows the other modules already wrote, so there is nothing to
    re-scrape and no reason to force a full rebuild."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS portfolio (
            slug TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
            ord INTEGER);
        CREATE TABLE IF NOT EXISTS portfolio_alias (
            label TEXT PRIMARY KEY,
            portfolio_slug TEXT NOT NULL REFERENCES portfolio(slug));
        CREATE TABLE IF NOT EXISTS portfolio_bill (
            portfolio_slug TEXT NOT NULL REFERENCES portfolio(slug),
            bill_id TEXT NOT NULL REFERENCES bill(id),
            role TEXT NOT NULL, label TEXT, event_date TEXT, period_number INTEGER,
            PRIMARY KEY (portfolio_slug, bill_id, role)) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS idx_portfolio_bill_bill ON portfolio_bill(bill_id);
        CREATE TABLE IF NOT EXISTS portfolio_speech (
            portfolio_slug TEXT NOT NULL REFERENCES portfolio(slug),
            speech_uid TEXT NOT NULL REFERENCES speech(uid),
            PRIMARY KEY (portfolio_slug, speech_uid)) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS idx_portfolio_speech_speech ON portfolio_speech(speech_uid);
        CREATE TABLE IF NOT EXISTS portfolio_office (
            portfolio_slug TEXT NOT NULL REFERENCES portfolio(slug),
            person_id TEXT NOT NULL REFERENCES person(person_id),
            title TEXT NOT NULL, category TEXT, date_start TEXT, date_end TEXT);
        CREATE INDEX IF NOT EXISTS idx_portfolio_office_slug ON portfolio_office(portfolio_slug);
        CREATE INDEX IF NOT EXISTS idx_portfolio_office_person ON portfolio_office(person_id);
    """)
    # `portfolio_bill.period_number` post-dates the table, so a DB built between
    # the two gets the column added here rather than needing a full rebuild.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(portfolio_bill)")}
    if cols and "period_number" not in cols:
        conn.execute("ALTER TABLE portfolio_bill ADD COLUMN period_number INTEGER")
    # Declared after the ALTER above, which is what puts the column there on a DB
    # that predates it — an index over a missing column is a hard error.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_portfolio_bill_period "
                 "ON portfolio_bill(portfolio_slug, role, period_number)")


def rebuild_portfolios(conn: sqlite3.Connection) -> int:
    """Rebuild the tárca tables (§6C) from the rows the other modules wrote.

    Three link kinds, all from data already in the database (MIN-10a): who
    **answered** a question (`bill_event`), which tárca the government
    **submitted** an iromány through (`bill_sponsor`), and which speeches were
    given in the tárca's offices (`speech.speaker_office`). Plus the office terms
    (`person_office`) filed under the tárca they belong to, so a profile can say
    who held it and when.

    Every label goes through `app.portfolios`: it resolves, or it is an excluded
    personal commission, or it stands alone as its own portfolio (MIN-3) — the
    last is logged, because an unmapped label is a gap in the table, not a
    finding about the House.
    """
    _ensure_portfolio_tables(conn)
    for tbl in ("portfolio_office", "portfolio_speech", "portfolio_bill",
                "portfolio_alias", "portfolio"):
        conn.execute(f"DELETE FROM {tbl}")

    used: dict[str, portfolios.Portfolio] = {}
    alias_of: dict[str, str] = {}          # normalized label -> slug
    weight: Counter = Counter()            # slug -> corpus rows behind it
    unmapped: Counter = Counter()

    def slug_for(label: str | None) -> str | None:
        p = portfolios.resolve(label)
        if p is None:
            if portfolios.is_excluded(label):
                return None
            p = portfolios.standalone(label)
            if p is None:                   # empty label
                return None
            unmapped[portfolios.normalize_label(label)] += 1
        used.setdefault(p.slug, p)
        alias_of.setdefault(portfolios.normalize_label(label), p.slug)
        weight[p.slug] += 1
        return p.slug

    # A link belongs to the cycle it **happened in**, taken from its own date —
    # not to the cycle its parent iromány is filed under. parlament.hu re-lists an
    # iromány that is still in progress under the new cycle, so a bill the
    # government submitted in 2024 comes back as a cycle-43 row; counted by the
    # parent, a ministry abolished in 2026 reappears in the 2026 listing on the
    # strength of a document it filed two years earlier.
    periods = [(r["number"], r["date_start"], r["date_end"]) for r in conn.execute(
        "SELECT number, date_start, date_end FROM electoral_period "
        "WHERE date_start IS NOT NULL ORDER BY date_start")]

    def cycle_of(date: str | None, fallback: int | None) -> int | None:
        """The electoral period whose span contains ``date``. Falls back to the
        parent iromány's cycle when the link carries no date (or predates the
        first recorded period), so a link is never dropped from every scope."""
        if not date:
            return fallback
        day = date[:10]
        for number, start, end in periods:
            if start <= day and (end is None or day <= end):
                return number
        return fallback

    # 1. answered — the responding tárca named on a question's answer event.
    ph = ",".join("?" * len(_PORTFOLIO_ANSWER_EVENTS))
    links: dict[tuple[str, str, str], tuple[str, str | None, int | None]] = {}
    for r in conn.execute(
            f"""SELECT e.bill_id, e.related_label, MIN(e.event_date) AS d,
                       b.period_number
                FROM bill_event e JOIN bill b ON b.id = e.bill_id
                WHERE e.name IN ({ph}) AND e.related_label IS NOT NULL
                GROUP BY e.bill_id, e.related_label""",
            _PORTFOLIO_ANSWER_EVENTS):
        slug = slug_for(r["related_label"])
        if slug:
            key = (slug, r["bill_id"], "answered")
            if key not in links:
                links[key] = (r["related_label"], r["d"],
                              cycle_of(r["d"], r["period_number"]))

    # 2. submitted — "kormány (belügyminiszter)" names the tárca it came through.
    for r in conn.execute(
            """SELECT bs.bill_id, bs.label, b.submitted_date, b.period_number
               FROM bill_sponsor bs JOIN bill b ON b.id = bs.bill_id
               WHERE bs.person_id IS NULL AND bs.label LIKE 'kormány (%'"""):
        slug = slug_for(r["label"])
        if slug:
            links.setdefault(
                (slug, r["bill_id"], "submitted"),
                (r["label"], r["submitted_date"],
                 cycle_of(r["submitted_date"], r["period_number"])))

    # 3. spoken for — the office a speaker actually spoke in. Only the cycles
    # scraped since `speaker_office` was added carry it (MIN-10), so this half is
    # partial by construction; the API discloses which cycles it covers rather
    # than letting an empty list read as silence.
    speeches = []
    for r in conn.execute(
            "SELECT uid, speaker_office FROM speech "
            "WHERE speaker_office IS NOT NULL AND speaker_office <> ''"):
        slug = slug_for(r["speaker_office"])
        if slug:
            speeches.append((slug, r["uid"]))

    # 4. who held it — the registry's dated terms (REP-2a) under their tárca.
    try:
        office_rows = list(conn.execute(
            "SELECT person_id, title, category, date_start, date_end FROM person_office "
            # `person_office` carries the same term from two sources (REP-2a): the
            # all-time registry and the per-MP roster's own list. Only the registry
            # copy is categorised, so an undeduped read lists a minister twice — once
            # under "miniszter" and again under "egyéb tisztség". The registry copy
            # wins, exactly as it does on the profile.
            "ORDER BY source = 'registry' DESC"))
    except sqlite3.OperationalError:        # a DB built before person_office
        office_rows = []
    offices = []
    seen_terms: set = set()
    for r in office_rows:
        term = (r["person_id"], r["title"], r["date_start"])
        if term in seen_terms:
            continue
        seen_terms.add(term)
        # An office term is not itself corpus activity, so it must not decide a
        # tárca's rank on the listing page — resolve without adding weight.
        p = portfolios.resolve(r["title"])
        if p is None:
            continue
        used.setdefault(p.slug, p)
        alias_of.setdefault(portfolios.normalize_label(r["title"]), p.slug)
        offices.append((p.slug, r["person_id"], r["title"], r["category"],
                        r["date_start"], r["date_end"]))

    # The portfolios themselves, ranked within their kind by how much of the
    # corpus stands behind them — the listing's default order (MIN-5). Written
    # first: every link table points at this one.
    order = sorted(used.values(), key=lambda p: (portfolios.KINDS.index(p.kind)
                                                 if p.kind in portfolios.KINDS else 99,
                                                 -weight[p.slug], p.name))
    conn.executemany(
        "INSERT INTO portfolio(slug, name, kind, ord) VALUES (?,?,?,?)",
        [(p.slug, p.name, p.kind, i) for i, p in enumerate(order)])
    conn.executemany(
        "INSERT OR IGNORE INTO portfolio_alias(label, portfolio_slug) VALUES (?,?)",
        list(alias_of.items()))
    conn.executemany(
        "INSERT INTO portfolio_bill(portfolio_slug, bill_id, role, label, event_date, "
        "period_number) VALUES (?,?,?,?,?,?)",
        [(s, b, role, lab, d, per)
         for (s, b, role), (lab, d, per) in links.items()])
    conn.executemany("INSERT OR IGNORE INTO portfolio_speech(portfolio_slug, speech_uid) "
                     "VALUES (?,?)", speeches)
    conn.executemany(
        "INSERT INTO portfolio_office(portfolio_slug, person_id, title, category, "
        "date_start, date_end) VALUES (?,?,?,?,?,?)", offices)
    conn.commit()

    if unmapped:
        logger.warning(
            "Portfolio table does not cover %d label(s); each stands alone as its "
            "own tárca — add them to app/portfolios.py: %s", len(unmapped),
            ", ".join(f"{lab!r} ({n})" for lab, n in unmapped.most_common(10)))
    logger.info("Rebuilt %d portfolios (%d iromány links, %d speeches, %d office terms)",
                len(order), len(links), len(speeches), len(offices))
    return len(order)


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
    # Derived from the bill/speech/office rows just loaded (§6C), so it belongs
    # to the same rebuild pass — never to a request.
    rebuild_portfolios(conn)
    logger.info("Rebuilt aggregate tables")


def _nlp_backend(model: str, *, modal_ok: bool = True) -> str:
    """Resolve the configured word-cloud backend for ``model`` to a concrete one
    ("modal", "huspacy" or "regex").

    "modal" (opt-in) offloads the HuSpaCy pipeline to Modal; if the client isn't
    installed / not authenticated it degrades to local HuSpaCy, then regex.
    "auto" prefers local HuSpaCy when the model loads, else regex (it never
    auto-selects Modal). An explicit backend that can't be used is logged and
    degrades so a build on a bare host still succeeds (OPS-4). Resolution is
    per model because the current and archive cycles may use different models
    with different local availability (the trf only ever loads on Modal).

    ``modal_ok=False`` means this sitting's electoral cycle is outside the Modal
    budget scope (``settings.modal_cycles``, default: the newest cycle only), so
    Modal is skipped even when configured and available — the same local
    HuSpaCy → regex degradation applies."""
    want = settings.wordcloud_backend or "auto"
    if want == "regex":
        return "regex"
    if want == "modal":
        if not modal_ok:
            logger.info("model %s is outside the Modal cycle scope (%s); using a "
                        "local backend for its sittings", model, settings.modal_cycles)
        elif nlp_modal.available():
            return "modal"
        else:
            logger.warning("wordcloud_backend=modal unavailable; trying local "
                           "HuSpaCy, then the regex tokenizer")
    if nlp.available(model):
        return "huspacy"
    if want == "huspacy":
        logger.warning("wordcloud_backend=huspacy but model %s is unavailable; "
                       "using the regex tokenizer instead", model)
    return "regex"


def _session_plan(conn: sqlite3.Connection) -> dict[str, tuple[str, bool]]:
    """Each session id → ``(model, modal_ok)``: which HuSpaCy model should process
    it, and whether it may be dispatched to Modal at all.

    The newest electoral period gets ``settings.huspacy_model`` (the expensive
    transformer), every earlier — frozen — period the cheaper
    ``settings.huspacy_model_archive``. The method/model name is hashed into each
    sitting's cache fingerprint, so archive sittings keep reusing whatever the
    archive model produced and never hit the expensive path. A session with no
    period recorded is treated as current (quality-safe; at most a handful of
    sittings).

    ``modal_ok`` is the metered-spend guard (``settings.modal_cycles``, default
    ``latest``): only the newest cycle is dispatched to Modal, so backfilling the
    archive — which is exactly when thousands of sittings miss the cache at once —
    cannot exhaust the Modal budget the live cycle depends on."""
    rows = conn.execute("SELECT id, period_number FROM session").fetchall()
    latest = max((p for _sid, p in rows if p is not None), default=None)
    return {sid: (settings.huspacy_model if (p is None or p == latest)
                  else settings.huspacy_model_archive,
                  settings.modal_cycle_allowed(p, latest))
            for sid, p in rows}


def _modal_app_for(model: str) -> str:
    """The deployed Modal app serving ``model`` — the primary app bakes
    ``huspacy_model``, the archive app ``huspacy_model_archive``."""
    return (settings.modal_app_name if model == settings.huspacy_model
            else settings.modal_app_name_archive)


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


def _richer_cache_hit(entry: dict | None, model: str, texts: list[str]) -> bool:
    """Whether ``entry`` already holds a HuSpaCy-quality cloud for this exact
    (unchanged) text, which the backend we are about to use could only make worse.

    The word-cloud method tag names the *backend* as well as the model, so a
    sitting that loses its model — an archive cycle once processed on Modal, now
    outside the Modal budget scope on a host with no local model — misses its cache
    entry under the regex method and would have its lemmatized cloud silently
    overwritten by a regex one on the next full rebuild. Re-checking the
    fingerprint under the method that *produced* the entry tells us the transcript
    is unchanged, so those cached words are still exactly right and are kept.
    Entries written before the cache became self-describing carry no ``method``;
    for those the sitting's assigned HuSpaCy model is the only candidate."""
    if not entry or entry.get("model") == "regex":
        return False
    method = entry.get("method") or nlp.method_tag(model)
    if method.startswith("regex"):
        return False
    return entry.get("fp") == _session_fingerprint(method, texts)


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

    The model is chosen **per sitting**: the newest electoral period uses
    ``settings.huspacy_model``, frozen earlier periods the cheaper
    ``settings.huspacy_model_archive`` (see ``_session_plan``). Each model
    resolves its own backend/method; since the method is hashed into the cache
    fingerprint, entries from different models coexist in one cache file and a
    model switch only ever recomputes the sittings whose assigned model changed.
    Each cache entry also records the ``model`` name (``"regex"`` for the
    fallback tokenizer) and its exact ``method`` tag, so which model produced a
    sitting's cloud is directly inspectable without recomputing fingerprints.

    Whether a sitting may use Modal *at all* is also decided per sitting
    (``settings.modal_cycles``, default: the newest cycle only) so that
    backfilling the archive never spends the live cycle's Modal budget. A sitting
    that is out of scope uses a local model when one is installed and the regex
    tokenizer otherwise — except that a cached HuSpaCy result for unchanged text
    is kept rather than overwritten with a poorer one (``_richer_cache_hit``).
    """
    plan = _session_plan(conn)
    # (model, modal_ok) → (backend, method)
    resolved: dict[tuple[str, bool], tuple[str, str]] = {}

    def _resolve(model: str, modal_ok: bool) -> tuple[str, str]:
        if (model, modal_ok) not in resolved:
            backend = _nlp_backend(model, modal_ok=modal_ok)
            method = "regex:v1" if backend == "regex" else nlp.method_tag(model)
            logger.info("Word-cloud extraction: model=%s backend=%s%s", model, backend,
                        "" if modal_ok else " (outside the Modal cycle scope)")
            resolved[(model, modal_ok)] = (backend, method)
        return resolved[(model, modal_ok)]

    cache_path = _wordcloud_cache_path(cache_dir) if cache_dir else None
    cache: dict = {"sessions": {}}
    if cache_path and cache_path.exists():
        try:
            loaded = json.loads(cache_path.read_text())
            # The method is part of each entry's fingerprint, so entries made
            # with another model/method simply miss — no whole-file staleness
            # gate needed (legacy files with a top-level "method" load fine).
            if isinstance(loaded.get("sessions"), dict):
                cache["sessions"] = loaded["sessions"]
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

    done: set[str] = set()      # sittings whose rows are in (cached or computed)

    def _write(sid, words):
        done.add(sid)
        if words:
            conn.executemany(
                "INSERT INTO session_word_count(session_id, word, count, kind) "
                "VALUES (?, ?, ?, ?)",
                [(sid, w, c, k) for w, (c, k) in words.items()])

    reused = recomputed = kept = 0

    def _emit(sid, fp, words, model, method):
        # Store a freshly-computed sitting: cache it, write its rows, and commit +
        # flush the cache periodically so a long pass is resumable (a re-run skips
        # what's already done rather than losing everything). ``model``/``method``
        # record which HuSpaCy model (or ``"regex"``) produced the entry, so the
        # cache is self-describing — no need to recompute fingerprints to tell.
        nonlocal recomputed
        cache["sessions"][sid] = {"fp": fp, "words": words,
                                  "model": model, "method": method}
        _write(sid, words)
        recomputed += 1
        if recomputed % 10 == 0:
            conn.commit()
            _flush_cache()
            logger.info("session_word_count progress: %d recomputed", recomputed)

    # Cache hits are written immediately; local misses (HuSpaCy or regex) are
    # processed one sitting at a time (low memory); Modal misses are collected
    # per model and shipped in batches to that model's deployed app.
    modal_misses: dict[str, list] = {}   # model → [(sid, fp, texts)]
    try:
        for sid in sids:
            model, modal_ok = plan.get(sid, (settings.huspacy_model, True))
            backend, method = _resolve(model, modal_ok)
            texts = _fetch(sid)
            fp = _session_fingerprint(method, texts)
            entry = cache["sessions"].get(sid)
            if entry and entry.get("fp") == fp:
                _write(sid, entry["words"])
                reused += 1
            elif backend == "regex" and _richer_cache_hit(entry, model, texts):
                # Nothing better is reachable for this sitting right now; keep the
                # HuSpaCy cloud it already has instead of downgrading it to regex.
                _write(sid, entry["words"])
                reused += 1
                kept += 1
            elif backend == "modal":
                modal_misses.setdefault(model, []).append((sid, fp, texts))
            elif backend == "huspacy":
                counts, entity_words = nlp.analyze_counts(texts, model=model)
                _emit(sid, fp, _words_map(counts, entity_words), model, method)
            else:
                # Regex fallback: no HuSpaCy model was used, so record it as such.
                counts, entity_words = count_words(texts), set()
                _emit(sid, fp, _words_map(counts, entity_words), "regex", method)
        conn.commit()
        for model, misses in modal_misses.items():
            _backend, method = _resolve(model, True)
            for sid, fp, words in nlp_modal.extract(misses,
                                                    app_name=_modal_app_for(model)):
                _emit(sid, fp, words, model, method)
    except Exception as exc:  # enrichment must never break the build (SCR-5)
        # The cloud is enrichment; the transcript it was derived from is the
        # product. An NLP backend that dies mid-pass (Modal workspace disabled,
        # network down, OOM) used to propagate out of the incremental update,
        # which discards its whole temp DB — so a dead backend silently froze the
        # site on stale sittings while the scrape kept succeeding. Keep what was
        # computed and name what is left without a cloud. Those sittings do NOT
        # refill by themselves once the backend is back: an update only revisits
        # sittings whose source file changed, so refilling means touching their
        # processed JSON (load_state is (mtime, size)) or a full rebuild — the
        # same gap `--reextract-entities` covers on the entity side.
        missing = [s for s in sids if s not in done]
        logger.warning("word-count extraction aborted (%s); keeping what was done "
                       "— %d sitting(s) left without a word cloud (%s)",
                       exc, len(missing), ", ".join(missing[:10]) or "none")

    conn.commit()
    _flush_cache()
    logger.info("session_word_count: %d sittings (%d processed, %d cached%s)",
                len(sids), recomputed, reused,
                f", {kept} of them kept from a now-unreachable model" if kept else "")


# Bump when the entity-span extraction logic changes in a way that should
# invalidate the on-disk entity cache even if the model/text are unchanged.
# ent-v2: extract ORG (institutions) alongside PER, and carry the span `kind`.
# ent-v3: extract EVERY label the model emits — LOC and MISC too — so the corpus
# keeps a complete entity layer. Only PER/ORG are linked, so this is invisible on
# the site; it does force a full re-run of NER (no cached entry has LOC/MISC).
_ENTITY_LOGIC = "ent-v3"


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
    """Extract named-entity mentions from transcript sentences into the ``entity``
    table (NEL, §10) so the transcript can link names inline.

    Every label the model emits is stored — PER, ORG, LOC and MISC — so the corpus
    carries a complete entity layer for analysis. Only PER/ORG are resolved to a
    destination (``resolve_entity_links``) and therefore only those ever render:
    the LOC/MISC rows are inert as far as the site is concerned.

    Uses the same HuSpaCy backend and per-cycle model routing as the word cloud
    (Modal when configured *and* the cycle is within the Modal budget scope, else
    the local model; current cycle vs archive — see ``_session_plan``); when a
    model is available neither way its
    sittings are skipped, so a bare-host build still succeeds (OPS-4). A skip is
    **non-destructive** — the sitting keeps whatever mentions it already had rather
    than being cleared and left empty — and is logged as a warning when it hits the
    current cycle, since that silently strips every inline link from the newest
    sitting days. Cached on disk like the word cloud
    (``entity-cache.json``), keyed by a fingerprint of the text + method, so a
    rebuild re-runs NER only for the sittings whose transcript actually changed.
    Each cache entry also records the ``model`` name and its exact ``method``
    tag, so which model produced a sitting's mentions is directly inspectable
    without recomputing fingerprints.
    ``only_sessions`` scopes the pass to just those ids (the ``--update`` path).

    This only fills ``entity`` (the mention spans); resolving each distinct name
    to a Wikidata item/Wikipedia article is a separate, network-side step
    (``app.wikidata.resolve_entities`` → ``entity_link``)."""
    if not settings.entity_links:
        return
    _ensure_entity_tables(conn)
    # Person spans need the neural NER; the regex tokenizer can't produce them.
    # The model is chosen per sitting (current cycle vs archive — see
    # ``_session_plan``); per model, an explicit Modal backend wins over a
    # locally-installed model (same precedence as ``_nlp_backend``) — the host
    # offloads, never grinds through the transformer itself. A model available
    # neither locally nor via Modal skips its sittings (cached spans are still
    # reused), so a bare-host build still succeeds. A sitting whose cycle is
    # outside the Modal budget scope (``settings.modal_cycles``) never counts
    # Modal as available: unlike the word cloud there is no regex fallback here,
    # so an archive backfill simply leaves those days without inline links until a
    # local model is installed (or the scope is widened for a one-off re-extract).
    plan = _session_plan(conn)
    # (model, modal_ok) → (mode|None, method)
    resolved: dict[tuple[str, bool], tuple[str | None, str]] = {}

    def _resolve(model: str, modal_ok: bool = True) -> tuple[str | None, str]:
        if (model, modal_ok) not in resolved:
            if _nlp_backend(model, modal_ok=modal_ok) == "modal":
                mode = "modal"
            elif nlp.available(model):
                mode = "local"
            else:
                # A warning, not info: the current cycle's model is Modal-only
                # (hu_core_news_trf), so a host without the Modal backend silently
                # produces NO mentions for the live cycle — see the per-sitting
                # summary below.
                logger.warning("entity extraction unavailable for model %s "
                               "(not installed locally, no Modal backend%s)", model,
                               "" if modal_ok else ", cycle outside the Modal scope")
                mode = None
            method = nlp.method_tag(model) + ":" + _ENTITY_LOGIC
            resolved[(model, modal_ok)] = (mode, method)
        return resolved[(model, modal_ok)]

    cache_path = _entity_cache_path(cache_dir) if cache_dir else None
    cache: dict = {"sessions": {}}
    if cache_path and cache_path.exists():
        try:
            loaded = json.loads(cache_path.read_text())
            # Per-entry fingerprints embed the method, so mixed-model entries
            # coexist and stale ones simply miss (legacy single-method files
            # with a top-level "method" load fine).
            if isinstance(loaded.get("sessions"), dict):
                cache["sessions"] = loaded["sessions"]
        except (OSError, ValueError):
            logger.warning("Could not read entity cache %s; recomputing", cache_path)

    def _flush():
        if cache_path:
            try:
                cache_path.write_text(json.dumps(cache, ensure_ascii=False))
            except OSError as exc:
                logger.warning("Could not write entity cache %s (%s)", cache_path, exc)

    sids = [r[0] for r in conn.execute("SELECT id FROM session ORDER BY id DESC")]
    if only_sessions is not None:
        sids = [sid for sid in sids if sid in only_sessions]

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

    # Classify every sitting BEFORE touching the `entity` table. Clearing it up
    # front — as this used to — made a skip DESTRUCTIVE: a sitting whose model had
    # become unavailable was wiped and then left empty, so its transcript silently
    # lost every inline link. Only the sittings we are about to (re)write are cleared.
    reuse: list[str] = []                     # cache hits — sids only, see below
    # (model, modal_ok) → [(sid, fp, sent_rows)]
    misses_by: dict[tuple[str, bool], list] = {}
    skipped_sids: list[str] = []
    for sid in sids:
        model, modal_ok = plan.get(sid, (settings.huspacy_model, True))
        mode, method = _resolve(model, modal_ok)
        sent_rows = _fetch(sid)
        fp = _session_fingerprint(method, [t for (_i, t) in sent_rows])
        entry = cache["sessions"].get(sid)
        if entry and entry.get("fp") == fp:
            reuse.append(sid)
        elif mode is None:
            skipped_sids.append(sid)
        else:
            misses_by.setdefault((model, modal_ok), []).append((sid, fp, sent_rows))

    if skipped_sids:
        # Skipping the CURRENT cycle is the loud case: those are the days the site
        # front page links to, and they end up with no inline links at all. Archive
        # sittings normally never reach here (their cached spans match), so a skip
        # there is worth a line too, just not an alarm.
        current = [s for s in skipped_sids
                   if plan.get(s, (settings.huspacy_model, True))[0]
                   == settings.huspacy_model]
        (logger.warning if current else logger.info)(
            "entity extraction skipped for %d sitting(s) with no usable model — "
            "%d of them in the CURRENT cycle (%s): their transcripts render with NO "
            "inline entity links. Set PARLAMONITOR_WORDCLOUD_BACKEND=modal with "
            "MODAL_TOKEN_ID/MODAL_TOKEN_SECRET, or install %s locally.",
            len(skipped_sids), len(current),
            ", ".join(sorted(current)[:10]) or "none", settings.huspacy_model)

    if only_sessions is None and not skipped_sids:
        conn.execute("DELETE FROM entity")   # whole corpus rewritten; nothing to keep
    else:
        for sid in reuse:
            _delete_session_entities(conn, sid)
        for misses in misses_by.values():
            for (sid, _fp, _rows) in misses:
                _delete_session_entities(conn, sid)

    processed = mentions = 0
    reused = len(reuse)
    skipped = len(skipped_sids)
    # A cache hit re-fetches its sentence rows here instead of carrying them across
    # the classification pass (which would hold the whole corpus' text at once); the
    # query is the same deterministic one, so the cached spans still line up.
    for sid in reuse:
        mentions += _write(_fetch(sid), cache["sessions"][sid]["spans"])

    def _emit(sid, fp, sent_rows, per_sentence_spans, model, method):
        # ``model``/``method`` record which HuSpaCy model produced the entry, so
        # the cache is self-describing (no fingerprint recomputation needed to tell).
        nonlocal processed, mentions
        cache["sessions"][sid] = {"fp": fp, "spans": per_sentence_spans,
                                  "model": model, "method": method}
        mentions += _write(sent_rows, per_sentence_spans)
        processed += 1
        if processed % 10 == 0:
            conn.commit()
            _flush()
            logger.info("entity mentions progress: %d sittings processed", processed)

    try:
        for (model, modal_ok), misses in misses_by.items():
            mode, method = _resolve(model, modal_ok)
            if mode == "modal":
                packed = [(sid, fp, [t for (_i, t) in rows]) for (sid, fp, rows) in misses]
                by_sid = {sid: rows for (sid, _fp, rows) in misses}
                for sid, fp, spans in nlp_modal.extract_spans(
                        packed, app_name=_modal_app_for(model)):
                    _emit(sid, fp, by_sid[sid], spans, model, method)
            else:
                for (sid, fp, sent_rows) in misses:
                    spans = [[list(s) for s in per_sent] for per_sent in
                             nlp.entity_spans([t for (_i, t) in sent_rows], model=model)]
                    _emit(sid, fp, sent_rows, spans, model, method)
    except Exception as exc:  # enrichment must never break the build (SCR-5)
        logger.warning("entity extraction aborted (%s); keeping what was done", exc)

    conn.commit()
    _flush()
    # Per-kind totals come from the table, not the run: on a scoped --update the
    # run touches a sitting or two, and what matters is what the corpus now holds.
    by_kind = ", ".join(
        f"{k} {n}" for k, n in conn.execute(
            "SELECT kind, COUNT(*) FROM entity GROUP BY kind ORDER BY 2 DESC"))
    logger.info("entity mentions: %d sittings (%d processed, %d cached, %d skipped), "
                "%d mentions written; table holds %s", len(sids), processed, reused,
                skipped, mentions, by_kind or "nothing")


def _entity_kinds(conn: sqlite3.Connection) -> dict[str, str]:
    """Each distinct ``entity_key`` → its majority ``kind`` (PER/ORG). One key is
    almost always one kind; the rare mixed key takes whichever mention kind is more
    frequent. Shared by both resolvers so they agree on how to query/match a name.

    Only the linkable kinds are considered: LOC/MISC mentions are stored but never
    resolved, and counting them here would both pull unlinkable names into the
    Wikidata queries and let a place flip the kind of a name that is also an org."""
    placeholders = ",".join("?" * len(nlp.LINKABLE_LABELS))
    try:
        rows = conn.execute(
            "SELECT entity_key, kind, COUNT(*) c FROM entity "
            f"WHERE kind IS NULL OR kind IN ({placeholders}) "
            "GROUP BY entity_key, kind",
            tuple(sorted(nlp.LINKABLE_LABELS))).fetchall()
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
# Per-speech readability + lexical diversity (READ-1..7)
# ---------------------------------------------------------------------------

# Column order of a cached metrics row. Stored positionally (a list per speech)
# rather than as a dict: the cache holds one entry per measurable speech in the
# corpus, and eleven repeated key names per row would dominate the file.
_METRIC_FIELDS = ("lix", "rix", "words", "sentences", "long_words",
                  "avg_sentence", "long_share", "ttr", "mattr", "types", "tokens")


def _speech_metrics_cache_path(cache_dir: Path) -> Path:
    return Path(cache_dir) / "speech-metrics-cache.json"


def _ensure_speech_metrics_tables(conn: sqlite3.Connection) -> None:
    """Create the metrics tables in place if they are missing, so the feature also
    lands on an existing DB through the incremental ``--update`` path rather than
    only on a full rebuild (cf. ``_ensure_entity_tables``)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS speech_metrics (
            speech_id    TEXT PRIMARY KEY REFERENCES speech(uid),
            session_id   TEXT NOT NULL REFERENCES session(id),
            lix          REAL,
            rix          REAL,
            words        INTEGER,
            sentences    INTEGER,
            long_words   INTEGER,
            avg_sentence REAL,
            long_share   REAL,
            ttr          REAL,
            mattr        REAL,
            types        INTEGER,
            tokens       INTEGER,
            lemma_model  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_speech_metrics_session
            ON speech_metrics(session_id);
        CREATE TABLE IF NOT EXISTS metric_distribution (
            metric TEXT NOT NULL,
            q      INTEGER NOT NULL,
            value  REAL NOT NULL,
            n      INTEGER,
            PRIMARY KEY (metric, q)
        ) WITHOUT ROWID;
    """)


def _lemma_backend(model: str, *, modal_ok: bool) -> str | None:
    """How this model's lemmas can be produced: ``"modal"``, ``"local"`` or
    ``None`` (no lemmatizer reachable → readability only, READ-4). Same precedence
    as the other NLP passes: an explicit Modal backend beats a locally installed
    model, and a cycle outside the Modal budget scope may not use Modal at all."""
    if _nlp_backend(model, modal_ok=modal_ok) == "modal":
        return "modal"
    return "local" if nlp.available(model) else None


def _metrics_row(read: dict, lemmas: list[str] | None) -> list:
    """One speech's cached metrics row, from its already-computed readability dict
    and (optionally) its lemma stream.

    The two halves come from deliberately different inputs — LIX from the
    transcript's surface text, diversity from the lemma stream — because saphes'
    contract is that feeding one stream to both silently corrupts one of them (see
    ``app/readability.py``)."""
    div = readability.diversity(lemmas) if lemmas else None
    merged = {**read, **(div or {})}
    return [merged.get(f) for f in _METRIC_FIELDS]


def rebuild_speech_metrics(conn: sqlite3.Connection,
                           cache_dir: str | Path | None = None,
                           only_sessions: set[str] | None = None,
                           lemmas: bool = True) -> None:
    """Precompute per-speech readability + lexical diversity into ``speech_metrics``
    (READ-1..7), then refresh the corpus-relative band cut points.

    Two metrics from **saphes**, over the *substantive* speeches only (procedural
    chairing speeches are excluded exactly as they are from the statistics and the
    word cloud — STAT-1):

    * **LIX/RIX** needs no model. It is computed from the transcript's own
      sentences, with the speaker attribution and the stenographer's stage
      directions stripped (``readability.spoken_sentences``), so it always lands —
      even on a bare host with no HuSpaCy at all.
    * **TTR/MATTR** needs *lemmas*, which needs the model. Sittings whose
      lemmatizer is unreachable get their readability row anyway, with the
      diversity columns left NULL and ``lemma_model`` NULL to say so; they are
      **never** approximated from surface forms, which in Hungarian would measure
      morphology instead of vocabulary.

    Cached on disk (``speech-metrics-cache.json`` in ``cache_dir``) exactly like
    the word cloud and the entity pass: per sitting, keyed by a fingerprint of its
    text *and* the method (saphes version, threshold, length policy, MATTR window,
    lemma model), so a rebuild only re-measures sittings whose transcript actually
    changed — and a readability-only entry is not mistaken for a complete one once
    a lemmatizer becomes available. A complete cached entry is likewise never
    downgraded to a readability-only one when the model goes away
    (``_richer_metrics_hit``).

    ``only_sessions`` scopes the pass to those ids (the ``--update`` path); the
    distribution is always recomputed over the whole table, since one new sitting
    still shifts the corpus it is banded against. ``lemmas=False`` forces the
    readability-only path (what ``--skip-wordcloud`` asks for: no neural pass, but
    the cheap half still lands), without downgrading sittings that already have
    complete measurements.
    """
    if not settings.speech_metrics:
        return
    _ensure_speech_metrics_tables(conn)
    plan = _session_plan(conn)
    # (model, modal_ok) → (lemma mode | None, method tag)
    resolved: dict[tuple[str, bool], tuple[str | None, str]] = {}

    def _resolve(model: str, modal_ok: bool) -> tuple[str | None, str]:
        if (model, modal_ok) not in resolved:
            mode = _lemma_backend(model, modal_ok=modal_ok) if lemmas else None
            if mode is None:
                logger.info("speech metrics: no lemmatizer for model %s%s — "
                            "readability only, no lexical diversity", model,
                            "" if modal_ok else " (cycle outside the Modal scope)")
            method = readability.method_tag(model if mode else None)
            resolved[(model, modal_ok)] = (mode, method)
        return resolved[(model, modal_ok)]

    cache_path = _speech_metrics_cache_path(cache_dir) if cache_dir else None
    cache: dict = {"sessions": {}}
    if cache_path and cache_path.exists():
        try:
            loaded = json.loads(cache_path.read_text())
            if isinstance(loaded.get("sessions"), dict):
                cache["sessions"] = loaded["sessions"]
        except (OSError, ValueError):
            logger.warning("Could not read speech-metrics cache %s; recomputing",
                           cache_path)

    def _flush():
        if cache_path:
            try:
                cache_path.write_text(json.dumps(cache, ensure_ascii=False))
            except OSError as exc:
                logger.warning("Could not write speech-metrics cache %s (%s)",
                               cache_path, exc)

    sids = [r[0] for r in conn.execute("SELECT id FROM session ORDER BY id DESC")]
    if only_sessions is not None:
        sids = [sid for sid in sids if sid in only_sessions]

    def _fetch(sid) -> list[tuple[str, list[str]]]:
        """The sitting's substantive speeches as ``(uid, [sentence, …])``, in a
        deterministic order so a cached fingerprint keeps matching."""
        rows = conn.execute(
            "SELECT sp.uid, se.text FROM sentence se "
            "JOIN speech sp ON sp.uid = se.speech_id "
            "WHERE sp.session_id = ? AND sp.procedural = 0 AND se.text IS NOT NULL "
            "ORDER BY sp.speech_index, se.id", (sid,)).fetchall()
        speeches: list[tuple[str, list[str]]] = []
        for uid, text in rows:
            if not speeches or speeches[-1][0] != uid:
                speeches.append((uid, []))
            speeches[-1][1].append(text)
        return speeches

    def _flat(speeches) -> list[str]:
        """Every sentence of the sitting, in order — what the fingerprint covers,
        so a change anywhere in the day's text invalidates its entry."""
        return [t for (_uid, texts) in speeches for t in texts]

    def _measurable(speeches) -> list[tuple[str, list[str], dict]]:
        """The speeches that clear the length floor, with their readability already
        computed: ``(uid, [sentence, …], readability)``.

        Filtering here — before the lemma pass rather than after it — is what keeps
        the neural half honest about its cost. Roughly half of a sitting's
        substantive speeches are one- or two-sentence contributions that will never
        be scored, and lemmatizing them would burn model time (and, on Modal,
        metered credit) to produce a row that is then discarded."""
        out = []
        for uid, texts in speeches:
            read = readability.readability(texts)
            if read is not None:
                out.append((uid, texts, read))
        return out

    def _lemma_texts(measurable) -> list[str]:
        return [t for (_uid, texts, _read) in measurable for t in texts]

    def _richer_metrics_hit(entry, model: str, speeches) -> bool:
        """Whether ``entry`` already holds a *complete* (lemma-backed) measurement
        of this exact, unchanged text — which the lemma-less run we are about to do
        could only make worse. Mirrors ``_richer_cache_hit`` on the word-cloud side:
        a sitting that temporarily loses its model must keep the diversity numbers
        it has instead of having them nulled out on the next rebuild."""
        if not entry or not entry.get("model") or entry.get("model") == "none":
            return False
        method = entry.get("method") or readability.method_tag(model)
        return entry.get("fp") == _session_fingerprint(method, _flat(speeches))

    def _write(sid, metrics: dict, lemma_model: str | None):
        if not metrics:
            return
        conn.executemany(
            "INSERT OR REPLACE INTO speech_metrics(speech_id, session_id, "
            + ", ".join(_METRIC_FIELDS) + ", lemma_model) VALUES (?, ?, "
            + ", ".join("?" * len(_METRIC_FIELDS)) + ", ?)",
            [(uid, sid, *row, lemma_model) for uid, row in metrics.items()])

    # Classify first, write second — so a sitting we end up not recomputing keeps
    # the rows it already has rather than being cleared and left empty.
    reuse: list[str] = []
    # (model, modal_ok) → [(sid, fp, measurable speeches)]
    misses_by: dict[tuple[str, bool], list] = {}
    for sid in sids:
        model, modal_ok = plan.get(sid, (settings.huspacy_model, True))
        mode, method = _resolve(model, modal_ok)
        speeches = _fetch(sid)
        fp = _session_fingerprint(method, _flat(speeches))
        entry = cache["sessions"].get(sid)
        if entry and entry.get("fp") == fp:
            reuse.append(sid)
        elif mode is None and _richer_metrics_hit(entry, model, speeches):
            reuse.append(sid)
        else:
            misses_by.setdefault((model, modal_ok), []).append(
                (sid, fp, _measurable(speeches)))

    if only_sessions is None:
        conn.execute("DELETE FROM speech_metrics")
    else:
        conn.executemany("DELETE FROM speech_metrics WHERE session_id = ?",
                         [(s,) for s in sids])

    processed = 0
    measured = 0
    diverse = 0                              # sittings that also got TTR/MATTR

    for sid in reuse:
        entry = cache["sessions"][sid]
        model = entry.get("model")
        model = None if model in (None, "none") else model
        _write(sid, entry.get("metrics") or {}, model)
        measured += len(entry.get("metrics") or {})
        if model:
            diverse += 1

    def _emit(sid, fp, metrics, lemma_model, method):
        nonlocal processed, measured, diverse
        cache["sessions"][sid] = {"fp": fp, "metrics": metrics,
                                  "model": lemma_model or "none", "method": method}
        _write(sid, metrics, lemma_model)
        processed += 1
        measured += len(metrics)
        if lemma_model:
            diverse += 1
        if processed % 25 == 0:
            conn.commit()
            _flush()
            logger.info("speech metrics progress: %d sittings measured", processed)

    def _measure(measurable, per_sentence_lemmas) -> dict:
        """``{uid: row}`` for one sitting. ``per_sentence_lemmas`` is one lemma list
        per sentence of ``_lemma_texts(measurable)`` (or ``None`` on the
        readability-only path); it is re-grouped here into the per-speech streams
        MATTR slides its window over."""
        out, at = {}, 0
        for uid, texts, read in measurable:
            lemmas = None
            if per_sentence_lemmas is not None:
                lemmas = [lem for per_sent in per_sentence_lemmas[at:at + len(texts)]
                          for lem in per_sent]
            at += len(texts)
            out[uid] = _metrics_row(read, lemmas)
        return out

    try:
        for (model, modal_ok), misses in misses_by.items():
            mode, method = _resolve(model, modal_ok)
            # A sitting with nothing measurable (all procedural, or every speech
            # under the floor) still gets a cache entry — so it is not reclassified
            # as a miss on every future run — but never a model call: dispatching an
            # empty batch to Modal would pay the round trip for no rows.
            empty = [m for m in misses if not m[2]]
            misses = [m for m in misses if m[2]]
            for (sid, fp, _meas) in empty:
                _emit(sid, fp, {}, None, method)
            if mode == "modal":
                packed = [(sid, fp, _lemma_texts(meas)) for (sid, fp, meas) in misses]
                by_sid = {sid: meas for (sid, _fp, meas) in misses}
                for sid, fp, lemmas in nlp_modal.extract_lemmas(
                        packed, app_name=_modal_app_for(model)):
                    _emit(sid, fp, _measure(by_sid[sid], lemmas), model, method)
            elif mode == "local":
                for (sid, fp, meas) in misses:
                    lemmas = list(nlp.lemma_streams(_lemma_texts(meas), model=model))
                    _emit(sid, fp, _measure(meas, lemmas), model, method)
            else:
                for (sid, fp, meas) in misses:
                    _emit(sid, fp, _measure(meas, None), None, method)
    except Exception as exc:  # enrichment must never break the build (SCR-5)
        # Same contract as the word cloud and the entity pass: the transcript is
        # the product, the metrics are enrichment. A backend dying mid-pass keeps
        # what was measured instead of failing the whole load.
        logger.warning("speech metrics aborted (%s); keeping what was measured", exc)

    conn.commit()
    _flush()
    rebuild_metric_distribution(conn)
    logger.info("speech metrics: %d sittings (%d measured, %d cached), %d speeches "
                "scored, %d sittings with lexical diversity",
                len(sids), processed, len(reuse), measured, diverse)


def rebuild_metric_distribution(conn: sqlite3.Connection) -> None:
    """Refresh the corpus-relative cut points the UI bands a speech against (READ-6).

    Björnsson's absolute difficulty labels do not survive the Hungarian long-word
    threshold (saphes returns ``band = None`` off threshold 6 rather than mislabel
    the number), so a speech is placed against the distribution of every measured
    speech instead. Computed over the WHOLE table — one new sitting still shifts
    the corpus its neighbours are compared to — but that is a single ordered scan
    of a column, so it stays cheap on an incremental update.
    """
    conn.execute("DELETE FROM metric_distribution")
    for metric in ("lix", "mattr"):
        values = [v for (v,) in conn.execute(
            f"SELECT {metric} FROM speech_metrics WHERE {metric} IS NOT NULL")]
        cuts = readability.quantiles(values)
        if not cuts:
            continue
        conn.executemany(
            "INSERT INTO metric_distribution(metric, q, value, n) VALUES (?,?,?,?)",
            [(metric, q, v, len(values)) for q, v in sorted(cuts.items())])
    conn.commit()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

# Writer processes currently holding the lock in THIS process, by resolved DB
# path → depth. flock is per open-file-description, so a nested acquire from the
# same process (update_database falling back to build_database) would deadlock
# against itself; count depth instead of re-locking.
_writer_lock_depth: dict[str, int] = {}


def _writer_lock_path(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + ".loader.lock")


@contextlib.contextmanager
def _writer_lock(db_path: Path):
    """Serialize DB-writing loader runs across processes (ING-2).

    The loader is the only writer, but there can be several writer *processes*:
    ``./deploy.sh`` runs ``init`` (``ensure-db``) while the ``sync`` sidecar may
    be mid ``--update``, and both stage their output at the same
    ``<db>.building`` path — each clears that path when it starts, so whichever
    finishes second finds its own temp file gone and dies with ``FileNotFoundError``
    on the final rename, after doing all the work. A blocking lock beside the DB
    makes the second one wait for the first instead.

    Best-effort: if the lock file cannot be created (read-only dir, no fcntl) the
    run proceeds unlocked rather than failing — the lock is a safety net, not a
    correctness requirement for the single-writer case.
    """
    db_path = Path(db_path)
    key = str(db_path.resolve())
    if _writer_lock_depth.get(key):
        _writer_lock_depth[key] += 1
        try:
            yield
        finally:
            _writer_lock_depth[key] -= 1
        return

    fh = None
    if fcntl is not None:
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(_writer_lock_path(db_path), "a+")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                logger.info("Another loader run holds %s — waiting for it to finish",
                            _writer_lock_path(db_path).name)
                fcntl.flock(fh, fcntl.LOCK_EX)
        except OSError as exc:
            logger.warning("Could not take the loader lock (%s); proceeding unlocked", exc)
            if fh is not None:
                fh.close()
                fh = None

    _writer_lock_depth[key] = 1
    try:
        yield
    finally:
        _writer_lock_depth.pop(key, None)
        if fh is not None:
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            finally:
                fh.close()


def build_database(data_dir: str | Path, db_path: str | Path, *,
                   only_session: str | None = None,
                   skip_wordcloud: bool = False) -> None:
    """Full rebuild into a fresh file, then atomic swap over the live DB (DB-4).

    ``skip_wordcloud`` skips the (expensive) per-sitting term extraction that feeds
    the word cloud / new-words features (WCLOUD-2). Those tables are left empty, so
    those views come up blank — handy for a fast dev rebuild when they aren't needed.

    Waits for any other loader process writing the same DB (see ``_writer_lock``).
    """
    with _writer_lock(Path(db_path)):
        _build_database(data_dir, db_path, only_session=only_session,
                        skip_wordcloud=skip_wordcloud)


def _build_database(data_dir: str | Path, db_path: str | Path, *,
                    only_session: str | None = None,
                    skip_wordcloud: bool = False) -> None:
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

        # Nationality advocates right after the MP roster: they are `person` rows
        # too, and load before bills/votes so an advocate who submitted an iromány
        # is linked as its sponsor rather than staying a label (EXT-2).
        for ap in sorted((data_dir / "processed").glob("advocates-*.json")):
            registry = json.loads(ap.read_text())
            _load_period_meta(conn, registry.get("meta", {}))
            load_advocates(conn, registry)

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

        # Office holders load LAST of the registries: a non-MP minister exists as a
        # `person` row only once the sittings they spoke in are in (the speaker stub),
        # and their office terms are exactly what this registry supplies (REP-2).
        _load_office_holders_file(conn, data_dir)

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
        # Per-speech readability + lexical diversity (READ-1..7). Cached on disk
        # like the passes above; the readability half needs no model, so it lands
        # even under --skip-wordcloud (which only drops the lemma pass).
        rebuild_speech_metrics(conn, db_path.parent, lemmas=not skip_wordcloud)
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
    _swap_in(tmp_path, db_path)
    logger.info("Built database at %s", db_path)


# ---------------------------------------------------------------------------
# Incremental update (SCR-2 / DB-4) — reload only changed files, then swap
# ---------------------------------------------------------------------------

# The processed-file globs, in the load order full builds use (reps → advocates →
# bills → votes → sessions) so cross-module person/faction/bill links resolve
# (EXT-2). A newly-added glob is "changed" for every existing DB (nothing is in its
# load_state yet), which is exactly how a new domain lands on an already-built
# deployment through the incremental path alone.
_PROCESSED_GLOBS = ("representatives-*.json", "advocates-*.json", "bills-*.json",
                    "votes-*.json", "*-session.json", "officeholders.json")


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


_SESSION_SUFFIX = "-session.json"


def _removed_sessions(processed: Path,
                      prev: dict[str, tuple[float, int]]) -> list[str]:
    """Session ids whose processed file was loaded before but is now GONE.

    The only way a sitting ever leaves the site: parlament.hu announces sittings
    days ahead and sometimes cancels one, at which point the scraper deletes its
    files (``proceedings.scrape.prune_cancelled``). Loading is otherwise purely
    additive — an unmatched session row would keep being served as an upcoming
    sitting for ever, and its ülésnap number is by then the day announced in its
    place, so the site would show the cancelled day *and* miss the real one.

    Scoped to ``load_state`` (what this data dir gave us last time) rather than
    to the whole ``session`` table, so a partially-populated or differently-rooted
    processed dir can never be read as "everything else was deleted"."""
    return sorted(name[:-len(_SESSION_SUFFIX)] for name in prev
                  if name.endswith(_SESSION_SUFFIX)
                  and not (processed / name).exists())


def _remove_db_side_files(base: Path) -> None:
    for suffix in ("-wal", "-shm"):
        side = base.with_suffix(base.suffix + suffix)
        if side.exists():
            side.unlink()


def _swap_in(tmp_path: Path, db_path: Path) -> None:
    """Atomically move the freshly built ``tmp_path`` over the live ``db_path``
    (DB-4), leaving neither file's WAL side-files behind.

    ``os.replace`` swaps the *main* file only. Any ``<db>-wal`` / ``<db>-shm`` next
    to the destination belongs to the file we just replaced — an orphan left by an
    earlier crashed or killed writer — and SQLite will happily try to recover it
    over the new database, whose pages it knows nothing about. The result is a DB
    that passes ``integrity_check`` when opened ``immutable=1`` and fails every
    ordinary open with "malformed database schema … invalid rootpage". Observed
    for real: a WAL orphaned hours earlier survived a swap and made the whole DB
    unreadable, while the file underneath was perfectly intact.

    Nothing is lost by deleting them: the temp DB was checkpointed
    (``wal_checkpoint(TRUNCATE)``) before it was closed, so it needs no WAL of its
    own, and the snapshot it was built from was read *through* the old WAL, so
    whatever that WAL held is already inside the file now being swapped in.

    The unlink happens immediately **after** the replace, not before: until the new
    file is in place the old WAL is still the truth for anyone reading the old one,
    and readers that already hold it open keep their descriptors regardless.
    """
    os.replace(tmp_path, db_path)
    _remove_db_side_files(db_path)
    _remove_db_side_files(tmp_path)


def _remove_db_files(base: Path) -> None:
    if base.exists():
        base.unlink()
    _remove_db_side_files(base)


def update_database(data_dir: str | Path, db_path: str | Path, *,
                    skip_wordcloud: bool = False) -> bool:
    """Incrementally reconcile the live DB to the scraper's processed JSON.

    Compares each ``processed/*.json`` against the ``load_state`` recorded at the
    last build/update, drops the sittings whose file has since been deleted
    (:func:`_removed_sessions`), and reloads **only the changed files** into a private
    snapshot of the current DB, rebuilds the (cheap, SQL-only) aggregates, and
    atomically swaps the result over the live file (DB-4) — so the running API
    picks it up on its next request with no restart and no downtime, having
    reprocessed only what actually changed (SCR-2).

    Degrades to a full :func:`build_database` when there is no DB yet, or when the
    existing DB predates the ``load_state`` table (one rebuild seeds the baseline).
    Returns ``True`` if the DB was changed, ``False`` if nothing was stale.

    Waits for any other loader process writing the same DB (see ``_writer_lock``) —
    notably ``./deploy.sh``'s ``init`` run racing the ``sync`` sidecar.
    """
    with _writer_lock(Path(db_path)):
        return _update_database(data_dir, db_path, skip_wordcloud=skip_wordcloud)


def _update_database(data_dir: str | Path, db_path: str | Path, *,
                     skip_wordcloud: bool = False) -> bool:
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
    removed = _removed_sessions(processed, prev)
    n_changed = sum(len(v) for v in changed.values())
    if n_changed == 0 and not removed:
        logger.info("No processed file changed since last load; DB is up to date")
        return False
    logger.info("Incremental update: %d changed file(s), %d removed sitting(s) — %s",
                n_changed, len(removed),
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
        for p in changed["advocates-*.json"]:
            registry = json.loads(p.read_text())
            _load_period_meta(conn, registry.get("meta", {}))
            load_advocates(conn, registry)
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

        # A sitting whose source file disappeared is gone from the site too (a
        # cancelled announced sitting — see _removed_sessions). _delete_session
        # takes its speeches/sentences/entities/agenda with it; the aggregates
        # below are rebuilt from what is left.
        for sid in removed:
            _delete_session(conn, sid)
            conn.execute("DELETE FROM load_state WHERE name = ?",
                         (f"{sid}{_SESSION_SUFFIX}",))
            logger.info("Removed sitting %s: its processed file is gone", sid)

        # Office terms after the sittings, and also when only a sitting changed: a
        # non-MP minister becomes a `person` row the day their first speech lands,
        # and this registry is the only thing that dates their office (REP-2). It is
        # a single small file, so re-reading it costs nothing.
        if changed["officeholders.json"] or loaded_sessions:
            _load_office_holders_file(conn, data_dir)

        # Aggregates are speech-derived, so they only need rebuilding when a
        # sitting changed (bills/votes/reps carry their own rows). Word counts are
        # scoped to just the changed sittings (cached; the rest stay intact).
        if loaded_sessions:
            if not skip_wordcloud:
                rebuild_session_word_counts(conn, db_path.parent,
                                            only_sessions=set(loaded_sessions))
                rebuild_entity_mentions(conn, db_path.parent,
                                        only_sessions=set(loaded_sessions))
            rebuild_speech_metrics(conn, db_path.parent,
                                   only_sessions=set(loaded_sessions),
                                   lemmas=not skip_wordcloud)
        # A removal needs no per-sitting pass (its rows are gone) but does need the
        # corpus-wide aggregates — word document frequencies and the §6C portfolio
        # links among them — recomputed from what remains.
        if loaded_sessions or removed:
            rebuild_aggregates(conn)
        # Wire any non-MP speaker portraits the scraper has downloaded since the
        # last load (global, cheap — see wire_nonmp_photos). Also runs for an
        # advocates-only update: advocates are non-MP rows, so this is what gives
        # them a face when their portrait landed after the registry did.
        if loaded_sessions or changed["advocates-*.json"]:
            wire_nonmp_photos(conn, Path(data_dir) / "media" / "photos")
        # Re-resolve entity links when the transcript OR a person registry changed:
        # new mentions need resolving, and a roster change can flip a name from a
        # Wikipedia link to an internal profile (or set a K-Monitor link). Cheap when
        # nothing new (K-Monitor index + Wikidata names are cached; entity_link is
        # just rebuilt from cache).
        if (loaded_sessions or changed["representatives-*.json"]
                or changed["advocates-*.json"]):
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

    _swap_in(tmp_path, db_path)
    logger.info("Updated database at %s (%d sittings, %d removed, "
                "%d rep/bill/vote file(s))", db_path, len(loaded_sessions),
                len(removed), n_changed - len(loaded_sessions))
    return True


def reextract_entities(db_path: str | Path, *, period: int | None = None) -> bool:
    """Re-run entity NER + link resolution over an EXISTING DB, in place (NEL, §10).

    The escape hatch for the case ``--update`` cannot cover: the NLP passes are
    scoped to the sittings whose *source file* changed, so a change on the model
    side alone — a model becoming available (the Modal backend finally configured),
    or a sitting having been skipped when it wasn't — is a no-op for an update even
    though those sittings hold no (or stale-method) mentions. This re-runs the NLP
    for a chosen electoral period against the DB that is already built, without
    reloading any JSON and without the multi-minute full rebuild.

    ``period`` scopes it to one electoral period (the usual case: only the current
    cycle needs the expensive model); ``None`` covers every sitting. Sittings whose
    cached spans still fingerprint-match are reused, so this is cheap for anything
    already done. Snapshot-and-swap like :func:`update_database`, so the serving API
    picks the result up on its next request with no downtime, and it takes the same
    writer lock as every other loader run.

    Returns ``True`` when the DB was replaced, ``False`` when there was nothing to do.
    """
    with _writer_lock(Path(db_path)):
        return _reextract_entities(db_path, period=period)


def _reextract_entities(db_path: str | Path, *, period: int | None = None) -> bool:
    db_path = Path(db_path)
    if not db_path.exists():
        logger.error("No DB at %s — build it first", db_path)
        return False

    tmp_path = db_path.with_suffix(db_path.suffix + ".building")
    _remove_db_files(tmp_path)
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn = connect(tmp_path)
    try:
        src.backup(conn)
    finally:
        src.close()

    ok = False
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        only: set[str] | None = None
        if period is not None:
            only = {r[0] for r in conn.execute(
                "SELECT id FROM session WHERE period_number = ?", (period,))}
            if not only:
                logger.warning("No sittings in period %s — nothing to re-extract", period)
                return False
            logger.info("Re-extracting entity mentions for %d sitting(s) in period %s",
                        len(only), period)
        rebuild_entity_mentions(conn, db_path.parent, only_sessions=only)
        # entity_link is rebuilt wholesale from the mentions of the WHOLE corpus, so
        # this always runs unscoped — a period-scoped mention pass still changes which
        # names exist overall.
        resolve_entity_links(conn, db_path.parent)
        n_ment, n_names = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT entity_key) FROM entity").fetchone()
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
        ok = True
    finally:
        conn.close()
        if not ok:
            _remove_db_files(tmp_path)

    _swap_in(tmp_path, db_path)
    logger.info("Re-extracted entities into %s (%d mentions, %d distinct names)",
                db_path, n_ment, n_names)
    return True


def remeasure_speeches(db_path: str | Path, *, period: int | None = None) -> bool:
    """Re-run the readability / lexical-diversity pass over an EXISTING DB, in place
    (READ-1..7). The metrics counterpart of :func:`reextract_entities`, and it
    exists for the same reason: ``--update`` only revisits sittings whose *source
    file* changed, so anything that changes on the measurement side alone — a
    saphes upgrade, a different long-word threshold or MATTR window, a lemmatizer
    finally becoming reachable — is a no-op for an update even though every stored
    row is now stale (or missing its diversity half).

    ``period`` scopes it to one electoral cycle; ``None`` covers every sitting.
    Sittings whose cache entry still fingerprint-matches are reused, so re-running
    it is cheap. Snapshot-and-swap like :func:`update_database`, under the same
    writer lock.

    Returns ``True`` when the DB was replaced, ``False`` when there was nothing to do.
    """
    with _writer_lock(Path(db_path)):
        return _remeasure_speeches(db_path, period=period)


def _remeasure_speeches(db_path: str | Path, *, period: int | None = None) -> bool:
    db_path = Path(db_path)
    if not db_path.exists():
        logger.error("No DB at %s — build it first", db_path)
        return False

    tmp_path = db_path.with_suffix(db_path.suffix + ".building")
    _remove_db_files(tmp_path)
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn = connect(tmp_path)
    try:
        src.backup(conn)
    finally:
        src.close()

    ok = False
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        only: set[str] | None = None
        if period is not None:
            only = {r[0] for r in conn.execute(
                "SELECT id FROM session WHERE period_number = ?", (period,))}
            if not only:
                logger.warning("No sittings in period %s — nothing to measure", period)
                return False
            logger.info("Re-measuring %d sitting(s) in period %s", len(only), period)
        rebuild_speech_metrics(conn, db_path.parent, only_sessions=only)
        n_rows, n_div = conn.execute(
            "SELECT COUNT(*), COUNT(mattr) FROM speech_metrics").fetchone()
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
        ok = True
    finally:
        conn.close()
        if not ok:
            _remove_db_files(tmp_path)

    _swap_in(tmp_path, db_path)
    logger.info("Re-measured speeches into %s (%d scored, %d with MATTR)",
                db_path, n_rows, n_div)
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


def _loads_json(v):
    """Parse a stored JSON column back to a Python value (``None`` if unusable)."""
    if not v:
        return None
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


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
    ap.add_argument("--reextract-entities", action="store_true",
                    help="re-run entity NER + link resolution over the EXISTING DB "
                         "in place (no JSON reload, no full rebuild) and swap it in; "
                         "use with --period to scope it to one electoral cycle. The "
                         "way to pick up a model-side change, which --update — scoped "
                         "to changed source files — never revisits")
    ap.add_argument("--remeasure-speeches", action="store_true",
                    help="re-run the readability / lexical-diversity measurement "
                         "over the EXISTING DB in place and swap it in; use with "
                         "--period to scope it to one cycle. Same escape hatch as "
                         "--reextract-entities, for a saphes upgrade, a changed "
                         "threshold/window, or a lemmatizer becoming reachable")
    ap.add_argument("--period", type=int,
                    help="electoral cycle number to scope --reextract-entities / "
                         "--remeasure-speeches to (e.g. 43); omit to cover every "
                         "sitting")
    ap.add_argument("--skip-wordcloud", action="store_true",
                    help="skip per-sitting word-cloud/new-words term extraction "
                         "(faster dev rebuild; those views come up empty)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("parlamonitor.loader").setLevel(logging.INFO)
    in_place = args.reextract_entities or args.remeasure_speeches
    if args.period is not None and not in_place:
        ap.error("--period is only valid with --reextract-entities or "
                 "--remeasure-speeches")
    if in_place:
        if args.update or args.session:
            ap.error("--reextract-entities / --remeasure-speeches operate on the "
                     "existing DB; they cannot be combined with --update or --session")
        # data_dir is unused here (nothing is reloaded) but stays a required
        # positional so every loader invocation has the same shape.
        if args.reextract_entities:
            reextract_entities(args.db_path, period=args.period)
        if args.remeasure_speeches:
            remeasure_speeches(args.db_path, period=args.period)
    elif args.update:
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
