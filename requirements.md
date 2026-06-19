# Országgyűlés Watch — Requirements

A third-party, civic-tech website for the Hungarian National Assembly
(*Magyar Országgyűlés*). The site makes parliamentary proceedings searchable
and watchable down to the individual sentence, and surfaces statistics about
representatives. It is built to grow: new data domains (bills, votes,
committees, …) can be added as self-contained modules.

This document specifies **what** the system must do and the constraints it must
respect. Concrete technology choices are recommended where useful but are not
binding unless marked **MUST**.

---

## 1. Goals & Non-Goals

### 1.1 Goals

- Let any citizen **search the full text of plenary proceedings** and jump from a
  matching sentence straight to the moment it was spoken on video.
- Provide **transparent statistics** about representatives (speaking time, number
  of speeches, bills submitted, attendance, etc.).
- Be **trustworthy**: every fact links back to its official `parlament.hu`
  source, and the data provenance/confidence is visible.
- Be **extensible**: adding a new feature area should not require touching
  existing modules.
- Be **cheap to run** and operable by a small civic-tech team.

### 1.2 Non-Goals (initial release)

- Not a real-time/live-streaming product; data is refreshed periodically.
- No user accounts, commenting, or social features in v1 (see §10, future).
- No editorializing or scoring of representatives; present neutral facts only.

---

## 2. Users & Key Use Cases

| User | Needs |
| ---- | ----- |
| Journalist / researcher | Find every time a topic or phrase was spoken; cite it with a permalink + video timestamp. |
| Engaged citizen | Watch what their representative said; see how active they are. |
| NGO / watchdog (e.g. K-Monitor) | Bulk-analyze activity; pull data via API. |
| Developer | Reuse the data through a documented, stable API. |

Primary use cases:

1. **Search → result → watch.** Search a phrase → see ranked sentence hits with
   speaker/date/context → click a hit → proceedings viewer opens with the video
   seeked to that sentence and the transcript synchronized.
2. **Browse a sitting.** Open a sitting day, read the transcript segmented by
   agenda item and speech, click any sentence to watch.
3. **Inspect a representative.** Open an MP's profile → biography/affiliation,
   their speeches, and activity statistics with charts.
4. **Programmatic access.** A developer queries the public API for the same data.

---

## 3. Data Source & Ingestion

### 3.1 Source of truth & ownership

- Data originates from `parlament.hu`. **This project owns the scraping
  pipeline end to end** — fetching from `parlament.hu`, parsing, merging,
  sentence segmentation, and timing estimation are all part of this codebase and
  maintained by this team.
- **SRC-1 (MUST).** The project replaces the legacy scraper. The existing
  `OpenParliamentTV-Tools/.../optv/parliaments/HU` pipeline is **reference
  material only** — it documents the `parlament.hu` access methods, the
  composite-key join, the whole-day-stream timing model, and the output schema.
  Its logic may be ported/adapted, but the reference tree is **not** a runtime
  dependency and **not** invoked in production.
- **SRC-2.** The scraper SHOULD remain compatible with the proven access
  strategy captured in the reference (Web-API XML backend with a token; public
  PAIR-proxy HTML backend without one; Felicitas JSON API to resolve the
  **whole-day HLS stream URL**), so behavior is well understood and verifiable
  against it. (Felicitas *per-speech offsets* are explicitly out of scope for
  v1 — see §3.4 and §10.)
- The scraper produces one **session record** per sitting day with the data
  shape established by the reference output (`data/processed/<session>-session.json`).
  Whether this is materialized as intermediate JSON or written straight to the
  database is an implementation choice (§3.3); the **logical shape** is:
  - `meta` (session id, schema version, date range, processing timestamps)
  - `data[]` — an ordered list of **speeches**, each containing:
    `electoralPeriod`, `session`, `agendaItem` (title + `type`), `originID`
    (`<term>-<sitting>-<speech>`), `people[]` (speaker(s) with `label`,
    `firstname`/`lastname`, faction `context`, optional Wikidata `wid`),
    `media` (whole-day HLS `videoFileURI`, `duration`, license),
    `textContents[].textBody[]` (the speech text split into **`sentences[]` with
    `timeStart`/`timeEnd`** day-absolute seconds, estimated per §3.4), and a
    `debug` block (`confidence`, `align-method`, source URIs). *(Per-speech
    `media.videoStart`/`videoEnd` offsets exist in the reference shape but are
    not populated in v1 — see §3.4/§10.)*

### 3.2 Scrapers (periodic jobs)

