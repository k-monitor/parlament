"""Parse a **napirend** (NR) PDF into a structured agenda (NR-2).

The NR is the sitting's order paper: the House publishes one per sitting, as a
PDF, in a layout that has been stable across the corpus we hold. It has three
parts, and this module reads all three:

1. a **cover** carrying the sitting's title and, per sitting day, its timetable
   (start, earliest decision times, expected end, breaks);
2. part **A)** *A napirendi pontok tárgyalásának időrendje* — the chronological
   listing, one block per sitting day, each holding numbered agenda items under
   free-text section headings ("Az összevont vita", "Az általános viták …");
3. part **B)** *A napirendi pontok részletes adatai* — per-item procedural
   detail (submission date, committees, amendment deadlines), keyed by the
   ``B./n`` reference that part A prints in every item's gutter.

The input is ``pdftotext -layout`` output, which is what makes this tractable:
the layout preserves the PDF's columns, so an item's gutter (its ordinal, its
``B./n`` back-reference and its iromány number) stays left of its text. We peel
that gutter off token by token rather than by fixed column, because the columns
move between documents and a page break can dedent a wrapped line to column 0.

Nothing here raises on a surprising document: an NR that carries no numbered
items at all — a purely ceremonial sitting, e.g. a government's swearing-in —
parses to its header and its days with empty item lists, which is the truth
about that sitting rather than a failure (SCR-5).
"""

from __future__ import annotations

import re
from datetime import date, timedelta

MONTHS = {
    "JANUÁR": 1, "FEBRUÁR": 2, "MÁRCIUS": 3, "ÁPRILIS": 4, "MÁJUS": 5, "JÚNIUS": 6,
    "JÚLIUS": 7, "AUGUSZTUS": 8, "SZEPTEMBER": 9, "OKTÓBER": 10, "NOVEMBER": 11,
    "DECEMBER": 12,
}
MONTHS_LC = {k.lower(): v for k, v in MONTHS.items()}
WEEKDAYS = ("HÉTFŐ", "KEDD", "SZERDA", "CSÜTÖRTÖK", "PÉNTEK", "SZOMBAT", "VASÁRNAP")

_DAY_RE = re.compile(r"^(%s)\s+(\d{1,2})\.\s+(%s)$" % ("|".join(MONTHS), "|".join(WEEKDAYS)))
_ORD_RE = re.compile(r"^(\d{1,3})\.(?:\s+(.*))?$")
_BREF_RE = re.compile(r"^B\s*[./]{1,2}\s*(\d{1,3})\.?$")
_CODE_RE = re.compile(r"^([A-ZÁÉÍÓÖŐÚÜŰ]/(?:\d+(?:/\d+)?|\.{3}|…))\.?$")
_PAGENO_RE = re.compile(r"^\d{1,3}$")
_STATUS_RE = re.compile(
    r"(\d{4})\.\s*([a-záéíóöőúüű]+)\s+(\d{1,2})\.\s*(\d{1,2}):(\d{2})\s*órai állapot")
_TERM_RE = re.compile(r"(\d{4})\.\s*ÉVI\s+(TAVASZI|ŐSZI)\s+ÜLÉSSZAKA")
# An unnumbered block opened by its own iromány gutter: "   T/669/1.   Az ..."
_CODEBLOCK_RE = re.compile(
    r"^\s{2,}[A-ZÁÉÍÓÖŐÚÜŰ]/(?:\d+(?:/\d+)?|\.{3}|…)\.?\s{2,}\S")
# A continuation of the title text, never an item ordinal: "2026. évi", "46. §".
_NOT_ORDINAL_RE = re.compile(r"^(?:évi|§|melléklet|cikk|oldal|bekezdés)\b", re.I)

_PART_A = "A napirendi pontok tárgyalásának időrendje"
_PART_B = "A napirendi pontok részletes adatai"

# Column-0 headings that group the items below them. They also close whatever
# item block is open, which is what keeps a heading out of an item's text.
_SECTIONS = (
    "Napirendi pontok tárgyalási sorrendje", "Az összevont vita",
    "Az általános vita", "Az általános viták", "A bizottsági jelentés",
    "A bizottsági jelentések", "A zárószavazás előtti",
    "A határozathozatalokat követően", "Döntések, határozathozatalok",
    "Az ülés napirendjének elfogadása", "Döntés kivételes eljárásban",
    "Döntés sürgős tárgyalásról", "Döntés a napirend", "Döntés napirend módosításáról",
    "Döntés határozati házszabályi", "Miniszterelnök napirend előtti felszólalása",
    "A választás menete", "A titkos szavazás", "Szünet",
)
# Headings that close a block without grouping anything after them.
_CLOSERS = ("Napirend előtt", "Napirend után", "Bejelentés az Országgyűlés következő",
            "Megemlékezés")

