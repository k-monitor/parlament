"""One-off in-place migration: build the interjection table (§6E).

The rows are derived entirely from text the proceedings module already wrote —
the parentheses inside `sentence.text` — and from the shared `person` register,
so this needs no scrape, no source files and no full rebuild: it reads the live
DB and writes `interjection` in place. Re-running it is safe (the pass replaces
the table wholesale) and it is the same code path the loader runs on every load,
so a later `--update` simply refreshes what this produced.

Run from backend/:  .venv/bin/python migrate_interjections.py [DB]
Defaults: parlamonitor.db. Takes ~2 minutes over the full 10-cycle corpus.
"""
import logging
import sys

from app import loader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

db_path = sys.argv[1] if len(sys.argv) > 1 else "parlamonitor.db"

conn = loader.connect(db_path)
total = loader.rebuild_interjections(conn)
for label, sql in (
        ("attributed", "SELECT COUNT(*) FROM interjection WHERE speaker_id IS NOT NULL"),
        ("in a chairing speech", "SELECT COUNT(*) FROM interjection WHERE procedural = 1"),
        ("graph pairs", "SELECT COUNT(*) FROM (SELECT 1 FROM interjection "
                        "WHERE speaker_id IS NOT NULL AND target_id IS NOT NULL "
                        "AND speaker_id <> target_id AND procedural = 0 "
                        "GROUP BY speaker_id, target_id)")):
    logging.info("%-22s %7d", label, conn.execute(sql).fetchone()[0])
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.commit()
conn.close()
logging.info("Done — %d interjections", total)
