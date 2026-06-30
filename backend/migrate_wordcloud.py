"""One-off in-place migration: add session_word_count and (re)build the word-cloud
term frequencies + corpus doc-frequencies with the HuSpaCy lemmatizer/NER backend.

Run from backend/:  .venv/bin/python migrate_wordcloud.py [DB] [DATA_DIR]
Defaults: parlamonitor.db, ../data. Slow (~1 min/cycle-43, ~45 min/cycle-42) the
first time; the on-disk wordcloud-cache.json makes subsequent runs near-instant.
"""
import logging
import sqlite3
import sys
import time

from app import loader

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

db_path = sys.argv[1] if len(sys.argv) > 1 else "parlamonitor.db"
data_dir = sys.argv[2] if len(sys.argv) > 2 else "../data"

conn = loader.connect(db_path)
conn.executescript("""
CREATE TABLE IF NOT EXISTS session_word_count (
    session_id TEXT NOT NULL REFERENCES session(id),
    word       TEXT NOT NULL,
    count      INTEGER NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'term',
    PRIMARY KEY (session_id, word)
) WITHOUT ROWID;
""")
conn.commit()

# Clear the stale (regex-era) corpus tables up front so that while the long pass
# runs, already-processed sittings score against a consistent (empty → raw-freq)
# corpus rather than surface-form doc-frequencies that don't match the new lemmas.
# rebuild_word_doc_freq repopulates them from the lemmas at the end.
conn.execute("DELETE FROM word_doc_freq")
conn.execute("DELETE FROM word_doc_total")
conn.commit()

t0 = time.time()
loader.rebuild_session_word_counts(conn, data_dir)
loader.rebuild_word_doc_freq(conn)
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.commit()
conn.close()
print(f"DONE in {time.time() - t0:.0f}s")
