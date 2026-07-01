"""One-off in-place migration: add the word_first_seen table (NEW-1) and populate
it from the existing session_word_count + session dates.

Run from backend/:  .venv/bin/python migrate_new_words.py [DB]
Default DB: parlamonitor.db. Fast — pure SQL, no lemmatization (session_word_count
must already be populated; run migrate_wordcloud.py first if it isn't).
"""
import logging
import sys
import time

from app import loader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

db_path = sys.argv[1] if len(sys.argv) > 1 else "parlamonitor.db"

conn = loader.connect(db_path)
conn.executescript("""
CREATE TABLE IF NOT EXISTS word_first_seen (
    word       TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    date       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'term'
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_word_first_seen_session ON word_first_seen(session_id);
""")
conn.commit()

t0 = time.time()
loader.rebuild_word_first_seen(conn)
n = conn.execute("SELECT COUNT(*) FROM word_first_seen").fetchone()[0]
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.commit()
conn.close()
print(f"DONE in {time.time() - t0:.1f}s — {n} distinct words")
