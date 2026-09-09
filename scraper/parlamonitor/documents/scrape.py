"""Mirror the iromány document files and extract their text (DOC-1).

The irományok registry (``bills-<cycle>.json``) has always held only *links* to
the documents — ``textUrl`` on each document and motion, plus the justification
and background files hanging off a bill's detail. That is enough to cite a
document but not to analyse one, and NLP over the actual legislative text needs
the text.

This stage walks a saved registry, downloads each distinct document once, and
stores what the run was configured to keep:

    <data_dir>/documents/<cycle>/
      text/<docid>.txt.xz     extracted text, compressed (the NLP input)
      pdf/<docid>.pdf         the source file, when it is being retained
      index.json              per-document manifest + incremental cache

**What it keeps is a deliberate choice, and the default is nothing.** Measured
over all 861 documents of cycle 43, the corpus is **868 MB** of PDF, of which
compression recovers barely a fifth — PDF streams are already deflated, so
gzip/zstd/xz all land near 82% of the original and a lossless ``qpdf`` rebuild
near 88%. The text inside those PDFs, by contrast, is 16.3 MB raw and
**3.96 MB** xz-compressed: **219x** smaller than the files it came from, and the
only part any NLP pass actually reads. So the retention ladder is

    off   (default)  download nothing — the live server stays as it was
    text             keep only the extracted text           (~4 MB / cycle)
    pdf              keep only the source PDFs             (~868 MB / cycle)
    all              keep both                             (~872 MB / cycle)

set by ``PARLAMONITOR_DOCUMENTS`` / ``--documents`` (see
:func:`parlamonitor.config.documents_retention`). Note that ``text`` still
*downloads* every PDF — it just does not keep it; the saving is disk, not
bandwidth.

Text extraction shells out to ``pdftotext`` (poppler-utils), the same way the
local Whisper backend leans on ``ffmpeg``. Where it is missing, the stage says
so once and carries on storing whatever else it was asked to (SCR-6); where a
document is an image-only scan it yields no text and is recorded as such rather
than as a failure — 8 of cycle 43's 861 documents, all of them scanned
stamps and signature pages.

Re-runs are incremental (SCR-2): a document already in the manifest, whose
stored artefacts are still on disk, is not re-fetched. Documents on
parlament.hu are static once published, so this makes a repeat run nearly free.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import lzma
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ..config import Paths
from ..http_client import HttpClient

logger = logging.getLogger(__name__)

# Referer the rest of the scraper sends to parlament.hu; the document resources
# sit on the same host and are served the same way.
_REFERER = {"Referer": "https://www.parlament.hu/"}

# How the compression choice maps onto a file suffix and a codec. ``xz`` is
# stdlib lzma and the best of the options measured over the whole cycle-43
# corpus (16.3 MB of extracted text → 3.96 MB, against 4.65 MB gzipped and
# 3.97 MB for `zstd -19`); gzip is here for a store other tools read without
# ceremony, and `none` for reading the text by hand.
_CODECS = {
    "xz": (".xz", lzma.compress, lzma.decompress),
    "gzip": (".gz", lambda b: gzip.compress(b, 9), gzip.decompress),
    "none": ("", lambda b: b, lambda b: b),
}

# Cap on the readable part of a document id, before its URL hash is appended.
_SLUG_MAX = 72


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def document_id(url: str) -> str:
    """A stable, readable, collision-free filename stem for a document URL.

    The readable half comes from the URL path (``/irom43/00477/00477.pdf`` →
    ``irom43-00477-00477``) so the store can be browsed by hand; the trailing
    8 hex chars of the URL's SHA-1 keep it unique, which the readable half alone
    would not be — the ``/documents/d/guest/…`` background files carry titles
    long enough to collide once truncated."""
    path = re.sub(r"^https?://[^/]+/?", "", url.strip())
    path = re.sub(r"\.(pdf|doc|docx|rtf|txt)$", "", path, flags=re.I)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", path).strip("-.")
    slug = re.sub(r"-{2,}", "-", slug)[:_SLUG_MAX].strip("-.")
    digest = hashlib.sha1(url.strip().encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}" if slug else digest


def document_refs(registry: dict) -> list[dict]:
    """Every distinct downloadable document in a saved irományok registry.

    Covers all three places a file link appears: the document's own ``textUrl``,
    the ``textUrl`` of each non-self-standing motion (módosítók, committee
    reports, …) under its detail, and the justification/background files in
    ``detail["documents"]``. A URL reached from several bills — a joint
    background file often is — is returned once, tagged with the first bill that
    referenced it, so it is downloaded and stored once."""
    seen: dict[str, dict] = {}

    def add(url, kind, bill, *, title=None, number=None, doc_type=None):
        if not url or url in seen:
            return
        seen[url] = {
            "id": document_id(url), "url": url, "kind": kind,
            "billId": bill.get("billId"), "billNumber": number or bill.get("billNumber"),
            "title": title or bill.get("title"), "docType": doc_type,
        }

    for rec in registry.get("data") or []:
        add(rec.get("textUrl"), "main", rec, doc_type=rec.get("type"))
        detail = rec.get("detail") or {}
        for m in detail.get("motions") or []:
            add(m.get("textUrl"), "motion", rec, title=m.get("note"),
                number=m.get("billNumber"), doc_type=m.get("type"))
        for d in detail.get("documents") or []:
            add(d.get("url"), d.get("kind") or "document", rec, title=d.get("title"))
    return list(seen.values())


# --- text extraction --------------------------------------------------------

def pdftotext_available() -> bool:
    return shutil.which("pdftotext") is not None


def extract_text(pdf: bytes, *, timeout: float = 300.0) -> str | None:
    """The text of a PDF via ``pdftotext``, or ``None`` when it cannot be read.

    ``-layout`` is deliberately **not** used: it pads with spaces to reproduce
    the visual columns, which reads worse as NLP input than the default reading
    order. ``None`` means the extractor itself failed (absent or erroring);
    an image-only scan succeeds and returns an empty/near-empty string, which is
    a different thing and is recorded differently by the caller."""
    if not pdftotext_available():
        return None
    try:
        p = subprocess.run(
            ["pdftotext", "-q", "-nopgbrk", "-enc", "UTF-8", "-", "-"],
            input=pdf, capture_output=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("pdftotext failed: %s", e)
        return None
    # poppler exits non-zero on a damaged file but still writes what it salvaged,
    # so the output is worth keeping whenever there is any.
    if p.returncode != 0 and not p.stdout:
        return None
    return p.stdout.decode("utf-8", "replace")


def page_count(pdf: bytes, *, timeout: float = 60.0) -> int | None:
    """Page count via ``pdfinfo``, or ``None`` if it is unavailable/unreadable."""
    if shutil.which("pdfinfo") is None:
        return None
    try:
        p = subprocess.run(["pdfinfo", "-"], input=pdf, capture_output=True,
                           timeout=timeout)
        for line in p.stdout.decode("utf-8", "replace").splitlines():
            if line.startswith("Pages:"):
                return int(line.split()[1])
    except (subprocess.SubprocessError, OSError, ValueError, IndexError):
        pass
    return None


# --- the store --------------------------------------------------------------

def _write_text(paths: Paths, cycle: int, doc_id: str, text: str,
                compression: str) -> tuple[str, int]:
    suffix, encode, _ = _CODECS[compression]
    out_dir = paths.document_text_dir(cycle)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{doc_id}.txt{suffix}"
    blob = encode(text.encode("utf-8"))
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_bytes(blob)
    tmp.replace(out)
    return f"text/{out.name}", len(blob)


def read_text(paths: Paths, cycle: int, entry: dict) -> str | None:
    """Read one manifest entry's stored text back, whatever it was compressed
    with — the accessor an NLP pass over the store should use."""
    rel = entry.get("textFile")
    if not rel:
        return None
    f = paths.documents_dir(cycle) / rel
    if not f.exists():
        return None
    for suffix, _, decode in _CODECS.values():
        if suffix and f.name.endswith(suffix):
            return decode(f.read_bytes()).decode("utf-8", "replace")
    return f.read_text(encoding="utf-8", errors="replace")


def _write_pdf(paths: Paths, cycle: int, doc_id: str, pdf: bytes) -> str:
    out_dir = paths.document_pdf_dir(cycle)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{doc_id}.pdf"
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_bytes(pdf)
    tmp.replace(out)
    return f"pdf/{out.name}"


def load_index(paths: Paths, cycle: int) -> dict:
    """The manifest of a previous run, keyed by document id (empty on the first
    run or an unreadable file — which simply means fetching everything)."""
    f = paths.documents_index(cycle)
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Document index %s unreadable (%s); re-fetching all", f, e)
        return {}
    return {e["id"]: e for e in (data.get("documents") or []) if e.get("id")}


def save_index(paths: Paths, cycle: int, entries: list[dict], meta: dict) -> None:
    out = paths.documents_index(cycle)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta,
               "documents": sorted(entries, key=lambda e: e.get("id") or "")}
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(out)


def _is_complete(entry: dict, paths: Paths, cycle: int, retention: str) -> bool:
    """Is this manifest entry's work already done, with its files still present?

    A ``missing``/``too-large`` document is complete too — it was resolved, and
    nothing about re-asking would change the answer. Only an outright fetch
    ``error`` is retried on the next run."""
    if entry.get("status") in ("missing", "too-large"):
        return True
    if entry.get("status") != "ok":
        return False
    root = paths.documents_dir(cycle)
    if retention in ("pdf", "all"):
        if not entry.get("pdfFile") or not (root / entry["pdfFile"]).exists():
            return False
    if retention in ("text", "all"):
        # No text is a settled outcome for an image-only scan, and so is a
        # resource that is not a PDF at all; a text file that was promised and
        # has since gone missing is not, and neither is an extraction that never
        # ran because pdftotext was absent — installing it should backfill.
        if entry.get("textFile") and not (root / entry["textFile"]).exists():
            return False
        if not entry.get("textFile") and entry.get("textChars") is None \
                and not entry.get("notPdf"):
            return False
    return True


def _local_pdf(entry: dict | None, paths: Paths, cycle: int) -> bytes | None:
    """The already-stored PDF for a manifest entry, if we kept one.

    Lets a run that only needs to *re-extract* — poppler installed since the
    last pass, or retention widened from ``pdf`` to ``all`` — do it from disk
    instead of asking parlament.hu for bytes it already has (SCR-4)."""
    if not entry or not entry.get("pdfFile"):
        return None
    f = paths.documents_dir(cycle) / entry["pdfFile"]
    try:
        return f.read_bytes() if f.exists() else None
    except OSError:
        return None


def _extract_into(entry: dict, pdf: bytes, paths: Paths, cycle: int,
                  doc_id: str, *, want_text: bool, compression: str,
                  counts: dict, content_type: str | None = None) -> None:
    """Fill ``entry``'s text/page fields from ``pdf`` bytes, writing the text out.

    Shared by the download path and the re-extract-from-disk path so a document
    is described the same way however its bytes were obtained."""
    is_pdf = pdf[:5] == b"%PDF-" or "pdf" in (content_type or
                                              entry.get("contentType") or "").lower()
    if not is_pdf:
        # An HTML error page or a Word attachment: keep the bytes if we were
        # asked to, but do not pretend text extraction applies.
        entry["notPdf"] = True
    if not (want_text and is_pdf):
        return
    text = extract_text(pdf)
    if text is None:
        entry["textError"] = "pdftotext unavailable or failed"
    else:
        entry.pop("textError", None)
        stripped = text.strip()
        entry["textChars"] = len(stripped)
        if stripped:
            rel, nbytes = _write_text(paths, cycle, doc_id, text, compression)
            entry["textFile"] = rel
            entry["textBytes"] = nbytes
        else:
            counts["noText"] += 1   # image-only scan: a real answer, not a failure
    pages = page_count(pdf)
    if pages is not None:
        entry["pages"] = pages


def fetch_documents(http: HttpClient, paths: Paths, cycle: int, registry: dict, *,
                    retention: str = "text", compression: str = "xz",
                    max_mb: float = 0.0, force: bool = False,
                    limit: int | None = None) -> dict:
    """Download the cycle's iromány documents and store them per ``retention``.

    ``registry`` is a loaded ``bills-<cycle>.json``. ``retention`` is one of
    ``text`` / ``pdf`` / ``all`` (``off`` is handled by the caller — this
    function is never reached with it). ``compression`` names the text codec,
    ``max_mb`` caps a single document (0 = no cap), ``force`` re-fetches
    everything, and ``limit`` stops after that many *fetched* documents, for a
    smoke run.

    Returns a summary dict; the per-document manifest is written to
    ``documents/<cycle>/index.json`` as it goes."""
    if retention not in ("text", "pdf", "all"):
        raise ValueError(f"unexpected retention {retention!r}")
    if compression not in _CODECS:
        raise ValueError(f"unexpected compression {compression!r}")

    want_text = retention in ("text", "all")
    want_pdf = retention in ("pdf", "all")
    if want_text and not pdftotext_available():
        logger.warning(
            "pdftotext (poppler-utils) is not installed — documents will be "
            "fetched but no text can be extracted%s",
            "; nothing would be stored, so this is very likely not what you want"
            if retention == "text" else "")

    refs = document_refs(registry)
    index = {} if force else load_index(paths, cycle)
    max_bytes = int(max_mb * 1024 * 1024) if max_mb else 0

    entries: list[dict] = []
    counts = {"total": len(refs), "fetched": 0, "reused": 0, "missing": 0,
              "tooLarge": 0, "noText": 0, "errors": 0}
    bytes_seen = pdf_stored = text_stored = 0

    for i, ref in enumerate(refs, 1):
        prior = index.get(ref["id"])
        if prior and not force and _is_complete(prior, paths, cycle, retention):
            entry = {**prior, **{k: ref[k] for k in
                                 ("url", "kind", "billId", "billNumber", "title",
                                  "docType")}}
            entries.append(entry)
            counts["reused"] += 1
            bytes_seen += entry.get("pdfBytes") or 0
            pdf_stored += entry.get("pdfStoredBytes") or 0
            text_stored += entry.get("textBytes") or 0
            continue

        if limit is not None and counts["fetched"] >= limit:
            if prior:
                entries.append(prior)
            continue

        entry = dict(ref)
        cached_pdf = None if force else _local_pdf(prior, paths, cycle)
        if cached_pdf is not None:
            # Everything below wants `pdf` + an `entry`; the only thing this
            # branch skips is the request.
            entry.update({k: prior[k] for k in
                          ("fetchedAt", "status", "contentType", "pdfBytes",
                           "sha256", "pdfFile", "pdfStoredBytes", "notPdf")
                          if k in prior})
            _extract_into(entry, cached_pdf, paths, cycle, ref["id"],
                          want_text=want_text, compression=compression,
                          counts=counts)
            if entry.get("textBytes"):
                text_stored += entry["textBytes"]
            bytes_seen += entry.get("pdfBytes") or 0
            pdf_stored += entry.get("pdfStoredBytes") or 0
            entries.append(entry)
            counts["reused"] += 1
            continue

        entry["fetchedAt"] = _now_iso()
        try:
            got = http.get_capped(ref["url"], max_bytes=max_bytes,
                                  headers=_REFERER)
        except Exception as e:  # one bad document never aborts the cycle (SCR-5)
            logger.exception("Document fetch failed: %s", ref["url"])
            entry.update(status="error", error=str(e))
            counts["errors"] += 1
            entries.append(entry)
            counts["fetched"] += 1
            http.polite_sleep()
            continue

        counts["fetched"] += 1
        if got.over_cap:
            entry.update(status="too-large", contentType=got.content_type)
            counts["tooLarge"] += 1
            logger.info("Document %d/%d over the %g MB cap, skipped: %s",
                        i, len(refs), max_mb, ref["url"])
            entries.append(entry)
            http.polite_sleep()
            continue
        if got.data is None:
            entry.update(status="missing", httpStatus=got.status,
                         contentType=got.content_type)
            counts["missing"] += 1
            entries.append(entry)
            http.polite_sleep()
            continue

        pdf = got.data
        entry.update(status="ok", contentType=got.content_type,
                     pdfBytes=len(pdf),
                     sha256=hashlib.sha256(pdf).hexdigest())
        bytes_seen += len(pdf)

        _extract_into(entry, pdf, paths, cycle, ref["id"], want_text=want_text,
                      compression=compression, counts=counts,
                      content_type=got.content_type)
        if entry.get("textBytes"):
            text_stored += entry["textBytes"]

        if want_pdf:
            entry["pdfFile"] = _write_pdf(paths, cycle, ref["id"], pdf)
            entry["pdfStoredBytes"] = len(pdf)
            pdf_stored += len(pdf)

        entries.append(entry)
        if counts["fetched"] % 25 == 0:
            logger.info("Documents %d/%d (%d fetched, %d reused, %.1f MB seen, "
                        "%.1f MB stored)", i, len(refs), counts["fetched"],
                        counts["reused"], bytes_seen / 1e6,
                        (pdf_stored + text_stored) / 1e6)
            save_index(paths, cycle, entries, _meta(cycle, retention, compression,
                                                    max_mb, counts, bytes_seen,
                                                    pdf_stored, text_stored))
        http.polite_sleep()

    meta = _meta(cycle, retention, compression, max_mb, counts, bytes_seen,
                 pdf_stored, text_stored)
    save_index(paths, cycle, entries, meta)
    logger.info("Documents cycle %s: %d total, %d fetched, %d reused, "
                "%d missing, %d over-cap, %d no-text, %d errors; "
                "%.1f MB downloaded, %.1f MB stored",
                cycle, counts["total"], counts["fetched"], counts["reused"],
                counts["missing"], counts["tooLarge"], counts["noText"],
                counts["errors"], bytes_seen / 1e6,
                (pdf_stored + text_stored) / 1e6)
    return meta


def _meta(cycle, retention, compression, max_mb, counts, bytes_seen,
          pdf_stored, text_stored) -> dict:
    return {
        "cycle": cycle,
        "scrapedAt": _now_iso(),
        "retention": retention,
        "compression": compression,
        "maxMb": max_mb or None,
        **counts,
        "bytesDownloaded": bytes_seen,
        "bytesStoredPdf": pdf_stored,
        "bytesStoredText": text_stored,
        "bytesStored": pdf_stored + text_stored,
    }


def load_registry(paths: Paths, cycle: int) -> dict:
    """The saved irományok registry a document run reads its links from."""
    f = paths.bills_file(cycle)
    if not f.exists():
        raise FileNotFoundError(
            f"{f} not found — run `bills --cycle {cycle}` first; the document "
            f"stage only mirrors files the registry already links to")
    return json.loads(f.read_text(encoding="utf-8"))
