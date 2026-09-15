"""One-off in-place migration: build the upcoming-sitting agenda tables (NR-3).

Creates the four `agenda_doc*` / `agenda_meta` tables on an existing DB and
loads `processed/aktualis.json` into them, so a deployment gains the section
without a full rebuild. Re-running it is safe (the load replaces the whole set),
and it is the same code path the loader runs on every load, so a later
`--update` simply refreshes what this produced.

Run from backend/:  .venv/bin/python migrate_upcoming_agenda.py [DATA_DIR] [DB]
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from app import loader

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

data_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "../data")
db_path = sys.argv[2] if len(sys.argv) > 2 else "parlamonitor.db"

conn = loader.connect(db_path)
loader._ensure_agenda_doc_tables(conn)
items = loader._load_aktualis_file(conn, data_dir)
# The items link to their irományok by the printed number; re-resolve in case the
# registry moved since the napirend was parsed.
loader._relink_agenda_bills(conn)
conn.commit()

for label, sql in (
        ("documents", "SELECT COUNT(*) FROM agenda_doc"),
        ("sitting days", "SELECT COUNT(*) FROM agenda_doc_day"),
        ("agenda items", "SELECT COUNT(*) FROM agenda_doc_item"),
        ("… linked to a bill",
         "SELECT COUNT(*) FROM agenda_doc_item WHERE bill_id IS NOT NULL")):
    logging.info("%-22s %7d", label, conn.execute(sql).fetchone()[0])

conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.commit()
conn.close()
logging.info("Done (%d items loaded)", items)
