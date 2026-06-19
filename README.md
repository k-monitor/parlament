# Parlamonitor

A third-party, civic-tech website making the **Hungarian National Assembly's**
public proceedings searchable and watchable down to the individual sentence, with
transparent statistics about representatives. See [requirements.md](requirements.md).

```
scraper/     parlamonitor — the scraping pipeline (parlament.hu / Felicitas JSON API)  [pre-existing]
backend/     FastAPI read-only API over SQLite+FTS5, plus the JSON→DB loader        §3.3, §4, §5, §6, §8
frontend/    Vue 3 + Vite SPA (search, viewer, representatives, statistics)         §5, §6, §8
data/        scraper output (processed/*.json) + the built runtime DB consumes it
```

The three tiers are decoupled exactly as the requirements demand: the **scraper**
owns fetch/parse/segment/timing; the **loader** is the only DB writer (ING-2); the
**backend** reads the DB read-only and serves a versioned, modular, documented API
(EXT-3, NFR-3); the **frontend** is a static bundle that talks to that API (NFR-1).

## Run it end to end

```bash
# 1. (already done) scrape -> data/processed/*.json
#    python -m parlamonitor proceedings --cycle 43 ./data
#    python -m parlamonitor representatives --cycle 43 --photos ./data

# 2. build the runtime database (JSON -> SQLite + FTS5 + aggregates)
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m app.loader ../data parlamonitor.db

# 3. build the frontend
cd ../frontend && npm install && npm run build

# 4. serve API + SPA from one process
cd ../backend
PARLAMONITOR_FRONTEND_DIST=$(pwd)/../frontend/dist uvicorn app.main:app
#   → app at http://localhost:8000  · API docs at /api/docs
```

For development, run the backend (`uvicorn app.main:app`) and the SPA dev server
(`cd frontend && npm run dev`, http://localhost:5173) separately; Vite proxies
`/api` and `/media` to the backend.

## Tests

```bash
cd backend && pytest        # 32 tests: loader, Hungarian search folding,
                            # sentence↔time mapping, API contracts (OPS-3)
```

## How the pieces map to the requirements

| Area | Where | Key requirements |
| --- | --- | --- |
| Normalized store, FTS5 (Hungarian) | `backend/app/schema.sql` | DB-1, DB-2 |
| JSON→DB loader, atomic, idempotent, aggregates | `backend/app/loader.py` | ING-1..4, DB-3/4, REP-7 |
| Search (ranked, accent/case-fold, phrase, filters) | `backend/app/modules/proceedings` + `search.py` | SEA-1..7 |
| Viewer (HLS, click-to-seek, karaoke, deep links) | `frontend/.../ViewerView.vue` | VIE-1..8 |
| Representatives + statistics + factions | `backend/.../representatives`, `frontend/.../representatives` | REP-1..7 |
| Module architecture (additive new domains) | `backend/app/modules/registry.py`, `frontend/src/router.js` | EXT-1..6 |
| Provenance / estimated-timing disclosure | `TimingBadge.vue`, `confidence`/`align_method` everywhere | TIM-3, VIE-6, TRUST-1 |
| Source attribution & license | `/api/v1/meta`, footer, viewer | LEGAL-1 |
| i18n (hu default, en added), a11y, responsive | `frontend/src/locales`, `styles.css` | I18N-1, A11Y-1, RESP-1 |

## Extending with a new module (the EXT-1 acceptance probe)

Adding **Bills/irományok** is additive only: new scraper step + `bill`/`bill_sponsor`
tables (referencing the shared `person`, EXT-2) + `app/modules/bills/router.py`
registered in `registry.py` + a lazy `frontend/src/modules/bills/` view + flipping
`BILLS_MODULE_AVAILABLE` to unhide the "bills submitted" stat. No existing module
changes. See `backend/README.md`.
