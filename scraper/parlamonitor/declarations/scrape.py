"""Fetch the EVNYR snapshot, resolve each declaration's PDF, write
``asset-declarations.json``.

One run reads the home page for the current CSV snapshot, parses the snapshot
into declarations (:mod:`.evnyr`), and reads each declaration's page for the
PDF it links to. The result is a single cycle-less registry, **rewritten whole**
on every run. The snapshot is the House's complete current list of public
declarations, so a declaration withdrawn or corrected upstream leaves or
changes here on the next run instead of lingering as a stale copy (REP-18).

**Incremental (SCR-2).** The snapshot is regenerated once a day under a new
date-stamped name, so when the name is the one the last run parsed, the CSV is
not downloaded again. A declaration's PDF link is carried over for as long as
the declaration is unchanged (same id, same finalisation/modification time).
An idle poll therefore costs one HTML request, and a new snapshot costs the CSV
plus one page per declaration that is new or changed.

**Politeness (SCR-4).** The first run has to read one page per declaration
already filed, a few hundred once the new cycle's MPs have all filed, so
``page_limit`` caps how many pages one run reads. The rest are published
without their PDF link (the declaration page link is always there) and are
picked up by the next run. A declaration whose page names no PDF is asked
about again only when the snapshot changes, never on every poll.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..config import Paths
from ..http_client import HttpClient, HttpError
from . import evnyr

logger = logging.getLogger(__name__)

SOURCE = "parlament-hu-evnyr"
# Today's snapshot is ~10 KB for one declaration; a few hundred declarations is
# a few MB. Anything far past that is not the export we asked for.
MAX_CSV_BYTES = 64 * 1024 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_previous(paths: Paths) -> dict | None:
    """The registry the last run wrote, for the incremental checks."""
    f = paths.asset_declarations_file()
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Could not read %s (%s): treating as a first run", f, e)
        return None


def _version(d: dict) -> tuple:
    """What identifies one state of a declaration: a correction upstream moves
    one of these, and the PDF behind it may then be a new file."""
    return (d.get("finalizedAt"), d.get("modifiedAt"), d.get("schemaVersion"))


def _download_csv(http: HttpClient, url: str) -> str:
    fetched = http.get_capped(url, max_bytes=MAX_CSV_BYTES,
                              headers={"Referer": evnyr.HOME_URL})
    http.polite_sleep()
    if fetched.over_cap:
        raise HttpError(f"CSV snapshot exceeds {MAX_CSV_BYTES} bytes: {url}")
    if fetched.data is None or fetched.status != 200:
        raise HttpError(f"CSV snapshot HTTP {fetched.status}: {url}")
    if "html" in (fetched.content_type or "").lower():
        raise HttpError(f"CSV snapshot came back as HTML: {url}")
    return fetched.data.decode("utf-8-sig", errors="replace")


def fetch_declarations(http: HttpClient, *, previous: dict | None = None,
                       seen_csv_url: str | None = None, force: bool = False,
                       page_limit: int | None = None) -> dict:
    """Read the current snapshot into one registry.

    ``previous`` is the last registry written (see :func:`load_previous`).
    ``seen_csv_url`` is the last snapshot already read into it, when the caller
    tracks that apart from the file (``sync`` does: a new day's snapshot with the
    same contents is not worth rewriting the file, and so reloading the DB, for).
    It defaults to the snapshot the file itself names. ``page_limit`` caps the
    declaration pages this run reads (``None``: no cap).

    Raises when the home page links no snapshot or the snapshot cannot be read:
    either means the site changed under us, and a registry built from nothing
    would only teach the loader to forget the declarations it holds."""
    home = http.get_text(evnyr.HOME_URL)
    http.polite_sleep()
    csv_url = evnyr.snapshot_url(home)
    if not csv_url:
        raise RuntimeError("the EVNYR home page links no CSV snapshot")

    prev_meta = (previous or {}).get("meta") or {}
    prev_data = (previous or {}).get("data") or []
    prev_by_id = {d.get("id"): d for d in prev_data if d.get("id")}
    seen = seen_csv_url or prev_meta.get("csvUrl")
    snapshot_changed = seen != csv_url

    if force or snapshot_changed or not previous:
        declarations = evnyr.parse_csv(_download_csv(http, csv_url))
        csv_reused = False
    else:
        # The same snapshot: its parse is already on file. Copies, so the PDF
        # bookkeeping below never edits the previous registry in place.
        declarations = [{k: v for k, v in d.items()
                         if k not in ("url", "pdfUrl", "pdfCheckedAt")}
                        for d in prev_data]
        csv_reused = True

    fetched = pending = missing = errors = 0
    for d in declarations:
        d["url"] = evnyr.declaration_url(d["id"])
        prior = prev_by_id.get(d["id"])
        unchanged = prior is not None and _version(prior) == _version(d)
        checked = bool(prior and prior.get("pdfCheckedAt"))
        need = (force or not unchanged or not checked
                or (prior.get("pdfUrl") is None and snapshot_changed))
        if not need:
            d["pdfUrl"] = prior.get("pdfUrl")
            d["pdfCheckedAt"] = prior.get("pdfCheckedAt")
            continue
        if page_limit is not None and fetched >= page_limit:
            # Not read yet: published without its PDF, asked again next run.
            d["pdfUrl"] = prior.get("pdfUrl") if unchanged else None
            d["pdfCheckedAt"] = None
            pending += 1
            continue
        fetched += 1
        try:
            page = http.get_text(d["url"], headers={"Referer": evnyr.HOME_URL})
        except HttpError as e:
            logger.warning("Declaration page %s failed: %s", d["url"], e)
            d["pdfUrl"] = prior.get("pdfUrl") if unchanged else None
            d["pdfCheckedAt"] = None
            errors += 1
            continue
        finally:
            http.polite_sleep()
        d["pdfUrl"] = evnyr.pdf_url(page, d["id"])
        d["pdfCheckedAt"] = _now_iso()
        if d["pdfUrl"] is None:
            missing += 1
            logger.warning("Declaration page %s links no PDF", d["url"])

    # Newest first, as the profile shows them.
    declarations.sort(key=lambda d: (d.get("finalizedAt") or "", d["id"]),
                      reverse=True)
    meta = {
        "source": SOURCE,
        "homeUrl": evnyr.HOME_URL,
        "csvUrl": csv_url,
        "snapshotAt": evnyr.snapshot_time(csv_url),
        "scrapedAt": _now_iso(),
        "count": len(declarations),
        "csvReused": csv_reused,
        "pagesFetched": fetched,
        "pdfPending": pending,
        "pdfMissing": missing,
        "pageErrors": errors,
    }
    logger.info("EVNYR snapshot %s: %d declaration(s), %d page(s) read, "
                "%d PDF link(s) pending, %d missing, %d error(s)",
                meta["snapshotAt"] or csv_url, len(declarations), fetched,
                pending, missing, errors)
    return {"meta": meta, "data": declarations}


def save_declarations(paths: Paths, registry: dict) -> None:
    out = paths.asset_declarations_file()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(out)
    logger.info("Wrote %s (%d declarations)", out, registry["meta"]["count"])
