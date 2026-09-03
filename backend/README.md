# Parlamonitor — Backend

A stateless **FastAPI** read-only API over a **SQLite + FTS5** database, plus the
**loader** that builds that database from the scraper's JSON output. Satisfies
the architecture in [`../requirements.md`](../requirements.md) §3.3, §4, §5, §6, §8.

```
app/
  schema.sql          normalized schema (core entities + per-module tables) — DB-1/DB-2
  loader.py           JSON session/representative records -> SQLite (ING-1..4, DB-3/DB-4, REP-7)
  db.py               read-only per-request connection (ING-2, NFR-2)
  search.py           Hungarian FTS5 query builder (SEA-1/SEA-2)
  readability.py      per-speech LIX/RIX + TTR/MATTR via `saphes` (§5.7, READ-1..7)
  publication.py      is a sitting day fully published? + the Hungarian long date —
                      shared by the API, the share cards and the announcer (SIT-2)
  haiku.py            accidental 5-7-5 sentences in the transcript (SOC-4)
  bluesky.py          minimal AT Protocol client, stdlib-only (§8.7 SOC-1)
  social.py           what to announce and when, remembered outside the DB (SOC-2..8)
  config.py           env-driven settings + enabled-module set (OPS-4, EXT-6)
  main.py             app factory, /api/v1, OpenAPI docs, /meta manifest (EXT-3/EXT-4, NFR-3)
  modules/
    registry.py       the extensibility seam — add a module here (EXT-1)
    proceedings/      search + viewer routes (§5)
    representatives/  list + profile + statistics routes (§6)
tests/                loader, search folding, timing map, API contracts (OPS-3)
```

## Build the database

The loader reads the scraper's output under `<data_dir>/processed/`
(`*-session.json` + `representatives-*.json`) and builds the runtime DB, swapping
it in atomically (DB-4):

```bash
python -m app.loader ../data parlamonitor.db          # full rebuild
python -m app.loader ../data parlamonitor.db --session 43003   # one sitting
python -m app.loader ../data parlamonitor.db --update           # only what changed
```

To re-run just the entity NER + link resolution over an already-built DB — the one
thing `--update` cannot do, since it only revisits sittings whose *source file*
changed and a model becoming available changes no file:

```bash
python -m app.loader --reextract-entities --period 43 ../data parlamonitor.db
```

The same escape hatch exists for the per-speech readability / lexical-diversity
measurement (§5.7), and for the same reason — a `saphes` upgrade, a changed
threshold or a lemmatizer becoming reachable changes no source file:

```bash
python -m app.loader --remeasure-speeches --period 43 ../data parlamonitor.db
```

The readability half (LIX/RIX) needs no model, so it lands on any install; the
diversity half (TTR/MATTR) needs lemmas and is **omitted rather than approximated**
from surface forms when none are reachable — see `app/readability.py` for why the
two metrics must never share a token stream. Both are cached per sitting in
`speech-metrics-cache.json` next to the DB.

### The shared lemma store

The diversity half and the word cloud both need HuSpaCy lemmas for the *same*
sentences — every non-procedural sentence of a sitting, of which the metric's
measurable subset is 98.7 % — so the corpus used to be lemmatized twice per build,
and billed twice on Modal. It now happens once: `nlp.analyze_all()` produces the
cloud's tallies and the lemma streams from a single pipeline pass, and the streams
are kept in `lemma-cache/` next to the DB (`app/lemma_cache.py`), one gzipped file
per sitting, keyed by sentence id so any later pass can read the sentences it
cares about in whatever order it wants. The metrics pass reads them back and does
not load a model at all; its log line says how many sittings came that way. It also
*fills* the store from its own work, so a repeated `--remeasure-speeches` (after a
`saphes` upgrade, say) does not buy the same lemmatization twice.

Sizeable — a few hundred MB gzipped over the full corpus, which is why it is
per-sitting files rather than one document like the other caches. Turn it off with
`PARLAMONITOR_LEMMA_CACHE=0` and each pass lemmatizes for itself again, exactly as
before; nothing else changes. `PARLAMONITOR_LEMMA_MEMO` (default 8) is how many
sittings the in-process memo holds, so an incremental `--update` never touches the
disk between the two passes.

On Modal the sharing needs the deployed service to carry `analyze_sessions_full`
(`modal deploy modal_app.py`). Against an older deployment the client falls back to
`analyze_sessions`, logs it once, and the metrics pass lemmatizes for itself — the
build is correct either way, just not yet cheaper.

Sittings whose cached spans still fingerprint-match are reused, so this is cheap
to re-run; omit `--period` to cover every sitting. The current cycle's default
model (`hu_core_news_trf`) loads only on Modal, so it needs
`PARLAMONITOR_WORDCLOUD_BACKEND=modal` + `MODAL_TOKEN_*`, or a locally installed
model via `PARLAMONITOR_HUSPACY_MODEL`. Without either, the current cycle's
sittings are skipped and their transcripts show no inline entity links — the
loader warns and names them.

