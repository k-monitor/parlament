"""One-off in-place migration: person-entity linking (NEL, §10, Part B).

Recreates the `entity` table with the mention-span shape (it was reserved and
never populated), adds `entity_link`, extracts PERSON mentions from every sitting's
transcript (HuSpaCy NER — cached), and resolves each distinct name to a
Wikidata item / Wikipedia article.

Run from backend/:  .venv/bin/python migrate_entity_links.py [DB]
Default DB: parlamonitor.db. Needs the HuSpaCy model (extraction) and network to
query.wikidata.org (resolution); each degrades gracefully if unavailable.
"""
import logging
import os
import sys
import time

from app import loader, wikidata

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

db_path = sys.argv[1] if len(sys.argv) > 1 else "parlamonitor.db"
cache_dir = os.path.dirname(os.path.abspath(db_path))

t0 = time.time()
conn = loader.connect(db_path)

# Bring the NEL tables to the current schema (replaces the old reserved `entity`
# shape, adds `entity_link`) — the same in-place migration the loader applies.
loader._ensure_entity_tables(conn)
conn.commit()
print("entity / entity_link tables ready")

loader.rebuild_entity_mentions(conn, cache_dir)
n_ment = conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
n_names = conn.execute("SELECT COUNT(DISTINCT entity_key) FROM entity").fetchone()[0]
print(f"extracted {n_ment} mentions across {n_names} distinct names")

wikidata.resolve_entities(conn, cache_dir)
n_link = conn.execute("SELECT COUNT(*) FROM entity_link WHERE wikidata_id IS NOT NULL").fetchone()[0]
n_amb = conn.execute("SELECT COUNT(*) FROM entity_link WHERE ambiguous=1").fetchone()[0]
n_mp = conn.execute("SELECT COUNT(*) FROM entity_link WHERE person_id IS NOT NULL").fetchone()[0]

conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.commit()
conn.close()
print(f"DONE in {time.time() - t0:.1f}s — {n_link} names linked "
      f"({n_amb} ambiguous, {n_mp} to internal MP profiles)")
