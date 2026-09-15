"""Fetch the Aktuális page, mirror the napirend PDF, write ``aktualis.json``.

One poll does three things: read the portal page (:mod:`.page`), download the
**napirend** PDF it links to, and parse that PDF into a structured agenda
(:mod:`.nr`). The result is a single cycle-less registry, rewritten whole on
every run — the page states only the House's *current* position, so the newest
read replaces the previous one rather than accumulating (NR-1).

**Incremental (SCR-2).** The napirend's slug carries the sitting it is for
(``nr_20260914_elfogadott``), and the House re-publishes the document under a
new slug rather than editing one in place. So when the slug is the one the last
run already parsed, the PDF is not fetched again — an idle poll costs exactly
one HTML request. ``force=True`` re-fetches anyway.

**Degradation (SCR-5 / SCR-6).** Every stage past the page fetch is optional
and recorded: no ``pdftotext`` on the box, a 404 on the document, a PDF that
parses to nothing — each leaves ``agenda`` empty with an ``agendaError``
saying why, and the page's own findings (the documents, the House Committee's
next meeting) are still written. Only the page fetch itself can fail the run.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import date, datetime, timezone

from ..config import Paths
from ..http_client import HttpClient
from . import nr, page

logger = logging.getLogger(__name__)

SOURCE = "parlament-hu-aktualis"
# The napirend is a text PDF of a few dozen pages; anything far past that is not
# the document we asked for, and we would rather notice than mirror it.
MAX_PDF_BYTES = 25 * 1024 * 1024
_REFERER = {"Referer": page.PAGE_URL}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pdftotext_available() -> bool:
    return shutil.which("pdftotext") is not None


def extract_layout_text(pdf: bytes, *, timeout: float = 120.0) -> str | None:
    """A PDF's text with its **columns preserved** (``pdftotext -layout``).

    The document mirror (DOC-1) extracts in reading order, because that reads
    better as NLP input. The napirend is the opposite case: its meaning is in
    its columns — an item's ordinal, its ``B./n`` back-reference and its
    iromány number are a left gutter, and in reading order they interleave with
    the title text they belong to. So this stage asks for the layout, and the
    parser peels the gutter off itself.
    """
    if not pdftotext_available():
        return None
    try:
        p = subprocess.run(
            ["pdftotext", "-q", "-nopgbrk", "-enc", "UTF-8", "-layout", "-", "-"],
            input=pdf, capture_output=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("pdftotext failed: %s", e)
        return None
    if p.returncode != 0 and not p.stdout:
        return None
    return p.stdout.decode("utf-8", "replace")


def load_previous(paths: Paths) -> dict | None:
    """The registry the last run wrote, for the incremental slug check."""
    f = paths.aktualis_file()
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Could not read %s (%s) — treating as a first run", f, e)
        return None


def _reusable_agenda(previous: dict | None, slug: str | None) -> dict | None:
    if not (previous and slug):
        return None
    prior = (previous.get("data") or {}).get("agenda")
    if prior and prior.get("slug") == slug and prior.get("days"):
        return prior
    return None


def _parse_pdf(http: HttpClient, doc: dict) -> dict:
    """Download and parse one napirend PDF into ``{...agenda}`` or an error."""
    out: dict = {"slug": doc["slug"], "url": doc["url"], "documentDate": doc.get("date"),
                 "label": doc.get("label"), "fetchedAt": _now_iso()}
    if not pdftotext_available():
        out["error"] = "pdftotext (poppler-utils) is not installed"
        return out
    fetched = http.get_capped(doc["url"], max_bytes=MAX_PDF_BYTES, headers=_REFERER)
    http.polite_sleep()
    if fetched.over_cap:
        out["error"] = "document exceeds %d bytes" % MAX_PDF_BYTES
        return out
    if fetched.data is None:
        out["error"] = "HTTP %s" % fetched.status
        return out
    if fetched.data[:5] != b"%PDF-" and "pdf" not in (fetched.content_type or "").lower():
        out["error"] = "not a PDF (%s)" % (fetched.content_type or "unknown type")
        return out
    out["pdfBytes"] = len(fetched.data)
    text = extract_layout_text(fetched.data)
    if not text or not text.strip():
        out["error"] = "no text could be extracted"
        return out
    reference = None
    if doc.get("date"):
        try:
            reference = date.fromisoformat(doc["date"])
        except ValueError:
            reference = None
    try:
        out.update(nr.parse(text, reference=reference))
    except Exception as e:                                   # noqa: BLE001 (SCR-5)
        logger.exception("Could not parse %s", doc["url"])
        out["error"] = "parse failed: %s" % e
    return out


def fetch_aktualis(http: HttpClient, *, previous: dict | None = None,
                   force: bool = False) -> dict:
    """Read the Aktuális page and the napirend behind it into one registry."""
    html_text = http.get_text(page.PAGE_URL)
    http.polite_sleep()
    parsed = page.parse(html_text)
    documents = parsed["documents"]

    agenda_doc = next((d for d in documents if d["kind"] == "agenda"), None)
    agenda: dict | None = None
    reused = False
    if agenda_doc is None:
        logger.warning("Aktuális linked no napirend (nr_*) document")
    else:
        prior = None if force else _reusable_agenda(previous, agenda_doc["slug"])
        if prior is not None:
            agenda, reused = prior, True
            logger.info("Napirend %s unchanged — reusing the parsed agenda",
                        agenda_doc["slug"])
        else:
            agenda = _parse_pdf(http, agenda_doc)
            if agenda.get("error"):
                logger.warning("Napirend %s not parsed: %s", agenda_doc["slug"],
                               agenda["error"])

    meta = {
        "source": SOURCE,
        "pageUrl": page.PAGE_URL,
        "scrapedAt": _now_iso(),
        "documentCount": len(documents),
        "agendaSlug": agenda_doc["slug"] if agenda_doc else None,
        "agendaReused": reused,
        "agendaError": (agenda or {}).get("error"),
        "itemCount": (agenda or {}).get("itemCount", 0),
        "pdftotext": pdftotext_available(),
    }
    return {"meta": meta,
            "data": {"documents": documents,
                     "houseCommittee": parsed["houseCommittee"],
                     "agenda": agenda}}


def save_aktualis(paths: Paths, registry: dict) -> None:
    out = paths.aktualis_file()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)
    logger.info("Wrote %s (%d documents, %d agenda items)", out,
                registry["meta"]["documentCount"], registry["meta"]["itemCount"])
