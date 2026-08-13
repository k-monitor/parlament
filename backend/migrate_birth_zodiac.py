"""One-off in-place migration: add person.date_of_birth / zodiac_sign /
chinese_zodiac_sign and populate them from Wikidata (P569) for every
representative and advocate.

Adds the three columns, then re-runs the canonical P4966 join (the same one the
scraper does) to pick up each person's day-precision birth date and the sun sign
and Chinese zodiac animal derived from it. The values are written back into the
``representatives-<cycle>.json`` / ``advocates-<cycle>.json`` source files — so a
later rebuild or ``--update`` keeps them, exactly as the scraper now produces
them — and loaded into the person table.

The columns are stored only; no endpoint exposes them yet.

Run from backend/:  .venv/bin/python migrate_birth_zodiac.py [DB] [DATA_DIR]
Defaults: parlamonitor.db, ../data. Needs network (query.wikidata.org).
"""
import glob
import json
import logging
import os
import sys
import time

# Reuse the canonical scraper resolver (P4966 -> QID/Wikipedia/birth date) rather
# than duplicating the SPARQL query here.
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
loader._ensure_person_birth_columns(conn)
conn.commit()

with HttpClient(RuntimeConfig.from_env()) as http:
    links = wikidata.fetch_mp_links(http)
born = sum(1 for v in links.values() if v.get("dateOfBirth"))
print(f"fetched {len(links)} P4966 links from Wikidata, {born} with a birth date")

# Both registries are keyed on the same parlament.hu person id, so one map serves
# them both (the advocate loader COALESCE-merges, so order does not matter).
patterns = ("representatives-*.json", "advocates-*.json")
paths = sorted(p for pat in patterns
               for p in glob.glob(os.path.join(data_dir, "processed", pat)))
for path in paths:
    registry = json.load(open(path, encoding="utf-8"))
    hit = 0
    for rec in registry.get("data", []):
        link = links.get(rec.get("personID")) or {}
        if link.get("dateOfBirth"):
            rec["dateOfBirth"] = link["dateOfBirth"]
            rec["zodiacSign"] = link.get("zodiacSign")
            rec["chineseZodiacSign"] = link.get("chineseZodiacSign")
            hit += 1
    registry.setdefault("meta", {})["birthDatesLinked"] = hit
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    if os.path.basename(path).startswith("advocates-"):
        loader.load_advocates(conn, registry)
    else:
        loader.load_representatives(conn, registry)
    print(f"  {os.path.basename(path)}: {hit}/{len(registry.get('data', []))} dated")

# Roster people whose registry file is no longer on disk (cycle 39's, say) have a
# person row but nothing to re-load, so the registry pass above can never reach
# them. The join is on the person id either way, so patch those rows straight from
# the same map — the alternative is leaving a former MP dateless until their cycle
# is re-scraped. Only fills what is empty, and only roster people (an id-space
# match for a speaker stub is left alone, as the link join already is).
direct = conn.execute(
    "SELECT person_id FROM person "
    " WHERE (is_mp = 1 OR is_advocate = 1) "
    "   AND (date_of_birth IS NULL OR zodiac_sign IS NULL "
    "        OR chinese_zodiac_sign IS NULL)").fetchall()
patched = 0
for row in direct:
    link = links.get(row["person_id"]) or {}
    if link.get("dateOfBirth"):
        conn.execute("UPDATE person SET date_of_birth = ?, zodiac_sign = ?, "
                     "chinese_zodiac_sign = ? WHERE person_id = ?",
                     (link["dateOfBirth"], link.get("zodiacSign"),
                      link.get("chineseZodiacSign"), row["person_id"]))
        patched += 1
conn.commit()
print(f"  person rows patched directly (no registry file on disk): {patched}")

n_dob = conn.execute(
    "SELECT COUNT(*) FROM person WHERE date_of_birth IS NOT NULL").fetchone()[0]
counts = {}
for col in ("zodiac_sign", "chinese_zodiac_sign"):
    counts[col] = conn.execute(
        f"SELECT {col} AS sign, COUNT(*) c FROM person WHERE {col} IS NOT NULL "
        f"GROUP BY {col} ORDER BY c DESC").fetchall()
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.commit()
conn.close()
print(f"DONE in {time.time() - t0:.1f}s — {n_dob} person rows with a birth date")
for col, rows in counts.items():
    print(f"  {col}: " + ", ".join(f"{r['sign']}={r['c']}" for r in rows))
