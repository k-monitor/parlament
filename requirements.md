# Parlamonitor — Requirements

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
2. **Browse a sitting.** Open a sitting day, see it segmented by agenda item →
   speech; **expand any speech to read its transcript inline** (in its original
   paragraphs) without leaving the page, or open the full video viewer to watch
   it with the transcript synced sentence-by-sentence.
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
  against it. (The Felicitas *per-speech offsets* — out of scope in the earliest
  draft — are now captured and used as the positional-timing fallback beneath the
  Whisper forced alignment; see §3.4.)
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
    `timeStart`/`timeEnd`** day-absolute seconds, estimated per §3.4, each also
    tagged with the **source paragraph** it came from — the `<p>` boundary in the
    upstream HTML — so the reader can re-group the flat sentence list back into
    the transcript's original paragraphs), and a `debug` block (`confidence`, `align-method`, the upstream per-speech type
    `felszolalasTipusa`, source URIs). *(Per-speech
    `media.videoStart`/`videoEnd` offsets exist in the reference shape but are
    not populated in v1 — see §3.4/§10.)* The per-speech type drives the
    statistics-exclusion of chairing speeches (STAT-1).

### 3.2 Scrapers (periodic jobs)

- **SCR-1 (MUST).** Scrapers run **periodically** (scheduled, e.g. nightly cron)
  and are **idempotent** — re-running a completed sitting does not duplicate or
  corrupt data. Stages are individually re-runnable, and a **lockfile** (or
  equivalent) prevents concurrent runs from colliding. (The reference pipeline's
  staged, opt-in, lockfile-guarded design is the model to follow.)
- **SCR-2.** Ingestion is **incremental**: only new/changed sittings are
  processed; a full re-import must also be possible (e.g. after a schema change).
  For a live deployment the scraper also offers a **continuous, low-load sync**
  of the latest cycle (SCR-7).
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
  run a scrape from a clean checkout. **The host's default dependency surface is
  deliberately small** — HTTP access to `parlament.hu`/Felicitas and Hungarian
  sentence segmentation. The forced-alignment ASR toolchain (`ffmpeg` + Whisper)
  is **optional and offloaded** (§3.4 TIM-5): it lives in the managed-GPU worker
  image, not on the host, and its absence degrades to the positional timing
  estimate rather than an error. NER/NEL endpoints remain out of scope (deferred
  with the entity work, §10).
- **SCR-7 (SHOULD).** For a **continuously-deployed** site, a **sync mode** keeps
  the store in step with `parlament.hu` while imposing **minimal load when nothing
  has changed**. Each poll cheaply probes only the **latest electoral cycle** —
  list-level queries plus a **one-request-per-day speech-listing fingerprint** for
  the still-live sitting — and re-scrapes only the sittings/bills/votes that
  actually changed since the last check, reusing the per-item detail caches
  (SCR-2). An idle poll costs a handful of requests and writes nothing; the
  last-seen signatures are **persisted** so a poll is stateless across restarts.
  Politeness knobs (SCR-4) and the poll cadence are deployment config (OPS-4). The
  sync may run **inside the deployment** (a sidecar loop) or from an **external
  cron**; both share one code path (§8.5 OPS-5). It refreshes the source records
  only — bringing the database up to date is the loader's incremental step (ING-5),
  so the two halves stay independently runnable (ING-1).
  > **✅ realized.** `parlamonitor sync` auto-detects the latest cycle, probes it
  > (one `ulesnapok-query` + per-live-day `ulesnapok-aktusok-query` fingerprint,
  > plus a cheap text probe for any day whose transcript is still pending — SCR-8),
  > re-scrapes only changed items, persists signatures in `sync-state.json`, and
  > emits an ingestion log (SCR-3). Representatives refresh on a slow cadence.
- **SCR-8 (MUST).** `parlament.hu` publishes a sitting in **stages**, not at once:
  first the bare day listing, then the **recording**, and only **days later** the
  **transcript text** (jegyzőkönyv). Ingestion MUST follow this to completion and
  **not treat a day as done the moment its video appears**:
  - A sitting counts as complete only once its **transcript text has been
    captured**. Until then it stays in the change-probe set and is re-checked on
    every poll — **not only while it is the latest day** — so a transcript that
    lands after the day is no longer the newest sitting is still pulled in, rather
    than the day being frozen text-less forever. The re-check is cheap: the day's
    one-request speech listing plus a **single speech-text probe** (attaching text
    to an already-listed speech does not move the listing fingerprint, so the text
    itself must be probed); the probe samples a few speeches because a transcript
    is published for the **whole day at once** (all-or-nothing), and a video-only
    day (VIE-8) can hold individual text-less speeches. Only not-yet-complete days
    incur the probe, so an otherwise-idle poll stays cheap (SCR-7).
  - An **announced sitting with no recording/speeches yet** — a day `parlament.hu`
    already lists before it is held (or before it is processed) — MUST be ingested
    as a **`scheduled` placeholder session**, never dropped, so the site can show a
    sitting is **coming** (SIT-1). The probe window reaches a little past today so
    an imminent announced day is captured. When the day is later held and
    populated, re-ingesting it (ING-4) flips it to `published`.
  > **✅ realized.** The sync's proceedings pass carries a per-day `has_text` signal
  > in `sync-state.json`; a day without it is re-listed + text-probed each poll
  > until its jegyzőkönyv lands, then it goes quiet. `scrape_day` returns a
  > placeholder bundle (no speeches) instead of `None`, and `transform_day` stamps
  > `meta.status` (`scheduled` / `published`). The batch `download_period` self-heals
  > the same way (re-scrapes a stored day still `_awaiting_content`).

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
- **ING-5.** The loader supports an **incremental update** so a routine refresh
  does **not** rebuild from scratch: it detects which source records changed since
  the last load (tracked per file), applies **only those** into a snapshot of the
  live DB, rebuilds the derived aggregates, and **atomically swaps** the result in
  (DB-4). Reprocessing is thus bounded to the changed sittings and, combined with
  the read-only-per-request API, the swap is **zero-downtime** (no restart). A
  full rebuild remains available for schema changes / forced re-imports (SCR-2).
  > **✅ realized.** `python -m app.loader --update` reconciles the DB to the
  > scraper's `processed/*.json` via a `load_state` (mtime/size) table, reloading
  > only changed files then atomically renaming the file over the live DB; it is a
  > cheap no-op when nothing is newer, and degrades to a full build when no DB
  > exists yet. This is the update half of the continuous sync (SCR-7 / OPS-5).

### 3.4 Sentence ↔ video timing (forced alignment, positional fallback)

- **TIM-1 (MUST).** Each sentence's `timeStart`/`timeEnd` is derived, by default,
  by **forced alignment of the recording against the transcript**. An ASR model
  (**whisper-large-v3-turbo**) transcribes the sitting's audio into time-stamped
  words; those spoken-word timestamps are then matched, **at the sentence level**,
  to the authoritative `parlament.hu` transcript text, so each official sentence
  receives the **real time it was spoken** (`align-method =
  "whisper-forced-alignment"`). This is accurate to the word, not merely the
  neighborhood of a passage. The ASR text is used only to *find the times*; the
  displayed and searched text is always the official transcript, never the
  hypothesis.
- **TIM-2.** Timing is **day-absolute** (offsets into the whole-day HLS stream),
  so a click on a sentence seeks into that stream (§5.2). The ASR decodes exactly
  that stream, so its word times share the stream's own `t=0` — the same origin
  the stored per-speech offsets are measured from — needing no coordinate
  conversion.
- **TIM-3 (fallback + provenance).** Where no usable transcription exists for a
  speech (no recording, an ASR failure, or an alignment too thin to trust), timing
  **degrades gracefully** to a positional estimate: the speech's real
  `[video_start, video_end]` window (from the Felicitas per-speech offsets) with
  its sentences distributed **by character position** (`align-method =
  "felicitas-speech-offset"`), and, lacking even that, the whole-day positional
  estimate (`align-method = "estimated-day-offset"`). Every sentence MUST be
  stamped with provenance — its `align-method` and a `confidence` — so the UI can
  disclose which sentences are word-accurate and which are approximate (§5.2
  VIE-6). The positional estimate needs no audio processing, so a clean install
  with no ASR backend still produces usable (approximate) timing (SCR-6).
- **TIM-4 (MUST).** The timing step is a **distinct, swappable stage**: it only
  reads a segmented, untimed session record and writes per-sentence times +
  provenance, never touching the fetch/parse/segment stages or the data shape.
  The (heavy) transcription is produced **once per sitting and cached** (keyed by
  the recording URL + model), so a re-run transcribes only genuinely new audio;
  the alignment itself is cheap, deterministic and runs offline (no GPU, no
  network), so it is fully unit-testable.
