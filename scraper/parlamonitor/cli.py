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
from .bills.scrape import DEFAULT_MAIN_TYPES, fetch_bills, save_bills
from .votes.scrape import fetch_votes, save_votes
from .proceedings.scrape import download_period
from .proceedings.transform import transform_day
from .representatives.scrape import fetch_representatives, save_representatives
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


def align_all(paths: Paths, sessions: list[str], *, backend: str, force: bool
              ) -> dict[str, list]:
    """Ensure a Whisper transcription is cached for each session's recording,
    returning ``{session: words}`` for those that have one (TIM-1). A no-op returning
    ``{}`` when the backend resolves to ``character`` or nothing needs building."""
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
        language=whisper_language(), force=force)


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
                        force=args.force, resolve_offsets=not args.no_offsets)
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
                        words_by_session = align_all(
                            paths, pending, backend=backend, force=args.force)
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
                photos_dir=photos_dir)
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
                skip_votes=args.skip_votes, skip_reps=args.skip_reps)
    finally:
        felicitas.close()

    _write_log(paths, {"command": "sync", **summary,
                       "ranAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "backend": "felicitas-json"})
    logger.info("Sync done: %d sitting(s) changed, bills=%s votes=%s reps=%s, %d error(s)",
                len(summary["sessions"]), summary["bills"], summary["votes"],
                summary["representatives"], len(summary["errors"]))
    # Machine-readable one-liner for a wrapping script / cron log.
    print(json.dumps({"changed": summary["changed"],
                      "sessions": summary["sessions"],
                      "bills": summary["bills"], "votes": summary["votes"],
                      "representatives": summary["representatives"],
                      "errors": summary["errors"]}))
    if summary["errors"]:
        sys.exit(1)


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
    sp.add_argument("--photos", action="store_true", help="download MP portraits")
    sp.add_argument("--limit", type=int, default=None,
                    help="cap number of MPs (for testing)")
    sp.set_defaults(func=cmd_representatives)

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
    sp.set_defaults(func=cmd_sync)
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args.func(args)


if __name__ == "__main__":
    main()
