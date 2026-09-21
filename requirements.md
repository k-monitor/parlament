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
  deployment config, not hard-coded. Politeness also governs how a run reacts to
  being **rate-limited**: `parlament.hu` answers a client it considers too eager
  with a **CAPTCHA challenge page** (HTTP 200 + HTML) in place of data, and that
  MUST NOT be retried like a transient error. A walled request stands off for
  **ten minutes** and tries once more; after a configurable number of such
  fruitless stand-offs (default 9, so ~1.5 h) the run is **abandoned** with a
  distinct exit status rather than left knocking, and the next scheduled run
  resumes where it stopped (SCR-1/SCR-2).
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
  - An announced sitting can also be **cancelled**: the day vanishes from the
    source's day list, and the **ülésnap number** it held moves to the day
    announced in its place. Ingestion MUST follow that too — **remove** the day
    that is gone (it will never be held, and nothing else in the pipeline ever
    removes a sitting) and scrape the day that inherited its number, which the
    session-key stability guard would otherwise refuse while the cancelled day
    still held the key. A day that already has **speeches** is never removed on
    this signal: an existing record disappearing from a listing is an upstream
    glitch, not a cancellation.
  > **✅ realized.** The sync's proceedings pass carries a per-day `has_text` signal
  > in `sync-state.json`; a day without it is re-listed + text-probed each poll
  > until its jegyzőkönyv lands, then it goes quiet. `scrape_day` returns a
  > placeholder bundle (no speeches) instead of `None`, and `transform_day` stamps
  > `meta.status` (`scheduled` / `published`). The batch `download_period` self-heals
  > the same way (re-scrapes a stored day still `_awaiting_content`).
  > `prune_cancelled` (run before each download/sync pass) deletes the raw +
  > processed files of a day no longer listed in the window that was actually
  > queried, freeing its number in the same pass; the loader drops the DB row whose
  > processed file is gone (ING-5).

- **NR-1 (SHOULD).** Everything the API exposes is a record of what the House
  **has done**. What it is **about to do** is published only as human documents
  hanging off one portal page, `/web/guest/aktualis`: the **napirend** (order
  paper) for the next sitting, the month's **ülésterv**, the submission
  deadlines, the legislative programme, and the date of the House Committee's
  next meeting. The scraper SHOULD read that page, and SHOULD mirror and parse
  the napirend behind it (NR-2), because the sitting a visitor most wants to
  know about is the one that has not happened yet (SIT-1).
  The page's markup is **disposable** — it is CMS-generated, restyled at will,
  and the block carrying the House Committee's meeting is a repurposed *year
  navigator* whose links still carry `data-year` attributes that are not years.
  So the parse MUST key on what is stable: the **shape of the href**
  (`/documents/d/guest/<slug>`, whose slug says both what the document is and
  which sitting it belongs to — `nr_20260914_elfogadott`) and the **Hungarian
  labels** the House writes in front of its own values. A CSS selector would not
  survive the next reskin.
  The pass MUST be **incremental** (SCR-2): the House issues a new napirend
  under a new slug rather than editing one in place, so an unchanged slug means
  an unchanged document and the PDF is not fetched again — an idle poll costs
  one HTML request. It MUST run on **every** sync pass and on no slower cadence
  of its own: what it carries is a *schedule*, and a napirend picked up the day
  after the sitting was never published at all as far as the site is concerned.
  Failure past the page fetch MUST degrade, never abort (SCR-5/SCR-6): a missing
  `pdftotext`, a 404 on the document, an unparseable PDF — each leaves the
  agenda empty with a recorded reason, and the page's own findings are still
  written.
  > **✅ realized.** `parlamonitor/aktualis/` (`page` + `nr` + `scrape`) writes
  > `processed/aktualis.json`; `python -m parlamonitor aktualis <data_dir>`, and
  > a `_sync_aktualis` pass inside `run_sync` (`--skip-aktualis` to opt out).

