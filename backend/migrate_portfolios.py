"""One-off in-place migration: build the portfolio (tárca) tables (§6C).

The tables are derived entirely from rows the other modules already wrote —
`bill_event`, `bill_sponsor`, `speech.speaker_office` and `person_office` — so
this needs no scrape, no source files and no full rebuild: it reads the live DB
and writes the five `portfolio*` tables in place. Re-running it is safe (the
rebuild replaces them wholesale) and it is the same code path the loader runs on
every load, so a later `--update` simply refreshes what this produced.

Run from backend/:  .venv/bin/python migrate_portfolios.py [DB]
Defaults: parlamonitor.db. Takes a few seconds.
"""
import logging
import sys

from app import loader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

db_path = sys.argv[1] if len(sys.argv) > 1 else "parlamonitor.db"

conn = loader.connect(db_path)
count = loader.rebuild_portfolios(conn)
for table in ("portfolio", "portfolio_alias", "portfolio_bill",
              "portfolio_speech", "portfolio_office"):
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    logging.info("%-18s %6d rows", table, n)
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.commit()
conn.close()
logging.info("Done — %d portfolios", count)
