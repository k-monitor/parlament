"""One-off in-place migration: add person.wikipedia_url and populate the
representatives' Wikidata/Wikipedia links (Part A of the entity-linking feature).

Adds the ``wikipedia_url`` column, then joins every MP to its Wikidata item and
Wikipedia article via property P4966 (== our personID). The enriched links are
written back into the ``representatives-<cycle>.json`` source files (so a future
rebuild/--update keeps them, exactly as the scraper now produces them) and loaded
into the person table.

Run from backend/:  .venv/bin/python migrate_wikidata_links.py [DB] [DATA_DIR]
Defaults: parlamonitor.db, ../data. Needs network (query.wikidata.org).
"""
import glob
import json
import logging
import os
import sys
import time

# Reuse the canonical scraper resolver (P4966 -> QID/Wikipedia) rather than
# duplicating the SPARQL query here.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scraper"))
from parlamonitor import wikidata  # noqa: E402
from parlamonitor.config import RuntimeConfig  # noqa: E402
from parlamonitor.http_client import HttpClient  # noqa: E402

from app import loader  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

db_path = sys.argv[1] if len(sys.argv) > 1 else "parlamonitor.db"
data_dir = sys.argv[2] if len(sys.argv) > 2 else "../data"

t0 = time.time()
conn = loader.connect(db_path)

cols = [r[1] for r in conn.execute("PRAGMA table_info(person)")]
if "wikipedia_url" not in cols:
    conn.execute("ALTER TABLE person ADD COLUMN wikipedia_url TEXT")
    conn.commit()
    print("added person.wikipedia_url")

with HttpClient(RuntimeConfig.from_env()) as http:
    links = wikidata.fetch_mp_links(http)
print(f"fetched {len(links)} P4966 links from Wikidata")

matched = 0
for path in sorted(glob.glob(os.path.join(data_dir, "processed", "representatives-*.json"))):
    registry = json.load(open(path, encoding="utf-8"))
    hit = 0
    for rec in registry.get("data", []):
        link = links.get(rec.get("personID"))
        if link:
            rec["wikidataId"] = link["wikidataId"]
            if link.get("wikipediaUrl"):
                rec["wikipediaUrl"] = link["wikipediaUrl"]
            hit += 1
    registry.setdefault("meta", {})["wikidataLinked"] = hit
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    loader.load_representatives(conn, registry)
    matched += hit
    print(f"  {os.path.basename(path)}: {hit}/{len(registry.get('data', []))} linked")

n_wd = conn.execute("SELECT COUNT(*) FROM person WHERE wikidata_id IS NOT NULL").fetchone()[0]
n_wp = conn.execute("SELECT COUNT(*) FROM person WHERE wikipedia_url IS NOT NULL").fetchone()[0]
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.commit()
conn.close()
print(f"DONE in {time.time() - t0:.1f}s — person rows: {n_wd} with Wikidata, {n_wp} with Wikipedia")
