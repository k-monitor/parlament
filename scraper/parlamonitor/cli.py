"""Command-line workflow for the Parlamonitor scraper.

Stages are individually runnable and idempotent (requirements SCR-1/SCR-2); a
whole run is guarded by a lockfile and writes an ingestion log (SCR-3). All
politeness/transport knobs come from the environment or flags, never hard-coded
(SCR-4 / OPS-4).

    # Proceedings: download + transform the current cycle's sittings
    python -m parlamonitor proceedings --cycle 43 ./data

    # Just re-run the (offline) transform over already-downloaded raw files
    python -m parlamonitor proceedings --cycle 43 --transform-only ./data

    # Representative registry (roster only, fast)
    python -m parlamonitor representatives --cycle 43 --no-details ./data

    # Full representative registry with per-MP detail + photos
    python -m parlamonitor representatives --cycle 43 --photos ./data

    # Bills (irományok) of the current cycle
    python -m parlamonitor bills --cycle 43 ./data

    # Nationality advocates (szószólók) — one cycle, or backfill every cycle
    python -m parlamonitor advocates --cycle 43 ./data
    python -m parlamonitor advocates --all-cycles ./data

    # Office holders (tisztségviselők): every office term with its real dates
    python -m parlamonitor officeholders ./data
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import whisper_align
from .config import (Paths, RuntimeConfig, timing_backend, whisper_language,
                     whisper_model)
from .felicitas import FelicitasClient
from .http_client import HttpClient
from .lockfile import acquire
from .advocates.scrape import advocate_cycles, fetch_advocates, save_advocates
from .bills.scrape import DEFAULT_MAIN_TYPES, fetch_bills, save_bills
from .officeholders.scrape import fetch_office_holders, save_office_holders
from .votes.scrape import fetch_votes, save_votes
from .proceedings.scrape import download_period
from .proceedings.transform import transform_day
from .representatives.scrape import fetch_representatives, save_representatives
from .speaker_photos import fetch_nonroster_photos
from .sync import DEFAULT_REPS_MAX_AGE, latest_cycle, run_sync

logger = logging.getLogger("parlamonitor")


def _client(args) -> FelicitasClient:
    cfg = RuntimeConfig.from_env(
        sleep=args.sleep, retry_count=args.retry_count, proxy=args.proxy,
        ssh_host=args.ssh_host, ssh_port=args.ssh_port, ssh_user=args.ssh_user,
        ssh_key=args.ssh_key, ssh_known_hosts=args.ssh_known_hosts)
    return FelicitasClient(HttpClient(cfg))


def _resolve_range(felicitas: FelicitasClient, cycle: int, args) -> tuple[str, str]:
    ranges = felicitas.cycle_ranges()
    rng = ranges.get(cycle) or {}
    start = args.date_from or rng.get("start")
    end = args.date_to or rng.get("end") or datetime.now(timezone.utc).date().isoformat()
    if not start:
        raise SystemExit(f"Cannot resolve start date for cycle {cycle}; "
                         f"pass --from explicitly")
    return start, end


def _latest_cycle_or_none(felicitas: FelicitasClient) -> int | None:
    """The newest cycle upstream, or ``None`` when it can't be resolved. Only used
    to scope the Modal offload (``PARLAMONITOR_MODAL_CYCLES``), which then falls
    back to the newest cycle on disk — so a failure here must never abort the run.
    The client memoizes the underlying fetch, so in a normal scrape this is free."""
    try:
        return latest_cycle(felicitas)
    except Exception as e:
        logger.debug("Could not resolve the latest cycle (%s); the Modal scope "
                     "falls back to the newest cycle on disk", e)
        return None


def _write_log(paths: Paths, payload: dict) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (paths.logs / f"ingest-{ts}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False))


# --- transform stage -------------------------------------------------------

def _pending_builds(paths: Paths, *, force: bool,
                    only: list[str] | None = None) -> list[str]:
    """Sessions whose session record is missing or older than its raw bundle (all,
    when ``force``). The align and transform stages act on this same set so their
    work stays in step."""
    pending: list[str] = []
    for raw_path in sorted(paths.raw_plenary.glob("raw-*-day.json")):
        session = raw_path.name[len("raw-"):-len("-day.json")]
        if only is not None and session not in only:
            continue
        out_path = paths.session_file(session)
        if (not force and out_path.exists()
                and out_path.stat().st_mtime >= raw_path.stat().st_mtime):
            continue
        pending.append(session)
    return pending


def align_all(paths: Paths, sessions: list[str], *, backend: str, force: bool,
              latest_cycle: int | None = None) -> dict[str, list]:
    """Ensure a Whisper transcription is cached for each session's recording,
    returning ``{session: words}`` for those that have one (TIM-1). A no-op returning
    ``{}`` when the backend resolves to ``character`` or nothing needs building.

    ``latest_cycle`` scopes the (metered) Modal offload — see
    :func:`whisper_align.ensure_words`; without it the newest cycle on disk stands
    in, so backfilling an old cycle into an existing corpus is still recognised as
    out of scope."""
    days: list[tuple[str, str | None, str | None]] = []
    for session in sessions:
        try:
            raw = json.loads(paths.raw_day(session).read_text())
        except (OSError, ValueError):
            continue
        video = raw.get("video") or {}
        days.append((session, video.get("m3u8"), video.get("playseq")))
    if not days:
        return {}
    return whisper_align.ensure_words(
        paths, days, backend=backend, model=whisper_model(),
        language=whisper_language(), force=force, latest_cycle=latest_cycle)


def transform_all(paths: Paths, sessions: list[str], *,
                  words_by_session: dict[str, list] | None = None) -> list[str]:
    """Transform the given raw day bundles into session records, applying Whisper
    forced-alignment timing where a transcription is available for the session."""
    words_by_session = words_by_session or {}
    built: list[str] = []
    for session in sessions:
        raw_path = paths.raw_day(session)
        out_path = paths.session_file(session)
        raw = json.loads(raw_path.read_text())
        record = transform_day(raw, words=words_by_session.get(session))
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        tmp.replace(out_path)
        built.append(session)
        logger.info("Built %s: %d speeches (%s)", session,
                    record["meta"]["counts"]["speeches"],
                    record["meta"]["timingMethod"])
    return built


# --- subcommands -----------------------------------------------------------

def cmd_proceedings(args) -> None:
    paths = Paths(args.data_dir)
    paths.ensure()
    felicitas = _client(args)
    errors: list[str] = []
    downloaded: list[str] = []

    try:
        with acquire(paths.lockfile, force=args.force_lock):
            if not args.transform_only:
                start, end = _resolve_range(felicitas, args.cycle, args)
                logger.info("Downloading cycle %s sittings in [%s, %s]",
                            args.cycle, start, end)
                try:
                    downloaded = download_period(
                        felicitas, paths, args.cycle, start, end,
                        force=args.force, resolve_offsets=not args.no_offsets,
                        reuse_text=args.reuse_text,
                        allow_renumber=args.allow_renumber)
                except Exception as e:
                    logger.exception("Download failed")
                    errors.append(f"download: {e}")

            built = []
            words_by_session: dict[str, list] = {}
            if not args.download_only:
                pending = _pending_builds(paths, force=args.force)
                if pending and not args.no_align:
                    try:
                        backend = args.timing_backend or timing_backend()
                        # Only the (metered) Modal path needs to know which cycle is
                        # the newest, so nothing else pays for the lookup.
                        latest = (_latest_cycle_or_none(felicitas)
                                  if whisper_align.resolve_backend(backend)
                                  == "whisper-modal" else None)
                        words_by_session = align_all(
                            paths, pending, backend=backend, force=args.force,
                            latest_cycle=latest)
                    except Exception as e:
                        logger.exception("Whisper alignment failed; falling back "
                                         "to positional timing")
                        errors.append(f"align: {e}")
                built = transform_all(paths, pending,
                                      words_by_session=words_by_session)
    finally:
        felicitas.close()

    _write_log(paths, {
        "command": "proceedings",
        "cycle": args.cycle,
        "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "backend": "felicitas-json",
        "timingBackend": args.timing_backend or timing_backend(),
        "downloaded": downloaded,
        "built": built,
        "aligned": sorted(words_by_session),
        "errors": errors,
    })
    logger.info("Proceedings run done: %d downloaded, %d built, %d errors",
                len(downloaded), len(built), len(errors))
    if errors:
        sys.exit(1)


def cmd_representatives(args) -> None:
    paths = Paths(args.data_dir)
    paths.ensure()
    felicitas = _client(args)
    photos_dir = (paths.data / "media" / "photos") if args.photos else None

    try:
        with acquire(paths.lockfile, force=args.force_lock):
            registry = fetch_representatives(
                felicitas, args.cycle,
                details=not args.no_details, limit=args.limit,
                photos_dir=photos_dir, link_wikidata=not args.no_wikidata)
            save_representatives(paths, args.cycle, registry)
    finally:
        felicitas.close()

    _write_log(paths, {
        "command": "representatives",
        "cycle": args.cycle,
        "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": registry["meta"]["count"],
        "withDetails": registry["meta"]["withDetails"],
    })


def cmd_advocates(args) -> None:
    """Scrape the nationality-advocate registry (nemzetiségi szószólók).

    ``--cycle`` does one cycle; ``--all-cycles`` backfills every cycle that has
    advocates (cycle 40 onward), which is what an already-scraped corpus needs to
    catch up — the sittings themselves are untouched, since the advocates already
    appear in them as speakers under the very ids this registry is keyed by.
    Portraits are downloaded by default: there are only ~13 advocates per cycle,
    so it costs a handful of requests and is what makes their profiles look like
    an MP's."""
    paths = Paths(args.data_dir)
    paths.ensure()
    felicitas = _client(args)
    photos_dir = None if args.no_photos else (paths.data / "media" / "photos")
    written: list[dict] = []

    try:
        with acquire(paths.lockfile, force=args.force_lock):
            if args.all_cycles:
                cycles = advocate_cycles(felicitas)
            elif args.cycle is not None:
                cycles = [args.cycle]
            else:
                sys.exit("advocates: pass --cycle N or --all-cycles")
            for cycle in cycles:
                registry = fetch_advocates(
                    felicitas, cycle, details=not args.no_details,
                    limit=args.limit, photos_dir=photos_dir,
                    link_wikidata=not args.no_wikidata)
                # A cycle that legitimately has none (or one whose query came back
                # empty) is not written: an empty registry file would only teach the
                # loader to forget the advocates it already holds for that cycle.
                if not registry["data"]:
                    logger.info("Cycle %s has no advocates; nothing written", cycle)
                    continue
                save_advocates(paths, cycle, registry)
                written.append({"cycle": cycle, "count": registry["meta"]["count"]})
    finally:
        felicitas.close()

    _write_log(paths, {
        "command": "advocates",
        "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cycles": written,
        "withDetails": not args.no_details,
    })
    logger.info("Advocates: wrote %d cycle registr(y/ies) — %s",
                len(written), written)


