"""One-off in-place migration: re-apply the procedural speech-type rule (STAT-1)
to an already-loaded DB and rebuild the statistics that depend on it.

``speech.procedural`` is decided once, at insert time, from
``settings.is_procedural_type(felszolalas_tipus)`` — so changing
``DEFAULT_PROCEDURAL_SPEECH_TYPES`` (or ``PARLAMONITOR_PROCEDURAL_SPEECH_TYPES``)
does NOT retroactively change existing rows, and ``loader --update`` won't notice
either: it keys off the processed files' (mtime, size), and a config change
touches no file.

This re-derives the flag for every speech from the CURRENT config and rebuilds
person/faction/session aggregates from it. Pure SQL — no scraping, no
lemmatization, no NER/NEL, so ``wordcloud-cache.json`` / ``entity-cache.json``
and the ``entity``/``session_word_count`` rows are all left untouched.

Caveat: the word cloud and new-words tables are built from *non-procedural* text
only, so sittings containing newly-(de)proceduralised speeches keep word counts
that no longer match the new rule. Pass --wordcloud to re-run term extraction for
just those sittings (that step DOES need HuSpaCy/Modal), or leave it and let the
next full build pick it up.

Idempotent — a second run reports 0 changes.

Run from backend/:  .venv/bin/python migrate_procedural_types.py [DB] [--wordcloud]
Default DB: $PARLAMONITOR_DB, else backend/parlamonitor.db.

In the container deployment the served DB is inside the `dbdata` volume, NOT in
the host checkout — migrate it from inside the image:

    podman-compose run --rm init migrate-procedural
"""
import logging
import sys
import time
from pathlib import Path

from app import loader
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

argv = [a for a in sys.argv[1:] if a != "--wordcloud"]
do_wordcloud = "--wordcloud" in sys.argv[1:]
db_path = Path(argv[0] if argv else settings.db_path)

# Bail out loudly rather than silently "succeeding" against the wrong file:
# loader.connect() is the WRITER connection, so a typo'd or host-checkout path
# would CREATE an empty DB, re-flag 0 speeches and exit 0 — the failure mode that
# makes a migration look applied while the site keeps serving the old numbers.
if not db_path.exists():
    sys.exit(f"No DB at {db_path} — pass the served DB's path explicitly. "
             f"In the container deployment it is /db/parlamonitor.db "
             f"(`podman-compose run --rm init migrate-procedural`).")

print(f"DB: {db_path.resolve()}")
print("procedural types in effect: "
      + ", ".join(sorted(settings.procedural_speech_types)))

t0 = time.time()
# Serialize against the loader (`init`/`sync`): an --update snapshots the live DB
# into <db>.building and swaps it in, so an unlocked migration running alongside
# one is silently discarded by that swap.
with loader._writer_lock(db_path):
    conn = loader.connect(db_path)

    # Re-derive the flag per DISTINCT type (a few hundred at most), then update in
    # bulk — far cheaper than walking 140k speech rows in Python.
    types = [r[0] for r in conn.execute(
        "SELECT DISTINCT felszolalas_tipus FROM speech")]
    wanted = {t: (1 if settings.is_procedural_type(t) else 0) for t in types}

    flipped: list[tuple[str | None, int, int]] = []  # (type, new flag, n rows changed)
    affected: set[str] = set()                       # sittings whose text set moved
    for t, flag in wanted.items():
        where, params = ("felszolalas_tipus IS NULL", []) if t is None else \
                        ("felszolalas_tipus = ?", [t])
        sids = [r[0] for r in conn.execute(
            f"SELECT DISTINCT session_id FROM speech "
            f"WHERE {where} AND procedural <> ?", params + [flag])]
        if not sids:
            continue
        cur = conn.execute(
            f"UPDATE speech SET procedural = ? WHERE {where} AND procedural <> ?",
            [flag] + params + [flag])
        flipped.append((t, flag, cur.rowcount))
        affected.update(sids)
    conn.commit()

    for t, flag, n in sorted(flipped, key=lambda f: -f[2]):
        print(f"  {'excluded' if flag else 'restored'}: {n:>7} speeches — {t or '(no type)'}")
    changed = sum(n for _, _, n in flipped)
    print(f"re-flagged {changed} speeches across {len(flipped)} type(s)")

    if do_wordcloud and affected:
        print(f"re-extracting terms + entities for {len(affected)} affected sitting(s)")
        loader.rebuild_session_word_counts(conn, db_path.parent, only_sessions=affected)
        loader.rebuild_entity_mentions(conn, db_path.parent, only_sessions=affected)
        loader.resolve_entity_links(conn, db_path.parent)
    elif affected:
        print(f"{len(affected)} sitting(s) have stale word clouds "
              f"(re-run with --wordcloud to refresh them)")

    loader.rebuild_aggregates(conn)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.commit()

    # Post-state, so a run that reports "0 changes" is still verifiable: this is
    # what the site will now serve.
    total, excluded = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(procedural), 0) FROM speech").fetchone()
    print(f"now excluded from statistics: {excluded} / {total} speeches")
    conn.close()

print(f"DONE in {time.time() - t0:.1f}s")
