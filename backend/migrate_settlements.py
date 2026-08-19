"""One-off in-place migration: build the settlement-mention tables (§6D).

Everything this module needs is already in the live DB — the transcript sentences,
the speeches that carry them, the person register and the NER layer — plus the
settlement register, which comes from the election office's own data over HTTP (the
same cached source the constituency lookup uses, TEL-5). So this needs no scrape, no
source files and no full rebuild: it reads the live DB, fetches (and caches) the
register once, scans the corpus and writes the `settlement*` tables in place.

It is the same code path the loader runs on every load, so a later `--update` simply
refreshes what this produced, and re-running it is safe (each pass replaces its own
rows wholesale).

It is also the repair path for a DB whose ambiguity policy was derived from
`word_doc_freq` (TEL-3 gate 2 before the case measurement replaced it): on an install
whose NLP never covered the whole corpus, that table reports every settlement in the
country as an everyday word, so most of them were demoted to needing a place cue and
their mentions were dropped. Re-running this re-measures the evidence and re-scans.

Run from backend/:  .venv/bin/python migrate_settlements.py [DB]
Defaults: parlamonitor.db. A few minutes on the full corpus: two passes of pure Python
over ~2.5 million non-procedural sentences — gate 2's case evidence, then the mentions.
"""
import logging
import sys

from app import loader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

db_path = sys.argv[1] if len(sys.argv) > 1 else "parlamonitor.db"

conn = loader.connect(db_path)
places = loader.rebuild_settlements(conn)
mentions = loader.rebuild_settlement_mentions(conn)
loader.rebuild_settlement_stats(conn)
for table in ("settlement", "settlement_constituency", "constituency",
              "settlement_h3", "settlement_mention", "settlement_stats",
              "settlement_speaker_stats", "person_settlement_stats"):
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    logging.info("%-26s %7d rows", table, n)
named = conn.execute(
    "SELECT COUNT(DISTINCT settlement_id) FROM settlement_mention").fetchone()[0]
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.commit()
conn.close()
logging.info("Done — %d settlements in the register, %d ever named, %d never "
             "(%d mentions)", places, named, places - named, mentions)
