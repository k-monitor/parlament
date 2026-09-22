"""Fetch and parse the committee jegyzőkönyv PDFs (BIZ-15).

The committee registry names a minutes PDF against every meeting that published
one (BIZ-9). This stage walks a saved registry, downloads each of those once,
extracts its text and hands it to :mod:`.minutes`, and writes the parsed
sittings to ``committee-minutes-<cycle>.json``.

**Scale.** Across cycles 40–43 the registry holds 3 725 meetings, 3 220 of them
with published minutes, and a sitting's text averages 51 kB — so the whole
corpus is around 165 MB of text, a tenth of what one cycle of irományok is
(DOC-1) and small enough that the text is kept by default rather than made
opt-in. The PDFs themselves are **not** kept unless asked for: they are 20x the
size of the text inside them and the live document stays linked, never mirrored
(BIZ-12 / LEGAL-1).

**Incremental (SCR-2).** A meeting already in the manifest, whose stored text is
still on disk and whose minutes URL has not changed, is not fetched again —
minutes are static once published, so a repeat pass costs nothing. ``force``
re-fetches anyway, and ``limit`` caps how many *new* documents one pass will
take so a first run over a full cycle can be paced across several (SCR-4).

**Degradation (SCR-5 / SCR-6).** Every failure is recorded against the meeting
it belongs to and none of them stops the pass: no ``pdftotext`` on the box, a
404 on a document the registry still lists, a scan with no text layer, a
document the parser could make nothing of. Each keeps its row carrying an
``error``, exactly as a meeting with no minutes at all keeps its row (BIZ-9) —
"we could not read this one" and "there is nothing to read" are different
findings and the site says which it is.
"""

from __future__ import annotations

import hashlib
import json
import logging
import lzma
import re
import shutil
import subprocess
from datetime import datetime, timezone

from ..config import Paths
from ..felicitas import COMMITTEE_FILE_BASE
from ..http_client import HttpClient
from . import minutes as parser

logger = logging.getLogger(__name__)