- **SCR-1 (MUST).** Scrapers run **periodically** (scheduled, e.g. nightly cron)
  and are **idempotent** — re-running a completed sitting does not duplicate or
  corrupt data. Stages are individually re-runnable, and a **lockfile** (or
  equivalent) prevents concurrent runs from colliding. (The reference pipeline's
  staged, opt-in, lockfile-guarded design is the model to follow.)
- **SCR-2.** Ingestion is **incremental**: only new/changed sittings are
  processed; a full re-import must also be possible (e.g. after a schema change).
- **SCR-3.** Each run records an **ingestion log** (run time, sittings added,
  errors, source backend used) viewable by operators.
- **SCR-4.** Scraper runs MUST be **polite** to `parlament.hu` (configurable
  delay, retries, optional proxy/API key) — these knobs MUST be exposed in
  deployment config, not hard-coded.
- **SCR-5.** A failed or partial sitting (e.g. PAIR-proxy error, no resolvable
  recording) MUST be ingested in degraded form (metadata/text without timing)
  and **flagged**, never silently dropped. The scraper MUST emit
  confidence/align-method provenance (per the reference `debug` block) and the
  store MUST retain it.
- **SCR-6.** The scraper's external dependencies MUST be either vendored into
  this project or clearly declared and pinned; the build MUST document how to
  run a scrape from a clean checkout. **For v1 the dependency surface is
  deliberately small** — HTTP access to `parlament.hu`/Felicitas and Hungarian
  sentence segmentation. No forced-alignment toolchain (`ffmpeg`/`espeak`/aeneas)
  and no NER/NEL endpoints are required (those belong to the advanced timing and
  entity work deferred in §10).

### 3.3 Ingestion into the database

- **ING-1 (MUST).** A loader transforms scraper output (the per-sitting session
  record, whether JSON files or in-memory) into the normalized SQLite schema
  (§4). The loader is a distinct, testable step separate from fetching/parsing,
  so the scrape and the DB build can be run and tested independently.
- **ING-2.** The loader is the **only writer** to the production database;
  the backend API treats the DB as read-only.
- **ING-3.** Loading MUST be **atomic per sitting** (transaction) so a crash
  mid-load never leaves a half-imported sitting visible.
- **ING-4.** Re-ingesting a sitting **replaces** its derived rows
  (upsert keyed on `originID` / session id) so corrections from the upstream
  source propagate.

### 3.4 Sentence ↔ video timing (v1: positional estimate)

- **TIM-1 (MUST).** v1 derives each sentence's `timeStart`/`timeEnd` by a
  **simple positional estimate**: the whole-day stream duration is distributed
  across the sitting's text **proportional to character position** — i.e. a
  sentence's share of the timeline equals its share of the day's transcript
  characters. This needs only the day-stream duration and the segmented text;
  no audio processing.
- **TIM-2.** Timing is **day-absolute** (offsets into the whole-day HLS stream),
  so a click on a sentence seeks into that stream (§5.2). The model is the
  reference pipeline's `estimated-day-offset` fallback, used as the *primary*
  (and only) v1 method.
- **TIM-3.** v1 timing is **approximate by design** — accurate to the rough
  neighborhood of a passage, not the word. Every sentence MUST be stamped with
  provenance marking it as estimated (e.g. `align-method = "estimated-day-offset"`,
  reduced `confidence`) so the UI can disclose imprecision (§5.2 VIE-6).
- **TIM-4.** The timing step MUST be a **distinct, swappable stage**, so a more
  precise method (per-speech offsets, forced alignment — §10) can replace it
  later without changing the scraper's fetch/parse/segment stages or the data
  shape.

---

## 4. Data Storage

- **DB-1 (MUST).** Storage is **SQLite**. The schema is normalized around the
  entities below; identifiers mirror the pipeline's stable keys.
- **DB-2 (MUST).** Full-text search uses SQLite **FTS5** over sentence text,
  configured for **Hungarian** (Unicode-aware tokenization, diacritic-insensitive
  matching, accent folding). See §5.1.
- **DB-3.** The runtime DB is **regenerable from source data** — it is a cache,
  not the system of record. Builds should be reproducible.
- **DB-4.** A read replica / file copy may be swapped in atomically after a load
  so queries never hit a half-written DB (e.g. build new file, then atomic rename).

### 4.1 Core entities (minimum)

- **electoral_period** — number, date range.
- **session** (sitting day) — id, period, date start/end, source page.
- **agenda_item** — title, official title, `type` (from the pipeline's agenda
  taxonomy, e.g. `CORE_OPENING`, `CORE_VOTING`, `CORE_QA`, …), order within session.
