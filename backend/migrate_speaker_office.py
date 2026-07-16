"""One-off migration/backfill for the speaker-office (tisztség) feature.

The scraper now carries each speaker's government office (Felicitas ``tisztseg``,
e.g. "igazságügyi miniszter") into the transformed ``people[]``; the loader stores
it on ``speech.speaker_office`` and surfaces it on a non-MP speaker's profile. This
tool brings EXISTING data up to date WITHOUT a re-transform (which would re-run
sentence segmentation and risk drifting timing), by reading the office straight
from the raw day bundles and matching it onto the stored speeches by UUID.

Two modes (combine as needed):

  --db DB        Directly back-fill ``speech.speaker_office`` in a SQLite DB from
                 the raw ``role`` (matched by ``speech_uuid``), and wire any
                 downloaded non-MP portraits (``loader.wire_nonmp_photos``). Fast,
                 immediate — used on a directly-reachable DB (the dev DB). Imports
                 the backend (run from backend/ with its venv).

  --patch-json   Patch the processed ``*-session.json`` in place, setting
                 ``people[0].office`` from the raw ``role`` (matched by
                 ``debug.speechUUID``). Stdlib-only, so it runs on any host with
                 no backend venv. This is the PRODUCTION path: patch the processed
                 records on the host, then reload with the loader's atomic-swap
                 ``update`` (``podman-compose run --rm init update``), which adds
                 the column (``_ensure_speaker_office``) and wires non-MP photos.
                 Only the touched (office-holder) sittings reload; the photo wiring
                 is global, so downloaded portraits are wired regardless.

Examples:
  # dev: back-fill the dev DB (all cycles) + wire photos already on disk
  cd backend && .venv/bin/python migrate_speaker_office.py --db parlamonitor.db

  # prod (on the host, then reload): patch the current cycle's processed records
  python3 backend/migrate_speaker_office.py --patch-json --cycle 43 --data ./data
  # then, in the deployment:  podman-compose run --rm init update

Idempotent: re-running re-derives everything from the current raw data.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("migrate_speaker_office")


def build_office_map(data_dir: str) -> dict[str, str]:
    """Map speech UUID -> government office (tisztség) from the raw day bundles.

    A raw day lists the same physical speech once per agenda act, so a UUID can
    recur — keep the first non-empty office (they agree for one UUID)."""
    office_by_uuid: dict[str, str] = {}
    raw_files = sorted(glob.glob(
        os.path.join(data_dir, "original", "plenary", "raw-*-day.json")))
    for fn in raw_files:
        try:
            day = json.load(open(fn, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for sp in day.get("speeches", []):
            uuid = sp.get("speech_uuid")
            role = (sp.get("role") or "").strip()
            if uuid and role and uuid not in office_by_uuid:
                office_by_uuid[uuid] = role
    log.info("Read %d raw days -> %d speech UUIDs carry an office",
             len(raw_files), len(office_by_uuid))
    return office_by_uuid


def patch_processed_json(data_dir: str, office_by_uuid: dict[str, str],
                         cycle: int | None) -> None:
    """Set ``people[0].office`` on the processed session records, from the raw
    office matched by ``debug.speechUUID``. Only rewrites a file that changes."""
    pattern = f"{cycle}*-session.json" if cycle is not None else "*-session.json"
    files = sorted(glob.glob(os.path.join(data_dir, "processed", pattern)))
    patched_files = patched_speeches = 0
    for fn in files:
        try:
            rec = json.load(open(fn, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        changed = False
        for sp in rec.get("data", []):
            uuid = (sp.get("debug") or {}).get("speechUUID")
            office = office_by_uuid.get(uuid) if uuid else None
            if not office:
                continue
            people = sp.get("people") or []
            if people and people[0].get("office") != office:
                people[0]["office"] = office
                changed = True
                patched_speeches += 1
        if changed:
            with open(fn, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False)
            patched_files += 1
    log.info("Patched %d processed files (%d speeches gained an office)%s",
             patched_files, patched_speeches,
             f" [cycle {cycle}]" if cycle is not None else "")


def backfill_db(db_path: str, data_dir: str, office_by_uuid: dict[str, str]) -> None:
    """Directly back-fill ``speech.speaker_office`` (by UUID) and wire non-MP
    photos. Imports the backend loader (run with the backend venv)."""
    from app import loader  # noqa: E402  (deferred so --patch-json needs no venv)

    conn = loader.connect(db_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(speech)")}
    if "speaker_office" not in cols:
        conn.execute("ALTER TABLE speech ADD COLUMN speaker_office TEXT")
        log.info("Added speech.speaker_office column")
    # Reset first so a re-run reflects the current raw data exactly (idempotent).
    conn.execute("UPDATE speech SET speaker_office = NULL")
    conn.executemany(
        "UPDATE speech SET speaker_office = ? WHERE speech_uuid = ?",
        [(office, uuid) for uuid, office in office_by_uuid.items()])
    wired = loader.wire_nonmp_photos(
        conn, os.path.join(data_dir, "media", "photos"))
    conn.commit()

    n_rows = conn.execute(
        "SELECT COUNT(*) FROM speech WHERE speaker_office IS NOT NULL").fetchone()[0]
    n_nonmp = conn.execute(
        "SELECT COUNT(DISTINCT s.person_id) FROM speech s JOIN person p "
        "ON p.person_id = s.person_id "
        "WHERE s.speaker_office IS NOT NULL AND COALESCE(p.is_mp,0)=0").fetchone()[0]
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.commit()
    conn.close()
    log.info("Backfilled %d speech rows (%d non-MP office-holders); wired %d non-MP photos",
             n_rows, n_nonmp, wired)


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill the speaker-office (tisztség) data.")
    ap.add_argument("--data", default="../data", help="data directory (default ../data)")
    ap.add_argument("--db", help="directly back-fill this SQLite DB + wire non-MP photos (dev)")
    ap.add_argument("--patch-json", action="store_true",
                    help="patch processed *-session.json in place (production path)")
    ap.add_argument("--cycle", type=int, default=None,
                    help="restrict --patch-json to one cycle (e.g. 43)")
    args = ap.parse_args()
    if not args.db and not args.patch_json:
        ap.error("choose at least one of --db (dev) or --patch-json (prod)")

    t0 = time.time()
    office_by_uuid = build_office_map(args.data)
    if args.patch_json:
        patch_processed_json(args.data, office_by_uuid, args.cycle)
    if args.db:
        backfill_db(args.db, args.data, office_by_uuid)
    print(f"DONE in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