# Procedural properties the House states in prose; worth a machine-readable flag
# because they are what makes an item notable (a two-thirds law, an urgent debate).
_FLAGS = (
    ("two_thirds", re.compile(r"kétharmad", re.I)),
    ("four_fifths", re.compile(r"négyötöd", re.I)),
    ("cardinal", re.compile(r"sarkalatos", re.I)),
    ("exceptional", re.compile(r"kivételes eljárás", re.I)),
    ("urgent", re.compile(r"sürgős tárgyalás", re.I)),
    ("derogation", re.compile(r"házszabályi rendelkezésekt[őo]l való eltérés", re.I)),
    ("nationality", re.compile(r"nemzetiségi napirendi pont", re.I)),
    ("eu", re.compile(r"uniós napirendi pont", re.I)),
    ("quorum", re.compile(r"határozatképesség szükséges", re.I)),
    ("secret_vote", re.compile(r"titkos szavazás", re.I)),
)

# A line that is *entirely* one parenthetical is the item's submitter. It has
# no useful upper length: a motion can carry 51 named sponsors.
_SUBMITTER_RE = re.compile(r"^\((.{2,})\)$", re.S)
_TIMEWIN_RE = re.compile(r"^(?:Kb\.|kb\.)\s*\d{1,2}:\d{2}")
_BULLET_RE = re.compile(r"^[-‒–—•*]\s")
# The debate stage an item is at. Kept to the openers the House actually uses;
# a stage line is short, so a long paragraph that happens to start with one of
# these words stays prose.
_STAGE_RE = re.compile(
    r"^(?:Általános vita|Összevont vita|Részletes vita|Bizottsági jelentés(?:ek)?|"
    r"A bizottsági jelentés(?:ek)?|Zárószavazás|A zárószavazás|Döntés az összegző|"
    r"Döntés a zárószavazás|A határozathozatal|Tárgyalás és döntés|A vita|"
    r"Vita a lezárásig|Határozathozatal)", re.I)
_STAGE_MAX = 140

