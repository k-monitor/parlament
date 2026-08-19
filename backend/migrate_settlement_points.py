"""One-off in-place migration: move every settlement's dot onto its label point (§6D TEL-5).

The settlement map was drawn from the election office's `centrum`, which centres a
settlement's *territory* rather than the place — a mean 3 km from where the basemap
prints the name, and up to 11 km, so the dots visibly missed their labels. The loader
now overlays `app/settlement_points.csv` (OpenStreetMap's place nodes, matched to the
register by point-in-polygon; see `build_settlement_points.py`), and this applies the
same overlay to a database that already exists — no scrape, no network, no rebuild.

The H3 cells are rebuilt after it, because a cell is a function of the point alone
(TEL-15) and a settlement that moved 3 km can move hexagon.

Run from backend/:  .venv/bin/python migrate_settlement_points.py [DB]
Defaults: parlamonitor.db. Seconds, not minutes.
"""
import logging
import math
import sys

from app import loader, settlements

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

db_path = sys.argv[1] if len(sys.argv) > 1 else "parlamonitor.db"
conn = loader.connect(db_path)

moves, missing = [], []
for sid, name, lat, lon in conn.execute(
        "SELECT id, name, lat, lon FROM settlement").fetchall():
    point = settlements.label_point(sid, name)
    if point is None:
        missing.append(name)
        continue
    if lat is not None and lon is not None:
        moves.append((math.hypot((point[0] - lat) * 111.32,
                                 (point[1] - lon) * 111.32 * math.cos(math.radians(lat))),
                      name))
    conn.execute("UPDATE settlement SET lat = ?, lon = ? WHERE id = ?",
                 (point[0], point[1], sid))
conn.commit()

cells = loader.rebuild_settlement_cells(conn)
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.commit()
conn.close()

moves.sort(reverse=True)
logging.info("Moved %d settlements (%d without a label point: %s)",
             len(moves), len(missing), ", ".join(missing[:5]) or "none")
if moves:
    logging.info("Displacement: median %.2f km, max %.2f km (%s)",
                 moves[len(moves) // 2][0], moves[0][0], moves[0][1])
logging.info("Rebuilt %d H3 rows", cells)
