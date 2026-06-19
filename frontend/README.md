# Parlamonitor — Frontend

A **Vue 3 + Vite** single-page app (NFR-1) talking to the `/api/v1` backend.
Hungarian-first, accessible (WCAG 2.1 AA target), responsive, with lazily-loaded
feature modules.

```
src/
  main.js / App.vue        app shell, header nav built from the live module manifest
  router.js                routes; feature-module views are lazy chunks (EXT-4), gated on EXT-6
  store.js                 /api/v1/meta manifest (which modules are enabled)
  api.js                   typed-ish API client (one owner of the base URL)
  i18n.js + locales/       externalized copy, hu (default) + en (I18N-1)
  components/              StateBlock, FactionBadge, TimingBadge, SpeakerLink, BarChart (a11y chart)
  modules/
    proceedings/           SearchView, ViewerView (hls.js), SessionsView, SessionView (§5)
    representatives/        RepListView, RepProfileView, FactionsView (§6)
  views/                   HomeView, AboutView, NotFoundView
```

## Develop

```bash
npm install
npm run dev          # http://localhost:5173  (proxies /api and /media to :8000)
```

Run the backend (`uvicorn app.main:app`) alongside it.

## Build

```bash
npm run build        # -> dist/  (static bundle)
```

Serve `dist/` from any static host, or let the backend serve it from one process:

```bash
PARLAMONITOR_FRONTEND_DIST=../frontend/dist uvicorn app.main:app   # SPA + API together (OPS-1)
```

The backend applies HTML5-history fallback, so deep links like
`/proceedings/43001-1?s=4` resolve to the app shell (VIE-5).

## Highlights against requirements

* **Search (§5.1):** URL-driven, deep-linkable/citable search state (SEA-6),
  combinable filters (SEA-3), highlighted hits (SEA-4), pagination (SEA-5).
* **Viewer (§5.2):** HLS via `hls.js` (VIE-2), **click-a-sentence → seek** (VIE-3),
  karaoke highlight + auto-scroll while playing (VIE-4), per-sentence deep links
  with copy-link (VIE-5), estimated-timing disclosure (VIE-6), source link (VIE-7),
  graceful no-transcript state (VIE-8).
* **Representatives (§6):** filterable list (REP-1), profile with bio + faction
  history + reverse-chron speeches (REP-2), precomputed stats with scope +
  methodology (REP-3/REP-5), accessible SVG charts with table equivalents (REP-6),
  faction colours (REP-4). "Bills submitted" stays hidden until the Bills module.
* **Extensible (EXT-4):** each module view is a separate lazy chunk; nav is built
  from the backend manifest, so disabling a module in backend config makes it
  vanish from the UI without a rebuild.