SOURCE = "parlament-hu-bizottsagi-jegyzokonyv"
# A jegyzőkönyv is a text PDF of a few dozen pages; the largest in a
# sixteen-sitting sample was 626 kB. Anything far past that is not the document
# we asked for, and we would rather notice than store it.
MAX_PDF_BYTES = 40 * 1024 * 1024
_REFERER = {"Referer": "https://www.parlament.hu/"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pdftotext_available() -> bool:
    return shutil.which("pdftotext") is not None


def extract_layout_text(pdf: bytes, *, timeout: float = 300.0) -> str | None:
    """A jegyzőkönyv's text with its indentation preserved (``-layout``).

    The iromány mirror extracts in reading order because that reads better as
    NLP input (DOC-1); minutes are the opposite case, in the same way the
    napirend is. Their indentation is not decoration — it is what separates an
    agenda heading from the paragraph under it, a sub-entry from its parent and
    a new paragraph from a wrapped line — and reading order throws all of it
    away."""
    if not pdftotext_available():
        return None
    try:
        p = subprocess.run(
            ["pdftotext", "-q", "-nopgbrk", "-enc", "UTF-8", "-layout", "-", "-"],
            input=pdf, capture_output=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("pdftotext failed: %s", e)
        return None
    # poppler exits non-zero on a damaged file but still writes what it salvaged.
    if p.returncode != 0 and not p.stdout:
        return None
    return p.stdout.decode("utf-8", "replace")


def normalise_url(url: str | None) -> str | None:
    """The minutes URL, with the missing slash upstream sometimes omits put back.

    :func:`parlamonitor.felicitas._committee_file_url` now joins the two with a
    separator, but registries scraped before it did carry 1 422 URLs of the form
    ``https://www.parlament.hubiz40/biz…`` — not a 404 but a hostname that does
    not resolve, which made the whole of cycles 40 and 41 unfetchable. They are
    repaired here rather than only at the source so a deployment can read its
    existing files without re-scraping four cycles.

    The repair is against the **known base**, not a general host pattern: once
    the two strings are concatenated, nothing in the URL says where the host was
    meant to end, so a regex over it would as happily cut ``www.parlament.h/u``.
    """
    if not url:
        return None
    url = url.strip()
    if url.startswith(COMMITTEE_FILE_BASE):
        rest = url[len(COMMITTEE_FILE_BASE):]
        if rest and not rest.startswith("/"):
            return f"{COMMITTEE_FILE_BASE}/{rest}"
    return url


def meetings_with_minutes(registry: dict) -> list[dict]:
    """Every meeting in a saved committee registry that published minutes."""
    out = []
    for m in registry.get("meetings") or []:
        url = normalise_url(m.get("minutesUrl"))
        if not url:
            continue
        out.append({**m, "minutesUrl": url})
    return out


# --- the store --------------------------------------------------------------

def _text_path(paths: Paths, cycle: int, meeting_id: str):
    return paths.committee_minutes_text_dir(cycle) / f"{_stem(meeting_id)}.txt.xz"


def _stem(meeting_id: str) -> str:
    """A safe filename for an upstream meeting id.

    The ids are opaque and change representation between cycles (numeric up to
    42, UUIDs in 43), so they are sanitised rather than trusted as paths."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(meeting_id)).strip("-.")
    return safe or hashlib.sha1(str(meeting_id).encode()).hexdigest()[:16]


def read_text(paths: Paths, cycle: int, meeting_id: str) -> str | None:
    """The stored text of one sitting, for a re-parse that fetches nothing."""
    path = _text_path(paths, cycle, meeting_id)
    if not path.exists():
        return None
    try:
        return lzma.decompress(path.read_bytes()).decode("utf-8")
    except (OSError, lzma.LZMAError, UnicodeDecodeError) as e:
        logger.warning("Could not read %s (%s)", path, e)
        return None


def _write_text(paths: Paths, cycle: int, meeting_id: str, text: str) -> int:
    out_dir = paths.committee_minutes_text_dir(cycle)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{_stem(meeting_id)}.txt.xz"
    blob = lzma.compress(text.encode("utf-8"))
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_bytes(blob)
    tmp.replace(out)
    return len(blob)


def _write_pdf(paths: Paths, cycle: int, meeting_id: str, pdf: bytes) -> int:
    out_dir = paths.committee_minutes_pdf_dir(cycle)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{_stem(meeting_id)}.pdf"
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_bytes(pdf)
    tmp.replace(out)
    return len(pdf)


def load_index(paths: Paths, cycle: int) -> dict:
    f = paths.committee_minutes_index(cycle)
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Could not read %s (%s) — treating as a first run", f, e)
        return {}


def save_index(paths: Paths, cycle: int, index: dict) -> None:
    f = paths.committee_minutes_index(cycle)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(json.dumps(index, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(f)


# --- the pass ---------------------------------------------------------------

def _fetch_one(http: HttpClient, meeting: dict) -> tuple[bytes | None, str | None]:
    """One minutes PDF, or ``(None, reason)``."""
    fetched = http.get_capped(meeting["minutesUrl"], max_bytes=MAX_PDF_BYTES,
                              headers=_REFERER)
    http.polite_sleep()
    if fetched.over_cap:
        return None, "document exceeds %d bytes" % MAX_PDF_BYTES
    if fetched.data is None:
        return None, "HTTP %s" % fetched.status
    if fetched.data[:5] != b"%PDF-" and \
            "pdf" not in (fetched.content_type or "").lower():
        return None, "not a PDF (%s)" % (fetched.content_type or "unknown type")
    return fetched.data, None


def fetch_minutes(http: HttpClient, paths: Paths, cycle: int,
                  meetings: list[dict], *, limit: int | None = None,
                  force: bool = False, keep_pdf: bool = False) -> dict:
    """Download, extract and parse the cycle's jegyzőkönyvek.

    ``meetings`` is :func:`meetings_with_minutes` over a saved committee
    registry — the meetings that published one. ``limit`` caps how many
    documents this pass will *fetch*; anything past it is left for the next run
    and counted as ``pending``, so a first pass over a full cycle can be paced
    (SCR-4) without the result looking complete when it is not.

    The stored text is the cache: a meeting whose text is on disk is re-parsed
    from it rather than re-downloaded, which makes a parser change cost nothing
    but CPU.
    """
    index = load_index(paths, cycle)
    if force:
        index = {}
    records: list[dict] = []
    fetched = reused = errors = pending = 0
    text_bytes = pdf_bytes = 0
    have_pdftotext = pdftotext_available()
    if not have_pdftotext:
        logger.warning("pdftotext (poppler-utils) is not installed — no minutes "
                       "can be read this pass")

    for meeting in meetings:
        mid = str(meeting["meetingId"])
        entry = index.get(mid)
        text = None
        if entry and not force and entry.get("url") == meeting["minutesUrl"]:
            text = read_text(paths, cycle, mid)
            if text is not None:
                reused += 1
        if text is None:
            if not have_pdftotext:
                records.append(_failed(meeting, "pdftotext is not installed"))
                errors += 1
                continue
            if limit is not None and fetched >= limit:
                pending += 1
                continue
            pdf, why = _fetch_one(http, meeting)
            if pdf is None:
                logger.warning("Minutes %s: %s", meeting["minutesUrl"], why)
                index[mid] = {"url": meeting["minutesUrl"], "error": why,
                              "fetchedAt": _now_iso()}
                records.append(_failed(meeting, why))
                errors += 1
                fetched += 1
                continue
            fetched += 1
            if keep_pdf:
                pdf_bytes += _write_pdf(paths, cycle, mid, pdf)
            text = extract_layout_text(pdf) or ""
            if not text.strip():
                # An image-only scan: it downloaded fine and simply has no text
                # layer. Recorded as its own finding, not as a fetch failure.
                index[mid] = {"url": meeting["minutesUrl"], "pdfBytes": len(pdf),
                              "error": "no text could be extracted",
                              "fetchedAt": _now_iso()}
                records.append(_failed(meeting, "no text could be extracted"))
                errors += 1
                continue
            stored = _write_text(paths, cycle, mid, text)
            text_bytes += stored
            index[mid] = {"url": meeting["minutesUrl"], "pdfBytes": len(pdf),
                          "textBytes": len(text), "storedBytes": stored,
                          "fetchedAt": _now_iso()}

        try:
            parsed = parser.parse(text)
        except Exception as e:                               # noqa: BLE001 (SCR-5)
            logger.exception("Could not parse %s", meeting["minutesUrl"])
            records.append(_failed(meeting, "parse failed: %s" % e))
            errors += 1
            continue
        index[mid]["stats"] = parsed["stats"]
        records.append({**_head(meeting), **parsed})
        if len(records) % 200 == 0:
            logger.info("  …%d/%d sittings read", len(records), len(meetings))

    save_index(paths, cycle, index)
    total_speeches = sum(r["stats"]["speeches"] for r in records if "stats" in r)
    total_chars = sum(r["stats"]["chars"] for r in records if "stats" in r)
    logger.info("Cycle %s minutes: %d read (%d fetched, %d reused, %d error(s), "
                "%d pending) — %d speeches, %.1f MB of text",
                cycle, len(records), fetched, reused, errors, pending,
                total_speeches, total_chars / 1e6)
    return {
        "meta": {
            "cycle": cycle,
            "source": SOURCE,
            "scrapedAt": _now_iso(),
            "pdftotext": have_pdftotext,
            "count": len(records),
            "counts": {
                "meetings": len(meetings), "fetched": fetched, "reused": reused,
                "errors": errors, "pending": pending,
                "speeches": total_speeches, "chars": total_chars,
                "textBytesStored": text_bytes, "pdfBytesStored": pdf_bytes,
            },
        },
        "data": records,
    }


def _head(meeting: dict) -> dict:
    """The registry facts a parsed sitting carries so the loader can key it
    without re-reading the committee registry beside it."""
    return {
        "meetingId": meeting["meetingId"],
        "committeeId": meeting.get("committeeId"),
        "committeeName": meeting.get("committeeName"),
        "datetime": meeting.get("datetime"),
        "minutesUrl": meeting["minutesUrl"],
    }


def _failed(meeting: dict, why: str) -> dict:
    """A sitting whose minutes could not be read. It keeps its row — the same
    rule BIZ-9 applies to a meeting that published nothing — because "we could
    not read this" and "there is nothing to read" are different findings."""
    return {**_head(meeting), "error": why}


def load_previous(paths: Paths, cycle: int) -> dict | None:
    f = paths.committee_minutes_file(cycle)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Could not read %s (%s)", f, e)
        return None


def save_minutes(paths: Paths, cycle: int, registry: dict) -> None:
    out = paths.committee_minutes_file(cycle)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)
    logger.info("Wrote %s (%d sittings, %d speeches)", out,
                registry["meta"]["count"],
                registry["meta"]["counts"]["speeches"])