def cmd_officeholders(args) -> None:
    """Scrape the office-holder registry (tisztségviselők): every government /
    House office term with its real start and end date.

    Cycle-less and cheap (a handful of paged requests for the whole archive), and
    the only source that dates the office of a **non-MP** minister or state
    secretary — who is in no roster, so nothing else does."""
    paths = Paths(args.data_dir)
    paths.ensure()
    felicitas = _client(args)
    registry = None
    try:
        with acquire(paths.lockfile, force=args.force_lock):
            registry = fetch_office_holders(felicitas, as_of=args.as_of)
            # An empty registry is never written: it would only teach the loader to
            # forget the office terms it already holds (cf. the advocates stage).
            if registry["data"]:
                save_office_holders(paths, registry)
            else:
                logger.warning("Office-holder registry came back empty; "
                               "nothing written")
    finally:
        felicitas.close()

    meta = (registry or {}).get("meta", {})
    _write_log(paths, {
        "command": "officeholders",
        "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "asOf": meta.get("asOf"),
        "people": meta.get("count", 0),
        "terms": meta.get("terms", 0),
    })
    logger.info("Office holders: %s people, %s office terms",
                meta.get("count", 0), meta.get("terms", 0))


def cmd_bills(args) -> None:
    paths = Paths(args.data_dir)
    paths.ensure()
    felicitas = _client(args)
    main_types = (tuple(t.strip() for t in args.main_types.split(",") if t.strip())
                  if args.main_types else DEFAULT_MAIN_TYPES)

    try:
        with acquire(paths.lockfile, force=args.force_lock):
            registry = fetch_bills(felicitas, args.cycle, main_types=main_types,
                                   with_detail=not args.no_detail,
                                   cache_path=paths.bills_file(args.cycle),
                                   force=args.force)
            save_bills(paths, args.cycle, registry)
    finally:
        felicitas.close()

    _write_log(paths, {
        "command": "bills",
        "cycle": args.cycle,
        "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mainTypes": list(main_types) if main_types else "all",
        "count": registry["meta"]["count"],
        "detailFetched": registry["meta"].get("detailFetched"),
        "detailReused": registry["meta"].get("detailReused"),
    })


