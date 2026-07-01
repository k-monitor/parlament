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
    `debug` block (`confidence`, `align-method`, the upstream per-speech type
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
- **speech** — `originID`, Felicitas speech UUID (`speech_uuid`, for
  cross-module links such as BILL-8), session, agenda item, order, speaker
  reference, speaker status/context (e.g. `main-speaker`, chair), the upstream
  **per-speech type** (*felszólalás típusa*, `felszolalas_tipus`, e.g.
  `napirend előtti felszólalás`, `ülésvezetés`), a derived boolean
  **`procedural`** flag (true for chairing/session-management speeches that are
  excluded from statistics — STAT-1), media reference, start/end, confidence,
  align-method.
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

## 4A. Cross-cutting: Global Electoral-Cycle Scope

The corpus spans many electoral cycles (§PERF-3). Rather than each module
carrying its own electoral-period filter, the site exposes **one global cycle
selector** that scopes every period-aware view at once.

- **CYC-1 (MUST).** A **cycle selector lives in the site header** and is visible
  on every page. It lists each electoral period plus an **"all cycles"** option,
  and **defaults to the latest cycle** on a first visit. Each period is labelled
  by its **start–end years** (e.g. `2018–2024`), not its ordinal cycle number,
  since the year span is what users recognise; an ongoing cycle (no end date yet)
  shows its start year with a trailing dash (`2026–`).
- **CYC-2 (MUST).** The chosen cycle is the **single source of period scope**
  across all period-aware modules — proceedings search, sittings, representatives,
  bills, other irományok and votes all honour it. Selecting a cycle (or "all")
  **immediately re-scopes the current view** and carries over as the user
  navigates between modules; modules no longer present their own redundant
  period filter.
- **CYC-3 (MUST).** The selection is **persisted** (e.g. `localStorage`) so it
  survives reloads and return visits; an invalid/stale saved value falls back to
  the latest cycle.
- **CYC-4.** "All cycles" simply **drops the period constraint** site-wide
  (no period parameter is sent); a numeric cycle constrains every query to that
  electoral period.
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
  surrounding snippet, speaker (linked to profile), faction, date, agenda title,
  and a thumbnail/affordance to play.
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
  result list. Quiet periods render as zero, not as gaps.
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
  **lemmatized with a HuSpaCy model** (the smallest, `hu_core_news_md`, by
  default) so inflected forms collapse to their dictionary lemma, and only
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
  - **vote absences** — how many roll-call votes the representative was absent
    from, **both nominally and as a percentage**, provided by the Votes module
    (§6B). The absence signal is the upstream *„előre bejelentett hiányzó”* vote
    value (normalized `value_code = absent`, VOTE-3); the percentage is that
    count over **all roll-call votes the MP could have cast in scope** (every
    vote they have a roll-call record for, present or not). Like the other
    metrics it is scoped to the global cycle (§4A). When the Votes module is
    disabled (EXT-6) the metric is hidden, not faked.
- **REP-4.** **Faction-level** aggregate statistics (totals and averages per MP),
  with each faction rendered in a consistent color. Like REP-3, these are
  computed over statistics-eligible speeches only (STAT-1).
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

- **STAT-1 (MUST).** **Procedural/chairing speeches are excluded from all
  representative and faction statistics** (speaking time, speech counts, trends —
  REP-3/REP-4/REP-7), but are **never dropped from storage or from the
  sitting-day viewer**. The signal is the upstream per-speech type
  (*felszólalás típusa*, §3.1/§4.1): a speech whose type is a session-management
  type — primarily **`ülésvezetés`**, the chair's procedural interjections
  (calling the next speaker, timekeeping) that would otherwise inflate the
  presiding officer's totals — is marked `procedural` and omitted from the
  aggregates. Such speeches remain fully searchable (§5.1) and are still shown
  in the proceedings viewer with their type label (VIE-1), so the record stays
  complete and the exclusion is transparent (TRUST-1). The set of
  statistics-excluded types is **configurable, not hard-coded** (OPS-4), so
  related chairing/ügyrendi types can be added without code changes.

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
  the diagram stays glanceable. **Each flow (ribbon) is clickable** — selecting
  it lists the individual questions behind it at the bottom of the page,
  paginated and newest-first, each linking to its detail view and showing its
  submitter(s) (EXT-2); the accessible table fallback offers the same drill-down
  by keyboard. It **honours the global cycle selector** (§4A) and is **derived at
  query time** from the shared `bill` / `bill_event` / `bill_sponsor` data
  (EXT-2) — no new tables, no new scraping. Like every other visualization the
  diagram carries an **accessible table fallback** (A11Y-1) and a short
  methodology note (TRUST-1). It is part of the Bills module's vertical slice
  (BILL-5): new `/api/v1/bills/questions/sankey` and `/api/v1/bills/questions/list`
  routes and a new frontend sub-tab, disabled with the rest of the module (EXT-6).

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
  (as a bar), and the bill(s) the vote decided.
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
  tally with no link (graceful degradation, SCR-5).
- **VOTE-7 (MUST).** The module is a self-contained vertical slice per EXT-1..6:
  its own scraper stage (`votes-<cycle>.json`), loader, `vote` / `vote_subject` /
  `vote_record` / `vote_faction_stat` tables, `/api/v1/votes` routes, and
  frontend views. Disabling it via `PARLAMONITOR_MODULES` removes its nav entry
  and routes and hides the profile's votes section — no errors (EXT-6). Detail
  fetching (roll call + faction breakdown) is a separately-skippable scraper step
  (`--no-detail`) for a fast list-only refresh.
- **VOTE-8 (scope).** v1 covers the current cycle's votes with their per-MP roll
  call, per-faction breakdown and bill links. Remaining future work: **the
  hemicycle seating chart** (the Felicitas `szavazas-patko-query` returns per-seat
  SVG geometry + each MP's vote — out of scope for v1), and the remaining
  **vote-based statistics** (party cohesion, defection rates). A first such
  statistic is already shipped: the **per-MP vote-absence count and percentage**
  on the representative profile (REP-3).

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
  Within Votes, the **hemicycle seating chart** and **vote-based statistics**
  (cohesion, attendance, defection rates) remain future work (VOTE-8).
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