_DETAIL_KEYS = (
    ("Benyújtva", "submittedOn"),
    ("Bizottság kijelölése, kijelölt bizottság", "committee"),
    ("Vitához kapcsolódó bizottság", "relatedCommittee"),
    ("Tárgysorozatba vétel időpontja", "agendaAdoptedOn"),
    ("Képviselői módosító javaslatok benyújtási határideje", "amendmentDeadline"),
    ("Képviselői módosító javaslatok (db)", "amendmentCount"),
    ("Módosító javaslat", "amendmentCount"),
    ("A részletes vita lezárása", "detailedDebateClosed"),
    ("Összegző módosító javaslat", "summaryAmendment"),
    ("Összegző jelentés", "summaryReport"),
    ("Egységes javaslat", "unifiedProposal"),
    ("Kivételességi javaslat", "exceptionalMotion"),
    ("Sürgősségi javaslat", "urgencyMotion"),
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _hhmm(s: str) -> str | None:
    m = re.search(r"(\d{1,2}):(\d{2})", s)
    return "%02d:%s" % (int(m.group(1)), m.group(2)) if m else None


# --------------------------------------------------------------------- gutter

def strip_gutter(line: str) -> tuple[str, str | None, str | None]:
    """Peel an item's left gutter off a listing line.

    Returns ``(text, b_ref, bill_code)``. A gutter token is a token at the
    start of the line that is *only* a marker — an ordinal (``5.``), a part-B
    back-reference (``B./5.``) or an iromány number (``T/438.``) — **and** is
    followed by the layout's column gap (two or more spaces) or is the whole
    line. Requiring the gap is what keeps a title that legitimately opens with
    a number or a code out of the gutter.
    """
    bref = code = None
    rest = line
    while True:
        m = re.match(r"^(\s*)(\S+)(\s{2,}|\s*$)", rest)
        if not m:
            break
        tok = m.group(2)
        if _BREF_RE.match(tok):
            bref = _BREF_RE.match(tok).group(1)
        elif _CODE_RE.match(tok) and code is None:
            code = _CODE_RE.match(tok).group(1)
        elif re.fullmatch(r"\d{1,3}\.", tok):
            pass
        else:
            break
        rest = rest[m.end():]
    return rest.strip(), bref, code


def _is_section(s: str) -> bool:
    return any(s.startswith(p) for p in _SECTIONS)


def _is_closer(s: str) -> bool:
    return any(s.startswith(p) for p in _CLOSERS)


# --------------------------------------------------------------------- header

def _parse_header(lines: list[str]) -> dict:
    head = [_norm(l) for l in lines[:40] if l.strip()]
    blob = " ".join(head[:16])
    out: dict = {"extraordinary": "RENDKÍVÜLI" in blob}
    m = _TERM_RE.search(blob)
    out["term"] = ({"year": int(m.group(1)),
                    "season": "spring" if m.group(2) == "TAVASZI" else "autumn"}
                   if m else None)
    m = _STATUS_RE.search(blob)
    out["statusAt"] = None
    if m and m.group(2).lower() in MONTHS_LC:
        out["statusAt"] = "%04d-%02d-%02dT%02d:%s" % (
            int(m.group(1)), MONTHS_LC[m.group(2).lower()], int(m.group(3)),
            int(m.group(4)), m.group(5))
    out["statusLabel"] = next((h for h in head[:18] if h.startswith("Elfogadott")), None)
    caps: list[str] = []
    for h in head[:10]:
        if h.upper() == h and any(c.isalpha() for c in h) and "ÁLLAPOT" not in h.upper():
            caps.append(h)
        elif caps:
            break
    out["title"] = _norm(" ".join(caps)) or None
    return out


def _parse_cover(lines: list[str]) -> dict:
    """The cover's per-day timetable, keyed by ``(month, day, weekday)``."""
    out: dict = {}
    cur = None
    last = None
    for raw in lines:
        l = _norm(raw)
        if not l:
            continue
        m = _DAY_RE.match(l)
        if m:
            cur = (MONTHS[m.group(1)], int(m.group(2)), m.group(3))
            out.setdefault(cur, {"decisionsFrom": []})
            last = None
            continue
        if cur is None:
            continue
        low = l.lower()
        if low.startswith(("ülésnap kezdete:", "üléskezdés:")):
            out[cur]["startsAt"] = _hhmm(l)
            last = None
        elif low.startswith("határozathozatalok:"):
            t = _hhmm(l)
            if t:
                out[cur]["decisionsFrom"].append(t)
            last = "decisions"
        elif low.startswith(("ülésnap befejezése:", "ülés befejezése:")):
            out[cur]["endsNote"] = l.split(":", 1)[1].strip()
            last = "endsNote"
        elif low.startswith("szünet:"):
            out[cur]["breakNote"] = l.split(":", 1)[1].strip()
            last = "breakNote"
        elif last == "decisions" and _hhmm(l) and low.startswith(("legkorábban", "és")):
            t = _hhmm(l)
            if t and t not in out[cur]["decisionsFrom"]:
                out[cur]["decisionsFrom"].append(t)
        elif last in ("endsNote", "breakNote") and not l.endswith(":") and ":" not in l:
            # A wrapped continuation of the value above it.
            out[cur][last] = _norm(out[cur][last] + " " + l)
    return out


# --------------------------------------------------------------------- part A

def _parse_part_a(lines: list[str], cover: dict) -> list[dict]:
    a_at = next((i for i, l in enumerate(lines) if _norm(l) == _PART_A), None)
    body = lines[a_at + 1:] if a_at is not None else lines

    days: list[dict] = []
    cur: dict | None = None
    section: str | None = None
    block: dict | None = None

    def flush() -> None:
        nonlocal block
        if block and cur is not None:
            item = _build_item(block)
            if item:
                cur["items"].append(item)
        block = None

    for raw in body:
        line = raw.rstrip()
        s = line.strip()
        if not s or _PAGENO_RE.match(s):
            continue
        m = _DAY_RE.match(s)
        if m:
            flush()
            key = (MONTHS[m.group(1)], int(m.group(2)), m.group(3))
            cur = {"month": key[0], "dayOfMonth": key[1], "weekday": m.group(3),
                   "items": [], **{k: v for k, v in cover.get(key, {}).items()}}
            days.append(cur)
            section = None
            continue
        indent = len(line) - len(line.lstrip())
        mo = _ORD_RE.match(s)
        if indent <= 2 and mo and not _NOT_ORDINAL_RE.match((mo.group(2) or "").strip()):
            flush()
            text, bref, code = strip_gutter(line)
            block = {"ordinal": int(mo.group(1)), "section": section, "bref": bref,
                     "code": code, "lines": [text] if text else []}
            continue
        if block is None and section and section.startswith("Döntés") \
                and _CODEBLOCK_RE.match(line):
            text, bref, code = strip_gutter(line)
            block = {"ordinal": None, "section": section, "bref": bref,
                     "code": code, "lines": [text] if text else []}
            continue
        if indent <= 2 and _is_section(s):
            flush()
            section = s
            continue
        if indent <= 2 and _is_closer(s):
            flush()
            continue
        if block is not None:
            text, bref, code = strip_gutter(line)
            if bref and not block["bref"]:
                block["bref"] = bref
            if code and not block["code"]:
                block["code"] = code
            if text:
                block["lines"].append(text)
    flush()
    return _merge_days(days)


def _merge_days(days: list[dict]) -> list[dict]:
    """Collapse repeats of the same calendar day into one.

    A document with no ``A)`` marker — the ceremonial sitting that carries no
    numbered items at all — has its cover and its listing scanned as one run,
    so the day's heading is seen twice. Merging keeps the timetable from the
    cover and the items from the listing, in the order first seen.
    """
    out: list[dict] = []
    seen: dict[tuple[int, int], dict] = {}
    for day in days:
        key = (day["month"], day["dayOfMonth"])
        first = seen.get(key)
        if first is None:
            seen[key] = day
            out.append(day)
            continue
        first["items"].extend(day["items"])
        for field, value in day.items():
            if field in ("items", "month", "dayOfMonth") or not value:
                continue
            if not first.get(field):
                first[field] = value
    return out


def _join_parentheticals(lines: list[str]) -> list[str]:
    """Fold a parenthetical that wraps over several lines into one line.

    A submitter list — "(Bujdosó Andrea Anna (TISZA), Melléthei-Barna Márton
    (TISZA), …)" for a 51-sponsor motion — runs over a dozen lines. Folded, it
    is one value; left alone, it would be appended to the item's title.
    """
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    for l in lines:
        if not buf and l.startswith("(") and l.count("(") > l.count(")"):
            buf, depth = [l], l.count("(") - l.count(")")
            continue
        if buf:
            buf.append(l)
            depth += l.count("(") - l.count(")")
            if depth <= 0:
                out.append(_norm(" ".join(buf)))
                buf, depth = [], 0
            continue
        out.append(l)
    if buf:
        out.append(_norm(" ".join(buf)))
    return out


def _build_item(block: dict) -> dict | None:
    lines = _join_parentheticals([l for l in block["lines"] if l])
    if not lines:
        return None
    title: list[str] = []
    extra: list[str] = []
    notes: list[str] = []
    submitter = stage = window = None
    mode = "title"
    # The procedural decisions the House takes before adopting the agenda carry
    # no debate stage — what follows their title is the motion's own proposed
    # timetable, in prose. Reading a stage out of that would invent one.
    wants_stage = block["ordinal"] is not None
    for l in lines:
        if l.startswith("Megjegyzés"):
            mode = "notes"
            continue
        if mode == "notes":
            notes.append(l)
            continue
        m = _SUBMITTER_RE.match(l)
        if m and mode in ("title", "meta"):
            submitter = _norm((submitter + " " if submitter else "") + m.group(1))
            mode = "meta"
            continue
        if _TIMEWIN_RE.match(l):
            window = l
            mode = "meta"
            continue
        if (wants_stage and mode in ("title", "meta") and stage is None
                and len(l) <= _STAGE_MAX and not _BULLET_RE.match(l)
                and _STAGE_RE.match(l)):
            stage = l
            mode = "stage"
            continue
        if mode == "stage" and l[:1].islower() and len(stage) + len(l) <= 2 * _STAGE_MAX:
            stage = _norm(stage + " " + l)
            continue
        (title if mode == "title" else extra).append(l)

    code = block["code"]
    if code and re.search(r"(?:\.{3}|…)$", code):
        code = None  # "S/..." is the House's own placeholder for "number to come".
    text = _norm(" ".join(title))
    blob = " ".join([text, submitter or "", stage or ""] + notes + extra)
    flags = sorted({name for name, rx in _FLAGS if rx.search(blob)})
    return {
        "ordinal": block["ordinal"],
        "ref": block["bref"],
        "billCode": code.rstrip(".") if code else None,
        "section": block["section"],
        "title": text or None,
        "submitter": submitter,
        "stage": _norm(stage) if stage else None,
        "timeWindow": window,
        "notes": [_norm(n) for n in notes] or None,
        "flags": flags or None,
    }


# --------------------------------------------------------------------- part B

def _parse_part_b(lines: list[str]) -> dict:
    blocks: dict[int, list[str]] = {}
    cur: list[str] | None = None
    for raw in lines:
        s = raw.strip()
        if not s or _PAGENO_RE.match(s):
            continue
        indent = len(raw.rstrip()) - len(raw.strip())
        mo = _ORD_RE.match(s)
        if indent <= 4 and mo and not _NOT_ORDINAL_RE.match((mo.group(2) or "").strip()):
            text, _, _ = strip_gutter(raw.rstrip())
            cur = [text] if text else []
            blocks[int(mo.group(1))] = cur
            continue
        if cur is not None:
            cur.append(s)
    out: dict = {}
    for n, body in blocks.items():
        out[str(n)] = _detail_fields(body)
    return out


# A line that opens a new labelled field, so it can never be the continuation of
# the one above. The detail sheet's own labels all read as a capitalised phrase
# ending in a colon.
_LABEL_LINE_RE = re.compile(r"^[A-ZÁÉÍÓÖŐÚÜŰ][^:]{2,80}:")
_CONTINUATION_MAX = 3


def _detail_fields(body: list[str]) -> dict:
    """Read the detail sheet's labelled fields.

    The sheet wraps: a label whose value does not fit beside it puts the value
    on the line **below** instead ("Bizottság kijelölése, kijelölt bizottság:" /
    "2026.07.29. Oktatási Bizottság"). Taking only what follows the colon would
    drop exactly the fields that carry the most — the committees and the
    deadlines — so an empty value reads on into the lines under it, up to the
    next label.
    """
    fields: dict = {}
    for i, line in enumerate(body):
        for label, key in _DETAIL_KEYS:
            if not line.startswith(label + ":") or key in fields:
                continue
            value = line.split(":", 1)[1].strip()
            if not value:
                tail = []
                for nxt in body[i + 1:i + 1 + _CONTINUATION_MAX]:
                    if not nxt or _LABEL_LINE_RE.match(nxt):
                        break
                    tail.append(nxt)
                value = " ".join(tail).strip()
            if value and value not in ("-", "–", "—"):
                fields[key] = _norm(value)
            break
    return fields


# --------------------------------------------------------------------- dates

def _resolve_dates(days: list[dict], reference: date | None) -> None:
    """Give every day a full ISO date.

    The listing prints a month and a day, never a year — so the year comes from
    the document's own reference date (its slug, or the "állapot szerint"
    stamp). Picking the candidate year nearest that reference keeps a sitting
    that straddles New Year on the right side of it.
    """
    for d in days:
        d["date"] = None
        if reference is None:
            continue
        best = None
        for year in (reference.year - 1, reference.year, reference.year + 1):
            try:
                cand = date(year, d["month"], d["dayOfMonth"])
            except ValueError:
                continue
            delta = abs((cand - reference).days)
            if best is None or delta < best[0]:
                best = (delta, cand)
        if best:
            d["date"] = best[1].isoformat()


def parse(text: str, *, reference: date | None = None) -> dict:
    """Parse ``pdftotext -layout`` output of an NR PDF into a structured agenda.

    ``reference`` anchors the listing's year-less dates; pass the date the
    document is published for (the NR slug carries it). Without one the days
    still parse, they just carry no ISO ``date``.
    """
    lines = text.split("\n")
    doc = _parse_header(lines)
    b_at = next((i for i, l in enumerate(lines) if _norm(l) == _PART_B), None)
    a_at = next((i for i, l in enumerate(lines) if _norm(l) == _PART_A), None)
    cover = _parse_cover(lines[:a_at] if a_at is not None else lines[:45])
    doc["days"] = _parse_part_a(lines[:b_at] if b_at is not None else lines, cover)
    details = _parse_part_b(lines[b_at + 1:]) if b_at is not None else {}

    if reference is None and doc.get("statusAt"):
        try:
            reference = date.fromisoformat(doc["statusAt"][:10])
        except ValueError:
            reference = None
    _resolve_dates(doc["days"], reference)

    for day in doc["days"]:
        for item in day["items"]:
            detail = details.get(item.get("ref") or "")
            if detail:
                item["detail"] = detail
    dates = [d["date"] for d in doc["days"] if d.get("date")]
    doc["firstDate"] = min(dates) if dates else None
    doc["lastDate"] = max(dates) if dates else None
    doc["itemCount"] = sum(len(d["items"]) for d in doc["days"])
    return doc
