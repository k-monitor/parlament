"""Read a parlament.hu iromány PDF with its layout and markup intact (BILL-14..16).

``pdftotext`` (DOC-1) reads these documents well enough for NLP, but it throws
away the two things a diff needs:

* **Insertions.** A módosító javaslat is the affected provision reprinted with
  deleted text in **bold inside [brackets]** and inserted text **underlined**
  (the Parlex template, the same since 2016). The underline is a vector line
  drawn under the glyphs, not a font property, so plain text extraction cannot
  see which words a proposal adds.
* **Real hyphens.** Hungarian legal text is full of them (``1-jétől``,
  ``§-a``) and the PDF generator never hyphenates words, so a line ending in
  ``-`` is a real hyphen — ``pdftotext`` drops it and glues the halves.

PyMuPDF reads both: characters with their font and box, and the page drawings.
Its AGPL licence matches this project's. Where it is not installed the stage
degrades to doing nothing (SCR-6).

Everything here works on bytes and returns plain JSON-able structures, so the
stage can cache the extraction per document and re-run its parsers from disk.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Bumped when an extractor changes its output, so cached extractions are redone
# (the document is fetched again: the PDF itself is not kept) — per extractor,
# so a change to one does not refetch what only the other reads.
# marked 3: a table's rules are not underlines.
EXTRACT_VERSIONS = {"lines": 2, "marked": 3}
EXTRACT_VERSION = EXTRACT_VERSIONS["lines"]


def available() -> bool:
    try:
        import pymupdf  # noqa: F401
        return True
    except ImportError:
        return False


def _open(pdf: bytes):
    import pymupdf
    return pymupdf.open(stream=pdf, filetype="pdf")


def is_scan(pdf: bytes) -> bool:
    """A paper filing: pages that are one full-page image (with, at most, an OCR
    text layer over it). Its markup is gone and its text is OCR quality, so the
    stage records it as such instead of parsing it."""
    doc = _open(pdf)
    try:
        if doc.page_count == 0:
            return True
        full = 0
        for page in doc:
            area = page.rect.width * page.rect.height
            for img in page.get_image_info():
                x0, y0, x1, y1 = img["bbox"]
                if (x1 - x0) * (y1 - y0) > 0.8 * area:
                    full += 1
                    break
        return full * 2 >= doc.page_count
    finally:
        doc.close()


# --- plain body lines (bills, egységes javaslat) --------------------------------

def _join_spans(spans: list[dict]) -> str:
    """Concatenate a line's spans, putting back the space a tab-like gap stood
    for (a list label and its text are separate spans with no space between)."""
    out, prev_x1 = "", None
    for sp in spans:
        if prev_x1 is not None and sp["bbox"][0] - prev_x1 > 2.0 \
                and not out.endswith(" ") and not sp["text"].startswith(" "):
            out += " "
        out += sp["text"]
        prev_x1 = sp["bbox"][2]
    return out.strip()


def lines(pdf: bytes) -> list[dict]:
    """Every text line of the document, in reading order: ``{text, x, y, page,
    italic}``. Lines at the same height (a list label and its text are laid out
    as two) are merged; page numbers are dropped."""
    doc = _open(pdf)
    rows: list[dict] = []
    try:
        for page in doc:
            height = page.rect.height
            bucket: dict[int, list] = {}
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    spans = [s for s in line["spans"] if s["text"].strip()]
                    if not spans:
                        continue
                    y = round(line["bbox"][1])
                    key = next((k for k in bucket if abs(k - y) <= 2), y)
                    bucket.setdefault(key, []).append((line["bbox"][0], spans))
            for y in sorted(bucket):
                parts = sorted(bucket[y], key=lambda p: p[0])
                text = re.sub(r"\s+", " ", " ".join(_join_spans(sp) for _, sp in parts)).strip()
                if y > height - 90 and re.fullmatch(r"\d+", text):
                    continue
                first = parts[0][1]
                rows.append({"text": text, "x": round(parts[0][0], 1), "y": y,
                             "page": page.number,
                             "italic": all(s["flags"] & 2 for s in first)})
    finally:
        doc.close()
    return rows


# --- marked lines (módosító javaslatok) ----------------------------------------

def _underlines(page) -> list[tuple[float, float, float]]:
    """The horizontal strokes that underline text — not the rules of a table
    (a nyelvhelyességi módosítás is a table of corrections): a table's
    horizontal rules meet its vertical ones at their ends, an underline stands
    alone."""
    segs, verticals = [], []
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l" and abs(it[1].y - it[2].y) < 0.6:
                x0, x1 = sorted((it[1].x, it[2].x))
                segs.append((x0, x1, it[1].y))
            elif it[0] == "l" and abs(it[1].x - it[2].x) < 0.6:
                y0, y1 = sorted((it[1].y, it[2].y))
                verticals.append((it[1].x, y0, y1))
            elif it[0] == "re" and it[1].height < 1.5 and it[1].width > 2:
                r = it[1]
                segs.append((r.x0, r.x1, (r.y0 + r.y1) / 2))
            elif it[0] == "re" and it[1].width < 1.5 and it[1].height > 2:
                r = it[1]
                verticals.append(((r.x0 + r.x1) / 2, r.y0, r.y1))

    def ruled(x0, x1, y):
        return any((abs(vx - x0) < 1.5 or abs(vx - x1) < 1.5) and vy0 - 1.5 <= y <= vy1 + 1.5
                   for vx, vy0, vy1 in verticals)

    return [s for s in segs if not ruled(*s)]


def marked_lines(pdf: bytes) -> list[dict]:
    """Every line as runs of ``(mark, text)`` where mark is ``""``, ``"b"``
    (bold) or ``"u"`` (underlined — an insertion).

    Footnotes (set smaller than the body) and page numbers are dropped. Each
    line also carries its geometry (``x0``, ``x1``, ``page``) so a consumer can
    tell a wrapped line from a new paragraph."""
    doc = _open(pdf)
    out: list[dict] = []
    try:
        sizes: dict[float, int] = {}
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for s in line["spans"]:
                        if s["text"].strip():
                            k = round(s["size"], 1)
                            sizes[k] = sizes.get(k, 0) + len(s["text"])
        body = max(sizes, key=sizes.get) if sizes else 12.0
        for page in doc:
            segs = _underlines(page)
            height = page.rect.height
            raw = page.get_text("rawdict")
            for block in raw["blocks"]:
                for line in block.get("lines", []):
                    runs: list[list[str]] = []
                    x0 = x1 = None
                    for s in line["spans"]:
                        if s["size"] < 0.85 * body:
                            continue
                        bold = "Bold" in s["font"] or bool(s["flags"] & 16)
                        for c in s["chars"]:
                            cx0, cy0, cx1, cy1 = c["bbox"]
                            mid = (cx0 + cx1) / 2
                            ul = any(sx0 - 0.5 <= mid <= sx1 + 0.5 and cy1 - 4.5 <= sy <= cy1 + 2.5
                                     for sx0, sx1, sy in segs)
                            mark = "u" if ul and c["c"].strip() else ("b" if bold else "")
                            if ul and not c["c"].strip() and runs and runs[-1][0] == "u":
                                mark = "u"      # a space inside an insertion
                            if runs and runs[-1][0] == mark:
                                runs[-1][1] += c["c"]
                            else:
                                runs.append([mark, c["c"]])
                            x0 = cx0 if x0 is None else min(x0, cx0)
                            x1 = cx1 if x1 is None else max(x1, cx1)
                    text = "".join(t for _, t in runs).strip()
                    if not text:
                        continue
                    if line["bbox"][1] > height - 90 and re.fullmatch(r"\d+", text):
                        continue
                    out.append({"runs": runs, "x0": round(x0, 1), "x1": round(x1, 1),
                                "y": round(line["bbox"][1], 1), "page": page.number,
                                "right": round(page.rect.width, 1)})
    finally:
        doc.close()
    return out