- **NR-2 (SHOULD).** The napirend is a **PDF**, and it is the only place its
  contents exist. Parsing it SHOULD yield the sitting's structure rather than a
  blob of text: per sitting **day** its timetable (start, earliest decision
  times, expected end, breaks), and per **agenda item** its ordinal, its
  `B./n` cross-reference, the **iromány number** it concerns, its title,
  submitter, debate stage, time window, the House's own notes, and the
  procedural properties those notes state (two-thirds, cardinal, exceptional
  procedure, urgent debate, nationality/EU item, secret ballot). Where the
  document carries a detail sheet (part **B**), its fields — submission date,
  committees, amendment deadlines — SHOULD be joined onto the item the `B./n`
  identifies.
  Extraction MUST preserve the PDF's **columns** (`pdftotext -layout`), unlike
  the iromány document mirror (DOC-1) which deliberately does not: the order
  paper's meaning *is* its layout — an item's ordinal, its cross-reference and
  its iromány number form a left **gutter**, and in reading order they
  interleave with the title text they belong to. The gutter MUST be peeled off
  by what it is (a marker followed by the layout's own column gap) rather than
  by a fixed column, because the columns move between documents and a page break
  dedents a wrapped line to column 0.
  Two numbers per item look alike and are not: the **ordinal** is the item's
  place within that sitting day, the **`B./n`** identifies it across the whole
  sitting — one bill debated and voted on the same day is two ordinals under one
  reference, and the listing's own numbering restarts on the second day. A
  document with **no numbered items at all** — a purely ceremonial sitting, a
  government being sworn in — MUST parse to its days with empty item lists,
  which is the truth about that sitting and not a failure (SCR-5).
  > **✅ realized.** `parlamonitor/aktualis/nr.py`; tested against a verbatim
  > capture of a real order paper in `scraper/tests/test_aktualis.py`.

- **DOC-1 (SHOULD).** The iromány scrape (§6A) records where each document
  *is* — the iromány's own text link, each non-self-standing motion's, and the
  justification/background files on its detail sheet — but holds none of the
  documents themselves, which is enough to **cite** a document and not enough to
  **analyse** one. For NLP over the legislative text, the scraper SHOULD be able
  to mirror those files and extract their text, and **what it retains MUST be
  deployment config** (OPS-4), defaulting to **retaining nothing**: the corpus is
  large (cycle 43 alone is 861 documents / 868 MB of PDF) and a live deployment
  has no use for it, so turning it on is a dev/analysis choice, never the
  default. Storing the **extracted text alone** is the intended opt-in — it is
  0.5% of the PDFs' size (4 MB per cycle, xz-compressed) and is the only part any
  NLP pass reads; compressing the **PDFs** is explicitly *not* worth doing, as
  their streams are already deflated and every general-purpose compressor lands
  near 82% of the original (a lossless structural rebuild does worse; only lossy
  image re-encoding beats it, at the cost of the archival copy). Extraction MAY
  depend on an external tool, whose absence MUST degrade rather than fail
  (SCR-6), and a document that yields no text (an image-only scan — 8 of cycle
  43's 861) MUST be recorded as such rather than retried as a failure (SCR-5). The
  mirror MUST be **incremental** (SCR-2): published documents are static, so a
  repeat pass costs no requests.
  > **✅ realized.** `parlamonitor documents --cycle <n> --documents text` (and
  > the same knobs on `sync`); `PARLAMONITOR_DOCUMENTS` = `off` (default) /
  > `text` / `pdf` / `all`, `…_COMPRESSION` = `xz` (default) / `gzip` / `none`,
  > `…_MAX_MB` caps a single document while streaming. The store is
  > `data/documents/<cycle>/{text,pdf}/` plus an `index.json` manifest carrying
  > each document's source URL, owning bill, kind, SHA-256, page count and
  > artefact paths. Text comes from `pdftotext` (poppler); an unrecognised
  > retention value reads as `off`, so a typo can never turn the storage on.
  > The **continuous sync** runs the same stage, and is the one caller that is
  > *paced*: switching the mirror on there faces the whole cycle's backlog
  > rather than the documents added since the last poll, so a pass fetches at
  > most `PARLAMONITOR_DOCUMENTS_PER_SYNC` (default 25, `0` = uncapped) new
  > documents and the manifest reports the remainder as `pending`, which
  > counts down over the passes that follow. Incrementality is what makes that
  > safe: stopping early costs the next pass nothing.

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
  > exists yet. Reconciling includes **deletions**: a sitting whose processed file
  > the scraper has removed (a cancelled announced day, SCR-8) is dropped with its
  > derived rows, so the update is not merely additive. This is the update half of
  > the continuous sync (SCR-7 / OPS-5).

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
  A day whose speeches `parlament.hu` has **not linked to any act yet** (the case
  on a freshly-held sitting, whose speeches all arrive from the flat day roster —
  SCR-8) gets **one unnamed, unclassified item holding the whole day in join
  order**, i.e. exactly the single chronological listing the source itself shows;
  the UI names that section generically. The per-speech **type is never used as a
  stand-in for the missing act**: it is not one, and keying sections on it splits a
  day into pseudo-sections that destroy its chronology (all of a chair's turns
  collected at the top of the page, plus an untitled bucket for the type-less
  rows). A later re-scrape, once the acts are published, rebuilds the day with its
  real sections (ING-4).
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
  who spoke). One person can be both across different cycles. The Wikidata join
  also carries a **date of birth** (P569, day-precision statements only) and the
  two astrological signs derived from it at scrape time — the **sun sign** and the
  **Chinese zodiac animal** (which turns over at the lunar new year, so it needs a
  tabulated one). The **signs** are surfaced (REP-16); the **date** stays stored and
  unserved — it is the input they are derived from, not a field a page publishes.
- **faction / party** — label, optional Wikidata id, color (for charts).
- **membership** — person ↔ faction ↔ period (factions change over time).
- **mandate** — person ↔ period: the term they held a seat for (start/end), whether
  it **ended early** and the upstream reason it did, the constituency or list it was
  won on, and the people on either side of the handover — the MP they replaced and
  the one who replaced them (REP-14). One row per person per cycle; it is what makes
  a cycle's roster a history rather than a snapshot.
- **entity** (optional, from NER stage) — in-transcript entities and their
  sentence offsets. **Every** NER label the model emits is stored (person,
  organisation, place, misc), so the corpus carries a complete entity layer for
  later analysis; only persons and organisations are resolved to a destination
  (Wikidata/K-Monitor/MP profile) and rendered as inline links.

### 4.2 Derived / statistics tables

- Precomputed aggregates per representative and per faction (speaking time,
  speech counts, etc.) so profile/statistics pages are fast. Rebuilt by the
  loader. (See §6.)
- Precomputed **per-speech language metrics** (readability, lexical diversity —
  §5.7) plus the **corpus cut points** they are banded against, so the request
  path only reads them. A speech that is not measurable simply has no row: the
  absence means "not measurable", never zero.

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
- **CYC-6.** The scope is mirrored into the URL as `?cycle=` **only when it is
  not the default** (the latest cycle): a reader browsing at the default keeps
  clean, parameter-free addresses, and a link shared from a non-default scope
  still reproduces it. This is a hard indexing requirement, not a cosmetic one —
  writing the param onto every address made every clean URL a client-side
  redirect to a duplicate of itself, and the site went unindexed (§SEO-2). A
  `?cycle=` that is already in the URL is honoured and left alone, whatever it
  says.
- **CYC-7 (SHOULD).** A deployment MAY be run as a **window onto part of the
  corpus** — `PARLAMONITOR_SITE_CYCLES` (OPS-4), empty by default, meaning every
  cycle in the DB. Named cycles ("43", "42,43") make the site behave as though
  only those exist: the CYC-1 chooser offers no others, every period-aware query
  is clamped to them, the homepage totals count only them, a by-id page
  (sitting, felszólalás, iromány, szavazás) outside them answers **404** — for
  the API, the share card (§SEO-5) and therefore the SPA alike — and the
  sitemaps stop advertising those pages, so a crawler is never handed a URL the
  site refuses.
  - The clamp **narrows, never widens**: a hand-written `?period=` naming an
    unserved cycle, and the "all cycles" selection alike, are answered over the
    window rather than over the corpus or as an empty result.
  - It is a **serving-time** window, not a build-time one: the DB keeps every
    cycle it was loaded with, so widening or lifting it is a restart, not a
    rebuild. (Distinct from `PARLAMONITOR_MODAL_CYCLES`, a spend guard on the NLP
    offload, and from the scraper's own cycle scope.)
  - **Profiles stay readable** whichever window is set, matching REP-2's standing
    exception that a biography is not cycle-scoped; what the window governs there
    is their *statistics* (already cycle-scoped) and whether the profile is
    advertised in the sitemap at all.
  - The **constituency lookup** (REP-10) keeps answering for the cycle its
    election data elects, the same standing exception to CYC-2 it already is — it
    states that cycle on the page. Windowing to a cycle *other* than the one the
    map describes is therefore the one place the window does not reach;
    `PARLAMONITOR_EVK_LOOKUP=0` turns the lookup off outright.

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
  title/number filter (BILL-1), the **other irományok** title/number filter (BILL-9),
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

## 4D. Cross-cutting: Returning to Where You Were

Every browsing flow on this site is *list → detail → back to the list*: a sitting
day's speeches, search results, the sittings list, a representative's speeches.
The lists are long — a single sitting day runs to hundreds of speeches — so
losing one's place on the way back means scrolling it all again by hand, and that
is where a browsing session ends. Restoring the reading position is therefore a
site-wide behaviour, not a per-view nicety.

- **BACK-1 (SHOULD).** Coming **back to a page the reader has already scrolled**
  returns them to **the position they left it at**, not to the top. This holds for
  the browser's own Back **and** for the site's in-page "back" affordances (the
  viewer's "‹ back to the sitting day", a day's "‹ Ülésnapok"), which are ordinary
  forward navigations and so carry no saved position of their own. Going back up
  **several levels** (speech → sitting day → sittings list) restores each level.
- **BACK-2 (MUST).** The offset is applied **only once the destination has
  rendered the content it was measured against**. Every list view loads its rows
  after mounting, so restoring immediately would clamp to the top of an empty
  page; and a sitting day's word cloud, new-words and speaker-toplist cards
  (WCLOUD-1, NEW-1, TOPSPK-1) each arrive in **their own request** and insert
  themselves **above** the transcript, so the position is **held** while the page
  is still growing. Nothing is stored server-side and nothing is measured per
  speech — this is a few offsets kept in the tab's memory.
- **BACK-3.** Restoration **never fights the reader**: if they scroll, touch or
  key the page themselves, or navigate on while the destination is still loading,
  the pending restore is **abandoned** rather than applied late as a surprise
  jump. Content that never loads likewise leaves them at the top.
- **BACK-4.** Only a **return** restores. Arriving at a page by a route the reader
  did not descend from — a fresh navigation from the main nav, a changed
  electoral-cycle scope (§4A), a new filter or query — starts at the **top**, as
  does any first visit. Landing mid-list out of nowhere is worse than landing at
  the top.

---

## 4E. Cross-cutting: The Elemzések Section

A few pages on this site are not a record of anything — they are a **calculation
over** the record: how the factions vote together, where a question travels from
the asker to the answering ministry, which settlements ever get named in the
chamber. Each was born inside the module whose data it reads and lived as a
sub-tab there, which scattered three pages of one kind across three corners of
the site and left each looking like a footnote to a browse list. They are now one
section — **Elemzések** — with its own entry in the top bar.

- **ANA-1 (MUST).** Elemzések is a **navigation section, not a module**. Every
  page in it goes on belonging to the module whose data it derives from — the
  cohesion charts to Votes, the questions Sankey to Bills, the settlement map to
  Settlements — and disappears with that module, never faked (EXT-6). The section
  owns no tables, no API routes and no scraper stage of its own: it owns a landing
  page and a tab bar. (Tárcák set the precedent from the other side — a module
  whose pages sit in *another* section's tab bar, §7/MIN-5.)
- **ANA-2 (MUST).** The section has a **landing page** (`/analyses`) listing every
  analysis the deployment actually serves: what each is computed from ("szavazások
  alapján"), what it shows, and a link into it. That page is the section's front
  door and the framing these figures were missing — an analysis reached as one
  more tab of a browse list reads as one more column of that list, and a
  co-voting percentage with nothing around it invites a conclusion it does not
  support (TRUST-1).
- **ANA-2a.** The section also **looks like a different part of the site**, since
  that is what it is. It is set apart **before it is entered**: in the top bar its
  entry is not a sixth item in the row of browse sections but a **chip** — set off
  by its own air and outline, and filled with the section's own band colour while
  the reader is inside it, so the bar and the page beneath it read as one piece.
  The separation is carried by the chip itself and never by a rule between items:
  that row wraps on a narrow screen, and under a wide "Segoe UI" fallback it is
  close to wrapping at any width, which leaves a separate rule element stranded at
  the end of a line. For the same reason the row's fit is left ~40 px of slack
  rather than a few pixels — enough for the widest system font a reader is likely
  to resolve it to. In place of the plain sub-tab bar the browse
  sections carry, every page under `/analyses` then opens with a **section
  masthead** — a band naming the section, saying in one line what it is, and
  holding its tabs — and the page ground behind it shifts a shade warmer for as
  long as the reader is inside. The site's own chrome (brand, cycle chooser,
  footer) is otherwise untouched: this is a room in the same building, not a
  second site. The masthead is **full** on the
  landing page (whose `h1` it is, so the page does not repeat it) and **compact**
  on an analysis's own page, where the page's title is the one the reader came
  for and the band only has to answer *where am I*.
- **ANA-3.** The section's contents are described in **one place**
  (`frontend/src/modules/analyses/registry.js`): the landing cards, the sub-tab
  bar and the set of routes that count as "inside the section" are all built from
  it, so adding an analysis is an entry, a route and its strings — no navigation
  code. The backend keeps the one thing it cannot read from there
  (`ANALYSIS_MODULES` in `seo.py`), which decides whether the section is
  advertised in the sitemap at all.
- **ANA-4 (MUST).** An analysis that is **within-cycle by nature** (the faction
  cohesion, VOTE-8) is *unavailable*, not silently absent, under the "all cycles"
  scope (§4A): its tab goes, its route bounces to the landing page, and its card
  stays on that page greyed out **with the reason**. A reader who cannot find a
  page they have seen before is worse served than one who is told why.
- **ANA-5 (MUST).** The pages **changed address** (`/votes/cohesion`,
  `/questions`, `/settlements/…` → `/analyses/…`), and every old address answers a
  **server-side 301** to its new one, carrying the query string across (§4A's
  `?cycle=`, a chart's `?tab=`) so an old link lands on the view it named. A
  client-side redirect would leave a crawler with a 200 at the old URL until it
  ran the app — the failure mode §SEO-2 exists because of. The SPA carries a
  mirror of the same moves for navigation inside the already-running app.

> **✅ realized.** `/analyses` (landing) + `/analyses/faction-cohesion`,
> `/analyses/questions`, `/analyses/settlements[/…]`, built from the registry;
> `app/redirects.py` answers the five old addresses with 301s ahead of both the
> card routes and the SPA mount. The nav chip (`.nav-analyses`) and the masthead
> (`.sectionhead`, `.compact` off the landing route) live in `App.vue`; the warm
> ground is a `body.section-analyses` class, following the viewer's
> `body.viewer-pane` precedent. All three tones are tokens (`--section-band` /
> `--section-bg` / `--section-line`) in `styles.css`, so the chip, the band and the
> page ground cannot drift apart. The section's own card and the two analyses that
> stand as entry points are in `_ROUTE_CARDS`/`STATIC_PATHS`; Települések keeps
> the indexing status it had at its old address. Frakcióelemzés, held back for
> want of exactly this framing, is **on** with the section.
>
> ANA-3's claim has since been tested by a fourth analysis: adding Közbeszólások
> (§6E) to the section cost one entry in the registry, one route, one pair of i18n
> strings and one line each in `ANALYSIS_MODULES` / `STATIC_PATHS` / `_ROUTE_CARDS`
> — the landing card, the sub-tab and the "inside the section" route set all
> followed from the registry entry, with no navigation code touched.
>
> A fifth, Témák (TOPIC-9), cost the same plus one thing the registry did not
> describe before: an analysis whose **module is mounted but whose data may not
> exist** (the classification pass needs a GPU). That is a `feature:` key on the
> entry — checked by `analysisEnabled` for the card and the tab, and by the
> router for the address — rather than a second list of routes somewhere.

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
  by the global cycle selector (§4A), not a per-search filter. The **speaker**
  filter can also stand on its own: with no search term the page lists that
  speaker's speeches — one result per **speech**, shown by its opening rather
  than by a matched sentence, newest first (there is no relevance to rank by) —
  so "everything they said" is reachable from the search page itself, still
  combinable with the date/faction/agenda filters and the cycle scope. No other
  filter searches without a term: a bare faction or agenda-type filter is a read
  of the whole corpus, and is refused.
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
  A name typed into the search box — from **4 characters** up, matched anywhere in
  the label with the site-wide accent folding (§4B) — offers the speakers it
  names, ranked by how much each has spoken and counted so two people of a
  surname can be told apart. Picking one applies the **speaker filter** (§SEA-3)
  rather than searching for the name: a speaker is named in the transcript's
  *label*, not in the sentences the index holds, so the name moves out of the
  query box and into the filter it was meant to be, and the box is left ready for
  the term. The same suggestions back the **speaker filter's own field**, which
  takes nothing but a name and so suggests **from the first character** — the
  floor belongs to the main box, where a short fragment would open a dropdown
  over an ordinary search term. The filter travels in the URL as `person_id`
  (§SEA-6) — a shared link that carries only the id still shows the person's
  name, resolved on arrival.
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
  no personal data — see **PRIV-2** (§8.4). The site's **other search boxes**
  (irományok, képviselők, tisztségviselők, szavazások, tárcák) are counted the
  same way, each under its own source.
- **SEA-12.** The analytics also record **whether the search worked**: how many
  hits it found, whether the reader had to page past the first page, and the
  **position of the first result they opened** (1 = the top hit). Together these
  give a click-through rate and a **mean first-click rank** per keyword, which is
  what makes a ranking regression visible. The click signal is an anonymous ping
  carrying only the search it belongs to and that position — no identifier, and
  at most one per executed search, so no reader's path can be reconstructed from
  it (PRIV-2).
- **SEA-13 (MAY).** One easter egg: a search whose query contains **"cica"** or
  **"macska"** (any suffixed form — the match is accent-folded like §4B and
  anchored to a word start) sets a small pixel cat loose on the page, which
  chases the pointer, sits down when it catches up, washes itself, scratches at
  the edges of the window and eventually falls asleep if it is kept waiting;
  clicking it pops a handful of hearts. It is **X11's `oneko`** itself: the
  `oneko.js` script and its sprite sheet (MIT, adryd / the tylxr59 fork)
  vendored into `frontend/src/lib/`, adapted only so the cat can be summoned by
  a search and sent away again — the file header lists every change. It is
  decoration and nothing else: fixed to the viewport, `aria-hidden` so it never
  enters the accessibility tree (A11Y-1), and **not summoned at all** under
  `prefers-reduced-motion: reduce` or on a coarse pointer. It leaves on the next
  search that isn't about cats, and when the search page is left.

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
  **may auto-advance to the next speech** (navigating to its page, §VIE-5) and
  keep playing; on the last speech of a sitting it rests at the end. That
  auto-advance is **opt-in and off by default** — being carried from the speech
  the reader chose into an unrelated one is a surprise a player should not spring
  — so the viewer offers a **switch** in the action row under the player, remembered
  per browser (like the metric chips, §READ-5) rather than carried in the URL.
  Explicitly moving to another speech while the video is playing keeps it playing
  regardless; doing so from a paused player lands paused. The clip's smil VOD is
  generated on demand, so its activation endpoint is pinged before the playlist is
  requested.
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
- **SITREAD-4.** Where a **recording exists, the day page reaches it**. On a day
  `parlament.hu` has recorded but not yet cut into per-speech clips (`awaiting_media`,
  SCR-8) the viewer affordance MUST stay — it opens the **whole-day recording**, and
  says so rather than promising this speech's clip — because the day page is the
  one place a reader looks for the day's video: the same speech already plays from a
  representative's profile and from a shared speech link (both go through the
  viewer's whole-day fallback, VIE-9), so hiding it here alone made the video look
  unpublished when it was not. It is hidden only when the day has **no recording at
  all** to open. The day's own notice matches what exists: it does not list the
  video among the missing pieces once the recording is up.

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
- **SIT-2 (SHOULD).** A held day is often **only partly published**: `parlament.hu`
  releases a sitting in instalments and in no fixed order — the speech listing
  first, then the per-speech video windows (in batches), then, days later, the
  jegyzőkönyv — so a day can be fully browsable while some of its speeches still
  have no transcript and no clip. Both the **sittings list** and the **day's own
  page** MUST say so, with a marker **next to** (never instead of) what is already
  there, so a half-published day is not presented as finished and a visitor
  understands that what is missing is still coming, not lost. The marker is
  **time-bounded**: past the publication-lag window the same gap is permanent — a
  genuinely **video-only** day (VIE-8), e.g. a sitting whose record was never
  digitised — and marking those "being processed" would be a promise that never
  resolves, so they carry no marker (their individual speeches already disclose the
  absence per VIE-8). This is a **narrower** state than `awaiting_media` (SCR-8),
  where nothing per-speech is available yet and the day's status carries the answer
  on its own.
  > **✅ realized.** `/proceedings/sessions` reports `speeches_with_text` /
  > `speeches_with_video` next to `speeches` and derives
  > `processing: complete | pending | incomplete` from them (`None` for a day whose
  > `status` already says it); the day endpoint derives the same field from the
  > speech rows it already reads. The SPA renders a quiet "Részben feldolgozva"
  > badge under the card's counts and in the day-page heading for `pending` only.
  > The window mirrors the scraper's own publication-lag grace, so the site stops
  > promising exactly when the sync stops chasing.

- **NR-3 (SHOULD).** The parsed order paper SHOULD be stored in **tables of its
  own**, never folded into `session` / `agenda_item`. Those record what was
  actually said and are joined to speeches; an order paper is a **plan**, and
  plans change — items are dropped, reordered and re-timed between the napirend
  and the sitting. Merging the two would let the record inherit a claim the
  House only intended. There is **no history** to keep: the page states only the
  current position, so a load replaces the whole set, and an order paper the
  House has withdrawn disappears with it rather than lingering as a sitting that
  will never happen.
  An item names its iromány by **number only** (`T/438`), so the link to the
  bill (§6A) is **derived** at load time and MUST be **re-derived** whenever
  either side moves — which also means an item announcing an iromány that was
  not registered yet when the napirend was parsed gains its link as soon as the
  iromány lands, with no re-scrape.
  > **✅ realized.** `agenda_doc` / `agenda_doc_day` / `agenda_doc_item` /
  > `agenda_meta`, loaded by `load_aktualis` and relinked by
  > `_relink_agenda_bills`; `backend/migrate_upcoming_agenda.py` backfills an
  > existing deployment.

- **NR-4 (MAY).** The **ülésterv** (the month's sitting plan) is published
  alongside the napirend and is *not* parsed: it is a wide grid whose cells wrap
  over many lines, whose column boundaries move between documents, and whose
  footnote markers glue onto the title text — a different and much harder
  extraction problem than the order paper's, for a document that mostly restates
  what the napirend already says in a form the site can use. It is recorded as a
  **link** so a reader can open it, and MAY be parsed later.

- **NR-5 (SHOULD).** The **home page** SHOULD show the coming sitting: its days,
  their timetables, and their agenda items, with each item's iromány number
  linking to the bill page where we hold that iromány. On a sitting week it is
  the most perishable thing on the site, and until now the site's answer to
  "what is the Parliament doing tomorrow" was nothing at all.
  Because it is a **plan and not a record**, the block MUST carry the document it
  came from and the moment that document was issued — the napirend's own
  *"…órai állapot szerint"* stamp — so a reader can see what said this and as of
  when, the disclosure rule every derived claim on the site follows (TRUST-1).
  Whether it *also* spells the caveat out in prose is an editorial call, not a
  requirement: the heading already frames the sitting as the coming one, and the
  provenance line is the part that has to be there. It MUST be gated on the data
  actually being there, so a deployment that does not run the stage shows nothing
  rather than an empty promise.
  The home page MUST show only the days still **ahead**: an order paper covers a
  whole sitting week and stays up while that week is being held, so by Wednesday
  its Monday is a record rather than a plan — and that record belongs to the
  sitting-day page (NR-6), not to this block. A day is therefore dropped from the
  home page once its date has passed, read on the Hungarian calendar and counting
  the current day as still coming; once no day of the document is left the block
  drops itself rather than heading an empty list with "the coming sitting". A
  napirend with no days *at all* is a different case — an unreadable PDF, or one
  published before anything was scheduled — and keeps its card, because the link
  to the document is then the useful thing on it.
  > **✅ realized.** `GET /proceedings/upcoming` + `features.upcoming_agenda` in
  > `/meta`; `frontend/src/components/UpcomingSitting.vue` on `HomeView`, which
  > drops past days (and hides itself once none remain) in home mode only — the
  > endpoint keeps serving the whole document, since NR-6 asks it for held days.

- **NR-6 (SHOULD).** A **sitting-day page with nothing in it yet** — an announced
  day, or one parlament.hu has not populated — SHOULD show that day's entry from
  the order paper (NR-5) instead of only a notice that the transcript is coming.
  It is the page a visitor is most likely to open on a sitting morning, and the
  one page on the site that has nothing to say; the napirend already knows what
  the House means to do that day, and a reader who arrived from the sittings list
  should not have to find their way to the home page for it.
  The same provenance obligation applies unchanged — this is still a plan, not a
  record — so the day is shown by the *same* block as the home page, carrying its
  document and its *"…órai állapot szerint"* stamp, rather than re-rendered as if
  it were the day's agenda. It MUST be shown only where the current order paper
  actually covers that date: a day the napirend says nothing about (every past
  day, once the House has moved on) is left as it was, so the page never invents
  a plan for a day no document describes.
  > **✅ realized.** `UpcomingSitting` takes a `date` prop that narrows it to one
  > day (no preview truncation, no House Committee / documents footer, no
  > repeated date heading) and renders nothing when the order paper has no such
  > day; `SessionView` mounts it under the "coming soon" notice whenever the day
  > holds no agenda items and the feature is on.

### 5.7 Speech readability & lexical diversity

- **READ-1 (SHOULD).** Each **speech** carries two measured language annotations —
  how **hard it is to read** and how **varied its vocabulary** is — available
  wherever a speech appears as an item the reader can act on: the sitting-day
  speech list (§5.5) and the viewer (§5.2). They give a citizen a sense of *how*
  something was said, next to the *what* the transcript already carries: whether a
  contribution is plain speech or dense officialese, and whether it draws on a wide
  vocabulary or circles a handful of words. They are **hidden by default and shown
  on request**: this is a second-order reading of a speech, not something a visitor
  came for, and it must not tax the page a reader did come for — a 400-row sitting
  day least of all. **One opt-in governs every surface** (persisted per device, and
  offered on the sitting day, where a list of speeches is what makes the annotation
  worth comparing at all); with it off no speech is annotated anywhere, and the
  measurement is still produced, served and current.
- **READ-2.** The metrics are **LIX** (readability: mean sentence length plus the
  share of long words, with **RIX** — long words per sentence — alongside) and
  **TTR/MATTR** (lexical diversity: the share of distinct word stems). They come
  from a **published, auditable implementation** rather than a local re-derivation
  — implementations of LIX disagree about how they count words, sentences and long
  words, so the counts *A*, *B*, *C* behind the score travel with it and are shown
  to the reader.
  > **✅ realized** with [`saphes`](https://github.com/crow-intelligence/saphes)
  > (`backend/app/readability.py`), which exposes the parameters other
  > implementations hardcode and records every one of them on its results.
- **READ-3.** The two metrics take **opposite token streams**, and the distinction
  is load-bearing rather than pedantic. **LIX must see surface forms**: word length
  *is* its signal, and Hungarian *házakban* is 8 characters where its lemma *ház*
  is 3. **Diversity must see lemmas**: *ház / házak / házban / házakat* is one word
  inflected four ways, and counting it as four types reports **morphology as
  vocabulary** — inflating exactly the languages this matters for. Feeding one
  stream to both raises no error and produces no NaN, just a plausible wrong
  number, so the two paths never share an input. Lemmas come from the **same
  HuSpaCy pipeline, model routing and Modal cycle scope** as the word cloud
  (WCLOUD-6), so the language stack stays single-sourced.
  > **✅ realized**, and single-sourced in the literal sense: the cloud and this
  > metric read the *same* sentences (every non-procedural sentence of a sitting;
  > the measurable subset is 98.7 % of that set), so lemmatizing for each in turn
  > meant paying twice — on Modal, billing twice. One pipeline pass
  > (`nlp.analyze_all`) now yields both, and the streams are kept per sentence in
  > `backend/lemma-cache/` (`app/lemma_cache.py`) for this pass and whatever needs
  > lemmas next. The metric loads no model when the cloud has already run.
  > Note the two products still need **different filters over that one parse** —
  > the cloud keeps content words, diversity keeps running text — which is why the
  > shared artefact is the parse's lemma stream and not the cloud's term counts.
- **READ-4 (degradation).** The **readability half needs no model** and must
  therefore land on any install, including a bare host with no HuSpaCy at all. The
  **diversity half needs lemmas**; when no lemmatizer is reachable it is **omitted
  entirely and never approximated from surface forms**, and the UI shows the
  readability chip alone rather than a diversity number that means something else.
  A build that loses its lemmatizer must **not overwrite** complete measurements it
  can no longer reproduce (cf. the word cloud's equivalent guard).
- **READ-5 (what is measured).** Only the **speaker's own words** count: the
  transcript's leading speaker attribution ("TUZSON BENCE (Fidesz):") and the
  stenographer's parenthetical **stage directions** ("(Taps a kormánypárti
  oldalon.)", heckles) are stripped first — they are not speech, and they would
  otherwise both inflate the token count and invent sentences. The same segments
  the reader sees lifted out of the flowing transcript (SITREAD-2) are the ones
  excluded here. **Procedural/chairing speeches (STAT-1) are excluded**, as they
  are from the statistics and the word cloud, and a speech **below a minimum
  length** is **not scored at all** — a LIX built from two sentences is noise
  wearing a number's clothes, and the UI must show nothing rather than a figure
  that looks like a finding.
- **READ-6 (interpretation).** The LIX long-word threshold is **calibrated for
  Hungarian, not inherited from Swedish**. At Björnsson's default (6) roughly 42 %
  of running tokens in this corpus count as "long" against a Germanic norm near
  25 %, so the index saturates and stops discriminating; at the calibrated
  threshold the corpus lands on the same share the original selects in Swedish. It
  follows that **Björnsson's difficulty labels do not apply** — they were fitted to
  Swedish prose at threshold 6 — so a speech is banded **relative to the corpus
  itself** (the quintiles of every measured speech), which is both honest and the
  comparison a reader actually wants: *"harder to read than 80 % of what is said in
  this House"*. Absolute labels borrowed from another language's calibration MUST
  NOT be shown. It follows further that **the chip shows the comparison, not the
  score**: a LIX of 54 or a MATTR of 0.71 is a number the reader has no scale for,
  so the chip says in words where the speech sits **against the House's median** —
  *easier / typical / harder to read*, *less / typical / more varied vocabulary* —
  collapsed from those quintiles, with the middle fifth (which straddles the median)
  as *typical* so the label never turns on a tenth of a point. The score itself, its
  quintile and the counts behind it stay one hover away and MUST remain reachable
  (TRUST-1 / REP-5). The value is carried by the **word**, never by colour alone
  (A11Y-1).
- **READ-7 (cost & provenance).** The measurement runs **at load time, never per
  request**, and its per-sitting output is **cached on disk** keyed by a
  fingerprint of the transcript *and* the method (package version, threshold,
  length policy, window, lemma model), exactly like the word cloud and the entity
  pass — so a rebuild re-measures only what actually changed, and a
  readability-only entry is never mistaken for a complete one once a lemmatizer
  appears. Only speeches that will actually be scored are sent to the model, since
  that half is metered work. A change on the **method** side alone (a package
  upgrade, a different threshold, a lemmatizer becoming reachable) is invisible to
  the incremental update, which only revisits sittings whose *source file* changed,
  so an **in-place re-measure** command exists for it. The parameters are
  **configurable, not hard-coded** (OPS-4) and are **published in the site
  manifest** so every annotation can state how it was produced (TRUST-1 / REP-5).

### 5.8 Speech policy topics (CAP)

- **TOPIC-1 (SHOULD).** Each **speech** carries the **policy topic** it is about,
  wherever a speech appears as an item the reader can act on: the sitting-day
  speech list (§5.5) and the viewer (§5.2). This is the question a reader scans a
  sitting day with — *what was this about?* — which the agenda title answers only
  for the item, not for the speech, and which the transcript answers only by being
  read. Unlike the language metrics (§5.7) it is therefore **shown by default**
  rather than behind an opt-in.
- **TOPIC-2 (the scheme).** Topics come from the **CAP (Comparative Agendas
  Project) master codebook** — 21 major topics plus "Other" — rather than an
  invented taxonomy. It is the scheme comparative political research already uses,
  which makes the labels joinable with other countries' parliamentary corpora and
  gives each one a published definition a reader can check. The **CAP code**
  travels with the name for that reason.
  > **✅ realized** with
  > [`classla/ParlaCAP-Topic-Classifier`](https://huggingface.co/classla/ParlaCAP-Topic-Classifier),
  > an XLM-R-large model further pretrained on parliamentary proceedings and
  > fine-tuned on 29 ParlaMint 4.1 corpora — ParlaMint-HU among them, so Hungarian
  > plenary speech is in-domain rather than merely covered by the tokenizer.
- **TOPIC-3 (the unit is a block of paragraph size).** Classification runs over
  **paragraph-sized blocks**, not whole speeches and not sentences. The model
  truncates at 512 tokens while 58 % of this corpus's speeches are longer, and the
  half it would read is the salutation rather than the argument — measured here,
  head-truncation and whole-speech averaging disagree on 19.5 % of long speeches. A
  single sentence, at the other end, carries too little context to place a topic.
  Because the corpus marks paragraphs **inconsistently across cycles** (real
  paragraphs in 42–43, none at all in 41, source line breaks in 39–40), blocks are
  assembled to a **word budget that prefers paragraph boundaries** where they are
  real, so the annotation's quality does not depend on which cycle is being read.
- **TOPIC-4 (speech-level aggregation).** A speech's topic is the **word-weighted
  majority** of its confident blocks: a speech is about what it spends its words
  on, so a one-line aside cannot outvote three paragraphs of argument. CAP's
  "Other" — procedural, rhetorical and interpersonal speech — is a real prediction
  but **never a subject**, so it is reported alongside the topic (how much of the
  speech was not policy) and excluded from the vote.
- **TOPIC-5 (confidence, and the right to say nothing).** A block counts only if
  its confidence clears a **configurable threshold** (default **0.90**). A speech
  with no block above it carries **no topic at all** — silence, not a "misc"
  bucket. Measured on a held-out coded sample of this corpus, the threshold trades
  coverage for accuracy along a curve (0.60 → 90 % of paragraphs at 74 % accuracy;
  0.90 → 69 % at 81 %; 0.95 → 62 % at 83 %), and an unlabelled speech costs a reader
  a filter while a wrongly labelled one costs them trust (TRUST-1).
- **TOPIC-6 (the threshold is a read-time policy).** Raw per-block predictions are
  stored; the threshold is applied **when a request is served**. Retuning it must
  therefore cost a **restart, never a reclassification or a rebuild** — it is a
  presentation choice about how much uncertainty to show, not a measurement, and an
  operator has to be able to move it while looking at the result. It is
  deliberately **not** part of the cache's method tag.
- **TOPIC-7 (cost, provenance & degradation).** Classification runs **at load
  time, never per request**, and its per-sitting output is **cached on disk** keyed
  by a fingerprint of the transcript *and* the method — like the word cloud, the
  entity pass and the language metrics. The model needs a GPU and the web server
  has none, so the cache is the pass's **portable deliverable**: classify once
  where a card is, ship the file, replay it into the DB with no model present.
  Ongoing sittings are handled by **offloading classification to a GPU service**
  (the same arrangement the word cloud's NER uses, WCLOUD-6), scoped by the same
  metered-cycle guard so only the newest cycle is ever dispatched and a
  full-archive backfill can never be bought by accident. Both routes run the same
  model and produce the same method tag, so they share one cache and neither
  invalidates the other — where a sitting was classified is not a property of the
  prediction. A host that misses the cache with no route available leaves those
  speeches **unlabelled rather than failing the build**, and never writes a guess;
  an offload that is down, unauthenticated or deployed on a different model costs
  a run its new topics and nothing else. **Procedural chairing
  speeches are excluded outright** (STAT-1) — the model reads an announcement as
  being about whatever bill it names, so the exclusion is structural rather than
  left to the threshold. The parameters are **configurable, not hard-coded**
  (OPS-4) and **published in the site manifest**, the threshold in force included,
  so every label can state how it was produced (TRUST-1 / REP-5).
  > **Not yet built (§10):** topic facets in search. The stored unit is
  > deliberately the block, and the label is indexed, so it is a query against
  > what already exists rather than a reclassification — which is how the other
  > two arrived: the **per-representative topic profile** is now TOPIC-10 below,
  > and topic classification of **irományok** is TOPIC-8.
- **TOPIC-8 (SHOULD).** The same labels are carried by **irományok**, wherever one
  appears as an item the reader can act on: the bills list, the *Kérdések* list
  (BILL-13), the *Minden iromány* list (BILL-9) and the iromány detail view. It answers the same question the
  speech topic does — *what is this about?* — which for a document the title
  answers only when the title is plain, and an iromány's title is routinely a
  citation ("Az általános forgalmi adóról szóló 2007. évi CXXVII. törvény
  módosításáról") that names the law being amended rather than the subject.
  - **The input is the document, not the title or the metadata.** The registry
    holds only a *link* to the PDF, so this rests on the scraper's mirrored,
    text-extracted documents (DOC-1) and covers exactly the irományok that have
    one. Where an iromány has no text — an image-only scan, or a document that
    was never mirrored — it carries **no topic**, never a guess from its title.
  - **The unit is a block of the document**, recovered from the extracted PDF
    layout (paragraphs are real there, unlike the transcript's paragraph column)
    and assembled to the same word budget as a speech's, with an over-long
    paragraph split rather than truncated. The **cover sheet is kept**: it is
    administrative, but it also names the subject, and dropping it is measurably
    worse — over cycle 43 it changes 3 verdicts and silences 32 documents.
  - Everything else is **deliberately identical** to the speech pass: the same
    model, the same read-time threshold (TOPIC-5/6), the same word-weighted
    aggregation with "Other" reportable but never winning (TOPIC-4), the same
    on-disk cache and the same refusal to fail a build (TOPIC-7). A topic must
    mean the same thing whichever text it was read off, so the reader is shown
    the same chip and the same breakdown, worded for a document.
  - **The lists are filterable by topic**, combining with the existing filters and
    carried in the URL like them (BILL-1). The filter matches an iromány's
    **dominant** topic — the one its chip shows — never merely a topic the
    document touches somewhere: a filter that sits beside a visible label and
    selects rows carrying a *different* label is worse than no filter. It follows
    that the selection is resolved by the same rule and the same threshold as the
    chip, at request time, so retuning the threshold moves the filter and the
    labels together (TOPIC-6) rather than desynchronising them. The picker offers
    only the topics **present in the current scope**, each with its **hit count**,
    so it never presents a choice that leads to an empty list; where the corpus
    carries no topics the control is absent rather than dead. A topic filter the
    deployment cannot honour MUST return **nothing**, never the unfiltered list —
    silently dropping it would show the reader every iromány under one label.
  > **✅ realized.** `bill_topic` rows written by `loader.rebuild_bill_topics`
  > from `parlacap.build_text_blocks`; served as `topic` on the bills list and
  > detail, rendered by the shared `TopicBadge.vue` (`kind="bill"`).
  > `PARLAMONITOR_BILL_TOPICS` switches the pass; `PARLAMONITOR_DOCUMENTS_DIR`
  > points at the mirror. Filtering is `?topic=` (repeatable) on the list, with
  > `/bills/facets` returning the topics in scope and their counts; both resolve
  > the dominant topic through `parlacap.DOMINANT_TOPIC_SQL`, which is the SQL
  > expression of `parlacap.aggregate`'s tie-break and is tested against it. Measured on cycle 43: **320 of 356** documents with
  > text get a topic at the 0.90 threshold (90 % coverage, against 69 % for
  > speeches — a document is longer and far more topically focused than a
  > speech). The predictions live in the same `parlacap-cache.json` under a
  > `bills` key, so the one shipped file still carries both passes, and a server
  > with no document mirror replays it as it stands.
- **TOPIC-9 (SHOULD).** The labels are also read as an **agenda**, on a page of
  their own in Elemzések (§4E): *Témák*. A chip answers "what is this speech
  about?" one item at a time; the question the corpus can only answer in the
  aggregate is **what this House spends itself on, and whether that has changed**
  — which is a different page, not a bigger chip.
  - **Two agendas, one chart.** The figure ranks the CAP topics and gives each one
    two bars: its share of what was **said** on the floor, and its share of what
    was **submitted** to the House as an iromány. They are deliberately not
    merged. The two are read off different texts covering different populations,
    and the gap between them — the House argues about what is contested and
    processes what is routine — is the finding. The ranking can follow either
    series, because the document agenda has an order of its own and seeing the
    same rows resorted is how the difference becomes visible. Where the iromány
    half is absent (the module is off, or nothing was ever classified), the page
    loses a series and nothing else.
  - **The measure is the word-weighted share of the classified policy text**, the
    same weight the per-item aggregation uses (TOPIC-4), beside the count of items
    the topic is the **subject** of — resolved by the same dominant-topic rule as
    the chip and the iromány filter, so a count on this page is the size of the
    set a reader would find by opening them. "Other" is excluded from every share
    and reported separately, exactly as it is per item.
  - **Coverage is stated before the chart, not under it** (TRUST-1). Every share
    here is a share of the labelled part: at the threshold in force roughly a
    third of the floor's blocks say nothing, chairing speeches are never
    classified at all (STAT-1), and only an iromány with readable text can carry a
    topic. A reader who is not told that reads the chart as a census.
  - **A topic opens** into how it is spoken about: its share of each **calendar
    year** (a share, so a quiet year shows the shape of its agenda rather than the
    lull), which **factions** carry it — both as a share of everything said on the
    topic *and* as a share of that faction's own policy speech, since the big
    faction wins the first by arithmetic and only the second shows a small party's
    specialism — and **who** speaks most about it. The opened topic and the
    ranking ride in the URL (`?topic=`, `?by=`), absent at their defaults (§SEO-2).
  - **Nothing is materialised.** A stored topic table would bake in the confidence
    threshold, which is a read-time policy an operator must be able to retune with
    a restart (TOPIC-6), so the page aggregates the stored blocks per request and
    memoises the result per (scope, threshold, DB) like every other aggregate
    here. The threshold is part of the cache key: a cached mix that outlived a
    retune would disagree with the chips on the speeches it links to.
  - The chart is **embeddable** (§4C), and the page is **absent rather than empty**
    on a deployment that never classified anything: no card, no tab, and its
    address answers the 404 the rest of the site would.
  > **✅ realized.** `/analyses/topics`, served by `/proceedings/topics`
  > (+ `/topics/{label}`) and `/bills/topics`, both folded by the shared
  > `parlacap.topic_mix` so the two halves cannot compute a share two ways. The
  > item counts come from `parlacap.dominant_topic_sql`, now generalised over both
  > block tables. The figure is `components/TopicMixChart.vue`, shared with the
  > `topic-mix` embed. The registry entry carries a `feature:` gate
  > (`speech_topics` in `/meta`), which the router honours as a 404 — the first
  > analysis whose data can be missing while its module is mounted.
  >
  > **Still not built (§10):** a topic filter in the proceedings search. The page
  > links out to the iromány list (which does filter by topic) and to a speaker's
  > profile — which now answers the mirror-image question itself (TOPIC-10) — but
  > there is still no "every speech on this topic" list to link to, so none is
  > offered.

- **TOPIC-10 (SHOULD).** The same mix is read **one member at a time**, as a
  figure on their representative profile: how their own speeches divide between
  the CAP topics. It is the Témák page's question asked of a person, so it is
  deliberately the same measure through the same fold at the same threshold — a
  member's share is their words on a topic over their *own* classified policy
  text, and the item count beside it is resolved by the same dominant-topic rule,
  so it names the set of speeches whose chips carry that label.
  - **The House's own share is drawn beside every bar, and it is not optional.**
    Most of what any member talks about is whatever was on the agenda, so a figure
    of one person's shares alone reads as a portrait of them when it is mostly a
    portrait of the term. The reference is the floor's mix for the same scope —
    the very number the Témák page plots, read off that page's memoised aggregate
    rather than computed a second time, so the two cannot disagree. It is a **tick
    across the bar's track, not a second bar**: speeches against irományok are two
    agendas of equal standing (TOPIC-9) and earn two bars, but a member against
    the House is a figure and the norm it sits above or below, and the only thing
    worth reading off it is which side of the tick the bar ends on.
  - The reference is the House's **share of words**, never an average over
    members — that would let a handful of talkative back-benchers define the norm.
  - **Coverage is stated before the figure** (TRUST-1): how many of the member's
    speeches carry a topic at all, since chairing turns are never classified
    (STAT-1), a speech with no transcript cannot be, and roughly a third of the
    rest stays under the threshold. The ranked list shows its head and opens to
    all of it — "the topics they speak about" and "their eight commonest" are
    different claims, and the shorter one is never made silently.
  - Rows are **not controls**. There is no per-member topic list to open them into
    (§10), so the way on is the Témák analysis, linked from the panel. The figure
    is **embeddable** (§4C), and on a deployment that classified nothing the panel
    is **absent rather than empty** — gated on the same capability flag as the
    analysis, not on a module switch.
  > **✅ realized.** `/proceedings/topics/representative/{person_id}` folds the
  > member's own blocks through the shared `parlacap.topic_mix` and hangs
  > `house_share` on each row, taken from the floor mix's cached aggregate
  > (`_floor_mix`, now shared with `/proceedings/topics` — one computation, two
  > readers). The figure is `components/TopicMixChart.vue` once more, given a
  > `reference` prop for the tick; the embed kind is `rep-topics`.

---

## 6. Functional Requirements — Module: Representatives & Statistics

- **REP-1 (MUST).** A browsable, searchable **list of representatives**, filterable
  by faction and constituency, and scoped to the **electoral period** chosen in
  the global cycle selector (§4A); each links to a profile. The name search is
  **accent-insensitive** (§4B FOLD-1): `dora` matches *Dóra*. A cycle's list covers
  **everyone who held a mandate in it**, those whose mandate ended early included,
  with a filter for the mandate holders alone (REP-14).
  - It is **one page — *Felszólalók* — for everyone who takes the floor**, shown a
    **category at a time** through a chip row under the search box: the
    representatives (**the default**, so the list still means "representatives"
    unless the reader asks otherwise), the **nationality advocates** (REP-9), the
    **other speakers** (REP-12), and — for a reader who does not know which of the
    three a name belongs to — **all of them at once**. A reader looking for a
    person should not have to know the constitutional category they fall in before
    they can find them; the categories stay visible and distinct, but as a filter
    of one list rather than as separate pages to hunt through.
  - Each chip keeps **its own URL**, so a category stays linkable, citable and
    separately indexable, and the mandate-less categories' pages keep the
    addresses they were published under. Switching chip keeps the typed name and
    the chosen sort order, and drops what cannot survive: the page offset, and the
    faction/mandate filters wherever they do not apply.
  - A card must read **the same under every chip**: in the mixed list an MP is
    still identified by their faction and an advocate by their nationality, so the
    office slot is filled in only for the speakers who have neither (REP-12).
  - The page's search box also has a **second mode**, keyed by place instead of by
    name: *"Ki a képviselőm?"* (REP-10), which answers the same question for a
    reader who knows where they live but not who represents them.
- **REP-2 (MUST).** A **representative profile** shows: name, photo (if available),
  current/past faction(s) with their **real start/end dates** (REP-14), the
  **mandate term** and, where it ended early, why (REP-14), constituency, Wikidata
  link, a
  reverse-chronological **list of their speeches** (each linking into the viewer),
  and — when the Bills module (§6A) is enabled — a **list of the bills they
  submitted**, each linking to the bill (the reciprocal of BILL-3's sponsor links).
  A speaker who holds (or held) a **government office** — *tisztség*, e.g.
  *„igazságügyi miniszter”* — is identified by it too: it is the primary identity of
  a **non-MP speaker** (a minister or state secretary with no mandate, hence no
  faction and no constituency). The office is derived from their speeches, and it
  MUST be shown **dated** — with the term it refers to — never as a bare title: the
  most recent office in scope is often *not* the person's current post (a state
  secretary promoted since, a former minister now on the back benches), and undated
  it reads as if it were. The dates are the upstream **appointment/dismissal
  boundaries** where a term for that office is on record (consecutive spells of one
  post, which upstream splits at every cycle boundary, read as a single term); an
  office **still held** has no end date — it reads *„… – jelenleg”*, never the last
  sitting day, which would announce a departure that never happened. Where no term is
  on record, the dates come from the speeches carrying the title, which bound the
  office only **from below** and are labelled as such (*„legalább … óta”*); an **end
  date is shown only once the person has spoken without that office since** — the
  only evidence in the data that they left it.
  The profile also lists the person's **whole office history** in its own panel —
  every office they ever held, **historical ones included**, newest first, each with
  its term. Unlike the statistics this is biography, so it is **not** cycle-scoped.
- **REP-2a (MUST).** Office terms come from the **office-holder registry**
  (*tisztségviselők*, SRC: `officeholders.json`), the upstream listing of every
  recorded office term with its real dates — **not** from what a speech happens to
  call the speaker. This is the only source that covers a **non-MP** minister or
  state secretary: they appear in no roster (not an MP, not a nationality advocate),
  so before it their office could only be guessed from their speeches — which never
  says when the office began, nor that they still hold it. The per-MP office list
  that comes with the MP/advocate roster is kept as a second, equivalent source; the
  two are stored separately so either can be reloaded on its own, and a term both
  report is shown once. A corpus scraped before the registry existed keeps working
  (offices then fall back to the speech-derived dating above).
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
  - Advocates are presented as **their own category of the *Felszólalók* page**
    (REP-1), selected by a chip — never merged into the representatives: they hold
    a different mandate, and the MP list, which is what that page opens on, must
    keep meaning "representatives". The category has its own URL, so it is
    linkable and citable, and it honours the global cycle scope (§4A) like every
    other view. An
    advocate's profile (REP-2) shows their mandate + nationality where an MP's
    faction badge goes, and their speech/document statistics (REP-3) are computed
    exactly as an MP's; the **vote-participation metrics are omitted** (not
    zeroed), since an advocate has no vote to cast.
  - The registry is a **separate, additive source record** per cycle: adding it to
    an already-scraped corpus must not require re-running the MP roster stage or
    rebuilding the database — the loader's incremental path (ING-5) picks up the
    new files and extends the `person` schema in place.

- **REP-10 (SHOULD).** **"Who represents me?" — a constituency lookup.** The most
  basic question a citizen has about the House is *which of these people is mine*,
  and nothing in the corpus answers it: `parlament.hu` records the single-member
  constituency an MP won (*"Budapest 12. OEVK"*, REP-2) but never says **where** that
  constituency is, nor how to get from a place to it. The site therefore answers it as
  the **place mode of the *Felszólalók* page** (REP-1): the same question that page
  answers by name, keyed by a **settlement** instead, returning the constituency it
  belongs to and the MP who holds it, each linking to their profile (REP-1/REP-2).
  - **A mode of that page, not a page beside it.** Finding an MP by name and finding
    one by place are the same errand, so both live in the one search card, switched
    by a two-way control at its top. A reader who does not know their MP's name —
    the reader this feature exists for — should not have to discover that a separate
    page exists for their case, and one who does should not have to leave the list to
    check where a constituency is.
  - Each mode is a **route**, so the lookup **keeps the URL it was published under**
    (`/representatives/lookup`, with its own share card): a resolved answer stays
    linkable and citable (SEA-6 in spirit), Back moves between the modes as between
    pages, and an inbound link still lands on the lookup and nothing else. The page
    heading stays *Felszólalók* — as it does under the category chips — and the card
    carries the question as its own heading.
  - The place mode **replaces** the name field, the category chips, the filters and
    the rows; it does not filter them. The answer is **one MP, not a list**, and what
    makes it an answer — the constituency, its boundaries, the MP's contact details,
    the provenance note and the cycle it holds for — does not fit a list row. Nor can
    the two modes share their state: both search boxes take a `q`, but there it means
    a person's name and here a place, so switching mode carries nothing across.
  - The mapping and the boundaries come from the **National Election Office**'s
    published election data (`valasztas.hu`), the only source that has them: the
    per-settlement constituency list, the constituency boundary polygons, and each
    settlement's own outline. This is the **one feature whose data is not from
    `parlament.hu`**, so it MUST **name its source and method on the page**
    (TRUST-1) — the MP and their mandate still come from the corpus, and only the
    geography from the election office.
  - The **data version is resolved at runtime** from the source's own config file
    rather than pinned in code, and the base URL is deployment config (OPS-4), so a
    revision upstream — or the next election's tree — needs no code change.
  - **Naming the settlement is the whole answer for all but 23 of them.** The
    exceptions — 15 Budapest districts and 8 large cities — are **split between
    several constituencies**, and no place name can resolve them. For those the page
    MUST show the **boundaries on a map** over enough street context for a reader to
    recognise their own neighbourhood, and let them **pick the part they live in**;
    the constituency polygons partition the settlement, so the choice is unambiguous
    once seen. Picking MUST also be possible **without the map** (a list of the
    candidates).
  - The map is fitted to the **settlement**, and the constituency regions MUST be
    **clipped to it**. A constituency is many times larger than the place the reader
    is looking for — several districts, or half a county — so drawn whole it sprawls
    across the view and invites choosing a region by a shape that is mostly somewhere
    else. Clipped, what remains on screen is exactly the question being asked. The
    settlement's own outline is drawn over the regions dividing it, and the basemap
    outside stays visible for orientation.
  - **Region identity MUST NOT be carried by colour.** In Hungarian politics no hue
    is unclaimed — the factions in this corpus alone carry orange, blue, green, teal,
    red, brown, purple and grey — and the MP's faction badge sits beside the map, so
    a per-region palette reads as a claim about each region's politics rather than as
    a neutral index. Every region therefore shares one neutral wash and is identified
    by its **number** and the **boundary between neighbours**, repeated as a numbered
    badge in the list. This also satisfies A11Y-1 outright: nothing is lost in
    greyscale or to any colour-vision deficiency.
  - Settlement search is **accent-insensitive** like every other text box (§4B
    FOLD-1), and MUST also find a Budapest district by the spellings people actually
    use (*"V. kerület"*, *"5. kerület"*), not only the source's zero-padded form.
  - **Not scoped by the global cycle selector** (§4A) — the deliberate exception to
    CYC-2, alongside REP-2's biography panel. Constituency boundaries are **redrawn
    between elections** (the 2026 map has 16 Budapest constituencies where the 2022
    one had 18), so a boundary answers for exactly one cycle: the one the election
    that produced the data seated. The page **states which cycle** it is answering
    for rather than inferring it from the reader's scope — answering an out-of-scope
    cycle off a map that did not exist then would be a *wrong* answer, not a
    narrower one.
  - Because a citizen is also represented by the **national-list** MPs, who are tied
    to no constituency, the page says so and links to the full list (REP-1).
  - The external source is **cached and degrades gracefully**: a copy that is merely
    stale is preferred to failing (the electoral map is fixed between elections), the
    boundary geometry is fetched **only when a split settlement is actually opened**,
    and a total outage surfaces as an honest "unavailable" — never an empty or
    invented answer (cf. SCR-5). The whole feature is **switchable off** (OPS-4), in
    which case its mode switch, its route and its endpoints disappear like a disabled
    module's (EXT-6).

- **REP-11 (SHOULD).** **The office holders (*tisztségviselők*) are browsable as a
  listing of their own.** The office-holder registry already dates each person's
  offices on their profile (REP-2), but it is only reachable one profile at a time
  — while the question it answers best is the cross-cutting one: *who holds (or
  held) this office, and when*. The section therefore carries its **own page in the
  Representatives tab bar** (§REP-1's precedent), mirroring the parliament's own
  listing (`/web/guest/tisztsegviselok`).
  - Listed **one row per term**, not per person: a career runs through several
    offices, and the (person, office, from–to) term is what the source records.
    The same person legitimately appears under each office they held.
  - The page covers the **whole registry, including the people who never spoke in
    the House** — over half of it: MNB and Közbeszerzési Hatóság members, ministers
    who only ever appeared in writing. They are loaded as people whose profile is an
    office history and nothing else, which is exactly what the source says about
    them. Listing only those who happen to be in the transcript corpus would present
    a silently halved version of an official register as if it were the register.
    They hold **no mandate**, so no mandate-scoped view (the MP list, the advocates,
    the search speaker suggestions) may show them.
  - Terms are grouped by the registry's **own office categories** (miniszterelnök,
    miniszter, államtitkár, országgyűlési tisztségviselő, egyéb vezető / egyéb
    tisztség). The category is **not a field in the data** — it is which of the
    portal's per-category listings returned the row — so the scraper asks for each
    category separately and tags the rows, rather than guessing a category from the
    free-text title (which would misfile *miniszterelnök-helyettes* and every
    ministry rename).
  - Cycle scope (§4A) is an **overlap**, since a term is dated rather than numbered
    by cycle: a cycle shows the terms running during it, not the terms that began in
    it — otherwise a minister appointed last cycle and still serving would be missing
    from the one where they actually served. The overlap MUST be computed on
    **instants**, not on calendar dates: upstream dates a term by the UTC instant of
    a *local* midnight (9 May 2026 arrives as `2026-05-08T22:00:00Z`), and every
    House office begins at the first local midnight of a cycle — so comparing date
    prefixes files a cycle's entire opening cohort under the cycle before it.
  - An office **still held keeps an open end** and is shown as such (REP-2): no end
    date is ever invented, and the last day of the data is not a departure.

- **REP-12 (SHOULD).** **The other speakers get a listing too.** Not only
  representatives speak in the House: ministers and state secretaries (often not
  MPs), the President of the Republic, the heads of independent bodies and invited
  guests do — and they are otherwise reachable only by stumbling on a speech.
  They are the third category of the *Felszólalók* page (REP-1), alongside the MP
  list and the advocates (REP-9), with the same search, sorting, statistics and
  cycle scope.
  - Membership is defined by **having spoken**, in the cycles in scope — never by
    "not an MP". `person` also holds the office-holder registry's people (REP-11),
    most of whom never spoke here; listing them as speakers would be false.
  - They have no faction and no constituency, so the **office they spoke in
    identifies them** and takes that slot on the card (REP-2), cycle-scoped like
    everything else — someone promoted mid-career is shown in the post they held
    then, not the one they hold now.
  - Because they speak in plenary, they MUST also be **offered by the transcript's
    speaker filter** (search-as-you-type): filtering the record by a minister that
    silently finds nothing is worse than not offering the filter.

- **REP-13 (SHOULD).** **Asset declarations (*vagyonnyilatkozatok*) and the CV on
  the profile.** Every MP files an asset declaration on taking their seat and once
  a year after; the House publishes each as a PDF. It is the single most-asked-for
  document about a representative and today costs a reader several clicks through
  parlament.hu's own adatlap, so the profile (REP-2) carries it in **its own
  panel**: every declaration on record, **newest first**, each linking to the
  original PDF on parlament.hu — **linked, never mirrored** (TRUST-1), so a
  correction or withdrawal upstream is never masked by a stale copy of ours.
  - Each entry is shown with the **date of the declared assets** (*a vagyoni
    állapot időpontja*) — the point in time the declaration is about, and the only
    thing that tells two same-titled filings of one year apart (one on taking the
    seat, one for the year's end).
  - Like the office and committee history this is **biography, not statistics**:
    it is **not cycle-scoped** (§4A), so the series stays whole whichever cycle is
    selected — a declaration's value is largely in the comparison with the ones
    before it.
  - A declaration that was **due but never published** stays in the list as a
    dated, unlinked row. Silently dropping it would hide exactly the fact a reader
    is looking for.
  - The profile also links the **CV** (*önéletrajz*) the representative had the
    House publish, when there is one. Publication is the MP's own choice and the
    file is taken down when the mandate ends, so the link MUST only be published
    once its target is **known to resolve** — never derived from the person id and
    hoped for.

- **REP-14 (SHOULD).** **Terminated mandates (*megszűnt mandátumok*).** A cycle is
  not the fixed set of 199 people it looks like. Over a four-year term a dozen or two
  mandates **end early** — a death, a resignation, an incompatibility — and each is
  filled from the same list some weeks later. The roster query upstream offers is
  **point-in-time**: it answers "who sat on this date", so scraping it once per cycle
  records only whoever held a seat the day it was asked, and everyone who left
  before that is **absent from the cycle altogether** — not merely unmarked. Their
  speeches are in the corpus and their profile is reachable from them, yet the
  cycle's own list of representatives does not contain them, and nothing on the site
  says a seat ever changed hands.
  - **A cycle lists everyone who held a mandate during it** (REP-1) — the departed
    included — and a **filter narrows the list to the mandate holders**. The
    unfiltered, complete set is the default, because that is what the cycle's
    membership *is*: a history, not a snapshot. "Active" is read against the
    **cycle**, not against today — in the running cycle it means still sitting, in a
    closed one that the mandate lasted to the end of the term — and the list is
    explicit about which reading applies. A row whose mandate ended early is
    **marked with the date it ended**, so the two kinds are never silently mixed.
  - The source is `parlament.hu`'s own **"Változások az Országgyűlés
    összetételében"** listing (the composition-changes registry), scraped **per
    cycle** alongside that cycle's roster. It has two halves: the **mandate changes**
    — one row per mandate that ended, carrying the term's start and end, the
    **reason** it ended, the constituency or list it was won on, and the **successor**
    who took the seat with the date theirs began — and the **faction changes**, one
    row per mid-cycle switch with its exact date. The mandate half is the only place
    the reason and the predecessor↔successor pairing exist at all; the faction half
    repeats what the per-MP faction history already dates, and is kept as a
    cross-check rather than as a second source of truth.
  - A departed MP is the **same core `person` entity** as any other (EXT-2), keyed by
    the same upstream person id, so the change registry names people the per-MP detail
    queries then answer for exactly as they do for a sitting MP — their mandate,
    faction, committee and election history all resolve, with no name matching and no
    re-scrape of the proceedings.
  - The mandate is shown **dated on the profile** (REP-2), with the reason it ended
    and a link to the person on the **other side of the handover** — the MP who
    succeeded them, or the one they replaced. Undated, a former MP's profile is
    indistinguishable from a sitting one; the mandate term is what makes the
    difference legible, exactly as the office term does for a minister.
  - **Faction history is shown with its real dates.** The per-MP history records each
    faction spell's actual start and end, and a mid-cycle switch is precisely where
    the reader needs them: labelling every spell with the cycle's year span instead
    renders an MP who left their faction and later rejoined it as the same span
    repeated three times, which reads as a bug. Consecutive spells in **one** faction
    that upstream splits at each cycle boundary are **collapsed into a single run**
    (as REP-2 already does for office terms), so an unbroken career reads as one
    line; a genuine gap — a cycle not served — is never bridged. A spell still running
    keeps an **open end** and never has the last known date filled in for it.
  - Adding this to an **already-scraped corpus** must not require re-fetching every
    MP's details: the departed are a handful per cycle, so backfilling them is a
    handful of requests (cf. REP-9's additive registry, SCR-4's politeness).

- **REP-15 (SHOULD).** **Comparing representatives side by side.** A profile answers
  *"what did this MP do"*; the question a reader almost always asks next is *"compared
  to whom?"* — and today they answer it by opening two profiles in two tabs and
  scrolling both. The site therefore offers an explicit **comparison page**: two to
  four people in **parallel columns**, one row per figure, laid out like a product
  spec sheet so a difference is read across a row rather than reconstructed from
  memory.
  - **Reached from a profile.** Every profile carries a quiet *"Összehasonlítás"*
    link on its navigation row — opposite the "back to the list" link, since it too
    is a way *out* of the page — which opens the comparison **with that person
    already in the first column** and an empty slot inviting the next. The reader
    never has to find the page first and then look both people up, and the offer
    never competes with the person whose page it is.
  - **No selection is kept between pages.** The list pages deliberately carry no
    "add to comparison" control and there is no basket: a comparison is assembled on
    the comparison page itself, by its own name picker. The reader's choice therefore
    lives in exactly one place — the URL below — so a comparison can never open with
    someone they don't remember choosing, and nothing about who was compared is
    stored on their machine (PRIV-1).
  - **The comparison is a page, with an address.** The people compared live in the
    **URL** (`/representatives/compare?ids=…`), so a comparison is shareable,
    citable and bookmarkable exactly like a profile — and honours the global cycle
    scope (§4A) like every other page, since "who spoke more" is only ever a
    question about a span of time.
  - **What is compared** is the record the site already holds, never a new
    measurement: the identity rows (faction, constituency, office, highest
    qualification), then the countable ones — speeches, speaking time, sentences,
    average speech length, sitting days spoken on, own motions submitted, irományok
    by kind, roll-call participation and absence, committee seats, published asset
    declarations. Every figure is the **same number the profile shows** for the same
    scope, computed by the same code (REP-3/REP-7/STAT-1), so the two pages can
    never disagree; each row links back to the profile it came from. The **mandate**
    (REP-14) is deliberately *not* a row: a term with its own dates, its termination
    and its handovers is the profile's subject, and flattened into one comparison
    cell it is a date range too cramped to read and too easily misread — the column
    heading links to the profile that states it properly.
  - **Comparison must not become a scoreboard.** The largest value in a row is
    marked **neutrally** ("a legnagyobb érték ebben a sorban") and a row's bars are
    scaled to that row's maximum — the page states *who spoke more*, never *who is
    better*. Rows where the numbers are not comparable are **not shown as if they
    were**: a metric the corpus cannot supply for one of the people (a non-MP's
    roll-call participation, an advocate's constituency) reads as an explicit "nem
    értelmezhető", never as a zero (TRUST-1, cf. REP-3's hidden-not-faked rule).
  - **Served by one endpoint** (`/representatives/compare`), so a four-way
    comparison is one request rather than a fan-out of a dozen, and it reads only
    the precomputed aggregates the profile reads (REP-7). A person id in the URL
    that no longer resolves is **reported and skipped**, not fatal: a comparison
    link shared before a cycle re-import must still open for the people it can
    still name.
  - Degrades per EXT-6: with the Bills or Votes module disabled the rows that
    depend on it are absent, not faked, exactly as on the profile.

- **REP-16 (MAY).** **Astrological signs.** The Wikidata join already carries each
  person's **birth date** (§4.1), and the scraper derives two signs from it: the
  **sun sign** and the **Chinese zodiac animal**. Both are surfaced — on the profile
  and on the comparison (REP-15) — as **openly labelled trivia**, behind a spoiler on
  either page.
  - **Framed as trivia, not as a finding.** This is a transparency site whose
    credibility rests on every number on it being checkable and meaningful; a sign
    printed in the same visual register as a voting record would corrode exactly
    that. So the signs sit apart from the statistics — on the profile, tucked into
    the top-right corner of the identity card, out of the reading path of the
    biographical block rather than appended to it; on the comparison, at the foot of
    the page, below the table rather than as rows inside it — visibly lighter than
    everything around them, with a note saying what they are: derived from a birth
    date, of no analytical value, and offered for curiosity.
    They are never combined with an activity figure, never aggregated into a
    "which sign speaks most" claim, and never used to sort or rank anybody.
  - **Behind a spoiler, on both pages.** Nothing astrological is visible when a
    profile or a comparison opens: the profile's corner holds a single **⛎** glyph,
    the comparison holds the same glyph under its table, and the signs appear only
    when the reader presses it (and collapse again when they press it a second time). A
    reader who came for a voting record is never shown a horoscope on the way; a
    curious one is one click from it. The trigger deliberately uses **Ophiuchus** —
    the one zodiac glyph that is never among the twelve the site prints — so the
    button itself cannot give the sign away. It is hidden from assistive tech as
    well as from the eye while collapsed, so the reveal means the same thing in
    both. The state is per page view: it is not remembered, and the next profile or
    comparison starts collapsed again.
    On the **comparison** the signs sit at the very foot of the table, below the
    "Adatlap" row, behind a trigger row holding nothing but the glyph. They stay
    *rows of that same table* — the columns are the same people and have to line up
    under the same heads, and a table of their own would resolve its own widths and
    drift out of register as soon as fewer than four people are compared — but they
    are outside the spec sheet's sections: "csak a különbségek" has no business
    hiding a piece of trivia as though it were a figure two people happened to
    share. The whole block, trigger included, is absent when not one of the chosen
    people has a sign.
  - **The birth date itself is not published.** It is the input the signs are
    derived from, and a sign is a far coarser disclosure than an exact date, so the
    endpoints serve the signs and keep the date internal (§4.1). A reader who wants
    the date has the Wikipedia/Wikidata link the profile already carries.
  - **Missing means missing.** Only about four in five sitting MPs have a
    day-precision Wikidata birth date, so a great many people have no sign at all —
    and it is never guessed from a partial date (a year-only P569 statement is
    rejected at scrape time, not rounded). In the **comparison**, once the spoiler is
    open, the cell says
    **"nincs adat"**, which the table keeps distinct from **"nem értelmezhető"**: one
    means the fact applies and is unknown, the other that it does not apply to this
    person at all, and printing the wrong one is a factual error (TRUST-1). On a
    **profile** the line is simply absent instead — there is no neighbouring column
    for a "no data" cell to line up with, and an absent piece of trivia needs no
    announcement.
  - Signs travel as **language-neutral keys** (`taurus`, `dragon`) so the display
    label stays a UI concern in both locales (I18N-1), including the Chinese animal
    year, whose turnover at the lunar new year is tabulated rather than computed.

- **REP-17 (SHOULD).** **Remuneration (*tiszteletdíj*).** "What does a
  representative earn" is among the first things a reader asks, and the House
  answers it: parlament.hu publishes each MP's **monthly gross fee**
  (`kepviselo-javadalmazasa-query`, pulled by the registry sync alongside the
  other per-MP detail). The profile carries that published figure in its own
  panel, newest month first, **with the statute read against it**.
  - **The figure is official; the calculation only explains it.** The Ogytv.
    fixes every fee as a multiple of one base amount, so dividing the published
    figure by the §104(1) base recovers the statutory **rate** and the **section**
    that sets it. That division is exact arithmetic on official data, and the
    panel shows it — a bare forint amount tells a reader nothing about why it is
    that and not something else.
  - **Published, not computed, because computing it is wrong too often.** Derived
    against 45 sitting MPs, a rule-table calculation matched 36 and missed 9. It
    cannot see a mid-month change, an absence deduction under §107, an office the
    registry does not record, or the part-month of a mandate that ends — one
    departing member's last month came to 253 450 Ft, which no rule table would
    ever produce. The statute gives the rate; only the House knows the payment.
  - **An office is named only where our own record agrees with what was paid.**
    Several offices share a rate (2.4× is a Deputy Speaker's fee, a standing
    committee chair's, a House Steward's and a deputy faction leader's), so the
    amount alone cannot say which applies. Where a committee seat or office we
    hold carries exactly that rate, it is named; where nothing of ours does — or
    where ours would contradict the payment — the panel states the rate and every
    section that sets it, and asserts no office. Claiming a post the payroll
    contradicts is the one thing this panel must never do.
  - **A month that is not a clean multiple is reported as such.** A part-month or
    a §107 deduction divides to no statutory rate, and the panel says so rather
    than rounding onto the nearest rule and inventing a fact.
  - **What it excludes is said, not implied.** The figure is a fee, not a total:
    it leaves out the **költségtérítés** frames (accommodation, office, staff,
    travel), which are reimbursements against a budget and whose drawn amounts are
    not published; and for a minister or state secretary it is only a
    **component**, since §106(2) pays the parliamentary fee on top of a government
    salary fixed by another law. Both are stated on the card.
  - **No published month, no panel.** This needs no special-casing for former MPs,
    ended mandates or advocates: the House publishes months, so someone who is not
    paid simply has none. The panel is **not cycle-scoped** — it is a fact about
    now, like `is_mp` — and the series currently begins **2026-07-01**, when
    publication started.
  - **The base is a curated, dated constant.** §104(1) states it as a formula
    (1.8× the KSH average gross wage of the preceding year, in force each 1 March),
    but which KSH series the House applies is not pinned down by the text, so the
    amount is carried as a dated, sourced constant rather than evaluated. It is
    **confirmed rather than inferred**: every clean published fee divides by it to
    a statutory rate exactly, which a wrong base could not do. A new entry is due
    each March; past entries are never edited.

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
case with their own page and the rich detail sheet (BILL-7); the **question
types** (kérdés, interpelláció, azonnali kérdés — `fotipus` A/I/K) have a page
of their own too (BILL-13), because they are ~90% of everything that is not a
törvényjavaslat and they carry attributes no other type has; and **every
iromány type together**, those two included, is browsable on a third page
(BILL-9). All three are the same data layer and the same detail view, scoped by
`fotipus`: they are three questions put to one list, not three lists.

- **BILL-1 (MUST).** A **browsable, filterable list of bills**, paginated and
  filterable by **status**, **type**, **sponsor**, and **free text**; filters
  combine. List/filter state is in the **URL query** (deep-linkable, shareable) —
  including the sponsor filter so a link like `/bills?sponsor=<personID>` reopens
  the list scoped to that representative. The **electoral period** is set by the
  global cycle selector (§4A). The free-text filter matches the **title *and* the
  iromány number** — readers cite an iromány by its number (`T/438`) as often as
  by title, so typing one must find it, and a fully typed number **ranks ahead**
  of the longer numbers it is a prefix of (`T/438` before `T/4388`). Text
  matching is **accent- and case-insensitive** (§4B FOLD-1).
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
- **BILL-12 (MUST).** The **1994-98 cycle (35)** is ingested from the **static
  iromány archive** `parlament.hu/iromany/`, not from the API: the Felicitas
  `iromany` query returns **zero** documents for that term, so the site the House
  published at the time (generated HTML, frozen 1998-04-03) is the only source
  for its 5 646 irományok. The scraper parses it into the same record shape as
  the API path, so nothing downstream special-cases the cycle
  (`scraper/parlamonitor/bills/legacy.py`; see its README section for the layers
  and their request cost). Constraints that follow from the source, and must not
  be papered over:
  - the pages are **ISO-8859-2**, and their accented letters are HTML entities
    whose *names* were chosen for the byte values — `&otilde;`/`&ucirc;` mean
    ő/ű, so a plain unescape silently mis-renders exactly the letters that make
    Hungarian Hungarian;
  - submitters carry the archive's own MP-page id, which **is** the cycle-35
    `personID`, so sponsorship joins per BILL-3 with no name matching;
  - the era recorded **no** legislative-stage diagram (BILL-4 degrades to the
    event history), **no** vote tallies, no deadlines and no background
    documents; those stay empty rather than being inferred;
  - the four event kinds that name a **responding tárca** are renamed to the
    API's vocabulary — enumerated, with the archive's wording kept on the event —
    because the §6C derivation matches event names exactly; the era's *ministry*
    names still need entries in the §6C portfolio table;
  - the archive's per-document **speech list** and a question's **addressee** are
    parsed and kept in the scrape output but not loaded: neither has a column
    that means it (the addressee for the same reason `cimzettNeve` is left out of
    the modern scrape).
- **BILL-9 (MUST).** **Every** iromány is browsable on one **filterable list
  page** ("Minden iromány") — the section's catch-all tab, for a reader who does
  not know which type a document is, or who wants the types side by side.
  Paginated and filterable by **document type** (interpelláció, kérdés,
  határozati javaslat, …), **status** and **free text** (title *and* iromány
  number, as BILL-1); filters combine and live in the **URL query**
  (deep-linkable). Type and status are **multi-select** — several categories can
  be in scope at once (a reader after "questions" wants kérdés *and*
  interpelláció), each value repeated in the query param, an empty selection
  meaning "all" as with the cycle scope (§4A). The **electoral period** is set by
  the global cycle selector (§4A). The free-text filter is
  **accent-insensitive** (§4B FOLD-1). It shares the Bills module's data layer
  and `/api/v1/bills` routes — passing **no `fotipus` scope at all**, where the
  bills page passes `main_type = T` and the kérdések page `main_type_in = A,I,K`
  (BILL-13) — and the **same detail view** (BILL-2/BILL-7), so no data, table or
  detail logic is duplicated. The three browse pages therefore **overlap by
  design**; what is not duplicated is the *detail* address, which stays one
  canonical per iromány (`/bills/:id` for a törvényjavaslat, `/documents/:id`
  for everything else, §SEO-2). With a `sponsor` in the URL the page is one MP's
  complete iromány list, which is what the profile's submitted-documents stat
  (REP-3) links to and counts. Each MP submitter links to their profile through
  the shared `person` entity (EXT-2), exactly as on bills (BILL-3).
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
  routes and a frontend page — in the Elemzések section (§4E), disabled with the
  rest of the module wherever it sits (EXT-6).
  The Sankey diagram is **embeddable** (§4C).

- **BILL-13 (SHOULD).** The **question-type irományok** — *kérdés*, *írásbeli
  kérdés*, *interpelláció* and *azonnali kérdés* (Felicitas `fotipus` A/I/K) —
  have their own **browsable, filterable list page** ("Kérdések") at
  `/bills/questions`, the **middle tab** of the Törvényjavaslatok section,
  between the törvényjavaslatok page (BILL-1) and the all-irományok page
  (BILL-9). It exists because these are **~90% of every non-törvényjavaslat
  iromány** (90 839 of 101 060 in the corpus): on a list of everything they bury
  each other and every other type, and they carry attributes no other iromány
  has, which had nowhere to live.
  - Filterable by **question type**, **status**, **CAP policy topic** (TOPIC-8)
    and **free text** (title *and* iromány number, as BILL-1, accent-insensitive
    per §4B FOLD-1), plus the three filters that only mean something for a
    question: **whether and how it was answered** (answered either way / from
    the floor / in writing / not at all), the **answering tárca** (§6C, MIN-7),
    and the asking MP's **verdict** on the answer (accepted / rejected — in
    practice interpellációk only). Filters combine, are **multi-select** where
    the value set is a category (type, status, topic) and live in the **URL
    query**, so a filtered list is shareable and citable. The **electoral
    period** comes from the global cycle selector (§4A).
  - The answer state is **derived at query time** from the answer events, never
    stored: *answered* is the presence of an answer event and *unanswered* its
    absence. That the absence of a record is not the same as a ministry that
    failed to reply is **said on the page** (TRUST-1): for a recently submitted
    question "nincs válasz" may simply mean the statutory deadline has not
    passed. Facets (type, status, topic, tárca) are scoped to the slice on
    display and a value matching nothing is not offered, so no filter is a dead
    end.
  - It adds **no data layer of its own**: `/api/v1/bills` with
    `main_type_in=A,I,K` — the same endpoint, row shape and detail view the other
    two browse pages use (BILL-9) — plus `answer_state` on that list and the
    `fotipus` include/exclude on `/api/v1/bills/facets`. An opened question keeps
    the **canonical detail address of every non-törvényjavaslat**,
    `/documents/:id` (§SEO-2), so this page adds a browse URL and no duplicate
    detail URL; its breadcrumb, however, names *Kérdések* as the page the
    document reads under.
  - It is **not** at `/questions`: that address is the shipped 301 to the
    Kérdések **Sankey** (BILL-11), which is a different page and keeps its
    indexing. The two are siblings rather than rivals — this page links to the
    analysis ("where do these questions travel?"), and their **titles differ**
    ("Kérdések és interpellációk" vs "Kérdések elemzése") so neither is filed as
    a duplicate of the other. Part of the Bills module's vertical slice
    (BILL-5): it disappears with the module (EXT-6), and its tárca filter
    disappears with the §6C module on its own.

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
  (*Frakcióelemzés*, a page of the Elemzések section — §4E; a Votes sub-tab until
  that section existed): computed house-wide over the cycle's
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

## 6C. Functional Requirements — Module: Portfolios (Tárcák / minisztériumok)

Almost everything the House puts to the executive is addressed to a **tárca** — a
ministry, or a portfolio held by a *tárca nélküli miniszter* — and almost
everything the government puts to the House comes back through one. The corpus
already records that relationship in four separate places, but only ever as a
**free-text office label** on a row, never as an entity: so the most natural
question a citizen has about the government side of the record — *what was put to
this ministry, and what did it put to the House* — cannot be asked at all today.
A reader who wants the interior ministry's year has to recognise
*„Belügyminisztérium államtitkára”*, *„belügyminiszter”* and *„kormány
(belügyminiszter)”* as the same thing by eye, across 67 000 irományok.

This module makes the portfolio a **browsable dimension of its own**, in both
directions: what **arrived at** a tárca (questions, interpellations, the speeches
answering them) and what **came from** it (the irományok it submitted, the
speeches its minister and state secretaries gave).

- **MIN-1 (MUST).** The unit is the **portfolio**, not the person and not the
  office title: one entity per *tárca* (Belügyminisztérium, Agrárminisztérium,
  Miniszterelnökség, …), to which the corpus's many surface forms of the same
  thing resolve — the ministry noun (*„Belügyminisztérium államtitkára”*), the
  minister's title (*„belügyminiszter”*), the older adjectival form
  (*„közigazgatási és igazságügyi minisztériumi államtitkár”*) and the
  government-submitter parenthetical (*„kormány (belügyminiszter)”*) are one
  portfolio, not four. A **person is not a portfolio**: ministers change, the
  tárca persists, and the office-holder registry (REP-2a/REP-11) already dates who
  held it when — so the portfolio **links to** those people rather than duplicating
  them (EXT-2).
- **MIN-2 (MUST).** A portfolio is linked to the corpus **four ways**, each from
  data the corpus already holds:
  1. **addressed to** — the iromány's *címzett* (MIN-4), i.e. every kérdés,
     interpelláció and azonnali kérdés put to that tárca, answered or not;
  2. **answered by** — the responding office named on the answer event
     (`bill_event.related_label` on *kérdés/interpelláció … megválaszolva*),
     which today drives the Kérdések Sankey's answerer column (BILL-11);
  3. **submitted by** — the government submitter on an iromány
     (`bill_sponsor.label` of the form *„kormány (…)”*), i.e. the bills and other
     writings the tárca itself laid before the House;
  4. **spoken for** — the plenary speeches whose speaker spoke **in** that office
     (`speech.speaker_office`, §4.1), so a tárca's own voice in the debate is part
     of its page.
  Links 2–4 need **no new scraping whatsoever**; link 1 needs two extra fields
  from a query the scraper already runs (MIN-4).
- **MIN-3 (MUST).** Resolution from label to portfolio is **auditable and
  configurable, never silently inferred** (OPS-4 / TRUST-1). The label space is
  small and closed enough to be checked by hand — corpus-wide the answer events
  use **94 distinct responder labels**, the government submitters **47**, the
  speeches **55** — so the mapping is a **checked-in table, seeded from the data**
  and reviewable in one sitting, not a morphological guess at runtime. Its rules:
  - A label the table does not cover **stands as its own portfolio** under its own
    name. An unmapped tárca is a visible gap to be fixed in the table; silently
    folding it into a neighbour, or dropping it, would misreport the record.
  - **Non-ministry state bodies are kept and labelled as such**, not discarded and
    not called ministries: the House also addresses the *legfőbb ügyész* (1 215
    answers), the *Magyar Nemzeti Bank elnöke*, the *Állami Számvevőszék elnöke*
    and the *alapvető jogok biztosa*. They belong to the same view — a reader
    looking for "who answers to Parliament" wants them — under their own category.
  - A **tárca nélküli miniszter** is a portfolio in its own right, keyed by its
    parenthesised remit (*„tárca nélküli miniszter (családokért felelős)”*),
    because that remit is the thing questions are addressed to.
  - Matching is **accent- and case-insensitive** (§4B FOLD-3), since the same
    tárca appears both capitalised as an institution and lowercased as a title.
- **MIN-4 (MUST).** The iromány's **addressee (*címzett*) is captured** at
  ingestion. Upstream carries it on the per-iromány header sheet the scraper
  **already fetches for every document** (`onallo-iromany-adatlap-for-intra-query`,
  BILL-7) as a `cimzettNeve` sub-table of `(nev, allamiSzerv)` — the **office
  addressed** (*„belügyminiszter”*) and the **state organ** it belongs to
  (*„Belügyminisztérium”*) — so capturing it costs **no additional request**, only
  two more mapped fields. It matters beyond convenience for two reasons: it is the
  only record of the tárca a question was put to when the question is **still
  unanswered** (~8% of the corpus's questions, and by definition every pending one
  in the live cycle), and its `(nev, allamiSzerv)` pairing is **upstream's own
  statement** of which office belongs to which organ — the ground truth MIN-3's
  table is seeded from, rather than a rule we invented.
  - Because the detail sheets already scraped were cached (SCR-2) before this
    field existed, a **targeted backfill** re-runs the header query alone for
    documents missing an addressee — one cheap request per document, not the ~9 of
    a full detail re-fetch, and skippable entirely for a corpus that does not want
    it (the module degrades to link 2 alone, MIN-10).
- **MIN-4a (MUST).** A link belongs to the cycle it **happened in** — the date the
  tárca answered or submitted — **not** to the cycle its iromány is filed under.
  `parlament.hu` re-lists an iromány that is still in progress under the **new**
  cycle, so a bill the government submitted in 2024 comes back as a cycle-43 row.
  Attributed to its parent's cycle, a ministry **abolished** at the change of
  government reappears in the new cycle's listing on the strength of a document it
  filed two years earlier — which reads as a claim that the ministry still exists.
  The count and the **document list behind it are scoped the same way**, so the two
  can never disagree (cf. VOTE-6); a link whose date resolves to no known cycle
  keeps its iromány's, so the date rule can never drop it out of every scope.
- **MIN-5 (MUST).** A **browsable list of portfolios**, carried as its **own page
  in the Representatives section's tab bar** (§REP-1's precedent, beside the
  office holders of REP-11 — the tárca is the institution those offices belong to,
  so the two are read together), scoped by the global cycle selector (§4A) and
  ordered by activity: each row names the tárca, **who held it in scope** (linked
  to their profiles, REP-2), and its headline counts — irományok addressed to it,
  irományok it submitted, plenary speeches in its name. Its text filter is
  accent-insensitive (§4B FOLD-1). Non-ministry bodies (MIN-3) are grouped apart
  from the ministries so the list reads as what it is.
- **MIN-5a (MUST).** "Who held it" on that row is **every holder of the tárca's
  most senior rank in scope** — a minister over a state secretary, the head of a
  body over its deputies — each with the **years of their term** beside them, and
  consecutive terms of the same person shown as one span (a minister re-appointed
  at each election is one stint, not four names). A scope of several cycles
  usually means several ministers, and named by the newest alone the row dated the
  whole ministry to whoever holds the post now: Belügyminisztérium read as the
  2026 minister's even for a reader who had asked for 2010–2014 as well. The ranks
  below the top, and the exact dates, stay on the profile (MIN-6) — the row places
  a name in time, it is not the register.
- **MIN-6 (MUST).** A **portfolio profile** at its own deep-linkable URL, showing:
  - the tárca's **office holders** in scope — minister and state secretaries, each
    with the dates of their term (REP-2a) and a link to their profile. Scoped by
    the term's **start**, not by REP-11's overlap: an outgoing government serves
    until the new one is sworn in, so every one of its ministers overlaps the
    cycle that replaced them, and by overlap a ministry's panel for a new cycle
    opens with the *previous* government's whole bench above the people actually
    running it. The exception is a term that is **still open**, kept whenever it
    began before the scope ended — the offices deliberately not synchronised with
    the House (the MNB's governor and deputies, the ombudsman, the Állami
    Számvevőszék, the legfőbb ügyész) run six to nine years across cycle
    boundaries, and a start-date-only rule would empty their panels of the very
    people holding them now. The scope is the **union of the selected cycles, not
    the span between them**: a selection that skips a cycle must not list the
    holders of the cycles it skipped, since the counts beside them are a per-cycle
    union (MIN-4a) and the two would then disagree on the same page;
  - **what was put to it**: the questions/interpellations addressed to it,
    paginated and newest-first, each showing its asker (linked, EXT-2), its
    status — **answered in plenary / answered in writing / still unanswered** —
    and linking to the iromány detail (BILL-2);
  - **what it submitted**: the irományok the government laid before the House
    through it, reusing the bills list rows (BILL-1/BILL-9);
  - **what it said**: the plenary speeches given in its offices, each linking into
    the viewer (VIE-5), subject to MIN-10's coverage caveat;
  - a small **over-time chart** of questions received, which honours the cycle
    scope and is **embeddable** (§4C).
- **MIN-7 (SHOULD).** The portfolio is also a **filter on the views that already
  exist**, so a reader who is on the irományok page does not have to leave it: the
  bills and egyéb-irományok lists (BILL-1/BILL-9) gain a portfolio filter in
  **both senses** — *addressed to* and *submitted by* — held in the **URL query**
  like every other filter (§CYC-5), and the Kérdések Sankey's answerer nodes
  (BILL-11) **link to the portfolio's profile**, so the diagram becomes a way in
  rather than a terminus. The Sankey's answerer grouping MUST then be the **same
  resolution** as MIN-3 rather than the raw labels it groups by today, so the
  diagram and the profile can never disagree about who answered what.
- **MIN-8 (SHOULD).** The profile carries the two figures a tárca is actually
  accountable for: **how many of the questions put to it were answered**, and
  **how long it took** (submission → answer event, median). The median MUST be
  computed over **written questions only** (`írásbeli kérdés`): their span is the
  tárca's own, whereas an interpelláció or azonnali kérdés is answered from the
  floor and its date is set by when the House next sat — so a mixed median would
  report the sitting calendar and make a plenary-heavy remit look slower. Both are
  computed only over questions whose outcome is on record, both state their method
  (TRUST-1) — including which questions the median covers — and neither is
  presented as a score or a ranking of ministries against one another (§1.2 — no
  editorializing). A question still within its statutory answer deadline is
  **pending, not late**, and is counted as such.
- **MIN-9 (MUST).** **Renames are disclosed, never silently merged.** Hungarian
  ministries are renamed, split and merged at nearly every change of government
  (*Nemzeti Erőforrás Minisztérium* → *Emberi Erőforrások Minisztériuma* → carved
  up in 2022), and upstream's own `allamiSzerv` sometimes files an office under a
  **successor** organ rather than the one that existed at the time — the same
  *„nemzetgazdasági miniszter”* comes back under both *Nemzetgazdasági
  Minisztérium* and *Gazdaságfejlesztési Minisztérium*. Therefore:
  - a portfolio is labelled with the name **in use in the cycle being viewed**,
    not today's name back-projected onto history;
  - where the mapping table records a **rename**, the profile says so and links
    the predecessor/successor, so a reader can follow the thread themselves;
  - the site never asserts a **merge or split** as an identity. Continuity that
    the source does not state is a claim about machinery-of-government, not a
    data fact, and belongs to the reader.
- **MIN-10 (coverage & degradation).** Each link kind degrades on its own, and the
  page says which one is thin rather than showing a confident zero:
  - links 2 and 3 are **complete today** across all five cycles — 92% of the
    corpus's 58 840 question-type irományok already carry a named responder
    (54 252), and all 2 461 government-submitted irományok carry their tárca in
    the submitter label;
  - link 1 arrives with the backfill (MIN-4) and is **absent, not empty**, until
    then;
  - link 4 is **partial by construction**: `speech.speaker_office` is populated
    only for the cycles scraped since it was added (39 and 43), so the other
    cycles show **no** speech attribution rather than an implied silence, until a
    re-scrape (SCR-2) fills them. The profile states the coverage it is drawing on.
- **MIN-10a (v1 scope).** v1 is built **entirely from data already in the
  database** — links 2, 3 and 4 (MIN-2) — so it ships with **no scraper change and
  no re-scrape**: the resolution table, the derived tables, the API, the two pages
  and the MIN-7 filters all read rows the loader already writes. **MIN-4's
  addressee is deferred**, and with it the *addressed to* sense of the MIN-7
  filter and the unanswered questions on a profile: until it lands, "what was put
  to this tárca" means the questions it **answered**, and the page says so rather
  than implying the set is complete. Adding MIN-4 later is purely additive — two
  fields on a query the scraper already runs, plus the backfill.
- **MIN-11 (MUST).** The module is a self-contained vertical slice per EXT-1..6:
  its own loader step and `portfolio` / `portfolio_alias` / iromány-link tables
  derived from the shared `bill`, `bill_event`, `bill_sponsor`, `speech` and
  `person_office` rows (EXT-2 — no duplication of them), `/api/v1/portfolios`
  routes, and its own frontend views. It **owns no scraping of its own** beyond
  MIN-4's two fields on an existing query. Disabling it via `PARLAMONITOR_MODULES`
  removes its nav entry, routes and the MIN-7 filters — no errors (EXT-6) — and
  the module is itself dependent on Bills (§6A): with Bills disabled it hides the
  iromány halves rather than failing.
  > **✅ realized (v1, per MIN-10a).** `app/portfolios.py` holds the reviewed
  > table — 92 tárcák over the corpus's 240 distinct office labels, with the
  > personal commissions excluded by name — and the loader's `rebuild_portfolios`
  > derives `portfolio` / `portfolio_alias` / `portfolio_bill` / `portfolio_speech`
  > / `portfolio_office` from rows the other modules already wrote (56 758 iromány
  > links, 6 864 speeches, 930 office terms; `migrate_portfolios.py` builds them
  > into a live DB with no re-scrape and no full rebuild). `/api/v1/portfolios`
  > serves the listing, the profile, the year trend and the speech panel; the
  > iromány panels are `/api/v1/bills?portfolio=…&portfolio_role=…` (MIN-7), so
  > there is one list implementation. The pages sit in the Representatives tab bar
  > as *Tárcák*, and the **Kérdések Sankey now groups its answerer column by the
  > same resolution** — before this a single ministry occupied two nodes, one for
  > its minister and one for its state secretary.
- **MIN-12 (MUST).** The derived tables **track both of their inputs on the
  continuous-sync path** (OPS-3), with no hand-run migration on a deployment.
  Being derived, they are only as fresh as the last rebuild, and they have two
  kinds of input that go stale independently:
  - **the rows.** Only one of MIN-2's four links is a sitting; the others come from
    `bill_event` / `bill_sponsor` (newly answered and newly submitted irományok)
    and `person_office` (the roster). Between sitting weeks a poll brings in the
    first two and no transcript at all, so an incremental update that rebuilds §6C
    only when a *sitting* changed leaves the section lagging the irományok it is
    derived from — the one failure mode a reader can see and cannot explain.
  - **the mapping.** MIN-3's table is checked-in code, so correcting a label
    changes no processed file and is invisible to the update's file comparison. A
    DB therefore records **which mapping its rows were derived from**
    (`build_meta.portfolio_map`, a fingerprint over the effective table including
    an OPS-4 override), and a mismatch is itself a reason to rebuild. A corrected
    label reaches the site on the next sync pass after the deploy, rather than
    waiting for whatever unrelated sitting next happens to land.

---

## 6D. Functional Requirements — Module: Settlement mentions (Települések)

Parliament is national; almost everything it argues about is local. A road, a
hospital, a factory, a flood, a closed school — each belongs to a **place**, and
the transcript names that place. Yet the corpus records places only as words
inside sentences, so the questions a citizen and a local journalist ask first
cannot be asked at all today: *has anyone in the House ever mentioned my town —
and if so, who, when, and in what?*

The inverse question is the sharper one. Hungary has **3 177 settlements**, and a
plenary term names a few hundred of them. The places that are **never** named are
not a gap in the data; they are the finding. This module therefore makes the
settlement a browsable dimension of the corpus — mapped, counted, and
deliberately showing its **blind spots** — and closes the loop back to the
representative: whether an MP elected by one constituency actually talks about the
places in it.

- **TEL-1 (MUST).** The unit is the **settlement as an entity**, not a place-name
  string: one row per settlement of the official register, carrying its name, its
  county, its coordinates and the constituency it belongs to. A mention resolves to
  that entity or it is not a mention — so *„Kaposváron”*, *„Kaposvár”* and
  *„kaposvári”* are three surface forms of one place, and counting them is counting
  a place rather than a word (which the word cloud already does, WCLOUD-6).
- **TEL-2 (MUST).** Extraction is **gazetteer-driven and deterministic**, run at
  **load time**, never on the request path (cf. WCLOUD-6). The register of
  settlements is a **closed list of known answers**, which is a fundamentally
  easier and more auditable problem than open-vocabulary place recognition: the
  matcher asks "is this token one of these 3 177 names, in one of Hungarian's
  case forms", not "is this a place". Consequently:
  - It needs **no model, no GPU and no network at scan time**, so it runs in every
    install (SCR-6) and every rebuild, and is fully unit-testable — the same
    property that makes the alignment step testable (TIM-4).
  - Morphology is handled explicitly, because Hungarian never names a place in the
    bare nominative when it can inflect it: the **case suffixes** a place takes
    (*-ban/-ben, -on/-en/-ön, -ra/-re, -ról/-ről, -ból/-ből, -tól/-től, -hoz/-hez,
    -ig, -ott/-ett/-ött, …*, with the stem lengthening *Kalocsa → Kalocsán* and the
    *-val/-vel* assimilation *Szeged → Szegeddel*), and the **adjectival/demonym
    form** (*szegedi*, *nyíregyházi*, *a szegediek*), which is how a speaker most
    often refers to a place at all (*"a kaposvári kórház"*).
  - **Procedural/chairing speeches are excluded** (STAT-1), as they are from the
    word cloud, the toplist and every statistic. This is not only consistency: the
    printed record's own boilerplate lives there, and the printer's colophon
    (*„Nyomda: … Bt., Vác”*) closes almost every sitting day — counted, it would
    have made Vác one of the most-discussed towns in Hungary.
- **TEL-3 (MUST).** **Precision is the requirement, not recall**, and the module
  MUST be explicit about why. This feature's headline claim is a claim about
  **silence** — "the House has never mentioned this village" — and a single false
  positive does not merely add noise, it *erases* a blind spot and asserts
  something untrue about a real place. Settlement names collide with ordinary
  Hungarian on a scale that makes naïve matching worthless: *Baj* (trouble),
  *Alap* (fund), *Hét* (seven/week), *Bár* (although), *Nyúl* (rabbit), *Pápa*
  (the Pope), with *Varga*, *Gyula* and *Szabolcs* also being the names of sitting
  MPs, and *Balaton*, *Velence*, *Zala* and *Hernád* naming a lake, a city, a river
  and a county far more often than the villages that share those names. The policy
  is therefore **graded, evidence-based and auditable**:
  1. **The existing entity layer vetoes.** A candidate whose characters fall inside
     a recognized **PER or ORG** mention (§4.1's NER layer) is rejected — this is
     what separates *Varga Mihály* from the village of Varga, and the *Nemzeti
     Foglalkoztatási Alap* from the village of Alap, without a hand-written rule
     per name.
  2. **Ambiguity is measured against the corpus itself**, not guessed: a name is
     **ambiguous by derivation** if the transcript *writes* it in **lower case** — as
     *alap* the fund rather than *Alap* the village — on a large enough **share** of
     the sitting days that write it at all; or if it is a Hungarian stop-word; or if
     it is the name of a person in the register. So the policy maintains itself as
     the corpus grows rather than rotting in code. Both halves of that measure are
     load-bearing, and each replaced a reading that failed in production:
     - **Case**, because it is the one signal Hungarian orthography guarantees here
       (a settlement is a proper noun, so a lower-case occurrence is somebody using
       the *word*) and because the measurement MUST need no model, exactly as the
       matching does (SCR-6). Reading the word cloud's lemma table (`word_doc_freq`)
       instead was only valid for the sittings a lemmatizer had actually analysed: it
       holds the raw transcript **lower-cased** for every sitting that fell back to
       the regex tokenizer, so on a deployment whose NLP covered one of eight
       electoral cycles it reported *every settlement in the country* as an everyday
       word — 1 300 of 3 178 demoted to needing a place cue, and 207 towns the House
       does name shown on the map as never named.
     - **A share, not a count**, because a count of lower-case appearances grows with
       how much a place is *discussed*: any absolute threshold eventually flags the
       county seats (*Miskolc*, *Győr*, *Nyíregyháza* and *Pécs* were all flagged
       even on a fully lemmatized corpus) while missing the villages called *Hét*,
       *Baj*, *Vál* and *Ura*. A measure of ambiguity MUST be independent of how
       loud a place is, or the feature erases the very places it exists to name.
  3. An ambiguous name must **earn** its match: the strongly ambiguous ones require
     an explicit **place cue** in the sentence (*község, város, település,
     polgármester, önkormányzat, határában, lakossága, …*), the weakly ambiguous
     ones a **place-marking case suffix** — a bare capitalized token is not
     evidence, least of all at the start of a sentence, where capitalization says
     nothing at all. **Which tier a name falls into is measured as well**, by a
     question of its own: does the *word* ever take those place endings? "Bajban
     vagyunk" is trouble, so no ending can vouch for the village of *Baj* and only a
     cue will do; nobody is ever "hatvanban", so a capitalized *Hatvanban* is the
     town of Hatvan and needs nothing further. Deciding this by how word-like a name
     looks instead would hold a real town of 20 000 to the strictest gate the module
     has, which is a blind spot invented in a different way.
  4. A **small hand-reviewed table** carries only what derivation cannot see — the
     lake, the river, the world city — each entry naming the homonym it exists for
     (the pattern §6C/MIN-3 already established: a checked-in table seeded from the
     data beats a runtime guess).
  5. Names that are also **county** names are rejected when the sentence goes on to
     say *megye/vármegye* (*"Veszprém megyére"* is not the town of Veszprém).
  Rejections are **counted by reason** in the load log, so the policy's effect is
  observable rather than a matter of faith, and tightening it is a measurable change.
- **TEL-4 (MUST).** A mention is stored **per sentence**, which makes it a
  **citation**: from any count on any of this module's pages a reader reaches the
  sentence that produced it, and from there the speech, the speaker and the moment
  in the video (VIE-3/VIE-5). A number that cannot be opened is not evidence, and
  every other aggregate on this site already honours that (WCLOUD-4, SEA-4).
- **TEL-5 (MUST).** Resolving a settlement to a **real place on the earth** reuses
  the **National Election Office** data the constituency lookup already depends on
  (REP-10): the settlement register, each settlement's published **centre point**,
  and the constituency it belongs to. This adds **no new external dependency** —
  the same cached, version-resolved, gracefully-degrading source, named on the page
  as the one thing here that is not from `parlament.hu` (TRUST-1).
  - **Budapest is handled as what it is**: the register knows only its 23
    districts, while speakers say *"Budapest"* — by a wide margin the most-named
    place in the corpus. The capital is therefore its own entity **alongside** its
    districts, and a district mention is never silently promoted into it (nor a
    *"Budapest"* mention distributed across 23 districts it says nothing about).
  - **Where a settlement is drawn is not where the register centres it.** The
    office's point centres a settlement's *administrative territory*, which over the
    24 largest towns lands a mean 3 km (max 11 km) from where the basemap prints
    that town's name — so every dot missed its own label, which on a map whose job
    is *find my town* reads as a bug. The drawn point is therefore the
    **OpenStreetMap place node** the basemap labels the settlement at, matched to
    the register **by point-in-polygon** against the office's own boundaries (no
    name matching, no shared key) and checked in as a table
    (`app/settlement_points.csv`, from `build_settlement_points.py`), so a rebuild
    still needs no second network source. The register's own point stays the
    fallback for anything the table misses, and OpenStreetMap — already credited for
    the basemap — is credited for the points (ODbL).
- **TEL-6 (SHOULD).** The module's front page is a **map of Hungary** showing how
  often each settlement is named, over the active cycle scope (§4A). Mention
  frequency spans four orders of magnitude (Budapest against a village named once),
  so the scale MUST be **compressed and legend-stated**, never raw-linear — a
  linear scale renders every place except the capital identical and the map says
  nothing. Intensity is **not the only carrier of meaning** (A11Y-1): every
  settlement is reachable by name through a folded text search (§4B) and a ranked
  list beside the map, and the figure is **embeddable** (§4C).
- **TEL-7 (SHOULD).** **Blind spots are a first-class view, not an absence.** The
  map can be switched to show the settlements with **no mention at all** in scope,
  and the module reports the plain numbers — how many of the 3 177 were named, how
  many were not, and how that breaks down by county — because "1 800 named, 1 300
  never" is the finding a reader came for. Two honesty rules bind this view:
  - It states that it measures **plenary mentions in the transcript corpus**, not
    attention in any wider sense: a village can be well served and never named on
    the floor of the House.
  - It states the **cycle scope** it was computed for, since "never mentioned" over
    one term is a different claim from "never mentioned in the corpus".
- **TEL-8 (SHOULD).** Each settlement has its **own page**: how often it was named
  and when (the same over-time histogram shape as SEA-8), **who named it** (ranked
  speakers, each linking to their profile), the **constituency** it belongs to and
  the **MP who holds it** (REP-10's join, so the reader lands on the person
  responsible for the place), and a link into **proceedings search** scoped to it
  (WCLOUD-4's pattern) plus the citable mentions themselves (TEL-4). A settlement
  with **no** mentions still has its page, saying so plainly — that page *is* the
  blind spot, and it is deep-linkable so it can be cited.
  - **"Who represents it" MUST be answered for one named cycle** — the **latest cycle
    in the reader's scope** — and the page MUST state which. A seat has one holder at a
    time but several across the corpus, and `person.constituency` records the seat *a
    person held*, not who holds it: 95 of its 138 seat labels belong to more than one
    person, one of them to five. Matched on the label alone the page answered with
    whichever row came back first — neither current, nor the same on two identical
    requests, nor visibly wrong. So a candidate must be shown to have **sat in that
    cycle** (`person_mandate` where the DB has it, else the stored seat corroborated by
    a `membership` row), and a seat with no holder on record for it is **left
    unanswered** rather than back-filled from a neighbouring cycle: the labels come from
    the register of the *current* map, so reaching past a redistricting names the member
    of a differently drawn constituency that merely shares a name. The same resolver
    answers the constituency map's cells (TEL-16), so the two cannot disagree.
- **TEL-9 (SHOULD).** **Does an MP talk about their own constituency?** For every
  representative elected in a single-member constituency, the module reports two
  distinct measures over the cycle scope, and MUST NOT conflate them:
  - **Focus** — what share of their settlement mentions fall on settlements inside
    **their own** constituency;
  - **Coverage** — what share of the settlements **in** their constituency they have
    ever named, which is the local-newspaper question ("has our member ever said
    the name of our village out loud?") and the one that surfaces a blind spot
    *inside* a constituency.
  The rules that keep this fair:
  - A **list MP** (*országos/területi lista*) has no constituency, so both measures
    are **omitted, never zeroed** — exactly as an advocate's vote statistics are
    (REP-9). Absence of a denominator is not a score of nought.
  - The **electoral map belongs to one cycle** (REP-10's standing exception to
    CYC-2): boundaries are redrawn between elections, so the measure is computed
    against the constituency the MP actually held, and the page says which map it
    used rather than inferring one from the reader's scope.
  - It is presented as a **neutral fact, never a ranking of diligence** (§1.2): a
    minister speaks to the nation, a member of an urban list has no village to
    name, and a low share is not a dereliction. The page says this where the number
    is shown, and the measure appears on the representative's profile (REP-3) as
    well as in the module.
- **TEL-10.** Everything here is **cycle-scoped** (§4A CYC-2) — counts, the map,
  the blind spots and both TEL-9 measures — with the single, stated exception of
  the geography itself (TEL-5/REP-10), which answers for the cycle its election
  produced.
- **TEL-11.** Every count is a **precomputed aggregate** rebuilt by the loader with
  the other statistics (REP-7): per settlement per cycle, per settlement per
  speaker, and the two TEL-9 measures per representative. The request path reads
  rows and never scans transcript text, so this module's pages meet the same
  performance budget as the rest of the site (§8.2), and the map is one aggregate
  request independent of the list beside it (cf. WCLOUD-5).
- **TEL-12.** The module **states its method** wherever it states a number
  (TRUST-1/REP-5): that mentions are found by matching the official settlement
  register against the transcript with Hungarian morphology, that procedural
  speeches are excluded, that ambiguous names require corroboration, and — the part
  a reader most needs — that the extraction is **conservative by design**, so a
  count is a lower bound and a blind spot is "not found", not "provably never said".
- **TEL-13.** The module is a self-contained vertical slice per EXT-1..6: its own
  loader step and `settlement` / `settlement_mention` / aggregate tables derived
  from the shared `sentence`, `speech`, `person` and `entity` rows (EXT-2 — it
  duplicates none of them), its own `/api/v1/settlements` routes, and its own
  lazily-loaded frontend views. Its **pages sit in the Elemzések tab bar** (§4E),
  not in a section of their own: what the module produces is a calculation over the
  proceedings — which places get named, and by whom — which is exactly what that
  section gathers, and a top-level entry beside *Keresés* and *Szavazások* claimed a
  prominence the reader's own path does not. (Before Elemzések existed it sat in the
  Felszólalók tab bar on the Tárcák precedent, MIN-5.) It **owns no scraping of its own**. Disabling it via
  `PARLAMONITOR_MODULES` removes its nav entry, routes and the profile panel with no
  errors (EXT-6), and because its geography comes from the constituency lookup's
  source, `PARLAMONITOR_EVK_LOOKUP=0` (or an upstream outage) leaves the mentions
  intact and only the map unavailable — the feature degrades a layer at a time
  rather than failing whole (cf. REP-10, SCR-5).
- **TEL-15 (SHOULD).** The map can also be read **in segments** — the settlements
  binned into **equal-area H3 hexagons** and the bin shaded instead of the places —
  as an option beside the per-settlement points, not a replacement for them. The two
  answer different questions and the reader chooses which they are asking:
  - A point map answers *which places*, and is the right default: the unit of the
    data is a settlement, and a reader looking for their own town needs to find it.
  - It is, however, a poor picture of **where** attention falls, for two reasons a
    hexagon fixes. Point maps encode a quantity as area and then let it overlap, so
    the dense middle of the country reads as loud whatever the numbers say; and
    3 178 marks, most of them small, leave the eye no regional pattern. Binning
    aggregates the overlap away, and **equal-area** bins mean a cell is never loud
    merely for being large — the flaw that would make a county choropleth of this
    data useless, since Hungary's counties differ several-fold in area and hold from
    60 to 358 settlements each.
  - H3 specifically, rather than a square grid or an administrative unit: its cells
    are near-equal-area and near-equal-shape at a given resolution, they nest
    hierarchically so a coarser reading is the same data re-binned rather than
    recomputed, and hexagons have uniform adjacency — a square grid's diagonal
    neighbours are 1.41× further than its edge ones, which quietly distorts every
    visual cluster the reader thinks they see.
  - **The cell size is deployment config, not a reader-facing control** (OPS-4). It
    matters, and the trade-off is real — at H3 resolution 4 (≈1 770 km² a cell) Hungary
    is about 70 cells and the reading is regional; at 5 (≈253 km²) about 400, roughly a
    district each; at 6 (≈36 km²) about 1 800, finer than the settlement pattern itself
    and approaching the point map with hexagons drawn round it — but it is a *calibration*
    of one view, not a question the reader came with. Offered as a control it competed
    for attention with the two choices that are genuinely theirs (which binning, and
    which reading), and three knobs over one map is how a figure stops being read at
    all. So the deployment picks the size, precomputes it, and the endpoint names which
    one it served.
  - Both readings of the map (TEL-6 mentions, TEL-7 blind spots) survive the
    binning, and MUST stay visually distinct: a segment's **mention total** is shaded
    on the same sequential ramp as the points, while its **share of never-named
    settlements** is shaded on a neutral one — silence is not a low quantity of
    speech, and reusing the speech hue for it would say that it is.
  - **The denominator MUST be disclosed.** A share over one settlement is not a
    share, and at any workable cell size some cells hold exactly one — so every segment
    reports the counts behind its shading (`n named of m`), and the caveat under the map
    says so. This is the same obligation the per-MP ratios carry (TEL-9), for the same
    reason.
  - Binning is a **precomputed, cycle-scoped aggregate** like every other count here
    (TEL-11): each settlement's cell per offered resolution is derived at load time
    from its coordinates (which never change between register versions), so the
    request path groups stored rows and never geocodes. Note that a segment payload is
    **not automatically the cheaper one** — a hexagon costs seven coordinate pairs
    where a point costs one, so a coarse resolution is smaller than the point map and
    the finest is larger than it. One more thing the deployment is choosing when it
    picks a cell size.
  - It **degrades to the point map**: the H3 library is an ordinary dependency, but
    where it is absent the loader skips the cell table, the endpoint reports the
    segmented view unavailable and the UI offers no such option — the map itself is
    unaffected (EXT-6, and the same posture as every other optional layer here).
- **TEL-16 (SHOULD).** The map can also be binned into the **106 single-member
  constituencies** (OEVK), as a third option beside the points and the hexagons. This
  does **not** contradict TEL-15's argument against an administrative choropleth, and
  the distinction is the whole point of offering it:
  - A **county** choropleth is misleading because a county is arbitrary as a
    denominator — Hungary's counties differ several-fold in area *and* hold from 60 to
    358 settlements. A **constituency** is near-equal in **electorate** by law, so
    "the places of these ~75 000 voters were named N times" is comparable from one cell
    to the next in a way no other Hungarian territorial unit offers. Where the hexagon
    is the geographically honest unit, this is the *politically* honest one.
  - It is also the only binning with a **member**. A hexagon cannot be asked why the
    places inside it are never named; a constituency can, which closes the loop back to
    TEL-9 on the map itself: each cell reports who holds it and how much of it they have
    named.
  - **Its own distortion MUST be disclosed**, because it is exactly the one the hexagons
    do not have: constituencies are equal in voters but not in area, so a rural cell
    paints far more of the map than an urban one at the same number. The two segmented
    views therefore cover each other's weakness, and neither is presented as *the*
    segmented map.
  - **The cells overlap, and the payload MUST say so.** 23 settlements — Debrecen,
    Szeged, Pécs and the split Budapest districts among them — lie in more than one
    constituency, and a mention names the *place*, never the part of it that falls in
    one seat. Their mentions are therefore counted in **each** constituency the place
    belongs to: for the question "are my constituency's places talked about" that is the
    true answer, and it is already what TEL-9 does for the members who share such a
    city. Dividing the count between the parts would fabricate a precision the text does
    not carry. The consequence — that the cells do **not** sum to the national total —
    is stated under the map with the number of doubly-counted mentions, and each cell
    reports how many of its settlements it shares.
  - **The capital is in no cell, and that MUST be stated too.** *Budapest* is its own
    entity beside its districts (TEL-5) and spans sixteen constituencies, so its
    mentions — the single largest figure in the corpus, near a quarter of all of them —
    are attributed to none. Left unsaid, a quiet capital would be the map's most visible
    and most wrong claim. (Its districts still count, in the constituencies that hold
    them.)
  - The **ramp is stretched over the observed range**, not anchored at zero: every
    constituency holds twenty-odd settlements, so the quietest still counts in the
    dozens, and anchored at zero two of the four steps would fall where no cell lives —
    103 of the 106 sharing two colours. The legend states the numeric edges it lands on,
    so what is drawn is still readable back into a count.
  - Like TEL-15 it is a **precomputed, cycle-scoped aggregate** (TEL-11) served as
    GeoJSON, and the boundaries are **generalised at load time**: the office draws them
    for a street-level map — 99 000 vertices over the 106 — where this view shows the
    whole country at once, so a Douglas–Peucker pass (tolerance in deployment config,
    OPS-4) reduces them by roughly seven-eighths for a payload comparable to the point
    map's. It **degrades a layer at a time**: the names, seats and electorates come from
    a different upstream file than the geometry, so unreachable boundaries cost this
    binning alone — not offered rather than drawn as a country with holes in it.
- **TEL-14 (v1 scope).** v1 is built **entirely from data already in the database**
  plus the election office's register, so it ships with **no scraper change and no
  re-scrape**: the matcher, the derived tables, the API, the three pages and the
  profile panel all read rows the loader already writes. Two things are deliberately
  **deferred, and named here rather than implied**:
  - **The map is not yet an embeddable figure** (§4C EMBED-1). It is a strong
    candidate — a map of who gets talked about is exactly what an article wants — but
    the embed view is a per-figure registration, and the map is the one figure here
    whose payload is large enough to want its own thought about caching in a
    third-party page.
  - **The TEL-9 table is built but not yet linked.** *Saját körzet* — the
    per-representative focus/coverage ranking — is complete and its route is live, but
    no tab advertises it for now: it is the most easily misread page in the module (a
    ranking of members by a ratio, however carefully caveated, §1.2), and it is held
    back until that framing has been settled. The same measures remain visible where
    they carry their own context: on each MP's profile panel, and per cell on the
    constituency map (TEL-16).
  - **Colloquial place names beyond Budapest's districts** are not resolved. Within
    the capital the district names people actually use are recognised
    (*Zugló, Csepel, Józsefváros*, …), because without them all 23 districts would
    have been permanent blind spots; elsewhere a settlement is found by its register
    name and its inflections only. Historic or informal names for other places
    (a merged village still called by its old name, a district of a county town) are
    therefore missed — which is a **recall** gap, consistent with TEL-12's statement
    that a count is a lower bound, not a precision one.
  > **✅ realized.** `app/settlements.py` holds the matcher — morphology, the four
  > gates and the reviewed table — and is pure, so it is unit-tested on plain
  > sentences. The loader's `rebuild_settlement_case_usage` (gate 2's evidence, stored
  > per settlement) / `rebuild_settlements` / `rebuild_settlement_mentions` /
  > `rebuild_settlement_stats` need no other pass's output, and the mention pass is
  > scoped to the changed sittings on an incremental update — which leaves the case
  > evidence alone, since it is a corpus-wide ratio a single sitting cannot move;
  > `migrate_settlements.py` builds them into a live DB with no re-scrape and no full
  > rebuild. Over cycles 39–43 the pass measures the case evidence over 982 sitting
  > days (254 of 3 178 names are ever written in lower case; 197 end up ambiguous,
  > 142 needing a cue and 55 an ending) and finds **68 517 mentions of 2 020
  > settlements, leaving 1 158 never named** — and rejects 42 898 candidates, counted
  > by reason in the load log (24 919 vetoed by the entity layer, 11 091 for want of a
  > cue, 3 631 as county readings, 1 704 as sentence-initial bare names, 1 553 for
  > want of a place ending). `/api/v1/settlements` serves the listing, the map,
  > the coverage summary, a settlement's page with its citations and its MP, and the
  > TEL-9 measures; the frontend adds *Települések* as a page of the Elemzések
  > section (§4E), with the map + the blind-spot mode and a settlement page, plus a
  > panel on every MP's profile. A ratio ordering carries a **five-mention floor**, stated on
  > the page: ranked without one, the top of the list was whoever named exactly one
  > place that happened to be theirs.
  >
  > The map ships in **three binnings** (TEL-6, TEL-15, TEL-16), each a separate cached
  > aggregate over the same rows: 3 178 points, 398 hexagons at the configured ≈253 km²
  > (the loader precomputes 70 / 398 / 1 813 for the three sizes an operator may pick
  > from), and the 106 constituencies. Gzipped that is 66 KB, 33 KB and 79 KB, so no
  > binning is uniformly the cheap one. The constituency boundaries generalise from **98 858
  > vertices to 14 046** at the default 220 m tolerance (250 KB stored) in 0.6 s at load
  > time. Building it surfaced a **pre-existing** join gap worth recording: the county
  > was renamed *Csongrád → Csongrád-Csanád* in 2020 and parlament.hu never relabelled
  > the seats, so all four constituencies around Szeged had silently been matching no
  > member at all — in the TEL-9 table as much as on the map. `valasztas.COUNTY_ALIASES`
  > reconciles the two spellings at the join while the current name stays the one stored
  > and shown; the own-constituency measures went from 94 to 98 of the 106 seats.

---

## 6E. Functional Requirements — Module: Interjections (Közbeszólások)

The record of a plenary sitting is a list of speeches, and that shape quietly
asserts something false: that members take turns. They do not. They shout across
the chamber while somebody else holds the floor, and the shorthand writers keep
the loudest of it — in parentheses, verbatim, inside the speech it interrupted:

> …hogy ön fél **(Balla György: Úgy van!)**; ön fél attól, hogy…

Those words exist nowhere else. The heckler has no speech of their own to hang
them on, so they appear in no speech count, no speaking-time total and no speaker
facet; a search finds them only inside somebody else's speech, attributed to that
somebody. Roughly **99 000** of them sit in the corpus, unaddressable.

What they are, once lifted out, is not a longer transcript but a **different kind
of fact**: a directed relation between two named members. Who shouts, and over
whom. That relation is a thing about the House that the ordered list of speeches
cannot express at all — antagonisms, and their asymmetry (a member may be shouted
down far more than they shout), and how much of both crosses the aisle.

- **INT-1 (MUST).** The unit is **one attributed, quoted interjection**: a named
  member, the words they shouted, and the speech they shouted them into. Both ends
  matter — a count without the words behind it is an assertion (INT-7), and words
  without a named shouter are not a relation.
- **INT-2 (MUST).** Extraction is **deterministic and runs at load time**, never on
  the request path (cf. TEL-2, WCLOUD-6). It needs no model, no GPU and no network,
  so it runs in every install and every rebuild (SCR-6), it is unit-testable with
  plain strings (TIM-4's discipline), and re-running it over the same transcript
  gives the same rows.
  - **The rules are the reader's rules.** The sitting-day transcript and the viewer
    already lift these parentheticals out of the body text, italicise them and split
    a `Name:` attribution off the front so the name can be linked to a profile
    (`frontend/src/format.js`). This module ports *those* rules rather than
    inventing its own, so the graph and the page a reader checks it against cannot
    disagree about what an interjection is: a parenthetical is `(` to the next `)`;
    one parenthetical can bundle several interjections separated by a **spaced**
    dash (the spaces are what keep *Ruszin-Szendi* and *2028-ig* whole); an
    attribution is **two to four title-case name tokens** (optionally behind
    *Dr.*/*ifj.*/…) and a colon.
  - **Chairing speeches are scanned but flagged**, and every count drops them
    (STAT-1). This matters more here than anywhere else on the site: a voting block
    is a single hours-long "speech" by the presiding officer, so a whole afternoon's
    heckling lands inside it. Left in, the deputy speakers are the most-interrupted
    members of every House by a wide margin.
- **INT-3 (MUST).** **Precision over recall, because an edge is a claim about two
  people.** A word frequency that is 3% wrong is noisy; an arrow that is wrong puts
  words in a named person's mouth. Three gates, and what fails them is stored
  *unattributed* rather than guessed at:
  1. The two-token name rule alone drops the transcript's own look-alikes with no
     hand-written exceptions — `Elnök:` (the chair, one token), `Igen:`,
     `Közbeszólás:`, `Moraj a kormánypárti oldalon:` (lowercase words) and
     `A táblán megjelenő eredmény:` (the voting display).
  2. **The name must resolve to one person in the register**, which is the real
     filter: a name nobody in the House bears buys no arrow.
  3. **A collision is never broken by guessing.** Hungarian surnames collide hard
     (the register holds five *Kovács László*s), so a name matching several people
     is resolved only when the electoral cycle the words were spoken in leaves
     exactly one of them in the House — and where two members of the same House
     shared a name (two sitting *Tóth István*s), the interjection stays
     unattributed. The module's **coverage is published, not assumed** (INT-8).
- **INT-4.** The module owns **no scraper stage and no source file of its own**: it
  derives everything from text the proceedings module already stored and names the
  representatives module already holds (EXT-2), the way Tárcák does (§6C/MIN-1). A
  changed sitting re-derives only that sitting (SCR-2).
- **INT-5 (MUST).** The relation is read as a **directed graph**: one arrow per
  ordered pair, its thickness the number of interjections, narrowed by the reader to
  the **top *N* people** — by default those most involved, ranked by interjections
  *made plus received*, since the exchange is the subject and a member who is only
  ever shouted at belongs in it as much as one who only ever shouts (the reader can
  rank the cut by either direction on its own instead, INT-10). The whole relation is
  1 400 people and 21 000 pairs; only the top of it is a diagram at all, so *N* is a
  control and not a constant.
  - ***N* is a control the reader is never fighting.** The number follows the
    slider continuously, but the figure is refetched and relaid out **only once the
    drag ends** — committing as it travels spends a request and a force layout per
    pixel to draw pictures nobody asked to see, and a held arrow key must coalesce
    the same way. The control MUST also sit *outside* whatever a reload replaces:
    inside it, the first commit takes the slider out of the hand dragging it and a
    keyboard reader's focus with it. While the figure is behind the number the page
    says so quietly, and a reload of a figure already on screen leaves it up rather
    than blanking back to a spinner — the reader asked for a different *N* of the
    same picture, not for a different page.
  - **Position carries the finding.** The layout is force-directed, so members who
    shout at each other are pulled together and the picture separates into the
    knots of people who actually argue — a member who heckles widely sitting apart
    from one locked into a single duel. Faction stays a **colour**, deliberately
    not a position, which is what lets the reader see whether the knots line up
    with the benches or cut across them. (The first version placed members on a
    ring sorted by faction; it was honest, but every distance on it was an artefact
    of the sort, which is a lot of ink spent saying nothing.)
  - The layout MUST nevertheless be **deterministic** — the same data always draws
    the same picture — so that two cycle scopes can be compared, a figure can be
    embedded (§4C) and a screenshot can be trusted. A force simulation is not
    inherently deterministic, so the two things that make this one so are
    load-bearing and MUST hold: the simulation seeds **its own** generator rather
    than calling `Math.random`, and it starts from a fixed initial placement
    derived from the node order the API sorts. It is then run to convergence
    **once, synchronously**, and the result drawn — never animated.
  - An arrow is drawn only where **both** ends survive the top-*N* cut, so every
    arrow shown is whole; what the cut leaves out is stated rather than implied
    (INT-8).
  - The figure is **navigable**: zoom and pan (wheel, pinch, drag — and buttons,
    since a gesture is not a control for a keyboard), and any member can be dragged
    out of the tangle. On touch a one-finger drag MUST still scroll the page: a
    figure in the middle of an article must not trap the reader.
  - **Names are placed, not merely drawn.** A label is a wide box on one side of a
    small dot, so spacing the dots enough to space the names would blow the layout
    apart and shrink the type to nothing. The names are laid out after the
    simulation — most-involved first, each taking the first free slot among the two
    sides and a few line offsets — and a name that finds no free slot is **left
    off** rather than printed over another. The omission is temporary: zooming in
    shrinks the labels relative to the layout, slots open, and the missing names
    appear.
  - A name is drawn at a **constant size on screen**, not a constant size in the
    layout, because legibility is a fact about the reader's screen and not about
    the data. (Shrinking the coordinate system to compensate on a phone does
    nothing at all: it shrinks the fit scale by exactly the same factor.) Where a
    narrow screen then leaves no room for "Surname Given", the chart falls back to
    the **surname alone** — Hungarian writes it first — with the full name kept in
    the tooltip and the accessible name. The *layout itself* is identical on every
    screen; only the framing and the labels adapt, so the picture a phone shows is
    the picture a desktop shows.
- **INT-6.** A **self-attributed** interjection (the transcript occasionally credits
  an aside to the member already holding the floor) and one whose interrupted speech
  has no identified speaker are **stored and not counted**: a self-loop on this graph
  says nothing, and neither is a relation between two people. Together they are 85
  rows corpus-wide.
- **INT-7 (MUST).** **A count is never a dead end.** Clicking an arrow lists the
  interjections behind it — the words, the sitting day, the agenda item they
  interrupted, and a link straight to the moment in the video (VIE-5/TEL-4). Clicking
  a person lists **one side** of their cross-talk: the side the figure is currently
  ranked by, which is the number printed beside their name and so the list the click
  promises (ranked by the sum, the bigger of their two sides). The figure lights up
  that same half of their arrows, so the picture and the list never say different
  things, and one click on the arrow in the panel turns the other side up.
- **INT-8 (MUST).** The page states **what it is drawn from and what it leaves out**
  (TRUST-1): how many interjections the extraction found in scope, how many were
  attributable to one member, how many were shouted over a chairing speech, how many
  people the relation has in total, and what share of it the drawn top *N* holds. A
  network diagram is unusually good at implying that it is everything.
- **INT-9.** The page belongs to the **Elemzések** section (§4E): it is a calculation
  over the record, not a browse list. Like every page there it goes on belonging to
  its own module and disappears with it (ANA-1/EXT-6). It is *not* cycle-scoped by
  nature — the relation is meaningful over any span, and pooling several cycles
  simply says which antagonisms outlast a term.
- **INT-10.** The reader chooses **which direction the cut is ranked by**:
  interjections *made plus received* (the default, INT-5), *made* alone
  ("közbeszólt") or *received* alone ("közbeszóltak neki"). The three name
  genuinely different people — the House's loudest hecklers are not its
  most-interrupted members, and the sum hides both behind each other — and that
  difference is the finding, so it MUST be a control rather than a fixed choice.
  - The cut keeps the **same shape** in every mode: exactly *N* people, and the
    arrows between them (INT-5). A mode changes *who* the figure is about, never
    how many members it holds — a "top *N*" control whose number is not the node
    count is a broken control. (An earlier version instead drew every arrow out of,
    or into, the ranked *N* whoever the far end was, so those far ends joined the
    picture too. It answered a fair objection — a member who is heckled constantly
    but never heckles back cannot appear in any both-ends cut — but at *N* = 16 it
    drew some 50 members and near 100 at *N* = 40, since a single member can have
    several hundred distinct counterparts. Reviving it needs a *node budget*, not a
    per-member arrow cap.)
  - The chosen direction MUST also be the number the figure **states and sizes by**:
    the marker areas and the number printed beside each name are that direction's
    count, not the sum. A "who heckles most" picture whose dots are sized by the
    total would draw a member large for being shouted *at*, which is the opposite
    of what the reader asked to see.
- **INT-11.** The panel is **one arrow with two choosers**: who shouted, who was
  interrupted, and a direction that turns round. Either end may be left at *anyone* —
  "everything shouted at P", "everything P shouted", or the single pair X → Y — so
  the same control carries the reader from one member to one antagonist and back
  without ever changing shape, and the other direction is one click rather than a
  differently-shaped panel. A member's arrow and a member's own list are the same
  object, differing only in whether the far end is named.
  - An unnarrowed mix of both directions is **not** a state. "Who shouts at this
    member" and "whom do they shout at" are different questions, and a list that
    answers both at once answers neither — it is also the state in which a row has
    to re-state its own direction, which is how the old panel spent a line of every
    row on what the heading should have said.
  - A chooser offers that end's **real counterparts with their counts**, busiest
    first, taken from the relation and not from the drawn figure — most of a member's
    counterparts are outside any top-*N* cut — so the shape of someone's cross-talk
    is legible before a single row is opened, a count is a promise about the list
    behind it, and picking a counterpart can never land on an empty list. Where the
    far end is *anyone* there is no such list to draw on, and the chooser offers the
    members the chart is currently drawing instead.
  - Turning the arrow round is offered as **unavailable** where nothing was ever
    shouted that way, which the counts already loaded make knowable without asking.
  - Both ends live in the URL (`?from=` / `?to=`, either one absent meaning
    *anyone*), so a narrowed panel is as citable as the arrow the graph opens
    (§4D/TRUST-1). Addresses shared before the merge (`?who=`) name exactly one
    direction too — the subject holds the end the narrowing did not — and are
    rewritten into it on arrival rather than dropped.

> **✅ realized.** `app/interjections.py` is the pure extractor + resolver (no DB, no
> network); `loader.rebuild_interjections` writes the one `interjection` table, and
> `migrate_interjections.py` derives it in place on an existing DB. Over cycles
> 34–43 the pass finds **99 054** attributed, quoted interjections in ~2 minutes and
> resolves **97 659 (98.6%)** to a single member; **29 430** of them were shouted
> over a chairing speech and **68 628** are left to draw, between **1 415** people
> over **21 080** ordered pairs. The exclusion is not a formality: of the 10 170
> interjections stored against Latorcai János in cycle 41, **10 168** landed in a
> speech where he was chairing and **2** in a speech of his own. What stays
> unattributed is almost entirely the honest residue — the two sitting *Tóth
> István*s of cycle 37, the two *Dr. Varga László*s of cycle 39, and the
> transcript's own typos (*Novád Előd*, *Arató Gerely*).
>
> The API is `/api/v1/interjections/graph` (grouped per request — 99 000 rows group
> in a few milliseconds, so the module carries **no aggregate table** to fall out of
> step with the transcript) and `/api/v1/interjections/list`. The figure is
> `InterjectionGraph.vue`: a force layout (`d3-force`) rendered as plain Vue-drawn
> SVG, pannable and zoomable with `d3-zoom`, each directed pair its own quadratic
> curve bowed consistently to the left of its own direction of travel (which is what
> separates A→B from B→A) and tipped with an arrowhead at the member being
> interrupted. Three d3 modules are pulled in — `d3-force`, `d3-zoom`,
> `d3-selection`, ~24 KB gzipped with the component — and they ride in a lazy chunk
> this page and the embed share, the way Leaflet does for the two maps; the entry
> bundle grew by 0.3 KB. INT-5's determinism was **verified, not assumed**: two
> independent headless renders of the same URL produce byte-identical screenshots.
> Density is answered by drawing a busier graph fainter (the rest-opacity falls with
> √links), so an individual arrow stays visible — and clickable — at forty members
> as well as twelve. The frame is sized to the settled layout rather than the layout
> scaled into a fixed frame, which is what keeps a wide graph from being
> letterboxed and a tall one from being cropped. The slider position, the ranking
> mode and the opened arrow all live in the URL (`?top=`, `?rank=`, `?from=`/`?to=`,
> either end of the arrow absent meaning *anyone*), so a claim about two members is
> citable and `back` walks the arrows a reader opened; all of them are absent at
> their defaults, keeping the canonical address parameter-free (§SEO-2).
> The chart is embeddable as `interjection-graph` (§4C).
>
> Two things this cost elsewhere, both additive: `_delete_session` clears the
> sitting's interjections before its sentences (guarded on the table existing, so a
> DB predating the module still loads), and `/api/v1/interjections/list` gained a
> `person` filter — one member's interjections in both directions at once, which is
> what a clicked node opened until the panel became a single reversible arrow
> (INT-11); the page now asks one direction at a time, and the filter stays part of
> the API.

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
> `/api/v1/bills` list gained `main_type` include/exclude filters, and the extra
> browse pages reuse the existing bill detail view. Three pages now sit over that
> one list, each just a `fotipus` scope: the bills page at `main_type = T`, the
> Kérdések page (BILL-13) at `main_type_in = A,I,K`, and "Minden iromány" at no
> scope at all. A fourth would be another route, another scope, and no backend
> work beyond a filter that is specific to it.
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
>
> The **Portfolios module (§6C)** is the third, and the first to be built with
> *no source of its own at all*: it owns no scraper stage and no source file,
> deriving its five `portfolio*` tables at load time from rows the proceedings,
> representatives and bills modules already wrote (EXT-2). Its cross-module reads
> follow the established pattern — resolved through shared keys at query time, not
> foreign keys into another module's tables — and its one intrusion into a
> neighbour is additive: `/api/v1/bills` gained a `portfolio` filter, which
> answers empty rather than erroring on a DB whose tables predate the module. Its
> pages are mounted in the Representatives section's tab bar while remaining a
> separately switchable module, which is the first time the two have come apart:
> a section's tab bar is a navigation choice, not a module boundary.
>
> The **Interjections module (§6E)** is the fourth, and the closest thing yet to a
> module that is *only* a reading of what was already stored: like Tárcák it has no
> scraper stage and no source file, and like Települések it derives its one table
> from the transcript text and the shared `person` register at load time (EXT-2).
> Its whole footprint outside itself is two additive lines — a delete in
> `_delete_session` so a reloaded sitting drops its own rows first (guarded on the
> table existing, so a DB predating the module still loads), and its entry in the
> Elemzések registry. Notably it also declined a derived aggregate table: 99 000 rows
> group per request in a few milliseconds, and the table it did not build is a table
> that cannot fall out of step with the transcript it summarises.
>
> The **Elemzések section (§4E)** carries that same observation to its conclusion
> from the other side: three pages that were sub-tabs of Votes, Bills and
> Settlements now sit together in a section with its own top-bar entry and landing
> page, while each remains a page *of the module whose data it reads* and switches
> off with it (EXT-6). It cost no tables, no API routes and no scraper stage — a
> landing view, a registry the navigation is generated from, and 301s from the
> addresses the pages used to have. A section, it turns out, is not a module at
> all; it is a claim about what belongs next to what.

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
  backend logs what people search for — the search **keyword**, the **filters**
  combined with it (§SEA-3: date range, speaker, faction, agenda type, sort, and a
  zero-result flag) and **which search box** it was typed into (§SEA-11) — to guide
  coverage and search improvements. It also records how well the search worked
  (§SEA-12): the **hit count**, whether a page past the first was asked for, and
  the **position of the first result opened**. It is **anonymous and aggregated by
  design**: no IP address, user agent, cookie or session identifier is read or
  stored, and **no exact timestamp** is kept — events are counted into **whole-hour
  buckets** (UTC), so only per-hour **counts** per `(search box, keyword, filters)`
  tuple are persisted, never a per-request row that could be correlated to a
  person. Only the canonical search request of each box is counted (not the
  trend/breakdown/suggest/facet calls the SPA fires for the same query), and the
  click signal is a separate ping carrying nothing but that same tuple and a
  position, at most once per executed search. Data is written to a **separate
  SQLite file** — never the read-only content DB — flushed hourly and mounted on a
  **host-accessible** volume so the aggregates can be inspected or exported from
  outside the container. Enabled by default, switchable off by config (OPS-4), and
  **disclosed in the privacy notice** (PRIV-1).
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
  Both offloads are additionally **scoped by electoral cycle**
  (`PARLAMONITOR_MODAL_CYCLES`, default: the newest one), so backfilling the
  archive — the one operation that can miss the cache thousands of times at once —
  degrades locally instead of spending the budget the live cycle depends on.

### 8.6 Discoverability & indexing (SEO)

The corpus is public-interest text that people search for by name and by phrase
("mit mondott X a költségvetésről"), so being **findable** is part of the
product, not decoration. The site is a client-rendered SPA over ~260 000 content
pages, which puts three things at risk: a crawler must be able to *reach* a page,
get *one* address for it, and find *content* on it without running the app.
Increasingly the reader asks an assistant rather than a search engine, and an
assistant that has to guess what this corpus is will guess wrong — so the site
also states it outright (SEO-7).

- **SEO-1 (MUST).** The site serves a **`robots.txt`** naming its sitemap, and an
  **XML sitemap index** covering every content page — speeches, sitting days,
  representatives, irományok, votes — plus the browse pages. It is generated from
  the database (never hand-maintained), split into ≤25 000-URL child sitemaps,
  carries each page's `lastmod`, and lists only sections whose module is
  mounted (EXT-6). Pages too thin to stand on their own (a one-line procedural
  interjection) are left out rather than offered up to be crawled.
- **SEO-2 (MUST).** Every page has **exactly one canonical address**, and the app
  must not redirect away from it. Concretely: the **global cycle scope stays
  implicit in the URL while it is the default** (§4A) — appending `?cycle=` to
  every address turned each clean URL into a client-side redirect to a
  parameterised twin whose canonical pointed back at the clean URL, and Google
  indexed neither; a filtered/paginated browse view canonicalises to its clean
  browse URL; a sentence anchor (`?s=`) canonicalises to its speech; and an
  iromány reachable at both `/bills/:id` and `/documents/:id` canonicalises to
  the one its type belongs to.
- **SEO-3 (MUST).** Every route carries a **distinct `<title>` and description**
  derived from what is on it. Faceted/parameterised browse URLs are closed off in
  `robots.txt` so the crawl budget is spent on content pages.
- **SEO-4 (MUST).** A URL that resolves to nothing answers **404** (with the app
  shell, so the SPA still renders its own not-found view), and a route the site
  does not serve is marked `noindex` — a 200 "not found" page is a soft 404 and
  is what a crawler files under "crawled, not indexed".
- **SEO-5 (SHOULD).** Detail pages carry **schema.org structured data** for what
  they are — `Person` for a representative (with `sameAs` to Wikipedia/Wikidata,
  EXT-2), `Article` for a speech, `Legislation` for an iromány, `Event` for a
  sitting day — plus breadcrumbs, and the home page declares the site and its
  search.
- **SEO-6 (SHOULD).** The server-rendered shell carries a **plain-HTML rendering
  of the page's own content** (the speech text, the sitting's speech list, the
  MP's details and recent speeches) inside the app's mount point, which the SPA
  replaces on boot. This is what a first-pass crawler and a reader without JS
  see, and — since nothing else links to a speech — it is also the site's
  internal link graph. It MUST be the same content the app renders; a
  crawler-only variant is cloaking.
- **SEO-7 (SHOULD).** The site serves **`/llms.txt`** (llmstxt.org): the corpus
  described for a language model rather than for a crawler — what it is and is
  not (a third-party mirror; `parlament.hu` governs), the Hungarian vocabulary it
  is written in (*ülésnap*, *iromány*, *frakció*…), which cycles it covers and
  how fresh they are, the shape of each detail URL, and where the
  **machine-readable data** lives, so an assistant calls the API instead of
  scraping the SPA. Like the sitemap it is **generated**, module-aware (EXT-6)
  and cycle-windowed (CYC-7), and it repeats the site's own attribution and
  caveats from the same constants the API serves them from: a figure or a
  disclaimer that holds on the site but not in the file a model reads is worse
  than none (TRUST-1).

### 8.7 Social announcements (Bluesky)

Nobody watches a corpus for changes. The site is only useful to someone who
already thought to open it, so the two things that are genuinely *new* — a sitting
day becoming readable in full, and the occasional accident worth a smile — are
pushed to where people already are. The bot is deliberately narrow: it reports what
the database now holds, in the site's own words, and never editorialises about
what was said.

- **SOC-1 (SHOULD).** The site MAY post to a **Bluesky** account. Credentials are
  **environment-only** (OPS-4) — one variable carrying handle + **app password** —
  and their presence is the feature's on switch: an unconfigured deployment runs
  the same code path as a no-op. No account secret is ever written to disk, logged,
  or stored in the database.
- **SOC-2 (MUST).** A sitting day is announced when it is **fully processed**, and
  that MUST mean what the site itself means by it (§5.6 SIT-2): every speech has
  both a transcript and a per-speech video window. `parlament.hu` publishes a day in
  instalments over days, so this is the one moment at which "you can now read and
  watch all of it" is a *true* statement — announcing on first ingest would promise
  a day that is still half-empty.
- **SOC-3 (SHOULD).** A day post carries **only figures the site can back up** and
  shows on the page it links to: how many speeches, by how many speakers, over how
  many agenda items, and the total speaking time as the day's own toplist (§5.4)
  sums it. Where the stored data is not what it appears to be — `video_duration`
  holds a *per-speech* clip length, and a day whose speeches carry whole-day offsets
  starts at midnight — the figure is **omitted rather than approximated**: a made-up
  number in a public post is worse than a missing one (TRUST-1).
- **SOC-4 (MAY).** A **haiku** said by accident — a whole transcript sentence that
  happens to be 5-7-5 in Hungarian syllables, from a mandate-holding member's
  substantive speech (never the chair's procedural boilerplate, whose formulas
  land on 17 syllables by coincidence of the formula, not of anybody's speech) —
  MAY be posted, quoted verbatim and linked to the sentence in the transcript so
  anyone can check it. It MUST be framed as **accidental**: the speaker wrote no
  poem, and saying so is the difference between a joke the reader is in on and a
  claim about somebody's intent.
- **SOC-5 (MUST).** **Nothing is ever posted twice.** What has been announced is
  remembered **outside** the content database — which is regenerable by design
  (DB-3) and gets rebuilt from scratch, so a memory kept inside it would
  re-announce the entire corpus on the next rebuild.
- **SOC-6 (MUST).** A **first run announces nothing**: with no memory yet it adopts
  the current state of the world as already-said. A fresh deployment, a wiped
  volume or an unreadable state file therefore cannot dump a backlog into the feed
  — the failure mode of a naive bot, and the one that would get the account muted.
  Posting that window instead is an explicit opt-in.
- **SOC-7 (MUST).** The pass is **bounded and resumable**: only days inside a
  recency window are considered at all (a day that completes months late is not
  news), at most a handful of posts go out per pass, and a poem quota is counted
  per sitting day rather than per pass — so a transcript arriving in five
  instalments still yields one poem, not five. Whatever a failure prevents from
  going out stays unrecorded and is retried; whatever went out is never repeated.
  Posts sent in one pass MUST be **distinguishable in time** — a distinct
  sub-second `createdAt` each, and a pause between sends — because the network's
  author feed keeps only one post per timestamp: a burst stamped with the same
  second is accepted and indexed, yet all but the last of it is invisible on the
  account's own profile.
- **SOC-8 (SHOULD).** Announcing MUST NOT be able to break the pipeline it rides
  on. It reads the database **read-only**, holds no lock, writes nothing the site
  serves, and runs *after* the DB has been updated and swapped in — a failed post
  leaves a correctly updated site and an entry to retry. A **dry run** (decide,
  print, send nothing, remember nothing) is available for trying a deployment out
  before letting it speak.

  > **✅ realized.** `app/social.py` decides and posts, `app/bluesky.py` speaks AT
  > Protocol over the stdlib (no SDK dependency), `app/haiku.py` holds the syllable
  > rules — shared with the `find_haikus.py` exploration CLI — and
  > `app/publication.py` holds the one definition of "fully processed" that the API,
  > the share cards and the bot all read. The sync pass (`docker/sync-once.sh`) runs
  > it after each incremental update; `announce [--dry-run]` runs it by hand.

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
  types** — törvényjavaslatok on their own page, the question types on theirs
  (BILL-13) and every type together on the "Minden iromány" page (BILL-9) — so
  document-level coverage of irományok is complete. **Votes** are now implemented as the
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
