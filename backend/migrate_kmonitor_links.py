"""One-off in-place migration: institutions + K-Monitor linking (NEL, §10 extension).

Brings an existing DB to the current NEL schema (adds `entity.kind`,
`person.kmonitor_url`, and the multi-destination `entity_link`), re-extracts PERSON
**and ORG** mentions from every sitting's transcript (HuSpaCy NER — cached; the
ent-v2 logic bump forces a re-run so institutions are captured), then resolves every
name to K-Monitor (primary) / Wikipedia (fallback) and sets MP K-Monitor links.

Run from backend/:  .venv/bin/python migrate_kmonitor_links.py [DB]
Default DB: parlamonitor.db. Needs the HuSpaCy model (extraction), network to
adatbazis.k-monitor.hu (K-Monitor tags) and query.wikidata.org (candidates); each
degrades gracefully if unavailable.
"""
import json
import logging
import os
import sys
import time

from app import kmonitor, loader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

db_path = sys.argv[1] if len(sys.argv) > 1 else "parlamonitor.db"
cache_dir = os.path.dirname(os.path.abspath(db_path))

t0 = time.time()
conn = loader.connect(db_path)

# Bring the NEL tables to the current schema (entity.kind, multi-destination
# entity_link, person.kmonitor_url) — the same in-place migration the loader applies.
loader._ensure_entity_tables(conn)
conn.commit()
print("entity / entity_link / person tables ready")

# Re-extract PER + ORG mentions (ent-v2 → full re-NER; the old cache is a miss).
loader.rebuild_entity_mentions(conn, cache_dir)
n_ment = conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
n_org = conn.execute("SELECT COUNT(*) FROM entity WHERE kind='ORG'").fetchone()[0]
n_names = conn.execute("SELECT COUNT(DISTINCT entity_key) FROM entity").fetchone()[0]
print(f"extracted {n_ment} mentions ({n_org} ORG) across {n_names} distinct names")

# Fetch the K-Monitor tag index once, set MP K-Monitor links, resolve entity links.
loader.resolve_entity_links(conn, cache_dir)

n_link = conn.execute("SELECT COUNT(*) FROM entity_link").fetchone()[0]
n_km = conn.execute("SELECT COUNT(*) FROM entity_link WHERE links_json LIKE '%\"kmonitor\"%'").fetchone()[0]
n_wp = conn.execute("SELECT COUNT(*) FROM entity_link WHERE links_json LIKE '%\"wikipedia\"%'").fetchone()[0]
n_prof = conn.execute("SELECT COUNT(*) FROM entity_link WHERE links_json LIKE '%\"profile\"%'").fetchone()[0]
n_amb = conn.execute("SELECT COUNT(*) FROM entity_link WHERE ambiguous=1").fetchone()[0]
n_mp_km = conn.execute("SELECT COUNT(*) FROM person WHERE kmonitor_url IS NOT NULL").fetchone()[0]

conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.commit()
conn.close()
print(f"DONE in {time.time() - t0:.1f}s — {n_link} names linked "
      f"({n_km} K-Monitor, {n_wp} Wikipedia fallback, {n_prof} internal profile, "
      f"{n_amb} ambiguous); {n_mp_km} MPs have a K-Monitor profile link")