Modal is scoped to the **newest cycle** by default (`PARLAMONITOR_MODAL_CYCLES`),
so backfilling the archive can't drain the credit the live cycle needs. To
re-extract an older cycle deliberately, widen it for that run:
`PARLAMONITOR_MODAL_CYCLES=42 python -m app.loader --reextract-entities --period 42
../data parlamonitor.db`.

## Run the API

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload                      # http://localhost:8000
```

* OpenAPI/Swagger docs: `/api/docs` — ReDoc: `/api/redoc` (NFR-3)
* Module manifest the SPA registers against: `/api/v1/meta` (EXT-4)
* MP portraits served from `PARLAMONITOR_PHOTOS_DIR` at `/media/photos/<file>`
* The API is read-only but for one anonymous counter: `POST /api/v1/search/click`
  reports which search result was opened, feeding the search-quality half of the
  aggregated analytics (PRIV-2, `app/analytics.py`). It stores no identifier and
  always answers `204`

### Configuration (all environment-driven — OPS-4)

| Variable | Default | Meaning |
| --- | --- | --- |
| `PARLAMONITOR_DB` | `./parlamonitor.db` | SQLite file path |
| `PARLAMONITOR_MODULES` | all | comma list of enabled modules (EXT-6) |
| `PARLAMONITOR_CORS_ORIGINS` | localhost:5173 | allowed SPA origins |
| `PARLAMONITOR_PHOTOS_DIR` | `../data/media/photos` | MP portrait directory |
| `PARLAMONITOR_FRONTEND_DIST` | — | if set, serves the built SPA from one process (OPS-1) |
| `PARLAMONITOR_MAX_SEARCH_TOTAL` | 5000 | cap on reported search totals |
| `PARLAMONITOR_HUSPACY_GPU` | `0` | `1` runs the local HuSpaCy model on a CUDA GPU (needs `torch` + `cupy-cuda12x` in the venv) — for a build or a `--reextract-entities`/`--remeasure-speeches` backfill on a GPU machine; logs and stays on the CPU when no GPU is usable |
| `PARLAMONITOR_MODAL_CYCLES` | `latest` | which electoral cycles may be processed on Modal (`latest`/`all`/`43,42`) — the metered-spend guard; out-of-scope sittings use a local model, else the regex tokenizer (and no entity mentions) |
| `PARLAMONITOR_SPEECH_METRICS` | `1` | measure + serve per-speech readability & lexical diversity (§5.7); `0` skips the pass and hides the annotations |
| `PARLAMONITOR_LIX_THRESHOLD` | `8` | LIX long-word threshold — a long word is *longer than* this. Defaults to `saphes`' calibration for Hungarian; Björnsson's Swedish `6` saturates here (42% of tokens "long" vs a ~25% norm) |
| `PARLAMONITOR_LIX_LENGTH_POLICY` | `nfc` | how a word's letters are counted: `nfc` / `graphemes` / `codepoints` / `hu-letters` (collapses the Hungarian digraphs — a sensitivity check, and it shifts the calibrated share) |
| `PARLAMONITOR_MATTR_WINDOW` | `100` | MATTR sliding window, in lemmas. A speech shorter than this reports no MATTR (plain TTR is not comparable across lengths) |
| `PARLAMONITOR_READABILITY_MIN_WORDS` | `50` | speeches shorter than this are not scored at all |
| `PARLAMONITOR_BLUESKY_AUTH` | — | `handle:app-password` for the announcement bot (§8.7) — the credential **and** the on switch. See [`../DEPLOYMENT.md`](../DEPLOYMENT.md#announcing-on-bluesky) for the rest of the knobs |
| `PARLAMONITOR_SITE_URL` | — | canonical public origin; required for posting (the posts link to it) and used by the OG share cards |

## Announcements (§8.7)

`python -m app.social` posts on Bluesky when a sitting day has become **fully
processed** and when a representative accidentally spoke a haiku. It reads the DB
read-only and remembers what it said in `bluesky-state.json` beside it, so it is
safe to run repeatedly and from anywhere:

```bash
python -m app.social --check-auth        # does the credential work? log in and stop
python -m app.social --dry-run          # decide + print; send nothing, remember nothing
python -m app.social                    # one real pass (needs PARLAMONITOR_BLUESKY_AUTH)
python find_haikus.py --period 43       # the corpus-wide haiku exploration CLI
```

A first run announces nothing — it adopts the current state of the world as
already-said, so a fresh checkout cannot post a backlog. The container runs it
after each sync pass; see [`../DEPLOYMENT.md`](../DEPLOYMENT.md#announcing-on-bluesky).

## Tests

```bash
pytest                 # 32 tests: loader, search, API contracts
```

## Adding a module (the EXT-1 acceptance probe)

A new domain (e.g. **Bills**) is purely additive:

1. add its tables to `schema.sql` (referencing shared `person`, not duplicating it — EXT-2);
2. add a loader step;
3. write `app/modules/bills/router.py` with an `APIRouter(prefix="/bills")`;
4. register it in `app/modules/registry.py` and list `"bills"` in `config.ALL_MODULES`;
5. flip `BILLS_MODULE_AVAILABLE` in the representatives module to unhide the
   "bills submitted" statistic (REP-3).

No existing route, table, or module changes.