- **TIM-5.** The ASR is the one heavy compute in the timing stage and MAY be
  **offloaded to a managed GPU service** (e.g. Modal), selected purely via
  environment (OPS-4/OPS-6). It **degrades gracefully** to a local model, then to
  the positional estimate, when unconfigured. Metered cost is bounded: only
  cache-miss sittings are sent, **batched** across a capped worker pool that scales
  to zero when idle, and a silence-skipping VAD means GPU time tracks actual
  speech, not the (≈⅓ non-speech) wall-clock of a sitting.
  > **✅ realized.** The scraper's `align` stage (`parlamonitor.whisper_align`)
  > transcribes each day's HLS recording with whisper-large-v3-turbo on Modal
  > (`whisper_modal_app.py`, `PARLAMONITOR_TIMING_BACKEND=auto|whisper-modal|
  > whisper-local|character`), caches the words per sitting, and aligns them to the
  > official sentences; `felicitas-speech-offset` (and then `estimated-day-offset`)
  > remain the automatic per-speech fallbacks.

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
  Because the API opens the DB **read-only, once per request**, an
  atomically-swapped file is picked up by the **next request with no restart and no
  downtime** — this is how the incremental update (ING-5) ships new data live.

### 4.1 Core entities (minimum)

- **electoral_period** — number, date range.
- **session** (sitting day) — id, period, date start/end, source page, and a
  **`status`** (`published` for a held sitting with speeches; `scheduled` for an
  announced upcoming sitting that has no recording/speeches yet — SCR-8 / SIT-1).
- **agenda_item** — **one row per agenda act** in the sitting: `title`,
  `official title` (the full act name as published, including any iromány code —
  e.g. *"Interpelláció megtárgyalása (I/94) …"* — shown as the section heading so
  it is not truncated), `type` (from the pipeline's agenda taxonomy, e.g.
  `CORE_OPENING`, `CORE_VOTING`, `CORE_QA`, …), and order within session. The
  type is **classified from the act name**, so **all of the act's speeches group
  under this one item** regardless of their individual per-speech types (a
  chair's `ülésvezetés` turn and a member's substantive speech alike) — matching
  how `parlament.hu` presents the act. Only when the act name itself carries no
  type signal does classification fall back to the per-speech type, so structural
  sections (e.g. *"Az ülés napirendjének megállapítása"*) keep a meaningful type.
- **speech** — `originID`, Felicitas speech UUID (`speech_uuid`, for
  cross-module links such as BILL-8), session, agenda item, order, speaker
  reference, speaker status/context (e.g. `main-speaker`, chair), the upstream
  **per-speech type** (*felszólalás típusa*, `felszolalas_tipus`, e.g.
  `napirend előtti felszólalás`, `ülésvezetés`), a derived boolean
  **`procedural`** flag (true for chairing/session-management speeches that are
  excluded from statistics — STAT-1), media reference, start/end, confidence,
  align-method.
- **sentence** — speech reference, order, **text**, `timeStart`, `timeEnd`
  (day-absolute seconds), and the **paragraph index** of the source `<p>` it
  belongs to (so the transcript can be rendered in its original paragraphs —
  §5.5). This is the unit of search and of video seeking.
- **media** — per session day: HLS `videoFileURI`, duration, license, creator.
- **person** (representative) — `label`, first/last name, optional Wikidata id,
  plus enrichable bio fields (party, constituency, term memberships, photo URL),
  and which mandate they hold: an MP, a **nationality advocate** (with the
  `nationality` they speak for — REP-9), or neither (a minister or invited guest
  who spoke). One person can be both across different cycles.
- **faction / party** — label, optional Wikidata id, color (for charts).
- **membership** — person ↔ faction ↔ period (factions change over time).
- **entity** (optional, from NER stage) — in-transcript linked entities
  (Wikidata) and their sentence offsets.

### 4.2 Derived / statistics tables

- Precomputed aggregates per representative and per faction (speaking time,
  speech counts, etc.) so profile/statistics pages are fast. Rebuilt by the
  loader. (See §6.)

---

## 4A. Cross-cutting: Global Electoral-Cycle Scope

The corpus spans many electoral cycles (§PERF-3). Rather than each module
carrying its own electoral-period filter, the site exposes **one global cycle
selector** that scopes every period-aware view at once.

- **CYC-1 (MUST).** A **cycle selector lives in the site header** and is visible
  on every page: a **dropdown** listing each electoral period plus an **"all
  cycles"** option, **defaulting to the latest cycle** on a first visit. Each
  period is labelled by its **start–end years** (e.g. `2018–2024`), not its
  ordinal cycle number, since the year span is what users recognise; an ongoing
  cycle (no end date yet) shows its start year with a trailing dash (`2026–`).
- **CYC-1a (MUST).** The selector is **multi-select**: several cycles can be in
  scope at once (tick as many as wanted), and every period-aware view then covers
  their union. The trigger names the selection — one or two cycles by label, more
  as a count.
- **CYC-2 (MUST).** The chosen scope is the **single source of period scope**
  across all period-aware modules — proceedings search, sittings, representatives,
  bills, other irományok and votes all honour it. Changing it **immediately
  re-scopes the current view** and carries over as the user navigates between
  modules; modules no longer present their own redundant period filter.
- **CYC-3 (MUST).** The selection is **persisted** (e.g. `localStorage`) so it
  survives reloads and return visits; an invalid/stale saved value falls back to
  the latest cycle.
- **CYC-4.** "All cycles" simply **drops the period constraint** site-wide
  (no period parameter is sent) and is the same state as an empty selection;
  otherwise every query is constrained to the selected electoral period(s) — the
  API's `period` param is repeatable (`?period=43&period=44`). Where an aggregate
  is precomputed per cycle, a multi-cycle scope sums the per-cycle rows; counts
  that would double-count across cycles (a faction's distinct speaking MPs) are
  derived over the whole scope instead.
- **CYC-5.** The remaining, module-specific filters (free-text query, status,
  type, faction, date range, sort, etc.) stay **per-page URL state** so a
  filtered/searched view is still deep-linkable and citable (§SEA-6); only the
  electoral period moved out of the per-page URL into the global selector.

---

## 4B. Cross-cutting: Accent-Insensitive Text Search

Hungarian text is heavily accented (á, é, í, ó, ö, ő, ú, ü, ű). Users routinely
type names and terms **without diacritics** — "dora" for *Dóra*, "ulesvezetes"
for *ülésvezetés*. Every user-facing text search/filter box on the site must
match regardless of accents, so a query never silently returns "no results"
merely because an accent was omitted (or added).

- **FOLD-1 (MUST).** **Every** user-facing free-text search/filter box matches
  **accent-insensitively and case-insensitively**. Typing an unaccented query
  matches accented content and vice versa (`dora` ↔ *Dóra*, `Dóra` ↔ *dora*).
  This is a site-wide rule, not a per-module feature.
- **FOLD-2 (MUST).** It applies to **all** such boxes, not only proceedings
  search — at minimum: the **representatives** name search (REP-1), the **bills**
  title-text filter (BILL-1), the **other irományok** title-text filter (BILL-9),
  and the **votes** subject/iromány-number filter (VOTE-1). Any text box added by
  a future module inherits the rule by default.
- **FOLD-3.** Folding is **symmetric and total**: the comparison normalizes both
  the query and the stored text to an accent-folded, case-folded form before
  matching, covering the full Hungarian accented set (including `ő`/`ű`, which
  naïve ASCII folding misses).
- **FOLD-4.** The full-text **proceedings search** already satisfies this via
  FTS5 accent folding (SEA-2, DB-2); FOLD-1 generalizes the same guarantee to the
  simpler `LIKE`-style name/title/subject filters in the other modules, which
  otherwise compare accent-sensitively. Where SQLite's default `LIKE`/`NOCASE`
  cannot fold accents, the loader/query layer MUST supply folding (e.g. a stored
  normalized column or a custom collation/function), not rely on the engine
  default.
- **FOLD-5.** Accent folding affects **matching only** — results, highlights,
  and labels are always shown with their **original accented text** intact.

---

## 4C. Cross-cutting: Embeddable Figures

The site's charts are civic-data assets: a journalist or NGO should be able to
drop one straight into their own article or page. Each headline visualization
therefore offers a one-click **embed** that yields a self-contained `<iframe>`
snippet — a site-wide capability, not a per-module feature.

- **EMBED-1 (SHOULD).** Designated charts carry an **embed affordance** in their
  **bottom-right corner**: a small button that opens a popover with a
  ready-to-paste `<iframe>` snippet. At minimum the embeddable figures are the
  **proceedings search popularity histogram** (SEA-8), the **faction vote
  analysis** — party cohesion / co-voting (VOTE-8), the **faction speaking-time**
  chart (REP-4), the **Kérdések questions Sankey** (BILL-11), and a
  **representative's vote-participation** breakdown (REP-3). A future chart opts
  into the same affordance rather than reinventing it.
- **EMBED-2 (MUST).** An embed renders **chrome-free**: the `<iframe>` points at a
  dedicated embed view (`/embed/<figure>`) that shows **only** the figure — its
  title, the active electoral-cycle scope, and a compact attribution line — with
  **no site header, navigation, cycle selector, or footer**.
- **EMBED-3 (MUST).** The snippet **captures the currently selected electoral
  cycle scope** (§4A): the embed URL carries the **explicit** cycle(s), so the
  embedded figure always shows the data for the scope the sharer was viewing, **independent
  of the reader's own saved scope** — an embed on a third-party page has no access
  to, and MUST NOT depend on, the visitor's preference. The URL likewise carries
  the figure's active parameters (e.g. the search query + filters, or the
  representative id) so the embed reproduces exactly what the sharer saw, and the
  active language (`?lang=`).