- **speech** — `originID`, session, agenda item, order, speaker reference,
  speaker status/context (e.g. `main-speaker`, chair), media reference,
  start/end, confidence, align-method.
- **sentence** — speech reference, order, **text**, `timeStart`, `timeEnd`
  (day-absolute seconds). This is the unit of search and of video seeking.
- **media** — per session day: HLS `videoFileURI`, duration, license, creator.
- **person** (representative) — `label`, first/last name, optional Wikidata id,
  plus enrichable bio fields (party, constituency, term memberships, photo URL).
- **faction / party** — label, optional Wikidata id, color (for charts).
- **membership** — person ↔ faction ↔ period (factions change over time).
- **entity** (optional, from NER stage) — in-transcript linked entities
  (Wikidata) and their sentence offsets.

### 4.2 Derived / statistics tables

- Precomputed aggregates per representative and per faction (speaking time,
  speech counts, etc.) so profile/statistics pages are fast. Rebuilt by the
  loader. (See §6.)

---

## 5. Functional Requirements — Module: Proceedings Search & Viewer

### 5.1 Search

- **SEA-1 (MUST).** Free-text search over **sentence text**, ranked by relevance,
  returning sentence-level hits.
- **SEA-2 (MUST).** Hungarian-aware matching: case- and accent-insensitive,
  handles Hungarian morphology at least via prefix/stemming-friendly tokenization;
  exact-phrase ("…") supported.
- **SEA-3.** Filters: by **date range**, **electoral period**, **speaker**,
  **faction**, **agenda-item type**. Filters are combinable.
- **SEA-4.** Each result shows: matched sentence with **highlighted** terms,
  surrounding snippet, speaker (linked to profile), faction, date, agenda title,
  and a thumbnail/affordance to play.
- **SEA-5.** Results are **paginated** (or infinite-scroll) and return within
  the performance budget in §8 for the full corpus.
- **SEA-6.** Search is reachable and shareable via **URL query params**
  (deep-linkable search state) so a search can be cited.
- **SEA-7 (SHOULD).** Search-as-you-type suggestions for speakers and factions.

### 5.2 Proceedings viewer (sentence ↔ video sync)

- **VIE-1 (MUST).** Opening a speech/sitting shows the transcript segmented by
  **agenda item → speech → sentence**, with speaker labels.
- **VIE-2 (MUST).** The viewer embeds an **HLS video player** (the day stream
  `videoFileURI`) able to play in modern browsers (e.g. `hls.js` where the
  browser lacks native HLS).
- **VIE-3 (MUST).** **Click a sentence → the player seeks to that sentence's
  `timeStart`** (day-absolute) and plays. This is the signature feature.
- **VIE-4 (MUST).** As the video plays, the **currently-spoken sentence is
  highlighted** and the transcript auto-scrolls (karaoke-style following based on
  `timeStart`/`timeEnd`).
- **VIE-5 (MUST).** Every sentence and every speech is **deep-linkable** by URL
  (e.g. `/proceedings/<originID>#s=<sentence>` or `?t=<seconds>`), so a citation
  reopens the viewer at the exact moment.
- **VIE-6.** Where timing is **estimated** (not real per-speech offsets) or
  confidence is low, the UI MUST **indicate reduced precision** (e.g. a subtle
  marker / tooltip explaining timing is approximate). Source: pipeline `debug`.
