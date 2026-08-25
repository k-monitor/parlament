"""One-off in-place migration: build `period_sentence_range` (search accelerator).

The table records the `sentence.id` span each electoral cycle occupies, letting a
cycle-scoped search bound itself as a rowid range on the FTS index instead of
fetching a `sentence` + `speech` row per match just to read `period_number` off
it. See the table's comment in app/schema.sql for why it can never change a
result.

Derived entirely from rows already in the DB, so this needs no scrape, no source
files and no full rebuild — and it is the same function the loader runs on every
load, so a later `--update` simply refreshes what this produced.

Run from backend/:  .venv/bin/python migrate_sentence_ranges.py [DB]
Defaults: parlamonitor.db. Takes a few seconds (one scan of `sentence`).
"""
import logging
import sqlite3
import sys

from app import loader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

db_path = sys.argv[1] if len(sys.argv) > 1 else "parlamonitor.db"
conn = loader.connect(db_path)

# The table ships in schema.sql, so a DB built since then already has it; an older
# one needs it created before it can be filled.
conn.execute("""
    CREATE TABLE IF NOT EXISTS period_sentence_range (
        period_number INTEGER PRIMARY KEY REFERENCES electoral_period(number),
        first_id      INTEGER NOT NULL,
        last_id       INTEGER NOT NULL
    )""")
conn.commit()

loader.rebuild_period_sentence_ranges(conn)

for row in conn.execute("SELECT period_number, first_id, last_id "
                        "FROM period_sentence_range ORDER BY period_number"):
    logging.info("cycle %-3s  sentences %12d … %-12d (%d)",
                 row[0], row[1], row[2], row[2] - row[1] + 1)

conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.commit()
conn.close()
logging.info("Done")