- **EMBED-4.** The embed view is **self-contained and deep-linkable**: it reads
  everything it needs from its own URL and calls the same public read-only API as
  the site, so the link works both standing alone (shareable / citable) and inside
  an `<iframe>`. It reuses the site's existing chart components, so an embedded
  figure is visually identical to its on-site counterpart.
- **EMBED-5.** The embed **attributes** the figure: a visible Parlamonitor mark
  and a link back to the **full interactive version** of that figure on the site
  (same cycle + parameters), opened in a new tab so a reader can explore the live
  chart. The underlying `parlament.hu` source attribution remains available
  (TRUST-1).
- **EMBED-6.** The embed is **cheap to run** (§1.1): it adds no server-side
  rendering and no new stored artefacts — the embed view is served by the same
  SPA shell + aggregate endpoints as every other page, and the host embeds it as
  a static snippet loaded lazily (`loading="lazy"`).

---

## 5. Functional Requirements — Module: Proceedings Search & Viewer

### 5.1 Search

- **SEA-1 (MUST).** Free-text search over **sentence text**, ranked by relevance,
  returning sentence-level hits.
- **SEA-2 (MUST).** Hungarian-aware matching: case- and accent-insensitive,
  handles Hungarian morphology at least via prefix/stemming-friendly tokenization;
  exact-phrase ("…") supported.
- **SEA-3.** Filters: by **date range**, **speaker**, **faction**,
  **agenda-item type**. Filters are combinable. The **electoral period** is set
  by the global cycle selector (§4A), not a per-search filter.
- **SEA-4.** Each result shows: matched sentence with **highlighted** terms,
  **surrounding transcript context** — a few sentences immediately before and
  after the match so the moment reads in context without opening the viewer,
  **spilling into the adjacent speeches** when the match sits at a speech
  boundary (context drawn "from the speeches before/after", not only the one the
  hit lands in), each context line attributed to its speaker so a **change of
  speaker is visible** — speaker (linked to profile), faction, date, agenda
  title, and a thumbnail/affordance to play. The excerpt is rendered with the
  **same transcript parsing as the sitting-day reader (§5.5)**: the redundant
  leading speaker label is stripped, parenthetical stage directions / heckles are
  lifted out as italic asides, and numeric/date references stay inline — so a hit
  on an interjection (e.g. *"(Derültség.)"*) reads cleanly instead of as raw text
  with dangling parentheses. The matched term stays highlighted through the
  parsing, and a parenthetical that spans the match/context boundary is lifted as
  one aside.
- **SEA-5.** Results are **paginated** (or infinite-scroll) and return within
  the performance budget in §8 for the full corpus.
- **SEA-6.** Search is reachable and shareable via **URL query params**
  (deep-linkable search state) so a search can be cited.
- **SEA-7 (SHOULD).** Search-as-you-type suggestions for speakers and factions.
- **SEA-8 (SHOULD).** A **popularity-over-time chart** accompanies a query: the
  number of matching sentences bucketed by calendar period (monthly, collapsing
  to yearly over a long span), so a user sees when a term was most discussed.
  The chart describes the **same result set** — it honors all active filters
  (§SEA-3) — and is computed over **all** matches, not just the current page. It
  is a separate aggregate from the paginated results, so it never slows the
  result list. Quiet periods render as zero, not as gaps. The chart is
  **embeddable** (§4C).
- **SEA-9 (SHOULD).** A **result-breakdown chart** accompanies a query: the
  number of matching sentences grouped **by faction** and **by representative**,
  so a user sees at a glance *who* and *which side of the house* a term comes
  from. Like the popularity chart (SEA-8) it describes the **same result set** —
  it honors all active filters (§SEA-3) and is computed over **all** matches, not
  just the current page — and is a **separate aggregate** from the paginated
  results so it never slows the result list. Each group is a **top-N** (the
  busiest factions/speakers) to stay glanceable; factions render in their
  consistent colour (§4.1) and each representative links to their profile
  (REP-1), and matching honours the site-wide accent folding (§4B).
- **SEA-10 (SHOULD).** Results can be **re-ordered**: by **relevance** (default,
  the bm25 rank) or by **sitting date** (newest-first / oldest-first). The chosen
  ordering is **per-page URL state** (§CYC-5) so a sorted view is deep-linkable,
  and changing it returns to the first page. The order is applied server-side
  from a **fixed whitelist** (never spliced from raw request input) and affects
  only the paginated result list — the accompanying aggregates (SEA-8/SEA-9)
  describe the whole result set regardless of order.
- **SEA-11.** Each executed search (the canonical `/search` request) is counted
  into the **anonymous, aggregated search analytics** — keyword + active filters,
  no personal data — see **PRIV-2** (§8.4).

### 5.2 Proceedings viewer (sentence ↔ video sync)

- **VIE-1 (MUST).** Opening a speech/sitting shows the transcript segmented by
  **agenda item → speech → sentence**, with speaker labels. Each speech is
  labelled with its **type** (*felszólalás típusa*, e.g.
  `napirend előtti felszólalás`, `ülésvezetés`). Procedural/chairing speeches
  (STAT-1) are **kept and rendered in full** here — they are only excluded from
  statistics, never from the sitting-day viewer — and their type label makes
  clear why they do not count toward a representative's totals.
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
- **VIE-10 (SHOULD).** The viewer offers a **client-side clip exporter**: from the
  speech being watched a user can **download a self-contained video file** of a
  chosen segment, optionally with the official transcript as subtitles. The whole
  export runs **in the browser** — no server-side transcoding job, no new stored
  artefact — so it costs the deployment nothing but the (already public) video
  bytes (NFR, §1.1 "cheap to run").
  - **Segment selection.** The default range is the **whole speech**; the user may
    narrow it to a **sub-range of the speech's sentences** (a start and an end
    sentence). The exported window is `[start.time_start, end.time_end]`
    (day-absolute), and the file is the stream cropped to exactly that window —
    reusing the same server-side smil cropping that already backs the per-speech
    clip (VIE-9), generalised to an arbitrary `[start, end]`. The selectable
    length is **bounded** (a sane cap) so an export can't try to buffer hours of
    video into memory.
  - **Subtitle options (three, user-chosen).** (1) **No subtitles** — video only.
    (2) **Soft subtitles** — the transcript muxed as a selectable, toggleable text
    track inside the file (fast: the video/audio are **stream-copied**, not
    re-encoded). (3) **Burned-in subtitles** — the transcript rendered permanently
    into the picture (for platforms that ignore soft tracks, e.g. social video);
    this **re-encodes** the video and is therefore **markedly slower**, which the
    UI MUST disclose before the user commits. The subtitle text is the **official
    transcript** (FTS/record source of truth, never an ASR hypothesis, cf. TIM-1),
    its cue times derived from the sentences' stored `time_start`/`time_end`
    rebased to the clip's `t=0`; only sentences overlapping the window are
    included. A video-only speech (VIE-8) offers export with **no-subtitles only**.
  - **Provenance & licence.** The export path surfaces the same source/licence
    attribution the viewer shows (VIE-7): the produced file is derived from the
    public `parlament.hu` recording and the official transcript, and the UI states
    so. Subtitles carry the transcript verbatim.
  - **Brand watermark.** The export MAY overlay the site's logo (top-right corner)
    so a shared clip is attributable at a glance. It is **user-toggleable** and
    default-on; because compositing the logo requires re-encoding the picture, the
    UI notes that enabling it forgoes the fast stream-copy path (as burned-in
    subtitles do). When the toggle is off, the file is un-watermarked.
  - **Aspect ratio.** The export offers a **portrait (9:16)** option alongside the
    original **landscape (16:9)**, so a clip drops straight into vertical-video
    feeds (TikTok/Reels/Shorts). Portrait is a **centre crop** of the source (the
    speaker sits centre-frame, so the podium survives); like the watermark it
    re-encodes. When burned-in subtitles and portrait combine, the crop is applied
    **before** the subtitle render so captions wrap to the narrow frame rather than
    being drawn wide and clipped. Landscape is the default.
  - **Graceful degradation & disclosure.** Export is **progressively enhanced**:
    where the browser cannot run the exporter (feature-gated) the affordance is
    hidden, not broken. Long-running work (segment download, re-encode) shows
    **progress** and is **cancellable**, and any failure surfaces a clear,
    retryable error rather than a dead button. The heavy in-browser codec runtime
    is **loaded on demand** (only when a user actually exports), so it never
    weighs on normal viewing.

