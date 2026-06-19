# Országgyűlés Watch — Scraper

The scraping pipeline for [Országgyűlés Watch](../requirements.md): it fetches
Hungarian National Assembly (*Magyar Országgyűlés*) data from `parlament.hu`,
parses it, segments speeches into sentences, estimates sentence↔video timing,
and writes self-contained JSON records ready to load into the search database.

This project **owns the scraping end to end** and replaces the legacy
`OpenParliamentTV-Tools` HU pipeline (requirements §3.1, SRC-1). That tree is
kept only as reference material; nothing here imports it at runtime.

## What it produces

| Output | Module | Shape |
| ------ | ------ | ----- |
| `processed/<session>-session.json` | `ogywatch.proceedings` | one record per sitting day: ordered speeches, each with agenda item, speaker(s), the whole-day HLS video, and `textContents → textBody → sentences[]` with day-absolute `timeStart`/`timeEnd`. |
| `processed/representatives-<cycle>.json` | `ogywatch.representatives` | the MP registry for a cycle: bio, faction & committee history, constituency, education, and per-cycle speech / bill-submission counts. |
| `logs/ingest-<ts>.json` | both | per-run ingestion log (run time, sittings added, errors, backend). |

Each speech's speaker carries a `personID` (`kepviseloId`) that joins directly
to the representative registry — no name matching needed (requirements EXT-2).

## How the data is fetched

Everything goes through the modern, **token-free Felicitas JSON API**
(`ogywatch/felicitas.py`), verified against browser captures (2026-06):

**Proceedings** — `plenaris-ules-adatok-query-provider`:

1. `ulesnapok-query` (cycle + date range) → session days + UUIDs.
2. `ulesnapok-aktusok-query` (day UUID) → every speech grouped by agenda act,
   with its join number, speaker, `felszolaloId`, type, committee and duration.
3. `ulesnap-felszolalas-adata-query` (speech UUID) → the full speech text.
4. `ulesnapok-video-query` (day UUID) → the whole-day HLS playlist on
   `sgis.parlament.hu`. Per-speech offsets are also resolved and recorded for
   provenance (used by a future precise-timing stage, not by v1).

**Representatives** — `kepviselo-query-provider`: a paged roster query plus a
family of per-MP detail queries and a photo resource endpoint.

The reference's CGI (token) and PAIR-proxy (HTML) backends remain documented in
`OpenParliamentTV-Tools` as fallbacks (SRC-2) but are not needed for v1.

## Sentence ↔ video timing (v1)

v1 timing is a **positional/character estimate** (requirements TIM-1): the
whole-day stream duration is distributed across the day's transcript in
proportion to character position, so a sentence's share of the timeline equals
its share of the day's characters. Offsets are **day-absolute** seconds into the
HLS stream, so clicking a sentence seeks into it (TIM-2). Every timed sentence is
stamped `align-method = "estimated-day-offset"` with reduced `confidence` so the
UI can disclose the imprecision (TIM-3 / VIE-6).

Timing is a **distinct, swappable stage** (`ogywatch/timing.py`, TIM-4): the
real per-speech offsets the scraper already captures into
`media.videoStart`/`videoEnd`, or forced alignment, can replace it later without
touching the fetch/parse/segment stages or the data shape (requirements §10).

## Running

From a clean checkout (requirements SCR-6 — the dependency surface is small):

```bash
cd scraper
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt        # just `requests`; spaCy is optional

# Proceedings: download + transform the current cycle (43) into ./data
python -m ogywatch proceedings --cycle 43 ./data

# Re-run only the offline transform/timing over already-downloaded raw files
python -m ogywatch proceedings --cycle 43 --transform-only ./data

# A bounded slice (e.g. backfill a date window)
python -m ogywatch proceedings --cycle 43 --from 2026-05-09 --to 2026-06-18 ./data

# Representative registry — roster only (one paged query, fast)
python -m ogywatch representatives --cycle 43 --no-details ./data

# Full registry with per-MP detail + portraits (heavier; one run per cycle)
python -m ogywatch representatives --cycle 43 --photos ./data
```

### Operational knobs (all environment-driven — OPS-4 / SCR-4)

| Flag | Env var | Default | Purpose |
| ---- | ------- | ------- | ------- |
| `--sleep` | `OGYWATCH_SLEEP` | `1.0` | politeness delay between requests |
| `--retry-count` | `OGYWATCH_RETRY_COUNT` | `5` | retries per request (exp. backoff) |
| `--proxy` | `OGYWATCH_PROXY` | — | SOCKS5/HTTP proxy for `parlament.hu` |
| — | `OGYWATCH_USER_AGENT` | civic-tech UA | request User-Agent |
| `--no-offsets` | — | off | skip per-speech offset resolution (faster) |

Runs are **idempotent** and guarded by a lockfile (`data/ogywatch.lock`,
SCR-1): a cached sitting is skipped unless it is the still-live latest sitting
or `--force` is given. A sitting with no resolvable recording or no transcript
is still written in degraded form and flagged (`confidence`, `confidence_reason`,
`align-method`), never silently dropped (SCR-5).

Schedule it from cron/systemd (OPS-2); each run appends an ingestion log under
`data/logs/` (SCR-3).

## Layout

```
ogywatch/
  config.py            paths + environment-driven runtime config
  http_client.py       polite, retrying HTTP client
  lockfile.py          PID lockfile (concurrency guard)
  felicitas.py         Felicitas JSON API client (plenary + representatives)
  names.py             speaker → name / faction / role / context
  agenda.py            HU agenda-type classification (+ bill-code extraction)
  segment.py           HTML cleanup + Hungarian sentence segmentation
  timing.py            v1 positional/character timing stage
  proceedings/
    scrape.py          download stage → raw-<session>-day.json
    transform.py       parse + classify + time → <session>-session.json
  representatives/
    scrape.py          roster + per-MP detail → representatives-<cycle>.json
  cli.py               workflow orchestration (stages, lockfile, ingest log)
tests/
  test_pipeline.py     offline tests: transform, timing, names, agenda, segment
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
(requirements §7, EXT-1): create a sibling package under `ogywatch/` with its own
`scrape.py`, add a subcommand in `cli.py`, and reference shared core entities
(`personID`, faction, session) rather than duplicating them. The captures show
the matching Felicitas providers already exist (`iromanyadatok-iromany-iromanyok`,
`szavazasok-szavazasok`), so each is a self-contained slice over the same API.
```