def cmd_votes(args) -> None:
    paths = Paths(args.data_dir)
    paths.ensure()
    felicitas = _client(args)

    try:
        with acquire(paths.lockfile, force=args.force_lock):
            start, end = _resolve_range(felicitas, args.cycle, args)
            registry = fetch_votes(felicitas, args.cycle, start, end,
                                   with_detail=not args.no_detail,
                                   cache_path=paths.votes_file(args.cycle),
                                   force=args.force)
            save_votes(paths, args.cycle, registry)
    finally:
        felicitas.close()

    _write_log(paths, {
        "command": "votes",
        "cycle": args.cycle,
        "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": registry["meta"]["count"],
        "detailFetched": registry["meta"].get("detailFetched"),
        "detailReused": registry["meta"].get("detailReused"),
    })


def cmd_sync(args) -> None:
    """Low-load continuous sync of the latest cycle (SCR-2/SCR-4).

    Cheaply checks parlament.hu and re-scrapes only what changed, refreshing the
    processed JSON. The DB is brought up to date separately by the loader's
    incremental ``--update`` (kept decoupled so either half can run alone)."""
    paths = Paths(args.data_dir)
    paths.ensure()
    felicitas = _client(args)
    reps_max_age = (args.reps_max_age if args.reps_max_age is not None
                    else float(os.environ.get("PARLAMONITOR_SYNC_REPS_MAX_AGE",
                                              DEFAULT_REPS_MAX_AGE)))
    try:
        with acquire(paths.lockfile, force=args.force_lock):
            cycle = args.cycle or latest_cycle(felicitas)
            logger.info("Sync check for latest cycle %s", cycle)
            summary = run_sync(
                felicitas, paths, cycle, force=args.force,
                no_detail=args.no_detail, no_offsets=args.no_offsets,
                reps_max_age=reps_max_age, skip_bills=args.skip_bills,
                skip_votes=args.skip_votes, skip_reps=args.skip_reps,
                skip_advocates=args.skip_advocates,
                skip_office_holders=args.skip_officeholders)
            # Top up portraits for non-roster speakers of this cycle (ministers /
            # nationality advocates who aren't in the MP roster). Cheap on an idle
            # poll: already-downloaded ids are skipped and 404s are negative-cached,
            # so only a genuinely new speaker triggers a request.
            try:
                summary["speakerPhotos"] = fetch_nonroster_photos(
                    felicitas, paths, cycle)
            except Exception as e:  # never let a photo hiccup fail the sync (SCR-5)
                logger.warning("non-roster photo top-up failed: %s", e)
    finally:
        felicitas.close()

    _write_log(paths, {"command": "sync", **summary,
                       "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "backend": "felicitas-json"})
    logger.info("Sync done: %d sitting(s) changed, bills=%s votes=%s reps=%s "
                "advocates=%s, %d error(s)",
                len(summary["sessions"]), summary["bills"], summary["votes"],
                summary["representatives"], summary["advocates"],
                len(summary["errors"]))
    # Machine-readable one-liner for a wrapping script / cron log.
    print(json.dumps({"changed": summary["changed"],
                      "sessions": summary["sessions"],
                      "bills": summary["bills"], "votes": summary["votes"],
                      "representatives": summary["representatives"],
                      "advocates": summary["advocates"],
                      "errors": summary["errors"]}))
    if summary["errors"]:
        sys.exit(1)


def cmd_speaker_photos(args) -> None:
    """Download portraits for speakers who aren't in the MP roster (REP-2).

    Ministers and nationality advocates (nemzetiségi szószólók) speak in plenary
    but aren't in the roster, so they otherwise show only a placeholder. This
    fetches whatever portrait the image resource has for each (advocates resolve;
    portrait-less ministers 404 and are negative-cached). The loader wires the
    on-disk file onto the person row on the next build/update."""
    paths = Paths(args.data_dir)
    paths.ensure()
    felicitas = _client(args)
    try:
        result = fetch_nonroster_photos(felicitas, paths, args.cycle)
    finally:
        felicitas.close()
    print(json.dumps(result))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="parlamonitor",
                                description="Parlamonitor scraper")
    p.add_argument("--debug", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    def _common(sp, *, cycle_required=True):
        sp.add_argument("data_dir", type=Path, help="output data directory")
        sp.add_argument("--cycle", type=int, required=cycle_required, default=None,
                        help="electoral cycle number (e.g. 43)")
        sp.add_argument("--sleep", type=float, default=None,
                        help="politeness delay between requests (s)")
        sp.add_argument("--retry-count", type=int, default=None)
        sp.add_argument("--proxy", type=str, default=None)
        sp.add_argument("--ssh-host", default=None,
                        help="route parlament.hu traffic through this SSH host "
                             "(needs --ssh-user and --ssh-key)")
        sp.add_argument("--ssh-port", type=int, default=None,
                        help="SSH port (default 22)")
        sp.add_argument("--ssh-user", default=None, help="SSH username")
        sp.add_argument("--ssh-key", default=None,
                        help="path to the SSH private key")
        sp.add_argument("--ssh-known-hosts", default=None,
                        help="known_hosts file (default: trust on first use)")
        sp.add_argument("--force-lock", action="store_true",
                        help="reclaim the lockfile even if it looks held")

    sp = sub.add_parser("proceedings", help="scrape plenary proceedings")
    _common(sp)
    sp.add_argument("--from", dest="date_from", default=None,
                    help="ISO start date (default: cycle start)")
    sp.add_argument("--to", dest="date_to", default=None,
                    help="ISO end date (default: cycle end or today)")
    sp.add_argument("--force", action="store_true",
                    help="re-download and rebuild even if cached")
    sp.add_argument("--no-offsets", action="store_true",
                    help="skip per-speech video-offset resolution (faster)")
    sp.add_argument("--allow-renumber", action="store_true",
                    help="let a sitting overwrite a session key currently held by "
                         "a different date. Refused by default: a source-side "
                         "renumbering silently destroys the day the key meant. "
                         "Use (with --force) only to repair a mis-numbered cycle")
    sp.add_argument("--reuse-text", action="store_true",
                    help="with --force: re-list every day but keep the speech "
                         "text/offsets already downloaded, fetching only speeches "
                         "the raw file lacks (cheap archive backfill)")
    sp.add_argument("--timing-backend", default=None,
                    choices=["auto", "whisper-modal", "whisper-local", "character"],
                    help="sentence-timing method (default: env "
                         "PARLAMONITOR_TIMING_BACKEND or 'auto' — Whisper forced "
                         "alignment where available, else the character estimate)")
    sp.add_argument("--no-align", action="store_true",
                    help="skip Whisper alignment; use the positional character "
                         "estimate (equivalent to --timing-backend character)")
    sp.add_argument("--download-only", action="store_true")
    sp.add_argument("--transform-only", action="store_true")
    sp.set_defaults(func=cmd_proceedings)

    sp = sub.add_parser("representatives", help="scrape the MP registry")
    _common(sp)
    sp.add_argument("--no-details", action="store_true",
                    help="roster only, skip per-MP detail queries")
    sp.add_argument("--no-wikidata", action="store_true",
                    help="skip the Wikidata/Wikipedia link query")
    sp.add_argument("--photos", action="store_true", help="download MP portraits")
    sp.add_argument("--limit", type=int, default=None,
                    help="cap number of MPs (for testing)")
    sp.set_defaults(func=cmd_representatives)

    sp = sub.add_parser("advocates",
                        help="scrape the nationality-advocate registry "
                             "(nemzetiségi szószólók)")
    _common(sp, cycle_required=False)
    sp.add_argument("--all-cycles", action="store_true",
                    help="scrape every cycle that has advocates (cycle 40 on) — "
                         "the backfill for an already-scraped corpus")
    sp.add_argument("--no-details", action="store_true",
                    help="roster only, skip the per-person detail queries")
    sp.add_argument("--no-wikidata", action="store_true",
                    help="skip the Wikidata/Wikipedia link query")
    sp.add_argument("--no-photos", action="store_true",
                    help="skip portrait downloads (on by default: ~13 per cycle)")
    sp.add_argument("--limit", type=int, default=None,
                    help="cap number of advocates (for testing)")
    sp.set_defaults(func=cmd_advocates)

    sp = sub.add_parser("officeholders",
                        help="scrape the office-holder registry (tisztségviselők): "
                             "every office term with its real dates, MPs and "
                             "non-MPs alike")
    _common(sp, cycle_required=False)
    sp.add_argument("--as-of", default=None,
                    help="upper date bound of the listing, YYYY-MM-DD "
                         "(default: today)")
    sp.set_defaults(func=cmd_officeholders)

    sp = sub.add_parser("bills", help="scrape the cycle's irományok (all document types)")
    _common(sp)
    sp.add_argument("--main-types", default=None,
                    help="comma-separated Felicitas fotipus codes to restrict to "
                         "(e.g. T,H); default: all iromány types")
    sp.add_argument("--no-detail", action="store_true",
                    help="skip per-document detail (events/votes/committees/…) "
                         "for a fast list-only refresh")
    sp.add_argument("--force", action="store_true",
                    help="re-fetch every bill's detail, ignoring the cache "
                         "(default: reuse cached detail for unchanged bills)")
    sp.set_defaults(func=cmd_bills)

    sp = sub.add_parser("votes", help="scrape the cycle's roll-call votes (szavazások)")
    _common(sp)
    sp.add_argument("--from", dest="date_from", default=None,
                    help="ISO start date (default: cycle start)")
    sp.add_argument("--to", dest="date_to", default=None,
                    help="ISO end date (default: cycle end or today)")
    sp.add_argument("--no-detail", action="store_true",
                    help="skip per-vote detail (per-MP roll call + faction "
                         "breakdown) for a fast list-only refresh")
    sp.add_argument("--force", action="store_true",
                    help="re-fetch every vote's detail, ignoring the cache "
                         "(default: only new votes are fetched)")
    sp.set_defaults(func=cmd_votes)

    sp = sub.add_parser("sync", help="one low-load sync pass over the latest cycle "
                                     "(re-scrape only what changed)")
    _common(sp, cycle_required=False)
    sp.add_argument("--force", action="store_true",
                    help="ignore all caches/signatures and re-scrape everything")
    sp.add_argument("--no-detail", action="store_true",
                    help="skip per-item bill/vote detail + per-MP detail "
                         "(fast list-only refresh)")
    sp.add_argument("--no-offsets", action="store_true",
                    help="skip per-speech video-offset resolution (faster)")
    sp.add_argument("--reps-max-age", type=float, default=None,
                    help="only refresh representatives when the last refresh is "
                         "older than this many seconds "
                         f"(default {DEFAULT_REPS_MAX_AGE}, env "
                         "PARLAMONITOR_SYNC_REPS_MAX_AGE)")
    sp.add_argument("--skip-bills", action="store_true")
    sp.add_argument("--skip-votes", action="store_true")
    sp.add_argument("--skip-reps", action="store_true")
    sp.add_argument("--skip-advocates", action="store_true",
                    help="skip the nationality-advocate refresh")
    sp.add_argument("--skip-officeholders", action="store_true",
                    help="skip the office-holder (tisztségviselők) refresh")
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("speaker-photos",
                        help="download portraits for non-roster speakers "
                             "(ministers / nationality advocates)")
    _common(sp, cycle_required=False)
    sp.set_defaults(func=cmd_speaker_photos)
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args.func(args)


if __name__ == "__main__":
    main()