### 5.3 Sitting-day word cloud

- **WCLOUD-1 (SHOULD).** A **sitting day's page** (use case 2, the
  browse-a-sitting view) carries a **word-cloud visualization** summarizing what
  was talked about that day, sized so the words **most characteristic of that
  day** stand out. It gives a citizen an at-a-glance sense of a day's themes
  before reading the transcript.
- **WCLOUD-2.** The cloud is computed over the **sitting's sentence text**, with
  **Hungarian stop-words removed** (function words, pronouns, conjunctions, …)
  and very short tokens / pure numbers dropped, so it surfaces topical words, not
  grammatical filler. Procedural/chairing speeches (STAT-1) are **excluded** so
  the cloud reflects substantive debate, not the chair's "a következő
  felszólaló…" boilerplate. Words are ranked not by raw frequency but by
  **distinctiveness to the day** — a **TF·IDF** weighting against the rest of the
  electoral cycle, so words frequent on this day yet rare on the cycle's other
  sitting days rank high while the ubiquitous parliamentary vocabulary that
  recurs every day (and would otherwise dominate every day's cloud identically)
  is suppressed. Both sides of the TF·IDF are **precomputed aggregates** rebuilt
  with the other statistics (REP-7): the per-sitting term frequency *and* the
  per-cycle document frequency are derived from the lemmatized terms (WCLOUD-6) at
  load time, so the request just reads, scores and ranks them. The stop-word set
  (and the lemmatizer backend, POS filter and entity labels) are **configurable,
  not hard-coded** (OPS-4).
- **WCLOUD-6.** The unit of the cloud is a **lemma, not a surface form**. Because
  Hungarian is agglutinative, the same concept appears in many inflected forms
  (*törvény / törvényt / törvényben / törvények*); counting them separately would
  scatter a day's real theme and weaken the TF·IDF signal. The text is therefore
  **lemmatized with a HuSpaCy model** (the transformer, `hu_core_news_trf`, by
  default — offloaded to Modal GPU workers in production; a lighter CPU model
  can be configured) so inflected forms collapse to their dictionary lemma, and only
  topical parts of speech (nouns, proper nouns, adjectives) are kept. The model's
  **named-entity recognition** keeps multi-word entities — people, places,
  organisations (*"Orbán Viktor"*, *"Európai Unió"*) — together as a **single
  term** rather than splitting them into low-value fragments; entities are tagged
  so the visualization can mark them (WCLOUD-3). Because the neural pipeline is
  expensive, it runs **only at load time** and its per-sitting output is **cached
  on disk** keyed by a fingerprint of the transcript + method, so a rebuild
  reprocesses only the sittings whose text actually changed (cf. the scraper's
  detail cache) and the **request path never invokes the model**. When the model
  is **not installed the build degrades** to the dependency-free regex tokenizer
  (raw lowercased forms), so the word cloud still works on a minimal install.
  Because this neural pipeline is the one heavy build step, it MAY additionally be
  **offloaded to a managed GPU/CPU service** (e.g. Modal) so a resource-constrained
  host stays responsive (OPS-6): the offloaded workers run the **same** model and
  extraction logic — so their output, and the on-disk cache keyed on it, is
  **identical** to the local path and interchangeable with it — and, exactly as
  above, only the sittings whose text changed are ever sent, in **batches**, so the
  compute (and any metered cost) tracks actual new work.
  > **✅ realized.** A `modal` backend runs the identical `app.nlp` HuSpaCy pipeline
  > on Modal workers (model loaded once per worker, pool bounded, scales to zero);
  > selected via `PARLAMONITOR_WORDCLOUD_BACKEND=modal`, it degrades to local
  > HuSpaCy then regex when unconfigured.
- **WCLOUD-3.** In the visualization, word sizing alone must not be the only
  carrier of meaning (A11Y-1).
- **WCLOUD-4 (SHOULD).** A word in the cloud is a **link into proceedings search
  (SEA-6)** scoped to that sitting day (the query plus the day's date range), so a
  click moves from "what was discussed" to "where exactly it was said".
- **WCLOUD-5.** The cloud is served from a **dedicated, paginated-free aggregate
  endpoint** (a top-N word list) computed per sitting; it is a separate request
  from the transcript so it never slows the sitting-day load (cf. SEA-8). Like all
  derived views it states its **methodology** briefly (lemmatized, stop-words
  removed, TF·IDF-ranked) so the user knows what they are seeing (REP-5 / TRUST-1).

### 5.4 Sitting-day speaker toplist

- **TOPSPK-1 (SHOULD).** A **sitting day's page** (use case 2) carries a
  **toplist of the representatives who spoke the most that day**, so a citizen
  sees at a glance who dominated the day's debate before reading the transcript
  (a companion to the day's word cloud, §5.3).
- **TOPSPK-2.** "Spoke the most" is ranked by **total speaking time** on the day
  (the sum of the speaker's speech durations), with the **number of speeches**
  shown alongside. Only **statistics-eligible speeches count**: procedural /
  chairing speeches (STAT-1) are **excluded**, exactly as for the per-MP
  statistics and the word cloud, so the list reflects substantive debate, not the
  chair's turn-management. Only **known representatives** (resolved `person_id`)
  are listed; unattributed or guest speakers are omitted.
- **TOPSPK-3.** Each entry **links to the representative's profile** (REP-1) and
  shows the speaker's **faction** (colour/label). The list is a **top-N**
  (default ~10) so it stays a glanceable summary, not a second transcript.
- **TOPSPK-4.** In the visualization, bar length alone must not be the only
  carrier of meaning (A11Y-1).
- **TOPSPK-5.** The toplist is served from a **dedicated aggregate endpoint**
  computed per sitting; it is a **separate request** from the transcript so it
  never slows the sitting-day load (cf. WCLOUD-5 / SEA-8).

### 5.5 Sitting-day transcript reading

- **SITREAD-1 (SHOULD).** On a **sitting day's page** (use case 2), each speech in
  the agenda → speech list can be **expanded in place to read its transcript
  inline** — an accordion/spoiler that reveals the speech's text without leaving
  the page — while a **distinct affordance still opens the full video viewer**
  (§5.2) for that speech. Reading the text and watching the video are both
  reachable from the list; neither replaces the other. The text is fetched **on
  demand per speech** (a lightweight request, separate from the sitting-day load —
  cf. WCLOUD-5), so a long day of hundreds of speeches stays responsive. A
  video-only speech (no transcript, VIE-8) has nothing to reveal and shows only
  the viewer affordance.
- **SITREAD-2.** The inline transcript preserves the text's **original paragraph
  structure** (the source `<p>` paragraphs, per the sentence paragraph index of
  §3.1/§4.1): it renders as separate paragraphs, not one undifferentiated block,
  so it reads like the official record. Text is shown at full contrast on the
  page's normal surface (A11Y-1), never washed out.
- **SITREAD-3.** Expanding/collapsing a speech is **instant** — a spoiler toggle
  must not re-render or re-fetch the rest of the (potentially very long) list, and
  its text is cached once loaded so re-opening is immediate.

### 5.6 Sitting-day list & upcoming sittings

- **SIT-1 (SHOULD).** The **sittings list** (use case 2's entry point) shows both
  held sittings and **announced upcoming sittings** (`status = scheduled`, SCR-8),
  so a visitor sees that **another sitting is coming** rather than the list simply
  ending at the last processed day. An upcoming day is rendered **distinctly** —
  an "upcoming" marker instead of speech/duration counts (which it does not have
  yet) — and, sorted by date, it naturally leads the list. Opening one shows a
  clear "recording and transcript coming soon" state, not an empty transcript;
  once the sitting is held and ingested it becomes an ordinary `published` day.
  The list is scoped by the global cycle selector (§4A) like every other view.

---

## 6. Functional Requirements — Module: Representatives & Statistics

- **REP-1 (MUST).** A browsable, searchable **list of representatives**, filterable
  by faction and constituency, and scoped to the **electoral period** chosen in
  the global cycle selector (§4A); each links to a profile. The name search is
  **accent-insensitive** (§4B FOLD-1): `dora` matches *Dóra*.
- **REP-2 (MUST).** A **representative profile** shows: name, photo (if available),
  current/past faction(s) with dates, constituency, Wikidata link, a
  reverse-chronological **list of their speeches** (each linking into the viewer),
  and — when the Bills module (§6A) is enabled — a **list of the bills they
  submitted**, each linking to the bill (the reciprocal of BILL-3's sponsor links).
- **REP-3 (MUST).** Per-representative **statistics**, computed over the
  **statistics-eligible speeches only** (procedural/chairing speeches excluded
  per STAT-1), including at least:
  - total **speaking time** (sum of speech durations) and number of speeches;
  - speeches per sitting / over time (trend chart);
  - **number of bills submitted** — now provided by the Bills module (§6A). When
    that module is disabled (EXT-6) the metric is hidden, not faked. The headline
    count links to the bills filtered by that representative as sponsor.
  - **votes not cast** — on how many roll-call votes the representative cast no
    vote, **both nominally and as a percentage**, provided by the Votes module
    (§6B). It counts **every non-voting category** together — *„jelen, nem
    szavazott”* (`value_code = novote`), *„igazoltan távol”* (the upstream
    *„előre bejelentett hiányzó”*, `value_code = absent`, VOTE-3) and *„nem volt
    jelen”* (no roll-call record at all) — i.e. exactly the non-voting slices of
    the participation pie; the percentage is that count over the **same 100%
    base as the pie** (all roll-call votes the MP could have cast in scope,
    excluding time outside their mandate), so headline and chart always agree.
    The headline links to those roll calls (`value=missed`). Like the other
    metrics it is scoped to the global cycle (§4A). When the Votes module is
    disabled (EXT-6) the metric is hidden, not faked.
- **REP-4.** **Faction-level** aggregate statistics (totals and averages per MP),
  with each faction rendered in a consistent color. Like REP-3, these are
  computed over statistics-eligible speeches only (STAT-1). The faction
  speaking-time chart is **embeddable** (§4C).
- **REP-5.** Statistics MUST state their **time scope** (which period/date range)
  and **how they are computed** (a short methodology note), and be consistent with
  the underlying speech records a user can click through to verify.
- **REP-8 (SHOULD).** A representative's profile carries a **GitHub-style activity
  board** — a contribution-calendar heatmap that shows, at a glance, **how much
  the MP did on each day**. Each cell is a calendar day, laid out as week columns
  × weekday rows, coloured by the day's **total activity intensity**, so dense and
  quiet stretches are immediately visible. The activity counted per day combines
  the MP's **statistics-eligible speeches** (STAT-1 — procedural/chairing speeches
  excluded, exactly as everywhere else) and the **irományok they submitted that
  day** (bills + every other writing, via the Bills module). Hovering or focusing
  a day reveals its exact breakdown (number of speeches, number of documents). The
  board honours the **global cycle selector** (§4A): it covers the selected cycle's
  date span and re-scopes when the cycle changes. It degrades gracefully per EXT-6
  — when the Bills module is disabled the documents contribution is simply absent,
  never faked. Cell colour/intensity is never the only carrier of meaning
  (A11Y-1). It is served from a
  **dedicated aggregate endpoint** (REP-7) so it never blocks on live aggregation.
- **REP-7.** Statistics are served from **precomputed aggregates** (§4.2) and
  recomputed on each ingest; they must never block on live aggregation of the
  full corpus. The aggregates are built over statistics-eligible speeches only,
  per STAT-1.
- **REP-9 (SHOULD).** **Nationality advocates (*nemzetiségi szószólók*) are
  ingested as people in their own right.** Each of Hungary's thirteen recognised
  nationalities elects a *szószóló* who sits in the House, speaks in plenary and
  submits irományok, but holds **no representative mandate** — and who therefore
  never appears in the MP roster query the representatives registry is built from
  (§6/REP-1). Until they are scraped, they exist in the corpus only as the bare
  speaker stubs the transcripts create: a name with no profile.
  - The source is `parlament.hu`'s own **Szószólók** page
    (`/web/guest/szoszolok`, and the active-only `/web/guest/szoszolok-aktiv-`),
    i.e. the Felicitas `szoszolo-lista-query`, scraped **per electoral cycle** —
    the office exists from the 2014 cycle on, and each cycle's roster is its own
    closed set.
  - An advocate is the **same core `person` entity** as an MP, keyed by the same
    upstream person id (EXT-2), so their registry **enriches the speaker rows the
    proceedings already carry** — their speeches, statistics, word-cloud and
    toplist contributions need no re-scrape and no name matching. They are
    **marked** as advocates and carry the **nationality** they speak for, which
    stands in for the faction and constituency they do not have.
  - Advocates are presented as **their own page in the Representatives section's
    tab bar** (between the MP list and the factions page), not as a filter of the
    MP list — they hold a different mandate, and the MP list must keep meaning
    "representatives". The page has its own URL, so it is linkable and citable, and
    it honours the global cycle scope (§4A) like every other view. An
    advocate's profile (REP-2) shows their mandate + nationality where an MP's
    faction badge goes, and their speech/document statistics (REP-3) are computed
    exactly as an MP's; the **vote-participation metrics are omitted** (not
    zeroed), since an advocate has no vote to cast.
  - The registry is a **separate, additive source record** per cycle: adding it to
    an already-scraped corpus must not require re-running the MP roster stage or
    rebuilding the database — the loader's incremental path (ING-5) picks up the
    new files and extends the `person` schema in place.

- **STAT-1 (MUST).** **Procedural/chairing speeches are excluded from all
  representative and faction statistics** (speaking time, speech counts, trends —
  REP-3/REP-4/REP-7), but are **never dropped from storage or from the
  sitting-day viewer**. The signal is the upstream per-speech type
  (*felszólalás típusa*, §3.1/§4.1): a speech whose type is one of the presiding
  officer's own procedural utterances is marked `procedural` and omitted from the
  aggregates. Three families qualify:

  1. **running the sitting** — **`ülésvezetés`** (the chair's interjections
     calling the next speaker and keeping time), opening and closing the sitting
     day, adopting the agenda;
  2. **opening/closing a debate** — the `… vita megkezdve` / `… vitája lezárva`
     markers in all their per-document variants;
  3. **announcing a vote and its outcome** — `Országgyűlés határozatképes`,
     `önálló indítvány elfogadva`, `mentelmi jog felfüggesztve` and the like.

  Excluding families 2–3 matters as much as family 1: the media segment behind an
  announcement typically spans the entire voting block it concludes, so each one
  contributes not just a spurious speech but *hours* of phantom speaking time
  (corpus-wide, `Országgyűlés határozatképes` averages 114 minutes). Left in,
  they put the deputy speakers at the top of every speaking-time ranking.

  What is **not** excluded: MP-initiated points of order (`ügyrendi kérdés`,
  `ügyrendi javaslat`) are real interventions by that MP, and `jegyzői
  ismertetés` is a procedural reading but performed by the notaries, spread thin
  across many MPs.

  Such speeches remain fully searchable (§5.1) and are still shown in the
  proceedings viewer with their type label (VIE-1), so the record stays complete
  and the exclusion is transparent (TRUST-1). The types are **enumerated
  explicitly** rather than pattern-matched, so the list is auditable and can never
  silently swallow a substantive type; the sanity check is the speaker base, since
  every excluded type is spoken by at most 27 people (the presiding officers)
  while substantive types have 100–478. The set is **configurable, not
  hard-coded** (OPS-4), so a type parlament.hu introduces later can be added
  without code changes.

---

## 6A. Functional Requirements — Module: Bills (Irományok)

The first additive feature module beyond proceedings and representatives, built
to validate the module architecture (§7). It surfaces the *irományok* — the
parliamentary documents submitted to the Assembly — sourced from the Felicitas
`iromany` API. **Törvényjavaslatok** (bills, `fotipus = T`) are the flagship
case with their own page and the rich detail sheet (BILL-7); **every other
iromány type** (határozati javaslatok, interpellációk, kérdések, beszámolók, …)
is surfaced on a separate browse page (BILL-9) over the same data layer.

- **BILL-1 (MUST).** A **browsable, filterable list of bills**, paginated and
  filterable by **status**, **type**, **sponsor**, and **title text**; filters
  combine. List/filter state is in the **URL query** (deep-linkable, shareable) —
  including the sponsor filter so a link like `/bills?sponsor=<personID>` reopens
  the list scoped to that representative. The **electoral period** is set by the
  global cycle selector (§4A). The title-text filter is **accent-insensitive**
  (§4B FOLD-1).
- **BILL-2 (MUST).** A **bill detail** view shows the bill number, title, type,
  status, submission date, and its sponsors. It links to the **official bill
  text on parlament.hu** (LEGAL-1); the PDF is **embedded inline but loaded on
  demand** (revealed by a button), so it is never fetched unless the user asks.
  Where no text exists, the view degrades gracefully and falls back to the
  generic portal page. **It also surfaces the full upstream detail sheet
  (BILL-7)** so the page is not limited to the bare list row.
- **BILL-3 (MUST).** **Sponsorship is bidirectional through the shared `person`
  entity (EXT-2):** every MP sponsor on a bill links to that representative's
  profile, and (reciprocally, per REP-2) a representative's profile lists the
  bills they submitted. Government/committee submitters with no `personID` keep
  their display label but no link. A sponsor whose id is not a known MP is never
  materialized as a stub representative.
- **BILL-4 (SHOULD).** A bill's **status/history is shown as a legislative-stage
  timeline** (the official stage diagram: Tárgysorozatban → … → Kihirdetve),
  distinguishing **completed (past)** from **upcoming (future)** stages and
  marking the current position. Provenance is the upstream diagram (TRUST-1).
- **BILL-5 (MUST).** The module is a self-contained vertical slice per EXT-1..6:
  its own scraper stage, loader, `bill`/`bill_sponsor` tables, `/api/v1/bills`
  routes, and frontend views. Disabling it via `PARLAMONITOR_MODULES` removes its nav
  entry and routes, and hides REP-3's bills-submitted metric — no errors (EXT-6).
- **BILL-7 (MUST).** The detail view surfaces the bill's full upstream
  **adatlap** sheet (the same data parlament.hu shows), each fetched per bill
  from the Felicitas `iromany-adatlap` sub-queries and stored in dedicated child
  tables: the **legislative event history** (with the speech number and vote
  each event is tied to, and an **in-site link to that speech** — BILL-8),
  **committee events** (modifying proposals, reports),
  **votes** (igen/nem/tartózkodás with the result), **deadlines**, the
  **negotiating committees**, **justification & background documents**, the
  **non-self-standing (dependent) irományok** — the individual motions attached
  to the bill (amendments, committee reports, urgency motions, …), each surfaced
  as its own row with its iromány number, type, submission date, submitter(s)
  and a **downloadable PDF/text viewable from the bill page** (revealed on
  demand, like the bill's own text — BILL-2), alongside their per-type **summary
  counts** — and extra header fields (subtype,
  character, negotiation mode, promulgation/Magyar Közlöny number & date).
  **When a bill is *kihirdetve* (promulgated)** and carries a Magyar Közlöny
  issue number and promulgation date, the detail view links to the **official
  gazette issue** on `magyarkozlony.hu`. The scraper resolves the link from the
  issue-listing page (`?year=<year>&serial=<issue>`), preferring the **direct
  gazette PDF-viewer URL** (`/dokumentumok/<hash>/megtekintes`) extracted from
  that page and falling back to the listing page itself when the direct link
  can't be extracted (SCR-5). Event,
  committee-event **and motion-submitter** references to an MP link to that
  representative's profile through the shared `person` entity (EXT-2);
  committee/government submitters keep their label but no link (BILL-3). A bill scraped without detail (or
  an early-stage bill with empty sections) degrades to empty sections, never an
  error (SCR-5). Detail fetching is a separately-skippable scraper step
  (`--no-detail`) for a fast list-only refresh.
- **BILL-6 (scope).** v1 covers **all iromány types** for the current cycle —
  törvényjavaslatok (Felicitas `fotipus = T`) with the full per-bill detail
  sheet of BILL-7, plus every other type (BILL-9). The scraper fetches the whole
  cycle's irományok in one list query, tagging each with its `main_type` from the
  iromány-number prefix (e.g. `T/253` → `T`); the per-document detail sheet
  (BILL-7) is fetched for all types and degrades to empty sections where a type
  has none (SCR-5). (The per-bill vote here is the aggregate tally; the **per-MP
  roll-call** is provided by the Votes module, §6B, and each bill-detail vote
  links into it — VOTE-6.)
- **BILL-9 (MUST).** The non-bill irományok have their own **browsable,
  filterable list page** ("Egyéb irományok"), separate from the
  törvényjavaslatok page, paginated and filterable by
  **document type** (interpelláció, kérdés, határozati javaslat, …), **status**
  and **title text**; filters combine and live in the **URL query**
  (deep-linkable). The **electoral period** is set by the global cycle selector
  (§4A). The title-text filter is **accent-insensitive** (§4B FOLD-1). It shares
  the Bills module's data layer and `/api/v1/bills`
  routes (scoped by `main_type`: `= T` for bills, `!= T` for this page) and the
  **same detail view** (BILL-2/BILL-7) — only the browse page is distinct, so no
  data, table or detail logic is duplicated. Each MP submitter links to their
  profile through the shared `person` entity (EXT-2), exactly as on bills
  (BILL-3).
- **BILL-8 (SHOULD).** Where a bill event references a plenary **speech** (as
  parlament.hu's adatlap does), the event links **into this site's own speech
  viewer** (VIE-5), not out to parlament.hu. The link is resolved through the
  shared core data (EXT-2): each event carries the Felicitas speech UUID
  (`felszolalasId`), which is matched against the same UUID stored on the
  proceedings **speech** records (`speech_uuid`) to recover the viewer's
  `uid`. The match is by UUID, never by the displayed *felszólalás-szám* (which
  is not the speech index); one UUID may map to several speech rows (a speech
  spanning agenda items), in which case the first is linked. An event whose
  speech has not been ingested keeps the plain speech-number chip with no link
  (graceful degradation, SCR-5).

- **BILL-10 (SHOULD).** Where a bill's event history (BILL-7) brackets a plenary
  **debate** — a debate-opening event paired with its closing event, e.g.
  *általános vita megkezdve* … *általános vita lezárva* (also *összevont vita*) —
  the detail view surfaces a **debate panel**: the run of plenary **speeches
  between the two anchor speeches**, in proceedings order. Each bracket event is
  already tied to the plenary speech that announced it (BILL-8, via the shared
  speech UUID); the speeches in between are recovered by their global proceedings
  order (sitting date, then per-session speech index — so a debate adjourned and
  resumed on a later day still reads end to end). Each listed speech links **into
  this site's own viewer** (VIE-5) and each speaker who is a known MP links to
  their profile through the shared `person` entity (EXT-2); a speech with no
  transcript is still listed and flagged (VIE-8). Only debate kinds whose events
  actually resolve to plenary speeches are bracketed — committee-phase
  *részletes vita* events carry no speech link and produce no panel (graceful
  degradation, SCR-5). The panel is **derived at query time** from the stored
  events and proceedings speeches; it adds no new tables and no new scraping.

- **BILL-11 (SHOULD).** A **Kérdések** ("Questions") sub-page of the
  Törvényjavaslatok section visualizes the flow of parliamentary questions —
  *kérdés*, *interpelláció* and *azonnali kérdés* (Felicitas `main_type`
  A/I/K) — as a **Sankey diagram**: **who asked → who answered**. The **asker**
  side groups questions by the **asking MP's faction** (§4.1 colours carry
  through the ribbons); the **answerer** side is, for a question answered **in
  speech**, the **responding ministry** (the minister / state secretary named on
  the oral-answer event), and for a question answered **in writing** a single
  combined node (the upstream data records no responding ministry there), with a
  further node for questions still **unanswered**. Only the busiest ministries
  stay as their own node — the remainder pool into one "other ministry" node so
  the diagram stays glanceable; that pool can be **ungrouped** on demand
  ("Egyéb tárca szétbontása", `?all=1`), giving every responder its own node —
  a taller but complete answerer column, and the drill-down follows the same
  grouping so a clicked flow always lists exactly its questions.
  **Each flow (ribbon) is clickable** — selecting
  it lists the individual questions behind it at the bottom of the page,
  paginated and newest-first, each linking to its detail view and showing its
  submitter(s) (EXT-2); the ribbons and nodes are keyboard-focusable so the same
  drill-down works without a mouse. It **honours the global cycle selector** (§4A) and is **derived at
  query time** from the shared `bill` / `bill_event` / `bill_sponsor` data
  (EXT-2) — no new tables, no new scraping. Like every other visualization the
  diagram is **keyboard-navigable and screen-reader-labelled** (A11Y-1) and
  carries a short methodology note (TRUST-1). It is part of the Bills module's vertical slice
  (BILL-5): new `/api/v1/bills/questions/sankey` and `/api/v1/bills/questions/list`
  routes and a new frontend sub-tab, disabled with the rest of the module (EXT-6).
  The Sankey diagram is **embeddable** (§4C).

---

## 6B. Functional Requirements — Module: Votes (Szavazások)

The second additive feature module beyond proceedings and representatives,
layered on the same module architecture (§7) as Bills (§6A). It surfaces the
Assembly's **roll-call votes** (*szavazások*) and — the point of the module —
**who voted how**, sourced from the Felicitas `szavazas` API (provider
`szavazasok-query-provider`: `szavazas-lista-query` for the list, plus the
per-vote `szavazat-by-szavazas-and-tipus-list-query` roll call,
`szavazas-by-frakcio-stat-query` faction breakdown and `szavazas-alap-adatok-query`
header).

- **VOTE-1 (MUST).** A **browsable, filterable list of votes**, paginated and
  filterable by **result** (elfogadva/elutasítva/…) and
  **subject/iromány-number text**; filters combine. The subject/iromány-number
  filter is **accent-insensitive** (§4B FOLD-1). The **electoral period** is
  set by the global cycle selector (§4A). List/filter state is in the
  **URL query** (deep-linkable, shareable), including a `bill` scope so a link
  like `/votes?bill=<iromanyId>` reopens the list scoped to one bill's votes.
  Each row shows the datetime, subject, result, the igen/nem/tartózkodás tally
  (as a bar), and the bill(s) the vote decided. The list is **sortable** by
  datetime, by **attendance**, and by **cross-voting** (VOTE-9).
- **VOTE-2 (MUST).** A **vote detail** view shows the vote's datetime, voting
  mode, subject, result, the aggregate tally and the total votes cast, and the
  **bill(s) it decided** (BILL/iromány links, VOTE-5). It surfaces the
  **per-faction breakdown** (igen/nem/tartózkodás/nem szavazott/távol per
  faction, with defection counts) and the full **per-MP roll call**.
- **VOTE-3 (MUST).** The **per-MP roll call** is the signature feature: every
  representative's individual vote (Igen / Nem / Tartózkodás / Nem szavazott /
  Jelen, nem szavazott / Előre bejelentett hiányzó), grouped by vote value and
  colour-coded, **each MP linked to their profile** through the shared `person`
  entity (EXT-2). The join is by the Felicitas `kepviseloId`, never by name; an
  id that is not a known MP keeps its recorded name but no link (SCR-5). The raw
  Hungarian vote type is normalized to a stable code (`yes`/`no`/`abstain`/
  `novote`/`absent`) for counting, colouring and the a11y tally. A vote with no
  roll call (a list/voice vote) degrades to its header + tally with a clear "no
  roll call available" note (SCR-5); the per-MP flag is derived from whether
  records actually exist, not the unreliable upstream `hasKepviselo`.
- **VOTE-4 (MUST).** **Reciprocal link on the representative profile (REP-2 /
  EXT-2):** an MP's profile lists **how they voted**, reverse-chronologically,
  each entry showing their vote value and linking to the full vote. The section
  (and the backend `/representatives/{id}/votes` route) is hidden when the Votes
  module is disabled (EXT-6), never faked.
- **VOTE-5 (MUST).** **Vote ↔ bill is bidirectional through the shared data
  (EXT-2):** a vote's subject links to the bill it decided where that bill is one
  we hold (`fotipus = T`); an iromány type we don't hold (resolutions, motions)
  keeps its number/title with no link. The link is resolved at query time
  (`vote_subject.iromany_id` → `bill.id`), **not** a hard FK, so re-ingesting
  either module cannot break the other (EXT-1).
- **VOTE-6 (MUST).** **Bill detail ↔ vote is bidirectional:** a vote's `id` is
  the upstream `szavazasId` — the very key a bill's aggregate vote tally
  (`bill_vote.vote_id`, BILL-7) already carries — so each vote on a bill's detail
  sheet links **into this site's own vote page** (the full roll call), with no
  new join key. A bill vote whose szavazás has not been ingested keeps the plain
  tally with no link (graceful degradation, SCR-5). Each ingested vote on that
  sheet also carries the **same derived figures the vote list shows** —
  attendance and cross-voting with its per-faction breakdown (VOTE-9) — computed
  from one shared aggregation, so the two views can never disagree.
- **VOTE-7 (MUST).** The module is a self-contained vertical slice per EXT-1..6:
  its own scraper stage (`votes-<cycle>.json`), loader, `vote` / `vote_subject` /
  `vote_record` / `vote_faction_stat` tables, `/api/v1/votes` routes, and
  frontend views. Disabling it via `PARLAMONITOR_MODULES` removes its nav entry
  and routes and hides the profile's votes section — no errors (EXT-6). Detail
  fetching (roll call + faction breakdown) is a separately-skippable scraper step
  (`--no-detail`) for a fast list-only refresh.
- **VOTE-8 (scope).** v1 covers the current cycle's votes with their per-MP roll
  call, per-faction breakdown and bill links, plus a **party-cohesion analysis**
  (*Frakcióelemzés*, a Votes sub-tab): computed house-wide over the cycle's
  roll-call set, it shows how factions vote together three ways — an **agreement
  matrix**, cohesion/alignment **bars**, and an MDS **bloc map** — each meeting
  the site's accessibility bar (A11Y-1) and carrying a methodology note (TRUST-1). This
  faction vote analysis is **embeddable** (§4C). Also shipped: the **per-MP
  vote-absence count and percentage** on the representative profile (REP-3), whose
  roll-call **participation breakdown** is likewise embeddable (§4C). Remaining
  future work: **the hemicycle seating chart** (the Felicitas
  `szavazas-patko-query` returns per-seat SVG geometry + each MP's vote — out of
  scope for v1) and further vote-based statistics (e.g. per-MP defection rates —
  the *per-vote* figure ships as VOTE-9).
- **VOTE-9 (MUST).** Every listed vote carries its **cross-voting**: how many MPs
  voted against their own faction's position, as a count and as a share of the
  votes cast, broken down by the factions that split — and the list can be
  **ordered by it**, so the divisions where party discipline broke down are one
  click away. The number **MUST** be the Assembly's own per-faction *„frakcióval
  szemben”* figure summed over the real factions, never derived from the tallies:
  a faction's line is its *declared* position, so a minority-against-majority
  reconstruction reproduces the official figure for only ~82% of non-zero faction
  rows. For the same reason the roll call **MUST NOT** mark any individual MP as
  having crossed — the source does not say who (TRUST-1). A presence check
  (*Jelenlét megállapítás*) puts no question to the House, so it carries **no**
  cross-voting number rather than a misleading one. The vote detail shows the
  per-faction figure it is summed from, with a methodology note (TRUST-1).

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

> **Acceptance probe for extensibility — ✅ realized.** The Bills module (§6A)
> was added as purely additive changes: a new scraper stage and `bills-<cycle>.json`
> output, `bill` + `bill_sponsor` tables, `/api/v1/bills` routes, Bills browser +
> detail views, and the now-unhidden "bills submitted" stat on profiles — with no
> schema changes to existing modules. The per-bill detail sheet (BILL-7) was then
> layered on the same way — extra `bill` columns plus `bill_event`,
> `bill_committee_event`, `bill_vote`, `bill_deadline`, `bill_committee`,
> `bill_document` and `bill_motion_summary` child tables, an expanded
> `/api/v1/bills/{id}`, and richer detail-view sections — again touching no other
> module. The non-self-standing-motions list (with per-motion PDFs and
> MP-linked submitters) was layered on the same way — `bill_motion` +
> `bill_motion_sponsor` child tables, a `motions` array on the detail API, and a
> motions section in the detail view — once more touching no other module.
> The **debate-speeches panel** (BILL-10) was lighter still — *no new tables and
> no new scraping at all*: it is derived at query time by pairing the stored
> debate-opening/closing events and selecting the proceedings speeches between
> their two anchor speeches, joined to the shared `person`/`session` entities
> (EXT-2). A new `debates` array on `/api/v1/bills/{id}` and a panel in the
> detail view were the only additions.
> The **Kérdések Sankey** (BILL-11) was in the same spirit — *no new tables and
> no new scraping*: a new `/api/v1/bills/questions/sankey` aggregate derives the
> asker-faction → answerer flows at query time from the stored
> `bill`/`bill_event`/`bill_sponsor` rows, and a new "Kérdések" sub-tab renders
> it with a dependency-free SVG Sankey component — touching no other module.
> Extending coverage to **all iromány types** (BILL-9) was lighter still — *no
> new tables at all*: the scraper now fetches every `fotipus` into the existing
> `bill` tables (tagging each row's `main_type` from its number prefix), the
> `/api/v1/bills` list gained `main_type`/`main_type_not` filters, and a new
> "Egyéb irományok" browse page reuses the existing bill detail view. The bills
> page scopes itself to `main_type = T`, the new page to `!= T`.
>
> The **Votes module (§6B)** is the second worked example, added the same purely
> additive way: a new scraper stage and `votes-<cycle>.json` output, `vote` +
> `vote_subject` + `vote_record` + `vote_faction_stat` tables, `/api/v1/votes`
> routes, Votes browser + detail (roll-call) views, the reciprocal votes section
> on the MP profile, and bidirectional bill ↔ vote links — with **no schema
> changes to existing modules**. Notably the two cross-module links (vote subject
> → bill, bill vote → vote) are resolved at query time through shared keys
> (`iromany_id`/`szavazasId`), not hard foreign keys, so the modules stay
> decoupled and either can be re-ingested independently (EXT-1). These two
> modules stand as the worked examples for the remaining domains (committees,
> interpellations).

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
- **PRIV-2.** **Search analytics** (the one concrete analytics under PRIV-1). The
  backend logs what people search for — the search **keyword** and the **filters**
  combined with it (§SEA-3: date range, speaker, faction, agenda type, sort, and a
  zero-result flag) — to guide coverage and search improvements. It is **anonymous
  and aggregated by design**: no IP address, user agent, cookie or session
  identifier is read or stored, and **no exact timestamp** is kept — events are
  counted into **whole-hour buckets** (UTC), so only a per-hour **count** per
  `(keyword, filters)` tuple is persisted, never a per-request row that could be
  correlated to a person. Only the canonical `/search` request is counted (not the
  trend/breakdown/suggest calls the SPA fires for the same query). Data is written
  to a **separate SQLite file** — never the read-only content DB — flushed hourly
  and mounted on a **host-accessible** volume so the aggregates can be inspected or
  exported from outside the container. Enabled by default, switchable off by config
  (OPS-4), and **disclosed in the privacy notice** (PRIV-1).
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
- **OPS-5.** The reference deployment is **Docker Compose** built from one image:
  a one-shot **DB builder** (so the possibly-long first build never blocks the
  API's healthcheck), the stateless **API**, and an optional **continuous-sync
  sidecar** (SCR-7) that keeps the data current via the incremental,
  **zero-downtime** loader update (ING-5). The sync can instead be driven by an
  **external cron** (the same one-shot), with an `flock` guard so overlapping runs
  skip. Routine updates never require a restart or a full rebuild.
  > **✅ realized.** `docker-compose.yml` runs `init` → `app` + `sync`; `sync`
  > loops `parlamonitor sync` then `app.loader --update` on a configurable interval
  > and the running API picks up each atomic swap on its next request.
- **OPS-6.** The expensive word-cloud NLP (WCLOUD-6) MAY be **offloaded to a
  managed GPU/CPU service** (e.g. Modal), selected and authenticated purely via
  environment (OPS-4). It **degrades gracefully** to the local model, then the
  regex tokenizer, when unconfigured; metered cost is bounded because only the
  changed sittings are sent, batched, to a capped worker pool that scales to zero
  when idle. The **timing-stage ASR** (whisper-large-v3-turbo, §3.4 TIM-1/TIM-5)
  uses the **same offload pattern** on a separate GPU app: only cache-miss sittings
  are sent, batched and run in parallel across a capped, scale-to-zero pool, and it
  degrades to a local model then to the positional estimate when unconfigured.

---

## 9. Suggested Technology Stack (non-binding)

- **Scrapers / pipeline:** a Python scraping pipeline owned by this project,
  ported/adapted from the `OpenParliamentTV-Tools` HU reference and run on a
  schedule. Staged, idempotent, lockfile-guarded.
- **Database:** SQLite + FTS5 (Hungarian tokenization). Atomic file swap on load.
- **Backend:** Python + FastAPI, read-only SQLite, OpenAPI docs.
- **Frontend:** SPA (React or Vue) with a client-side router and lazily-loaded
  feature modules; **hls.js** for HLS video; a charting lib for statistics.
- **Deploy:** Docker Compose from one image — a one-shot DB builder, the
  containerized API, and a continuous-sync sidecar (or external cron) that keeps
  the DB current via incremental, zero-downtime atomic swaps (OPS-5). The heavy
  word-cloud NLP can be offloaded to a managed GPU/CPU service such as Modal
  (OPS-6). Scheduled jobs via cron/systemd.

---

## 10. Out of Scope / Future

- User accounts, saved searches, alerts/notifications on topics or speakers.
- **Bills** are implemented as the first additive module (§6A), including the
  full per-bill detail sheet (BILL-7: event history, aggregate votes, committee
  timelines, deadlines, documents, motions). The module now covers **all iromány
  types** — törvényjavaslatok on their own page and every other type on the
  "Egyéb irományok" page (BILL-9) — so document-level coverage of irományok is
  complete. **Votes** are now implemented as the
  second additive module (§6B): the roll-call list, per-MP breakdown, per-faction
  breakdown, and bidirectional links to bills and representatives. **Committees**
  and a richer dedicated **interpellations** module (linking interpelláció →
  answer → debate speech, beyond the document-level listing already provided by
  §6A) remain planned future **modules** (§7); the architecture
  already accommodates them, with Bills and Votes as the worked examples.
  Within Votes, the **party-cohesion analysis** has since shipped (VOTE-8,
  *Frakcióelemzés*); the **hemicycle seating chart** and further vote-based
  statistics (defection rates) remain future work (VOTE-8).
- **Precise sentence ↔ video sync — ✅ realized (§3.4).** The originally-planned
  progression of the swappable timing stage (TIM-4) is now shipped:
  1. **Real per-speech offsets** via the Felicitas per-speech video query
     (`ulesnapok-aktusok-query` → per-speech `offset1`/`offset2`) give
     second-precise speech boundaries (`felicitas-speech-offset`); they are now
     the positional-timing fallback.
  2. **Forced alignment** with whisper-large-v3-turbo is now the **default**
     (TIM-1): the ASR-timed transcript is aligned to the official text at the
     sentence level for word-accurate timing. The audio/ASR toolchain
     (`ffmpeg` + Whisper) is offloaded to a managed GPU service and degrades to
     the positional estimate, so the host stays light (SCR-6/TIM-5).
- Entity recognition/linking (NER/NEL) — tagging in-transcript entities and
  linking representatives/factions to Wikidata. Deferred with its external
  dependencies (entity-fishing endpoint, HuSpaCy model); the data shape reserves
  room for it (entity table, `people[].wid`).
- Whisper is used for **forced-alignment timing** (§3.4 TIM-1, realized), but
  using ASR to **replace** the official transcript text, or **speaker
  diarization**, remain out of scope — the displayed/searched text stays the
  authoritative `parlament.hu` record.
- Cross-parliament comparison or non-HU parliaments.
- AI summarization/Q&A over proceedings (possible later module).

---

## 11. Open Questions

1. **Bills/votes data:** ~~confirm the source endpoints for `irományok`/`szavazások`~~
   — **resolved.** The Felicitas `iromany` API (provider `iromanyok-query-provider`,
   plus the `iromany-adatlap` sub-queries, BILL-7) backs the Bills module (§6A),
   and REP-3's "bills submitted" reads from it. The Felicitas `szavazas` API
   (provider `szavazasok-query-provider`: `szavazas-lista-query` +
   `szavazat-by-szavazas-and-tipus-list-query` per-MP roll call +
   `szavazas-by-frakcio-stat-query` faction breakdown) backs the **Votes module
   (§6B)** — the per-MP roll-call is implemented and links to representatives and
   bills both ways.
2. **Historical backfill scope:** ship current cycle (43) first, then backfill
   37–42, or backfill all up front?
3. **Entity enrichment:** NER/NEL is deferred (§10). For v1, do we still want a
   lightweight Wikidata-based enrichment of representatives (photos, bios,
   constituencies) — independent of in-transcript NER — or ship with only the
   names/factions the scraper yields?
4. **Hosting & domain:** target infrastructure and expected traffic, to size
   PERF budgets and caching.
