# Parlamonitor — Scraper

The scraping pipeline for [Parlamonitor](../requirements.md): it fetches
Hungarian National Assembly (*Magyar Országgyűlés*) data from `parlament.hu`,
parses it, segments speeches into sentences, estimates sentence↔video timing,
and writes self-contained JSON records ready to load into the search database.

This project **owns the scraping end to end** and replaces the legacy
`OpenParliamentTV-Tools` HU pipeline (requirements §3.1, SRC-1). That tree is
kept only as reference material; nothing here imports it at runtime.

## What it produces

| Output | Module | Shape |
| ------ | ------ | ----- |
| `processed/<session>-session.json` | `parlamonitor.proceedings` | one record per sitting day: ordered speeches, each with agenda item, speaker(s), the whole-day HLS video, and `textContents → textBody → sentences[]` with day-absolute `timeStart`/`timeEnd`. |
| `processed/representatives-<cycle>.json` | `parlamonitor.representatives` | the MP registry for a cycle: bio, faction & committee history, constituency, education, and per-cycle speech / bill-submission counts. |
| `processed/advocates-<cycle>.json` | `parlamonitor.advocates` | the **nationality-advocate** registry (*nemzetiségi szószólók*) for a cycle: same record shape as an MP plus the `nationality` they speak for. They are not in the MP roster, but share its id space — so the file is additive and needs no re-run of the MP stage. |
| `processed/officeholders.json` | `parlamonitor.officeholders` | the **office-holder** registry (*tisztségviselők*): every recorded term of government / House office with its **real** start and end date (open while still held) and the portal's own office **category**, grouped by person. Cycle-less, and the only source that dates the office of a **non-MP** minister or state secretary — who is in no roster at all. The category is not in the data: it is *which* per-category listing returned the row, so the stage asks for each category in turn (six paged listings) and tags what comes back. |
| `processed/committees-<cycle>.json` | `parlamonitor.committees` | the **committee** registry (*bizottságok*) for a cycle: every body — main committees and subcommittees in one flat list keyed by its own id — with its type, dates and contact, plus who sits on it (`members`, the roster on one date), every **dated** membership and office term (`terms`), every `meeting` with its minutes PDF, and the irományok it dealt with (`documents`) and tabled (`submissions`), plus the meetings it has scheduled but not yet held (`upcoming` — state, not history). Membership needs both listings: see [Committees](#committees-6f). |
| `documents/<cycle>/` | `parlamonitor.documents` | **optional** mirror of the iromány document *files* themselves — the extracted text (`text/<docid>.txt.xz`), the source PDF (`pdf/<docid>.pdf`), or both, plus an `index.json` manifest. **Off by default**; see [Document files](#document-files-doc-1). |
| `processed/aktualis.json` | `parlamonitor.aktualis` | the **Aktuális** page: the documents it links (napirend, ülésterv, submission deadlines, legislative programme) and the **order paper for the sitting that is coming**, parsed out of the napirend PDF into days, timetables and agenda items — plus the House Committee's next meeting. The one source on the site for what the House is *about to* do; see [The Aktuális page](#the-aktuális-page-nr-1). |
| `logs/ingest-<ts>.json` | both | per-run ingestion log (run time, sittings added, errors, backend). |

Each speech's speaker carries a `personID` (`kepviseloId`) that joins directly
to the representative registry — no name matching needed (requirements EXT-2).

## How the data is fetched

Everything goes through the modern, **token-free Felicitas JSON API**
(`parlamonitor/felicitas.py`), verified against browser captures (2026-06):

**Proceedings** — `plenaris-ules-adatok-query-provider`:

1. `ulesnapok-query` (cycle + date range) → session days + UUIDs.
2. `ulesnapok-aktusok-query` (day UUID) → every speech grouped by agenda act,
   with its join number, speaker, `felszolaloId`, type, committee and duration.
3. `ulesnap-felszolalasai` (day UUID, `pUlesnapId`) → the day's **complete** flat
   speech listing. Query 2 reports a speech only through the agenda act it is
   linked to, so speeches linked to none — chiefly those with no "Felszólalás
   oka" (type) — are missing from it (≥8 200 speeches, 5.8%, over the archive);
   the two are merged so no speech is lost, each recovered one inheriting the act
   of the speech it follows.
4. `ulesnap-felszolalas-adata-query` (speech UUID) → the full speech text.
5. `ulesnapok-video-query` (day UUID) → the whole-day HLS playlist on
   `sgis.parlament.hu`. Per-speech offsets are also resolved and recorded for
   provenance (used by a future precise-timing stage, not by v1).

**Representatives** — `kepviselo-query-provider`: a paged roster query plus a
family of per-MP detail queries and a photo resource endpoint. The same provider
serves `szoszolo-lista-query` (cycle → the **nationality-advocate** roster with
each advocate's nationality and per-cycle counts, verified 2026-07); advocates
share the MP id space, so the per-MP detail queries and the photo endpoint answer
for them unchanged.

The reference's CGI (token) and PAIR-proxy (HTML) backends remain documented in
`OpenParliamentTV-Tools` as fallbacks (SRC-2) but are not needed for v1.

## Sentence ↔ video timing

Timing is **Whisper forced alignment** (`whisper_align.py`, TIM-1) wherever a
transcription of the day's recording is available, and the positional estimate
below wherever one is not — per speech, so a day degrades gracefully rather than
all-or-nothing. See [Reusing a cached transcription](#reusing-a-cached-transcription)
for how the (expensive, cached) transcriptions move between machines.

The fallback is a **positional/character estimate** (requirements TIM-3): the
whole-day stream duration is distributed across the day's transcript in
proportion to character position, so a sentence's share of the timeline equals
its share of the day's characters. Offsets are **day-absolute** seconds into the
HLS stream, so clicking a sentence seeks into it (TIM-2). Every timed sentence is
stamped `align-method = "estimated-day-offset"` with reduced `confidence` so the
UI can disclose the imprecision (TIM-3 / VIE-6).

Timing is a **distinct, swappable stage** (`parlamonitor/timing.py`, TIM-4): it
consumes the alignment where there is one and the per-speech offsets the scraper
captures into `media.videoStart`/`videoEnd` where there is not, so which method ran
is invisible to the fetch/parse/segment stages and to the data shape
(requirements §10) — only the per-sentence `align-method`/`confidence` records it.

### Reusing a cached transcription

Transcription is the expensive half and is cached per sitting as a plain
`data/original/plenary/whisper-<session>.json` (words + a fingerprint of the
recording URL and model). That file is **portable**: copy it to another machine and
that machine gets word-accurate timing for the sitting without a GPU, a Modal
token, or `faster-whisper`. Two things make the difference between a cache that is
used and one that is silently ignored:

```bash
# Which copied caches will actually be used? A day re-cut upstream since it was
# transcribed, or a different PARLAMONITOR_WHISPER_MODEL, fails the fingerprint
# and is reported STALE (the sitting then falls back to the estimate).
python check_whisper_cache.py ../data

# A sitting is served as a CUT of one continuous daily recording, and the cut's
# bounds are in the URL (/vod/smil:<date>.<time>.<startMs>.<endMs>.smil/). When
# only those moved, the audio is identical and the words merely need re-anchoring
# — a shift, not a GPU. Anything else (different recording, different model) is
# left alone to be re-transcribed.
python check_whisper_cache.py ../data --rebase

# A host with no backend resolves `auto` to `character`, which returns BEFORE
# reading any cache — name the backend explicitly to load the copied words. No
# install needed: every day is a cache hit, so none reaches the backend.
python -m parlamonitor proceedings --cycle 43 --transform-only \
    --timing-backend whisper-local ../data
```

The transform only rebuilds sittings whose raw bundle is newer than their session
JSON, and copying a cache changes neither — `touch` the day bundles you have words
for first. It is also scoped to `--cycle`, so name the cycle the copied caches
belong to. Do **not** reach for `--force`: it also forces `ensure_words`, which
bypasses the cache and tries to re-transcribe. The deployment-side version of this
recipe (containers, DB reload) is in
[DEPLOYMENT.md](../DEPLOYMENT.md#reusing-a-whisper-cache-copied-from-another-machine).

## Running

From a clean checkout (requirements SCR-6 — the dependency surface is small):

```bash
cd scraper
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt        # just `requests`; spaCy is optional

# Proceedings: download + transform the current cycle (43) into ./data
python -m parlamonitor proceedings --cycle 43 ./data

# Re-run only the offline transform/timing over already-downloaded raw files.
# `--cycle` bounds the build/align stage too, so this touches cycle 43 only —
# pass the cycle you actually mean, or the older sittings sitting in ./data stay
# as they are (run it once per cycle to rebuild several).
python -m parlamonitor proceedings --cycle 43 --transform-only ./data

# A bounded slice (e.g. backfill a date window)
python -m parlamonitor proceedings --cycle 43 --from 2026-05-09 --to 2026-06-18 ./data

# Representative registry — roster only (one paged query, fast)
python -m parlamonitor representatives --cycle 43 --no-details ./data

# Full registry with per-MP detail + portraits (heavier; one run per cycle)
python -m parlamonitor representatives --cycle 43 --photos ./data

# Committees (bizottságok): bodies, membership, meetings and irományok
python -m parlamonitor committees --cycle 43 ./data

# Nationality advocates (szószólók) — one cycle, or backfill every cycle that
# has them (40 on). Portraits are downloaded by default (~13 people per cycle).
python -m parlamonitor advocates --cycle 43 ./data
python -m parlamonitor advocates --all-cycles ./data

# Irományok of a cycle (every document type) from the Felicitas API
python -m parlamonitor bills --cycle 43 ./data

# The Aktuális page + the napirend PDF behind it: the ORDER PAPER for the
# sitting that is coming. Cycle-less, one HTML request unless the House has
# published a new napirend since the last run.
python -m parlamonitor aktualis ./data

# …except 1994-98, which the API has none of — see below
python -m parlamonitor bills --cycle 35 --archive ./data

# The document FILES those irományok link to, text extracted for NLP.
# Stores nothing unless asked: see "Document files" below.
python -m parlamonitor documents --cycle 43 --documents text ./data
```

Adding advocates to an **already-scraped** corpus needs nothing else: the
sittings already carry the advocates' speeches under the very ids the registry is
keyed by, so the new files are all the loader's incremental `--update` needs (it
tracks processed files individually, and adds the two `person` columns in place).

### Operational knobs (all environment-driven — OPS-4 / SCR-4)

| Flag | Env var | Default | Purpose |
| ---- | ------- | ------- | ------- |
| `--sleep` | `PARLAMONITOR_SLEEP` | `1.0` | politeness delay between requests |
| `--retry-count` | `PARLAMONITOR_RETRY_COUNT` | `5` | retries per request (exp. backoff) |
| `--captcha-retries` | `PARLAMONITOR_CAPTCHA_RETRIES` | `9` | 10-minute retries spent waiting out a [CAPTCHA wall](#when-parlamenthu-rate-limits-us) (~1.5h) before the run is abandoned |
| `--proxy` | `PARLAMONITOR_PROXY` | — | SOCKS5/HTTP proxy for `parlament.hu` |
| — | `PARLAMONITOR_USER_AGENT` | civic-tech UA | request User-Agent |
| `--no-offsets` | — | off | skip per-speech offset resolution (faster) |
| `--ssh-host` | `PARLAMONITOR_SSH_HOST` | — | route traffic through an SSH host |
| `--ssh-port` | `PARLAMONITOR_SSH_PORT` | `22` | SSH port |
| `--ssh-user` | `PARLAMONITOR_SSH_USER` | — | SSH username |
| `--ssh-key` | `PARLAMONITOR_SSH_KEY` | — | path to the SSH private key |
| `--ssh-known-hosts` | `PARLAMONITOR_SSH_KNOWN_HOSTS` | — | `known_hosts` file (else trust-on-first-use) |
| — | `PARLAMONITOR_SSH_KEY_PASSPHRASE` | — | passphrase for an encrypted key |
| `--timing-backend` | `PARLAMONITOR_TIMING_BACKEND` | `auto` | sentence timing: `auto`/`whisper-modal`/`whisper-local`/`character` (TIM-1). `auto` resolves to the first backend actually present, then `character` — which reads **no** cache at all, so name a backend explicitly to use [copied words](#reusing-a-cached-transcription) |
| `--documents` | `PARLAMONITOR_DOCUMENTS` | `off` | what the [document mirror](#document-files-doc-1) keeps: `off` / `text` / `pdf` / `all`. The storage guard — an unrecognised value reads as `off`, so a typo can never turn ~870 MB per cycle on |
| `--documents-compression` | `PARLAMONITOR_DOCUMENTS_COMPRESSION` | `xz` | codec for the stored text: `xz` / `gzip` / `none` |
| `--documents-max-mb` | `PARLAMONITOR_DOCUMENTS_MAX_MB` | `0` | skip any single document above this size (`0` = no limit); enforced while streaming, so an over-cap file is never fully downloaded |
| `--documents-per-pass` | `PARLAMONITOR_DOCUMENTS_PER_SYNC` | `25` | **`sync` only:** how many NEW documents one pass may fetch (`0` = no cap). A freshly enabled mirror faces the whole cycle, not what arrived since the last poll, so a pass takes a slice and the manifest reports the rest as `pending`; a hand-run `documents` is never capped by it |
| — | `PARLAMONITOR_MODAL_CYCLES` | `latest` | which cycles may be transcribed on **Modal** (`latest`/`all`/`43,42`) — the metered-GPU guard; out-of-scope days keep their cached words, or fall back to the positional estimate |

#### SSH tunnel proxy

When `parlament.hu` is only reachable from a specific egress IP, route every
request through an SSH host you control. Setting `--ssh-host` (with `--ssh-user`
and `--ssh-key`) opens one key-based SSH connection via **paramiko** and serves
a local HTTP-CONNECT proxy backed by `direct-tcpip` channels, so all
parlament.hu traffic exits from the SSH host. It takes precedence over
`--proxy`. Needs `paramiko` installed (`pip install paramiko`); imported lazily,
so a plain scrape works without it.

Compression (`-C`) is on, and the host key is trusted on first use unless
`--ssh-known-hosts` is given (i.e. `StrictHostKeyChecking=no` by default). Old
servers that only accept the legacy `ssh-rsa` (RSA-SHA1) signature are handled
automatically: if the first attempt fails with an RSA key, it retries forcing
`ssh-rsa` — the equivalent of `ssh -o PubkeyAcceptedKeyTypes=ssh-rsa`.


```bash
python -m parlamonitor representatives --cycle 43 \
  --ssh-host bastion.example.org --ssh-user scraper \
  --ssh-key ~/.ssh/parlamonitor_ed25519 ./data
```

#### When parlament.hu rate-limits us

A client the site considers too eager stops getting data and starts getting a
**CAPTCHA challenge page** — with **HTTP 200** and an HTML body, so only the body
gives it away:

```text
WARNING parlamonitor.http_client: GET .../kepviselo-lista-idopontban-query/p-ciklus
  hit the CAPTCHA wall (CAPTCHA challenge page returned for ...); sleeping 600s,
  then retrying (1/9)
```

That is a wall, not a glitch, so it is **not** retried on the exponential backoff
(seconds-scale retries only re-confirm the block and deepen it). Instead the
client stands off for **10 minutes** and tries the same request again, keeping the
run in place: nothing already downloaded is lost or re-fetched.

The count is of *consecutive* walls across the whole run, so an occasional
challenge never adds up; a success clears it. Once `--captcha-retries` stand-offs
(default 9, i.e. ~1.5 hours of being walled) have all come back walled, the run is
**abandoned** with exit status **3** — distinct from the `1` that means "some items
failed" — so a cron wrapper can tell the two apart. Nothing is lost: every stage is
idempotent and the next scheduled run resumes where this one stopped
(SCR-1/SCR-2).

Runs are **idempotent** and guarded by a lockfile (`data/parlamonitor.lock`,
SCR-1) — an `flock` on that file, so a killed run releases it and cannot wedge
the schedule; the PID inside is diagnostic only, because the file is shared with
containers whose PID namespace is not ours. The file is never deleted (the lock
lives on the inode); empty contents mean nobody holds it. `--force-lock` runs
without it. A cached sitting is skipped unless it is the still-live latest sitting
or `--force` is given. A sitting with no resolvable recording or no transcript
is still written in degraded form and flagged (`confidence`, `confidence_reason`,
`align-method`), never silently dropped (SCR-5).

### Incremental detail caching (cheap frequent re-runs — SCR-2)

The expensive part of the `bills` and `votes` stages is the **per-item detail**
(≈9 sub-queries per bill, 3 per vote). To let the scraper run often without
re-fetching everything, both stages **reuse the detail already saved** in the
previous `bills-<cycle>.json` / `votes-<cycle>.json` for items whose cheap list
row is unchanged (`parlamonitor/detail_cache.py`):

- a **vote** is immutable once recorded, so its detail is fetched **exactly
  once** — a re-run only fetches votes new since last time;
- a **bill** is keyed on its status + legislative-stage diagram, so a finished
  or dormant bill is skipped while any progression re-fetches its `adatlap`.

So a nightly re-run spends network only on new/changed items. Pass `--force`
(bills/votes) to ignore the cache and re-fetch every detail — e.g. for a full
re-import after a schema change (SCR-2). `--no-detail` still skips detail
entirely for a fast list-only refresh.

Schedule it from cron/systemd (OPS-2); each run appends an ingestion log under
`data/logs/` (SCR-3).

### Document files (DOC-1)

The `bills` stage records where each document *is* (`textUrl` on the iromány and
on every non-self-standing motion, plus the justification/background files on
its detail sheet) but never fetches it. `documents` mirrors those files and
extracts their text, which is what makes NLP over the legislative text possible.

```bash
# Off by default — this stores nothing and makes no requests:
python -m parlamonitor documents --cycle 43 ./data

# The useful opt-in: keep the extracted text, throw the PDFs away
python -m parlamonitor documents --cycle 43 --documents text ./data

# Keep the source files too (two orders of magnitude more disk)
python -m parlamonitor documents --cycle 43 --documents all ./data

# Smoke run, and a size cap for a small disk
python -m parlamonitor documents --cycle 43 --documents text --limit 20 \
    --documents-max-mb 5 ./data
```

It reads the links out of the saved `bills-<cycle>.json`, so run `bills` first.
The same three knobs exist on `sync`, so a watcher can keep the mirror current —
with one of its own, `--documents-per-pass` (env
`PARLAMONITOR_DOCUMENTS_PER_SYNC`, default 25, `0` = no cap). A sync that has
just had the mirror switched on faces the *whole cycle*, not the documents added
since the last poll, so a pass fetches at most that many new ones and the
manifest reports the remainder as `pending` for the passes after it to pick up.
A hand-run `documents` is never capped by it: `--limit` above is the manual
equivalent.

#### Why it is off by default, and why `text` is the right opt-in

A full pass over cycle 43 downloaded all **861** documents — **868 MB** of PDF,
6 607 pages — and extracted their text:

| What is kept | Cycle 43 | vs. the PDFs |
| ------------ | -------- | ------------ |
| the PDFs as they come | **868 MB** | — |
| …`qpdf`-rebuilt (lossless) † | 764 MB | −12% |
| …gzip / xz / zstd / brotli around them † | 710–725 MB | −16…−18% |
| …Ghostscript re-distilled, 150 dpi (lossy) † | 451 MB | −48% |
| …Ghostscript re-distilled, 72 dpi (lossy) † | 291 MB | −66% |
| the extracted text, uncompressed | 16.3 MB | −98% |
| …gzipped | 4.65 MB | −99.5% |
| **the extracted text, xz — what is stored** | **3.96 MB** | **−99.5%** (219x) |

Everything but the four rows marked † is measured over the whole corpus; those
four are ratios from a 72-document / 80 MB stratified sample, since keeping the
PDFs around to compress them is the very thing the stage avoids.

Two things follow. First, **compressing the PDFs is not worth doing**: their
streams are already deflated, so every general-purpose compressor lands within a
few points of 82% and a lossless structural rebuild does worse than that.
Only re-encoding the images helps, and that is lossy — it degrades the archival
copy to save a third of a lot.

Second, **the text is ~200x smaller than the files it came from** and is the
only part any NLP pass reads. So the ladder is `off` (default, the live server)
→ `text` (~4 MB per cycle) → `pdf`/`all` (~868 MB) — and `text` is what an
opt-in should normally pick. Note that `text` still *downloads* every PDF; the
saving is disk, not bandwidth or politeness budget — a first full pass is 861
requests and took ~21 minutes at `--sleep 0.5`.

`xz` is the default codec because it is both stdlib (`lzma`) and the best of the
measured options on this corpus: 16.3 MB of extracted text becomes 3.96 MB,
against 4.65 MB for gzip and 3.97 MB for `zstd -19`.

Compressing each document on its own does cost real ratio — one solid `.tar.xz`
of the same text is 2.25 MB, so per-document streams are **76% larger**, because
most irományok are small and none of them get to share a dictionary. It is still
the right trade here: the absolute difference is 1.7 MB, and being able to read
(or re-extract) one document without unpacking the corpus is worth more. If the
store ever grows to where that matters — every cycle mirrored, not just the
latest — a shared `zstd` dictionary would recover most of it while keeping random
access.

On disk the store is larger than the byte totals suggest: 861 mostly ~1 KB files
against a 4 KB block size means cycle 43 occupies **6.7 MB** of actual disk.

#### What is in the store

```
data/documents/43/
  index.json                     manifest + incremental cache
  text/irom43-00448-00448-cf49ae25.txt.xz
  pdf/irom43-00448-00448-cf49ae25.pdf     (only under `pdf` / `all`)
```

The id is the URL path made readable, plus 8 hex of the URL's SHA-1 — the guest
background files have titles long enough to collide once truncated. Each
manifest entry carries the source URL, the owning bill (`billId`/`billNumber`),
the document kind (`main` / `motion` / `justification` / `background`), the
SHA-256 of the bytes, page count, and where the artefacts landed. Read the text
back with `documents.scrape.read_text`, which handles whichever codec was used.

Text extraction shells out to **`pdftotext`** (poppler-utils) — the same way the
local Whisper backend leans on `ffmpeg`. Where it is missing the stage says so
and keeps going (SCR-6). About 1 document in 70 is an image-only scan and yields
no text; that is recorded (`textChars: 0`) as an answer, not retried as a
failure — 8 of cycle 43's 861 documents. Extracting the whole cycle takes a few
seconds of CPU; the network is the slow part.

Re-runs are incremental (SCR-2): documents on parlament.hu are static once
published, so anything already in the manifest with its files still on disk is
skipped, and a repeat pass over cycle 43 costs no requests at all. A 404 or an
over-cap document is *settled* and not asked about again; only a genuine fetch
error is retried next run. `--force` re-fetches everything.

### Committees (§6F)

The committee registry is nine Felicitas queries behind five portal pages
(*Bizottságok és albizottságaik*, *Bizottságok tagjai és tisztségviselői*,
*Bizottsági tagság, tisztség változásai*, *Bizottsági jegyzőkönyvek*,
*Bizottságok által tárgyalt irományok*). Each was found the documented way —
`data-page` on the page, then its page definition — not guessed.

Three things about the shape are worth knowing before touching this stage.

**A body's identity is not in the column you'd expect.** The committee listing
is built for a two-level table: one row per *(main committee, body)* pair, where
`albizottsagId`/`albizottsagNev` name the body the row is about and
`bizottsagId`/`bizottsagNev` *always* name the main committee. On a main
committee's own row the two agree; on a subcommittee's they do not. Read the
wrong pair and every subcommittee is silently renamed after its parent.
`bizottsagKod` is the parent's on both, which is why it is dropped for
subcommittees — it builds the *homepage URL*, and the child would point at the
parent's page.

**Membership needs two queries, not one.** The roster
(`bizottsag-tagjai-query`) answers "who sits on this *on this date*" and the
term listing (`…-tagsag-tisztseg-valtozasai-…`) "which seats started or ended
during the cycle". Neither alone is the cycle's membership:

| | roster | terms |
| --- | --- | --- |
| **closed cycle** | its final state | **complete** — every seat ends when the term does |
| **running cycle** | **complete** — who sits today | only the churn so far |

Measured on cycle 42: the roster gave 212 *(committee, person)* pairs, the term
listing 426 — every one of the roster's plus 214 more it cannot show, people who
left before the cycle ended. On cycle 43 the same term query returns 19 rows.
So both are fetched and the API unions them, which is also why the roster query
is asked twice per cycle: `pBizottsagAlbizottsagai` *switches* it between main
committees and subcommittees rather than adding the latter to the former.

**The two meeting figures differ on purpose.** `bizottsag-ulesei` lists meetings
individually (with `ulesHosszaMasodPercben` in **seconds**, despite the upstream
caption saying óra:perc), while `www-bizottsagi-ulesek-szama-query` gives
per-committee totals (in **minutes**) that count sittings the listing does not
include. Both are stored and both are shown; neither is recomputed from the
other.

**The schedule ahead is asked from today, not from the cycle's start.**
`tervezett-bizottsagi-ules-idorend-query` takes `pIdoszakEleje` as a *from* date
with no upper bound, so from the start of the term it returns every sitting the
committees ever put in the diary — 369 rows for cycle 43 in September, against
the five actually ahead. `committee_upcoming(..., from_date=…)` overrides it
for a backfill.

Everything the registry links — the jegyzőkönyv PDFs, the iromány texts — is
**linked, not mirrored** (LEGAL-1); only the URL is stored.

### The Aktuális page (NR-1)

Everything the Felicitas API exposes is a record of what the House **has done**.
What it is **about to do** lives on one portal page,
[`/web/guest/aktualis`](https://www.parlament.hu/web/guest/aktualis), as human
documents:

| Slug | What it is | Parsed? |
| ---- | ---------- | ------- |
| `nr_<YYYYMMDD>_elfogadott` | **napirend** — the order paper for one sitting | **yes**, in full |
| `ut_<YYYYMMDD>_elfogadott` | **ülésterv** — the month's sitting plan | link only (NR-4) |
| `tajek_benyhatido_<YYYYMMDD>` | submission deadlines | link only |
| `torvenyalkotasi-program_<term>` | the term's legislative programme | link only |
| `munkarend_<term>_…` | the House Committee's work-schedule resolution | link only |

`aktualis.json` carries those links, the House Committee's next meeting, and the
**parsed napirend**.

**Why the page is parsed the way it is.** The markup is Liferay-generated and
disposable — the House restyles it, the `div` ids change, and the block holding
the House Committee's meeting is a repurposed *year navigator* whose links still
carry `data-year="2025"` attributes that are not years. So nothing here keys on
structure. Documents are found by the **shape of the href**, and the slug itself
says what the document is and which sitting it belongs to; the House Committee's
meeting is found by the two Hungarian labels the House writes in front of it
(*Helyszíne:*, *Időpontja:*). Both survive a reskin. Only the links between the
page's first heading and its footer count, because the site chrome links
documents of its own — twice, once at the top and once in the footer.

**The napirend parser** (`aktualis/nr.py`) reads
`pdftotext -layout` output — the opposite choice from the iromány document mirror
(DOC-1), which deliberately extracts in reading order. The order paper's meaning
*is* its layout:

```
5.      T/438.       A szakképzésről szóló 2019. évi LXXX. törvény
B./5.                módosításáról
                     (Kormány - oktatási és gyermekügyi miniszter)
                     Bizottsági jelentések és az összegző módosító javaslat vitája
                     Megjegyzés:
                     A Törvényalkotási Bizottság az előterjesztést megtárgyalta…
```

The ordinal, the `B./5.` cross-reference and the iromány number are a left
**gutter**; in reading order they interleave with the title text. The parser
peels the gutter off by *what a token is* — a marker followed by the layout's own
column gap — rather than by a fixed column, because the columns move between
documents and a page break dedents a wrapped line to column 0.

Two numbers per item look alike and are not. **`ordinal`** is the item's place in
that sitting day; **`ref`** (`B./n`) identifies it across the whole sitting. A
bill debated and voted on the same day is two ordinals under one ref, and the
day-2 listing restarts at 1. `ref` is also what joins an item to part **B**'s
detail sheet (submission date, committees, amendment deadlines).

What the parser produces per item: `ordinal`, `ref`, `billCode`, `title`,
`submitter`, `stage`, `timeWindow`, `section`, `notes`, `detail`, and `flags` —
the procedural properties the House states in prose (`two_thirds`, `cardinal`,
`exceptional`, `urgent`, `nationality`, `eu`, `quorum`, `secret_vote`, …), lifted
into something machine-readable because they are what makes an item notable.

Edge cases it is built for, all seen in the corpus:

- A **ceremonial sitting** (a government being sworn in) has an order paper with
  no numbered item on it. It parses to its days with empty item lists — the truth
  about that sitting, not a failure.
- The **procedural decisions** the House takes before adopting the agenda are
  listed *without* a number; they are kept with a null `ordinal`.
- `S/...` is the House's own placeholder for "number to come", not a bill code.
- A motion can carry **51 named sponsors** wrapping over a dozen lines; the
  parenthetical is folded into `submitter` rather than trailing into the title.
- The listing prints a month and a day and **never a year** — resolved against
  the document's own date, picking the candidate nearest it so a sitting that
  straddles New Year stays on the right side of it.

**Cost.** One HTML request per pass. The napirend's slug names the sitting and
the House issues a new slug rather than editing one in place, so an unchanged
slug means the PDF is not fetched again (`--force` overrides). Text extraction
needs `pdftotext` (poppler-utils); without it the page's own findings are still
written and the agenda records why it is empty (SCR-6).

### The 1994-98 irományok (`bills --archive`)

The Felicitas `iromany` API knows nothing about the **35th cycle**: the query that
returns 19 408 documents for cycle 37 returns **zero** for 1994-98. What survives
is the static site the House published at the time, still served under
`parlament.hu/iromany/` — plain generated HTML, frozen on 1998-04-03. It is the
only source for those 5 646 documents, so `parlamonitor/bills/legacy.py` parses it
into the same record shape the API stage emits; the result is a drop-in
`bills-35.json` the loader ingests with no special-casing.

```bash
# Scrape the MP registry first — the archive links submitters by the very
# personID it uses (`s322`), so this is what makes them joinable.
python -m parlamonitor representatives --cycle 35 ./data

python -m parlamonitor bills --cycle 35 --archive ./data
```

What it reads, and what that costs:

| Layer | Requests | Default |
| ----- | -------- | ------- |
| the two "Összes" listings (number, date, type, title) | 2 | on — `--no-detail` stops here |
| one `<NNNNN>ir.htm` adatlap per document (submitters, status, promulgation, event history, committee events, roll-call links) | 5 646 | on |
| `mod/<NNNNN>imo.htm` — the non-self-standing motions, only where the adatlap says there are any | ~1 000 | on — `--no-motions` skips |
| `felsz/<NNNNN>npl.htm` — the speeches held on the document | ≤5 646 | **off** — `--speakers` |

At the default 1 s politeness that is a couple of hours, once. The run is
**resumable**: the pages cannot change, so any document already in
`bills-35.json` is reused whole and an interrupted scrape picks up where it
stopped (`--force` re-fetches everything). A `bills-35.json` left behind by the
*API* stage is ignored rather than merged.

Two things are parsed and saved but deliberately **not** loaded, for want of a
column that means them: the addressee a question was put to (`header.addressee` —
the modern scrape leaves the equivalent `cimzettNeve` out for the same reason;
the *answering* side is loaded, off the answer event's `relatedLabel`) and the
speaker listing (`detail.speakers`), which addresses speeches already in the
database — `sessionId` + `speechNumber` are `speech.session_id` and
`speech.speech_index` — but has nowhere to hang a bill→speech link except off an
event. That is why `--speakers` is opt-in.

The archive records no stage diagram, no vote tallies, no deadlines and no
background documents, so those stay empty rather than being guessed at. The
roll-call sheets it links (`/szavaz/szavlist/`) are, like the irományok, the only
surviving source for the cycle's votes — a separate stage, not this one.

One thing **is** rewritten, deliberately: the four event names that carry a
responding tárca. The archive abbreviates them into a fixed-width column and puts
them in the active voice ("kérdést megválaszolja" for what the API calls "kérdés
megválaszolva"), and the §6C portfolio derivation matches event names *exactly* —
so left alone, the cycle's ~4 000 answered questions would contribute nothing to
it. The map is enumerated in `_EVENT_NAMES` and the archive's own wording is kept
on the event's `remark`.

That gets the events matched; the **ministry names** on them are still the era's
(`művelődési és közokt. miniszter`, and ~30 more, half of them *politikai
államtitkár* posts). Those need entries in the hand-reviewed portfolio table in
`backend/app/portfolios.py` — without them each stands alone as its own tárca,
which the loader warns about by name at the end of a build.

### Continuous sync (`sync`) — stay in step with parlament.hu, cheaply

For a production deployment that must keep updating, the `sync` command does one
**low-load** pass over the **latest cycle** (auto-detected) and re-scrapes only
what changed since the last check (`parlamonitor/sync.py`):

```bash
python -m parlamonitor sync ./data                 # latest cycle, auto
python -m parlamonitor sync --cycle 43 ./data      # pin a cycle
```

- **Proceedings:** one `ulesnapok-query` lists the days; a sitting is re-scraped
  only when it is new, its date or duration changed, or (for the still-live latest
  day) its single-request speech listing changed — so finished days are never
  re-fetched. A day that has **dropped out of the listing** — an announced sitting
  parlament.hu has since **cancelled** — has its raw + processed files deleted
  (`--no-prune` on `proceedings` opts out), which both stops the site showing a
  sitting that will never happen and frees its ülésnap number for the day
  announced in its place, scraped in the same pass. A day that already holds
  speeches is never pruned this way: an existing record vanishing from a listing
  is an upstream glitch, so it is logged and kept. The DB row goes on the loader's
  next `--update`, which drops sittings whose processed file is gone.
- **Committees:** re-read whole each pass (there is nothing upstream to key a
  per-body cache on) and rewritten only when a count, a newly published minutes
  file or the newest meeting moved. `--skip-committees` drops the stage;
  `--no-detail` keeps the bodies and the membership but skips the per-body
  iromány listings, which are most of its ~130 requests.
- **Bills / votes:** the cheap list query runs, but per-item detail reuses the
  detail cache above, and the registry JSON is rewritten only when it differs.
- **Representatives:** refreshed on a slow cadence (`--reps-max-age`, default 12h,
  env `PARLAMONITOR_SYNC_REPS_MAX_AGE`); local portraits are preserved.
- **Nationality advocates:** same slow cadence, latest cycle only (`--skip-advocates`
  opts out). Past cycles' advocate rosters are closed, so they are backfilled once
  with `advocates --all-cycles`.
- **Aktuális:** every pass, on no slower cadence of its own (`--skip-aktualis`
  opts out) — what it carries is a *schedule*, and a napirend picked up the day
  after the sitting was never published at all as far as the site is concerned.
  One HTML request unless the House has issued a new order paper, and the file is
  rewritten only when something actually changed.

An idle poll is a handful of requests and writes nothing. Last-seen signatures
live in `data/sync-state.json`; the run prints a one-line JSON summary of what
changed and appends an ingestion log (SCR-3). `--force` ignores all caches.

`sync` only refreshes the JSON — bringing the **database** up to date is the
loader's incremental step (`python -m app.loader --update <data> <db>`), which
reloads only the changed files and atomically swaps the DB in (zero downtime).
The two are decoupled by the `processed/*.json` files, so you can run the scrape
on one host and the DB update on another. The Docker deployment wires both
together in a `sync` sidecar (or an external cron) — see
[../DEPLOYMENT.md](../DEPLOYMENT.md).

## Layout

```
parlamonitor/
  config.py            paths + environment-driven runtime config
  http_client.py       polite, retrying HTTP client
  ssh_proxy.py         optional SSH-tunnel HTTP proxy (paramiko)
  lockfile.py          flock concurrency guard (never a PID check)
  felicitas.py         Felicitas JSON API client (plenary + representatives
                       + szószólók + irományok + szavazások)
  names.py             speaker → name / faction / role / context
  agenda.py            HU agenda-type classification (+ bill-code extraction)
  segment.py           HTML cleanup + Hungarian sentence segmentation
  timing.py            timing stage: alignment where available, else positional
  whisper_align.py     forced alignment + the per-sitting transcription cache
  whisper_modal.py     Modal GPU transcription backend (see whisper_modal_app.py)
  detail_cache.py      incremental per-item detail caching (bills/votes)
  proceedings/
    scrape.py          download stage → raw-<session>-day.json
    transform.py       parse + classify + time → <session>-session.json
  representatives/
    scrape.py          roster + per-MP detail → representatives-<cycle>.json
  advocates/
    scrape.py          szószóló roster (+ reused per-person detail)
                       → advocates-<cycle>.json
  officeholders/
    scrape.py          tisztségviselő registry (office terms, real dates,
                       MPs and non-MPs) → officeholders.json
  bills/
    scrape.py          irományok list + per-bill detail → bills-<cycle>.json
    legacy.py          the 1994-98 static iromány archive → bills-35.json
  votes/
    scrape.py          szavazások list + per-vote detail → votes-<cycle>.json
  documents/
    scrape.py          the iromány document FILES + extracted text
                       → documents/<cycle>/ (optional; off by default)
  aktualis/
    page.py            the Aktuális portal page: its documents + the House
                       Committee's next meeting
    nr.py              the napirend (order paper) PDF → days, timetables, items
    scrape.py          fetch + parse + write → aktualis.json
  cli.py               workflow orchestration (stages, lockfile, ingest log)
whisper_modal_app.py   the Modal app deployed for the GPU backend
check_whisper_cache.py which copied whisper-<session>.json caches are usable
tests/
  test_pipeline.py     offline tests: transform, timing, names, agenda, segment
  test_advocates.py    offline tests: szószóló registry shape + cycle discovery
  test_bills_legacy.py offline tests: the static 1994-98 iromány archive
  test_officeholders.py offline tests: office-term grouping + open-ended terms
  test_documents.py    offline tests: the document mirror — retention, the
                       incremental index, graceful degradation
```

## Tests

```bash
cd scraper && python -m pytest tests/ -q
```

The tests are fully offline (OPS-3): they cover the JSON→record transform, the
sentence↔time mapping, speaker parsing, agenda classification and segmentation.
The parsers were additionally validated by replaying the real browser captures
in `../captures/`.

## Extending (new modules)

Adding a domain (Bills/*irományok*, Votes/*szavazások*, Committees) is additive
(requirements §7, EXT-1): create a sibling package under `parlamonitor/` with its own
`scrape.py`, add a subcommand in `cli.py`, and reference shared core entities
(`personID`, faction, session) rather than duplicating them. The captures show
the matching Felicitas providers already exist (`iromanyadatok-iromany-iromanyok`,
`szavazasok-szavazasok`), so each is a self-contained slice over the same API.
```
