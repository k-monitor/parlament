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

Sittings whose cached spans still fingerprint-match are reused, so this is cheap
to re-run; omit `--period` to cover every sitting. The current cycle's default
model (`hu_core_news_trf`) loads only on Modal, so it needs
`PARLAMONITOR_WORDCLOUD_BACKEND=modal` + `MODAL_TOKEN_*`, or a locally installed
model via `PARLAMONITOR_HUSPACY_MODEL`. Without either, the current cycle's
sittings are skipped and their transcripts show no inline entity links — the
loader warns and names them.

## Run the API

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload                      # http://localhost:8000
```

* OpenAPI/Swagger docs: `/api/docs` — ReDoc: `/api/redoc` (NFR-3)
* Module manifest the SPA registers against: `/api/v1/meta` (EXT-4)
* MP portraits served from `PARLAMONITOR_PHOTOS_DIR` at `/media/photos/<file>`

### Configuration (all environment-driven — OPS-4)

| Variable | Default | Meaning |
| --- | --- | --- |
| `PARLAMONITOR_DB` | `./parlamonitor.db` | SQLite file path |
| `PARLAMONITOR_MODULES` | all | comma list of enabled modules (EXT-6) |
| `PARLAMONITOR_CORS_ORIGINS` | localhost:5173 | allowed SPA origins |
| `PARLAMONITOR_PHOTOS_DIR` | `../data/media/photos` | MP portrait directory |
| `PARLAMONITOR_FRONTEND_DIST` | — | if set, serves the built SPA from one process (OPS-1) |
| `PARLAMONITOR_MAX_SEARCH_TOTAL` | 5000 | cap on reported search totals |

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