- **VIE-7.** A **"view original on parlament.hu"** link is always present, and the
  video license/attribution is shown. It points as **specifically** as possible:
  to the speech's own `playseq` player on parlament.hu's stream server (which
  plays exactly that speech's clip) in the viewer, and to the day recording's
  player on the sitting page. Because the modern proceedings portal exposes no
  working anonymous per-speech/per-day text permalink (the legacy `naplo_fadat`
  link 404s), the per-speech video player is the most specific resolvable
  original; the generic portal page (`sourcePage`/`sourceURI`) is the fallback
  when no clip is available.
- **VIE-8 (SHOULD).** Speeches with no transcript (video-only,
  `confidence = 0.5`, `no-proceedings-text`) still render with video + metadata
  and a clear "no transcript available" note.
- **VIE-9 (MUST).** The player shows **only the current speech**, not the whole
  day stream. The video **source is the speech's own clip URL**, derived from the
  day recording's stream URL by shifting its offsets to the speech's real
  `[video_start, video_end]` window (the same offsets that anchor sentence
  timing). The streaming server serves that URL as a playlist cropped to exactly
  the speech, so the player's timeline is the speech's own length (0-based), not
  a multi-hour day stream, and the controls reflect only it. Deriving the clip
  needs no extra scraping (offsets are already stored); the generic whole-day
  stream URL is **not** used as the player source except as a fallback when a
  speech has no usable offsets (degraded, §VIE-8). When the clip ends the viewer
  **auto-advances to the next speech** (navigating to its page, §VIE-5) and keeps
  playing; on the last speech of a sitting it rests at the end. The clip's smil
  VOD is generated on demand, so its activation endpoint is pinged before the
  playlist is requested.

---

## 6. Functional Requirements — Module: Representatives & Statistics

- **REP-1 (MUST).** A browsable, searchable **list of representatives**, filterable
  by faction, electoral period, and constituency; each links to a profile.
- **REP-2 (MUST).** A **representative profile** shows: name, photo (if available),
  current/past faction(s) with dates, constituency, Wikidata link, and a
  reverse-chronological **list of their speeches** (each linking into the viewer).
- **REP-3 (MUST).** Per-representative **statistics**, including at least:
  - total **speaking time** (sum of speech durations) and number of speeches;
  - speeches per sitting / over time (trend chart);
  - **number of bills submitted** *(requires the Bills module, §7 — until then
    this metric is hidden, not faked)*.
- **REP-4.** **Faction-level** aggregate statistics (totals and averages per MP),
  with each faction rendered in a consistent color.
- **REP-5.** Statistics MUST state their **time scope** (which period/date range)
  and **how they are computed** (a short methodology note), and be consistent with
  the underlying speech records a user can click through to verify.
- **REP-6.** Charts MUST have accessible text/table equivalents (§8 accessibility).
- **REP-7.** Statistics are served from **precomputed aggregates** (§4.2) and
  recomputed on each ingest; they must never block on live aggregation of the
  full corpus.

---

## 7. Extensibility — Module Architecture

The site must accommodate new data domains (next likely: **Bills/irományok**,
**Votes/szavazások**, **Committees/bizottságok**, **Interpellations**) without
rework of existing features.

- **EXT-1 (MUST).** A **module** is a vertical slice owning its own:
  scraper/ingestion step, DB tables, backend API routes, and frontend
  route(s)/views. Modules MUST NOT require schema changes in other modules.
- **EXT-2 (MUST).** Cross-module links go through **shared core entities**
  (person, faction, session, agenda item). E.g. a future Bills module references
  `person` for sponsorship; it does not duplicate the person table.
- **EXT-3.** The backend exposes module routes under a **stable, versioned,
  namespaced API** (e.g. `/api/v1/proceedings/…`, `/api/v1/representatives/…`,
  `/api/v1/bills/…`). Adding a module adds routes; it does not break existing ones.
- **EXT-4.** The frontend supports **lazily-loaded feature modules** registered
  into the router and navigation, so a new section appears without rebuilding the
  shell.
- **EXT-5.** The ingestion pipeline supports **registering new loaders/scrapers**
  driven by config, mirroring the upstream pipeline's opt-in, idempotent staged
  design.
- **EXT-6.** A **module disabled in config** must degrade gracefully (its nav
  entry and any dependent metrics hidden), not error.

> **Acceptance probe for extensibility:** introducing the Bills module
> (a new scraper, `bill` + `bill_sponsor` tables, `/api/v1/bills` routes, a Bills
> browser view, and unhiding the "bills submitted" stat on profiles) must be
> achievable as additive changes only.

---

## 8. Non-Functional Requirements

### 8.1 Architecture

- **NFR-1 (MUST).** **SPA frontend** + **separate backend API** over the SQLite
  DB. The backend handles search and content loading; the frontend is a static
  bundle that talks to the API.
- **NFR-2 (SHOULD).** Backend in **Python** (e.g. FastAPI) to share language and
  models with the existing scrapers; SQLite accessed read-only with FTS5.
- **NFR-3.** API is **documented** (OpenAPI/Swagger) and stable per §EXT-3.

### 8.2 Performance

- **PERF-1.** Typical search query returns in **< 500 ms** server-side over the
  full multi-cycle corpus; profile/statistics pages in **< 1 s**.
- **PERF-2.** Viewer initial transcript load and video start in **< 2 s** on a
  broadband connection.
- **PERF-3.** Designed for the full historical scope (cycles 37–43, 2002→present);
  search and pagination must not degrade unacceptably as the corpus grows.

### 8.3 Quality, accessibility, i18n

- **A11Y-1 (MUST).** Primary flows (search, viewer, profiles) meet **WCAG 2.1 AA**:
  keyboard-navigable, screen-reader labels, sufficient contrast, captions/transcript
  serve as the video's text alternative.
- **I18N-1 (MUST).** Primary UI language **Hungarian**; copy externalized so an
  English locale can be added (the upstream platform already ships locale files).
- **RESP-1.** Responsive: usable on mobile, tablet, and desktop.

### 8.4 Trust, legal, privacy

- **LEGAL-1 (MUST).** Display the `parlament.hu` **source attribution and license**
  on media and text, and link to the original page for every record.
- **PRIV-1 (MUST).** No tracking of users beyond privacy-respecting, anonymized
  analytics; **no third-party ad/marketing trackers**; GDPR-compliant. Any
  analytics disclosed in a privacy notice.
- **TRUST-1.** Provenance and **confidence/timing-precision** indicators (from the
  pipeline `debug` block) are surfaced, not hidden, wherever they affect what the
  user sees (see VIE-6, REP-5).
- **DATA-1.** The processed dataset SHOULD be **downloadable** (open data) to honor
  the civic-tech mission, subject to source licensing.

### 8.5 Operations

- **OPS-1.** Deployable on modest infrastructure; the DB is a single file and the
  backend is stateless, enabling simple/atomic deploys and rollbacks.
- **OPS-2.** Scraper/ingest jobs run via scheduler (cron) with logging (§SCR-3)
  and alerting on repeated failures.
- **OPS-3.** Automated tests cover: the loader (JSON→DB), search correctness
  (incl. Hungarian accent/case folding), sentence↔time mapping, and API contracts.
- **OPS-4.** Configuration (source backend/API key, proxy, sleep, schedule,
  enabled modules) is environment-driven, not hard-coded.

---

## 9. Suggested Technology Stack (non-binding)

- **Scrapers / pipeline:** a Python scraping pipeline owned by this project,
  ported/adapted from the `OpenParliamentTV-Tools` HU reference and run on a
  schedule. Staged, idempotent, lockfile-guarded.
- **Database:** SQLite + FTS5 (Hungarian tokenization). Atomic file swap on load.
- **Backend:** Python + FastAPI, read-only SQLite, OpenAPI docs.
- **Frontend:** SPA (React or Vue) with a client-side router and lazily-loaded
  feature modules; **hls.js** for HLS video; a charting lib for statistics.
- **Deploy:** static frontend + containerized API; scheduled jobs via cron/systemd.

---

## 10. Out of Scope / Future

- User accounts, saved searches, alerts/notifications on topics or speakers.
- Bills, votes, committees, interpellations — planned as future **modules** (§7);
  the architecture must already accommodate them.
- **Precise sentence ↔ video sync (planned enhancement to the timing stage,
  §3.4).** v1 uses a positional/character-length estimate (TIM-1). A later
  iteration replaces it — as a drop-in swap of the timing stage (TIM-4) — with
  progressively more accurate methods:
  1. **Real per-speech offsets** via the Felicitas per-speech video query
     (`ulesnapok-aktusok-query` → per-speech `offset1`/`offset2`), giving
     second-precise speech boundaries (the reference's
     `felicitas-speech-offset`); timing is then estimated only *within* a speech.
  2. **Forced alignment** (e.g. aeneas/whisper) for word/sentence-precise timing,
     which adds an audio-processing toolchain (`ffmpeg`/`espeak`).
- Entity recognition/linking (NER/NEL) — tagging in-transcript entities and
  linking representatives/factions to Wikidata. Deferred with its external
  dependencies (entity-fishing endpoint, HuSpaCy model); the data shape reserves
  room for it (entity table, `people[].wid`).
- Automatic speech-to-text/whisper re-transcription or speaker-diarization
  improvements.
- Cross-parliament comparison or non-HU parliaments.
- AI summarization/Q&A over proceedings (possible later module).

---

## 11. Open Questions

1. **Bills/votes data:** the current scraper covers only proceedings + media.
   Confirm the source endpoints for `irományok`/`szavazások` before scheduling
   the Bills/Votes modules. (REP-3's "bills submitted" depends on this.)
2. **Historical backfill scope:** ship current cycle (43) first, then backfill
   37–42, or backfill all up front?
3. **Entity enrichment:** NER/NEL is deferred (§10). For v1, do we still want a
   lightweight Wikidata-based enrichment of representatives (photos, bios,
   constituencies) — independent of in-transcript NER — or ship with only the
   names/factions the scraper yields?
4. **Hosting & domain:** target infrastructure and expected traffic, to size
   PERF budgets and caching.
