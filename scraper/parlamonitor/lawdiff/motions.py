"""A módosító javaslat as a diff of the bill (BILL-16).

Every amendment filed through Parlex — an MP's módosító javaslat, a committee's
részletes vitát lezáró bizottsági módosító javaslat, the Törvényalkotási
Bizottság's összegző módosító javaslat — has the same body: numbered points,
each naming the provision it changes and reprinting it with the deletions in
bold inside ``[brackets]`` and the insertions underlined::

    Módosítópont sorszáma: 1.
    Törvényjavaslat érintett rendelkezése: 2. § (2) bekezdés - Szkt. 26. § (4a) bekezdés
    Módosítás jellege: módosítás
    „(4a) … a [főigazgató a ]szakképzésért felelős miniszter …”

So each point already *is* a diff; reading it only needs the formatting that
plain text extraction loses (:func:`.pdftext.marked_lines`). A paper filing
(scanned, with an OCR layer at most) has lost it for good and is recorded as a
scan, not parsed.
"""

from __future__ import annotations

import re

# Motion types whose document is an amendment text. A request to keep one
# alive ("… fenntartásának kérése"), a committee's decision about one ("…
# tárgyalására vonatkozóan") or a consolidated text ("… egységes javaslat")
# are not.
_AMENDMENT_TYPE = re.compile(r"módosító javaslat$", re.I)
_CONSOLIDATED_TYPE = re.compile(r"egységes javaslat$", re.I)


def is_amendment(motion_type: str | None) -> bool:
    return bool(motion_type and _AMENDMENT_TYPE.search(motion_type.strip()))


def is_consolidated(motion_type: str | None) -> bool:
    return bool(motion_type and _CONSOLIDATED_TYPE.search(motion_type.strip()))


_POINT = re.compile(r"^Módosítópont sorszáma:\s*(\S+?)\.?\s*$")
_TARGET = re.compile(r"^(?:Törvényjavaslat|Javaslat|Határozati javaslat|Önálló indítvány)"
                     r" érintett rendelkezése:\s*(.*)$")
_KIND = re.compile(r"^Módosítás jellege:\s*(.*)$")
_STOP = re.compile(r"^(Indokolás|INDOKOLÁS)\b")
_STARTS_PARAGRAPH = re.compile(
    r"^(?:„|\(\d+[a-z]?\)\s|[a-z]{1,2}\)\s|\d+(?:/[A-Z])?\. §|\d+[a-z]?\.\s|\[)")


def _paragraphs(lines: list[dict]) -> list[list[list[str]]]:
    """Group PDF lines into paragraphs of marked runs.

    A line starts a new paragraph when it opens with a label or a quote, when
    it is one of the template's field lines, or when the line before it ended
    well short of the right margin. Wrapped lines are joined with a space —
    without one after a real hyphen."""
    paras: list[list[list[str]]] = []
    prev = None
    for ln in lines:
        text = "".join(t for _, t in ln["runs"]).strip()
        new = (prev is None
               or _STARTS_PARAGRAPH.match(text)
               or _POINT.match(text) or _TARGET.match(text) or _KIND.match(text)
               or _STOP.match(text)
               or (prev is not None and prev["x1"] < 0.78 * prev["right"]))
        runs = [[m, t] for m, t in ln["runs"]]
        if new or not paras:
            paras.append(runs)
        else:
            last = paras[-1]
            joiner = "" if re.search(r"[\w§)]-$", "".join(t for _, t in last)) else " "
            if joiner:
                mark = last[-1][0] if last and runs and last[-1][0] == runs[0][0] else ""
                last.append([mark, joiner])
            last.extend(runs)
        prev = ln
    return [_merge(p) for p in paras]


def _merge(runs: list[list[str]]) -> list[list[str]]:
    out: list[list[str]] = []
    for m, t in runs:
        if out and out[-1][0] == m:
            out[-1][1] += t
        else:
            out.append([m, t])
    return out


def _plain(runs: list[list[str]]) -> str:
    return re.sub(r"\s+", " ", "".join(t for _, t in runs)).strip()


def _segments(paras: list[list[list[str]]]) -> list[list[str]]:
    """Marked paragraphs → diff segments ``[op, text]`` (op ``eq``/``ins``/``del``).

    Underlined text is an insertion. A deletion is what sits inside a bracket
    that *bold* text opened (the template's ``[…]``) — across paragraphs, since
    a deleted provision can run to several — and bold elsewhere (a heading
    reprinted in the provision) is plain text. Only the outer brackets are the
    template's marking; brackets nested inside a deletion are the law's own
    (a section title) and stay."""
    segs: list[list[str]] = []

    def add(op, text):
        if not text:
            return
        if segs and segs[-1][0] == op:
            segs[-1][1] += text
        else:
            segs.append([op, text])

    depth = 0
    for pi, runs in enumerate(paras):
        if pi:
            add("del" if depth else "eq", "\n")
        for mark, text in runs:
            if mark == "u":
                add("ins", text)
                continue
            for ch in text:
                if ch == "[" and (mark == "b" or depth):
                    depth += 1
                    if depth > 1:
                        add("del", ch)
                    continue
                if ch == "]" and depth:
                    depth -= 1
                    if depth:
                        add("del", ch)
                    continue
                add("del" if depth else "eq", ch)
    # tidy: whitespace-only edits read as noise; fold them into the text around
    out: list[list[str]] = []
    for op, text in segs:
        if op != "eq" and not text.strip():
            op = "eq"
        if out and out[-1][0] == op:
            out[-1][1] += text
        else:
            out.append([op, text])
    return [[op, re.sub(r"[ \t]+", " ", text)] for op, text in out]


def parse(lines: list[dict]) -> dict:
    """An amendment's marked lines → ``{points, marked}``.

    ``points`` are ``{n, target, kind, segments}``; ``marked`` says whether the
    document carried any insert/delete marking at all (an old or unusual
    template parses to points with plain text only)."""
    paras = _paragraphs(lines)
    points: list[dict] = []
    cur = None
    for runs in paras:
        text = _plain(runs)
        m = _POINT.match(text)
        if m:
            cur = {"n": m.group(1), "target": None, "kind": None, "paras": []}
            points.append(cur)
            continue
        if cur is None:
            continue
        if _STOP.match(text):
            cur = None
            continue
        m = _TARGET.match(text)
        if m and cur["target"] is None and not cur["paras"]:
            cur["target"] = m.group(1).strip()
            continue
        m = _KIND.match(text)
        if m and cur["kind"] is None and not cur["paras"]:
            cur["kind"] = m.group(1).strip()
            continue
        cur["paras"].append(runs)
    out = []
    marked = False
    for p in points:
        segs = _segments(p["paras"])
        marked = marked or any(op != "eq" for op, _ in segs)
        out.append({"n": p["n"], "target": p["target"], "kind": p["kind"],
                    "segments": segs})
    return {"points": out, "marked": marked}
