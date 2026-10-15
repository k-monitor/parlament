"""Read an amending bill (novella) and apply it to a law's text (BILL-14).

Hungarian amending provisions are written to a closed set of formulas fixed by
the drafting rules (61/2009. IRM rendelet), which is what makes them parseable:

* **replace** — ``Az Szkt. 26. § (4) bekezdése helyébe a következő rendelkezés
  lép: „(4) …”``
* **insert** — ``Az Szkt. 26. §-a a következő (4a) bekezdéssel egészül ki: „…”``
* **text replace** — ``Az Szkt. 14/D. § (5) bekezdésében az „X” szövegrész
  helyébe az „Y” szöveg lép`` (one occurrence; ``szövegrészek`` = all of them)
* **repeal** — ``Hatályát veszti az Szkt. 14/D. § (2) és (3) bekezdése``, or
  only a fragment: ``… 26. § (2) bekezdésében a „vagy a kancellár” szövegrész``

The law is named once with its abbreviation (``a szakképzésről szóló 2019. évi
LXXX. törvény (a továbbiakban: Szkt.)``) and by the abbreviation after that.
Several instructions share one sentence as an enumeration (``Az Szkt. a) …,
b) … lép.``). An italic ``(Az oktatói testület dönt)`` line before a quote only
says which sentence the quoted point continues; it is not part of the new text.

The target of every instruction is an njt unit id (:mod:`.njt`), so applying a
bill is editing a list of units. Validated against njt's own post-amendment
states over the 41 born-digital bills of cycle 43 that became law: 98% of the
instructions parse, and 90% of the provisions they change come out exactly as
njt has them when the egységes javaslat is the input.
"""

from __future__ import annotations

import re

from . import njt

# Bumped whenever parsing/applying changes, so cached per-bill results are redone.
PARSER_VERSION = 10

_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def roman(s: str) -> int:
    total = prev = 0
    for ch in reversed(s.upper()):
        v = _ROMAN[ch]
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ").replace("­", "")).strip()


# --- the bill's body ------------------------------------------------------------

_TITLE_LINE = re.compile(r"^\d{4}\. évi [.…]+ ?törvény")
_END_LINE = re.compile(r"^(Általános indokolás|Indokolás|INDOKOLÁS|Részletes indokolás|"
                       r"ÁLTALÁNOS INDOKOLÁS|RÉSZLETES INDOKOLÁS)$")
_OWN_SECTION_LINE = re.compile(r"^\d+(?:/[A-Z])?\. §$")


def body(lines: list[dict]) -> list[dict]:
    """The enacting text: from the ``2026. évi ….. törvény`` title line to the
    justification. A bill and its egységes javaslat both carry a cover letter
    before it; a document without such a line is not a bill text (``[]``)."""
    start = next((i for i, r in enumerate(lines) if _TITLE_LINE.match(r["text"])), None)
    if start is None:
        return []
    end = next((i for i, r in enumerate(lines) if i > start and _END_LINE.match(r["text"])),
               len(lines))
    return lines[start:end]


_HEADING_START = re.compile(r"^\d+\.\s+[A-ZÁÉÍÓÖŐÚÜŰ]")


def _heading_lines(lines: list[dict]) -> set[int]:
    """The bill's own subtitles ("2. A nemzetközi bűnügyi jogsegélyről szóló …
    törvény módosítása"): a numbered line, wrapped over at most one more, right
    before one of the bill's own ``N. §`` lines. A long one is centred so wide
    that it starts near the margin, so position alone does not find it."""
    out: set[int] = set()
    for i, r in enumerate(lines):
        if not _OWN_SECTION_LINE.match(r["text"]):
            continue
        for start in (i - 1, i - 2):
            if start < 0 or not _HEADING_START.match(lines[start]["text"]):
                continue
            span = range(start, i)
            if all(not lines[k]["text"].rstrip().endswith((":", ",")) for k in span):
                out.update(span)
                break
    return out


def tokenize(lines: list[dict]) -> list[tuple[str, str]]:
    """The body as alternating ``("out", text)`` / ``("q", text)`` segments.

    Quotes are „…”; nested ones are »…« in Hungarian, so a „ inside a quote is
    still counted, in case a source nests them anyway. Centred lines outside a
    quote are the bill's own headings (``3. Záró rendelkezések``) and are
    dropped; the bill's own ``N. §`` lines are kept. A line ending in a real
    hyphen joins the next one without a space."""
    headings = _heading_lines(lines)
    segs: list[tuple[str, str]] = []
    cur: list[str] = []
    kind, depth = "out", 0
    for i, r in enumerate(lines):
        t = r["text"]
        if depth == 0 and (i in headings or (r.get("x", 0) > 110
                                             and not _OWN_SECTION_LINE.match(t) and "„" not in t)):
            continue
        joiner = "" if re.search(r"[\w§)]-$", t) else "\n"
        for ch in t + joiner:
            if ch == "„":
                if depth == 0:
                    segs.append((kind, "".join(cur))); cur = []; kind = "q"
                else:
                    cur.append(ch)
                depth += 1
                continue
            if ch == "”" and depth > 0:
                depth -= 1
                if depth == 0:
                    segs.append((kind, "".join(cur))); cur = []; kind = "out"
                else:
                    cur.append(ch)
                continue
            cur.append(ch)
    segs.append((kind, "".join(cur)))
    return [s for s in segs if s[1] or s[0] == "q"]


def template(segs: list[tuple[str, str]]) -> tuple[str, list[str]]:
    """OUT text with ``⟦n⟧`` placeholders for the quotes, plus the quotes."""
    out, quotes = [], []
    for kind, text in segs:
        if kind == "q":
            out.append(f"⟦{len(quotes)}⟧")
            quotes.append(text)
        else:
            out.append(text)
    return "".join(out), quotes


def restore(text: str, quotes: list[str], limit: int = 0) -> str:
    """Put the quotes back into a template fragment (for showing a reader the
    bill's own wording)."""
    def sub(m):
        q = norm(quotes[int(m.group(1))])
        if limit and len(q) > limit:
            q = q[:limit].rstrip() + " …"
        return f"„{q}”"
    return re.sub(r"⟦(\d+)⟧", sub, text)


_SEC_RE = re.compile(r"(?m)^(\d+(?:/[A-Z])?)\. §\s*$")
_PAR_RE = re.compile(r"(?m)^\((\d+[a-z]?)\) (?!bekezdés)")


def own_sections(tmpl: str) -> list[tuple[str, str]]:
    """The bill's own sections as ``("1. §", text)``; text before the first one
    (the title and preamble) as ``("", text)``."""
    marks = list(_SEC_RE.finditer(tmpl))
    out = [("", tmpl[:marks[0].start()] if marks else tmpl)]
    for i, m in enumerate(marks):
        out.append((f"{m.group(1)}. §",
                    tmpl[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(tmpl)]))
    return out


def own_provisions(tmpl: str) -> list[tuple[str, str]]:
    """The bill's own paragraphs: ``("2. § (1)", text)`` — or the whole section
    where it has no numbered paragraphs."""
    out = []
    for sec, text in own_sections(tmpl)[1:]:
        pars = list(_PAR_RE.finditer(text))
        if not pars:
            out.append((sec, text.strip()))
            continue
        for j, p in enumerate(pars):
            out.append((f"{sec} ({p.group(1)})",
                        text[p.end(): pars[j + 1].start() if j + 1 < len(pars) else len(text)].strip()))
    return out


# --- references ---------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"(?P<fe>[IVXLCDM]+)\. Fejezet|"
    r"(?P<ac>\d+(?:/[A-Z])?)\. alcím|"
    r"(?P<mel>\d+)\. (?:számú )?melléklet|"
    r"(?P<sz>\d+(?:/[A-Z])?\.?(?:\s*(?:,|és|–|-)\s*\d+(?:/[A-Z])?\.?)*) ?§|"
    r"(?P<be>\(\d+[a-z]?\)(?:\s*(?:,|és|–|-)\s*\(\d+[a-z]?\))*)\s*bekezdés|"
    r"(?P<ap>[a-z]{2}\)(?:\s*(?:,|és|–|-)\s*[a-z]{2}\))*)\s*alpont|"
    r"(?P<po>(?:[a-z]\)|\d+[a-z]?\.)(?:\s*(?:,|és|–|-)\s*(?:[a-z]\)|\d+[a-z]?\.))*)\s*pont|"
    r"(?P<ny>nyitó szövegrész)|(?P<za>záró szövegrész)")


def _expand(spec: str, kind: str) -> list[str]:
    """``(2) és (3)`` → ``['2', '3']``; ``b)–d)`` → ``['b', 'c', 'd']``."""
    if kind == "be":
        items = re.findall(r"\((\d+[a-z]?)\)", spec)
    elif kind == "po":
        items = re.findall(r"([a-z]|\d+[a-z]?)[\).]", spec)
    else:
        items = re.findall(r"([a-z]{2})\)", spec)
    if len(items) == 2 and re.search(r"–|-", spec) and "és" not in spec:
        a, b = items
        if len(a) == len(b) == 1 and a.isalpha() and b.isalpha():
            items = [chr(c) for c in range(ord(a), ord(b) + 1)]
        elif a.isdigit() and b.isdigit():
            items = [str(i) for i in range(int(a), int(b) + 1)]
        elif len(a) == len(b) == 2 and a[0] == b[0]:
            items = [a[0] + chr(c) for c in range(ord(a[1]), ord(b[1]) + 1)]
    return items


def _sections(spec: str) -> list[str]:
    """``18/C–18/E.`` → ``['18C', '18D', '18E']``; ``3. és 4.`` → ``['3', '4']``.
    A range across numbers with letters at an end (``17/A–19.``) cannot be
    enumerated without the law, so it keeps its ends and the plain numbers."""
    items = re.findall(r"(\d+)(?:/([A-Z]))?", spec)
    if len(items) == 2 and re.search(r"–|-", spec) and not re.search(r",|és", spec):
        (a, la), (b, lb) = items
        if a == b and lb:  # 18–18/B. § = 18, 18/A, 18/B
            first = ord(la) if la else ord("A")
            return [a] * (not la) + [a + chr(c) for c in range(first, ord(lb) + 1)]
        if int(a) < int(b):
            mid = [str(n) for n in range(int(a) + 1, int(b))]
            return [a + la] + mid + [b + lb]
    return [n + let for n, let in items]


def parse_ref(text: str) -> tuple[list[str], str | None]:
    """A reference phrase → (njt unit ids, part) where part is ``"ny"`` (the
    opening text), ``"za"`` (the closing text) or ``None`` (the whole unit)."""
    paths: list[list[str]] = [[]]
    part = None
    for m in _TOKEN_RE.finditer(text):
        k = m.lastgroup
        if k == "fe":
            paths = [p + [f"FE{m.group('fe')}"] for p in paths]
        elif k == "ac":
            paths = [p + [f"AC{m.group('ac').replace('/', '')}"] for p in paths]
        elif k == "mel":
            paths = [p + [f"MEL{m.group('mel')}"] for p in paths]
        elif k == "sz":
            paths = [[f"SZ{n}"] for n in _sections(m.group("sz"))]
        elif k == "be":
            paths = [p + [f"BE{x.upper()}"] for p in paths for x in _expand(m.group("be"), "be")]
        elif k == "po":
            paths = [p + [f"PO{x.upper()}"] for p in paths for x in _expand(m.group("po"), "po")]
        elif k == "ap":
            paths = [p + [f"AP{x.upper()}"] for p in paths for x in _expand(m.group("ap"), "ap")]
        elif k == "ny":
            part = "ny"
        elif k == "za":
            part = "za"
    return ["@".join(p) for p in paths if p], part


def ref_label(jh: str) -> str | None:
    """The label a unit is introduced with in a quoted block: ``BE14`` → ``(14)``."""
    last = jh.split("@")[-1]
    m = re.match(r"^(SZ|BE|PO|AP)(\d*)([A-Z]*)$", last)
    if not m:
        return None
    kind, num, let = m.groups()
    if kind == "SZ":
        return f"{num}{'/' + let if let else ''}. §"
    if kind == "BE":
        return f"({num}{let.lower()})"
    if kind == "PO":
        return f"{num}{let.lower()}." if num else f"{let.lower()})"
    return f"{let.lower()})"


def split_units(text: str, jhs: list[str]) -> list[str]:
    """Split a quoted block into one chunk per target, at each target's label —
    a label at the start of a line, not one quoted in running text (``a (4a),
    (4b) és (4d) bekezdés``)."""
    if len(jhs) < 2:
        return [norm(text)]
    cuts, pos = [0], 0
    for jh in jhs[1:]:
        lab = ref_label(jh)
        m = re.compile(r"(?m)^\s*" + re.escape(lab) + r"\s").search(text, pos + 1) if lab else None
        if not m:
            return [norm(text)] + [""] * (len(jhs) - 1)
        cuts.append(m.start())
        pos = m.start()
    cuts.append(len(text))
    return [norm(text[cuts[k]:cuts[k + 1]]) for k in range(len(jhs))]


# --- instructions --------------------------------------------------------------

_ABBR_RE = re.compile(r"(\d{4})\. évi ([IVXLCDM]+)\. törvény\w*\s*\(a továbbiakban: ([^)]+?)\)")
_CIT_RE = re.compile(r"(\d{4})\. évi ([IVXLCDM]+)\. törvény")
_AMEND_HINT = re.compile(r"helyébe|egészül ki|[Hh]atályát veszti|szövegrész|nem lép hatályba")
_ENUM_SPLIT = re.compile(r"(?:(?<=^)|(?<=[\s,]))([a-z]{1,2})\) (?=\d|[IVXLCDM]+\. |⟦)")


def abbreviations(tmpl: str) -> dict[str, str]:
    """``Szkt.`` → ``2019-80-00-00`` for every law the bill names with one."""
    out = {}
    for m in _ABBR_RE.finditer(norm(tmpl)):
        out[m.group(3).strip()] = njt.doc_id(m.group(1), roman(m.group(2)))
    return out


def _law_of(text: str, abbr: dict[str, str]) -> tuple[str | None, int | None]:
    """The amended law named first in an instruction, and where its mention ends."""
    best = None
    for ab, doc in abbr.items():
        m = re.search(r"(?<![\w.])" + re.escape(ab) + r"(?!\w)", text)
        if m and (best is None or m.start() < best[1]):
            best = (doc, m.start(), m.end())
    m = _CIT_RE.search(text)
    if m and (best is None or m.start() < best[1]):
        end = m.end()
        tail = re.match(r"\w*\s*\(a továbbiakban: [^)]+\)", text[end:])
        if tail:
            end += tail.end()
        best = (njt.doc_id(m.group(1), roman(m.group(2))), m.start(), end)
    return (best[0], best[2]) if best else (None, None)


def _items(text: str) -> list[tuple[str | None, str]]:
    """An enumerated tail ``a) X, b) Y`` → ``[('a', 'X'), ('b', 'Y')]``."""
    parts = _ENUM_SPLIT.split(text)
    if len(parts) < 3:
        return [(None, text)]
    return [(parts[i], parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


def parse_instruction(tmpl: str, quotes: list[str], abbr: dict[str, str],
                      at: str) -> list[dict]:
    """One paragraph of the bill → its instructions (``op`` + targets + text).

    ``op`` is one of ``replace``, ``insert``, ``replace_text``, ``delete_text``,
    ``insert_text``, ``append_sentence``, ``repeal``, ``repeal_law``; a
    paragraph that amends something we cannot read is ``unparsed`` (with a
    ``why``), and one that amends nothing is ``none``."""
    t = norm(tmpl)
    base = {"at": at, "src": t}
    # "E törvény …" / "Ez a törvény …" is the bill speaking of itself (entry
    # into force, cardinality, EU conformity) — never an amendment of a law.
    if not _AMEND_HINT.search(t) or re.match(r"^(?:E|Ez a) törvény\b", t):
        return [{**base, "op": "none"}]
    doc, lend = _law_of(t, abbr)
    if not doc:
        why = "rendelet" if re.search(r"rendelet\b|határozat\b", t) else "nincs törvény"
        return [{**base, "op": "unparsed", "why": why}]
    rest = t[lend:].strip()
    q = lambda n: quotes[int(n)]  # noqa: E731 — raw, newlines kept for splitting
    out: list[dict] = []

    law_mention = t[:lend].strip()

    def at_item(label):
        return f"{at} {label})" if label else at

    def quote_of(label, item):
        """What to quote of an enumerated instruction: its own item, with the
        law named — not the whole enumeration again for every item."""
        return f"{law_mention} {label}) {item.strip()}" if label else t

    # repeal (whole units, or a fragment of one)
    if re.match(r"^Hatályát veszti", t) or re.search(r"hatályát veszti\.?$", rest):
        body_ = re.sub(r"\s*hatályát veszti\.?$", "", rest).rstrip(".")
        if not body_.strip():
            return [{**base, "op": "repeal_law", "doc": doc}]
        for label, it in _items(body_):
            for sub in re.split(r",\s+(?=\d)|\s+és\s+(?=\d+(?:/[A-Z])?\. ?§)", it):
                mq = re.search(r"(?:az?|a) ⟦(\d+)⟧ szövegrész", sub)
                refs, part = parse_ref(sub)
                if not refs:
                    continue
                if mq:
                    out.append({**base, "at": at_item(label), "src": quote_of(label, sub),
                                "op": "delete_text", "doc": doc,
                                "refs": refs, "part": part, "old": norm(q(mq.group(1)))})
                else:
                    out.append({**base, "at": at_item(label), "src": quote_of(label, sub),
                                "op": "repeal", "doc": doc, "refs": refs, "part": part})
        return out or [{**base, "op": "unparsed", "why": "hatályon kívül helyezés", "doc": doc}]

    if re.search(r"melléklete? helyébe az? \d+\. melléklet lép", rest) \
            or re.search(r"melléklettel egészül ki", rest):
        return [{**base, "op": "unparsed", "why": "melléklet", "doc": doc}]

    # replace whole provision(s)
    m = re.match(r"^(?P<ref>.+?) helyébe a következő (?:rendelkezés|rendelkezések|szöveg)"
                 r"\s+(?:lép|lépnek)\s*:?\s*(?P<tail>.*)$", rest)
    if m:
        refs, part = parse_ref(m.group("ref"))
        qs = re.findall(r"⟦(\d+)⟧", m.group("tail"))
        if refs and qs:
            return [{**base, "op": "replace", "doc": doc, "refs": refs, "part": part,
                     "new": "\n".join(q(n) for n in qs)}]

    # insert new unit(s)
    m = re.match(r"^(?P<ref>.*?)\s*a következő (?P<what>.+?)\s+egészül ki\s*:?\s*(?P<tail>.*)$", rest)
    if m and "szövegrész" not in m.group("what"):
        qs = re.findall(r"⟦(\d+)⟧", m.group("tail"))
        parent, part = parse_ref(m.group("ref"))
        what = m.group("what")
        if qs:
            new = "\n".join(q(n) for n in qs)
            if re.search(r"mondattal|mondatokkal", what):
                return [{**base, "op": "append_sentence", "doc": doc, "refs": parent,
                         "part": part, "new": new}]
            newrefs, _ = parse_ref(what)
            if not newrefs and re.search(r"alcímm|fejezett|címmel|résszel", what, re.I):
                # A new subtitle or chapter: what enters the law is the
                # sections it contains, each under its own number.
                newrefs = [f"SZ{n.replace('/', '')}" for n in
                           re.findall(r"(?m)^\s*(\d+(?:/[A-Z])?)\. §", new)]
                parent = []
            if newrefs:
                return [{**base, "op": "insert", "doc": doc,
                         "parent": parent[0] if parent else "", "newrefs": newrefs,
                         "new": new}]

    # a fragment inserted after an existing one
    for mm in re.finditer(r"(?P<ref>[^⟦]*?)(?:az?|a) ⟦(?P<a>\d+)⟧ szövegrész(?:t|e)? követően "
                          r"(?:az?|a) ⟦(?P<b>\d+)⟧ szöveg(?:gel|résszel)", rest):
        refs, part = parse_ref(mm.group("ref"))
        if refs:
            out.append({**base, "op": "insert_text", "doc": doc, "refs": refs, "part": part,
                        "after": norm(q(mm.group("a"))), "new": norm(q(mm.group("b")))})

    # text replacements, possibly enumerated and chained
    last = None
    for label, it in _items(rest):
        prev_end = 0
        for mm in re.finditer(r"(?:az?|a) ⟦(\d+)⟧ szövegrész(?:ek)? helyébe (?:az?|a) ⟦(\d+)⟧ "
                              r"szöveg(?:ek)?", it):
            refs, part = parse_ref(it[prev_end:mm.start()])
            if refs:
                last = (refs, part)
            elif last is None:
                continue
            out.append({**base, "at": at_item(label), "src": quote_of(label, it),
                        "op": "replace_text", "doc": doc,
                        "refs": last[0], "part": last[1],
                        "old": norm(q(mm.group(1))), "new": norm(q(mm.group(2))),
                        "all": "szövegrészek" in mm.group(0)})
            prev_end = mm.end()
    if out:
        return out
    if "helyett" in rest and "szöveggel" in rest:
        why = "módosított szöveggel hatályba lépés"
    else:
        why = "ismeretlen szerkezet"
    return [{**base, "op": "unparsed", "why": why, "doc": doc}]


def parse_bill(lines: list[dict]) -> dict:
    """A bill's PDF lines → ``{abbr, instructions, quotes, provisions}``."""
    segs = tokenize(body(lines))
    tmpl, quotes = template(segs)
    abbr = abbreviations(tmpl)
    instrs: list[dict] = []
    for at, text in own_provisions(tmpl):
        instrs.extend(parse_instruction(text, quotes, abbr, at))
    return {"abbr": abbr, "instructions": instrs, "quotes": quotes,
            "template": tmpl}


# --- applying ------------------------------------------------------------------

def _subtree_idx(units: list[dict], jh: str) -> list[int]:
    return [i for i, u in enumerate(units)
            if u["jh"] == jh or (u["jh"] or "").startswith(jh + "@")]


def resolve(units: list[dict], ref: str) -> str | None:
    ids = {u["jh"] for u in units}
    if ref in ids:
        return ref
    # a § without numbered paragraphs keeps its points under BE0
    alt = re.sub(r"^(SZ[0-9A-Z]+)@(PO)", r"\1@BE0@\2", ref)
    return alt if alt in ids else None


def _targets(units: list[dict], jh: str, part: str | None) -> list[int]:
    idx = _subtree_idx(units, jh)
    if part == "ny":
        return [i for i in idx if units[i]["jh"] == jh]
    if part == "za":
        return [i for i in idx if units[i]["jh"] == jh + "@ZARO"]
    return idx


def _label_key(jh: str) -> tuple:
    """Sibling order: 4 < 4a < 5; ``SZ14D`` → (14, 'D')."""
    last = jh.split("@")[-1]
    m = re.match(r"([A-Z]+?)(\d*)([A-Z]*)$", last)
    return (int(m.group(2) or 0), m.group(3)) if m else (0, last)


def _new_unit(jh: str, text: str) -> dict:
    return {"jh": jh, "cls": "new", "label": "", "text": text, "fn": []}


def _insert_one(units: list[dict], jh: str, text: str) -> None:
    """Insert after the last sibling that sorts before it; a slot njt still
    lists empty (a repealed number being refilled) is reused."""
    if not units:
        units.append(_new_unit(jh, text))
        return
    idx = _subtree_idx(units, jh)
    if idx and not any(units[i]["text"] for i in idx):
        units[idx[0]] = _new_unit(jh, text)
        for i in reversed(idx[1:]):
            del units[i]
        return
    prefix = jh.rsplit("@", 1)[0] + "@" if "@" in jh else ""
    depth, kind = jh.count("@"), jh.split("@")[-1][:2]
    pos = None
    for i, u in enumerate(units):
        uj = u["jh"] or ""
        if (uj.count("@") == depth and uj.startswith(prefix)
                and uj.split("@")[-1][:2] == kind and _label_key(uj) <= _label_key(jh)):
            pos = i
    if pos is None:  # a first child: right after its parent's own unit
        pos = next((i for i, u in enumerate(units) if u["jh"] == prefix.rstrip("@")),
                   len(units) - 1)
    end, base = pos, units[pos]["jh"] or ""
    while end + 1 < len(units) and (units[end + 1]["jh"] or "").startswith(base + "@"):
        end += 1
    units.insert(end + 1, _new_unit(jh, text))


def apply(units: list[dict], instrs: list[dict], doc: str) -> tuple[list[dict], list[dict]]:
    """Apply a bill's instructions for one law. Returns the edited units and a
    log entry per instruction: ``{ins, ok, touched, why}``."""
    units = [dict(u) for u in units]
    log: list[dict] = []
    for ins in instrs:
        if ins.get("doc") != doc:
            continue
        op = ins["op"]
        res = {"ins": ins, "ok": False, "touched": [], "why": None}
        log.append(res)
        if op in ("replace", "repeal", "replace_text", "delete_text", "append_sentence",
                  "insert_text"):
            hits = 0
            for ref in ins["refs"]:
                jh = resolve(units, ref)
                if not jh:
                    # Annexes are tables on njt and are not read as units (yet).
                    res["why"] = ("melléklet" if "MEL" in ref
                                  else f"nem található: {njt.unit_label(ref) or ref}")
                    continue
                if op == "replace":
                    idx = _subtree_idx(units, jh)
                    chunk = split_units(ins["new"], ins["refs"])[ins["refs"].index(ref)]
                    units[idx[0]] = _new_unit(jh, chunk)
                    for i in reversed(idx[1:]):
                        del units[i]
                    hits += 1
                elif op == "repeal":
                    for i in _targets(units, jh, ins.get("part")):
                        units[i] = {**units[i], "text": "", "repealed": True}
                    hits += 1
                elif op == "append_sentence":
                    tg = _targets(units, jh, ins.get("part"))
                    if tg:
                        i = tg[-1]
                        units[i] = {**units[i], "text": norm(units[i]["text"] + " " + norm(ins["new"]))}
                        hits += 1
                else:
                    old = ins.get("old") if op != "insert_text" else ins.get("after")
                    new = {"replace_text": ins.get("new"), "delete_text": "",
                           "insert_text": f"{ins.get('after')} {ins.get('new')}"}[op]
                    found = False
                    for i in _targets(units, jh, ins.get("part")):
                        t = units[i]["text"]
                        if old and old in t:
                            # "szövegrész" = one occurrence, "szövegrészek" = all
                            t = t.replace(old, new) if ins.get("all") else t.replace(old, new, 1)
                            units[i] = {**units[i], "text": norm(t)}
                            found = True
                            if not ins.get("all"):
                                break
                    if not found:
                        res["why"] = f"a szövegrész nem található: {njt.unit_label(jh)}"
                        continue
                    hits += 1
                res["touched"].append(jh)
            res["ok"] = hits > 0 and hits == len(ins["refs"])
        elif op == "insert":
            parent = ins["parent"]
            ids = {u["jh"] for u in units}
            if parent and parent.startswith("SZ") and parent + "@BE0" in ids \
                    and ins["newrefs"][0].startswith("PO"):
                parent += "@BE0"
            full = [nr if nr.startswith("SZ") or not parent or parent[:2] in ("FE", "AC")
                    else f"{parent}@{nr}" for nr in ins["newrefs"]]
            for jh, chunk in zip(full, split_units(ins["new"], full)):
                _insert_one(units, jh, chunk)
                res["touched"].append(jh)
            res["ok"] = True
        else:
            res["why"] = ins.get("why") or op
    return units, log


def _ancestor_in(jh: str, seen: set[str]) -> bool:
    parts = jh.split("@")
    return any("@".join(parts[:k]) in seen for k in range(1, len(parts)))


def _collapse(touched: dict[str, dict]) -> list[dict]:
    """Keep the outermost touched units; a change inside one (a point of a
    paragraph that is also changed) is shown as part of it, its provisions and
    operations merged in so the attribution is not lost."""
    keys = set(touched)
    keep = {jh: c for jh, c in touched.items() if not _ancestor_in(jh, keys - {jh})}
    for jh, c in touched.items():
        if jh in keep:
            continue
        parts = jh.split("@")
        anc = next(("@".join(parts[:k]) for k in range(1, len(parts))
                    if "@".join(parts[:k]) in keep), None)
        if anc:
            for field in ("provisions", "ops"):
                for v in c[field]:
                    if v not in keep[anc][field]:
                        keep[anc][field].append(v)
    return list(keep.values())


def changes(before: list[dict], after: list[dict], log: list[dict]) -> list[dict]:
    """One change per touched unit (merged when several instructions touched it),
    in the law's reading order: ``{jh, ref, before, after, provisions, ops}``."""
    touched: dict[str, dict] = {}
    for res in log:
        for jh in res["touched"]:
            c = touched.setdefault(jh, {"jh": jh, "provisions": [], "ops": []})
            if res["ins"]["at"] not in c["provisions"]:
                c["provisions"].append(res["ins"]["at"])
            if res["ins"]["op"] not in c["ops"]:
                c["ops"].append(res["ins"]["op"])
    keep = _collapse(touched)
    order = {u["jh"]: i for i, u in enumerate(after) if u["jh"]}
    keep.sort(key=lambda c: order.get(c["jh"], len(order)))
    out = []
    for c in keep:
        b, a = njt.flatten(before, c["jh"]), njt.flatten(after, c["jh"])
        if b == a:
            continue
        out.append({**c, "ref": njt.unit_label(c["jh"]), "before": b, "after": a})
    return out


# --- attribution from njt (adopted bills) ----------------------------------------

_FN_VERBS = (("hatályon kívül", "repeal"), ("iktatta be", "insert"),
             ("beiktatott", "insert"), ("módosított szöveg", "replace_text"),
             ("módosította", "replace_text"), ("megállapított", "replace"),
             ("megállapította", "replace"))


def _fn_kind(link: dict) -> str:
    """What one act did to a unit, from its footnote's wording around the act."""
    for side in ("after", "before"):
        t = (link.get(side) or "").lower()
        for verb, kind in _FN_VERBS:
            if verb in t:
                return kind
    return "replace"


def attributed_changes(before: list[dict], after: list[dict], act_doc: str) -> list[dict]:
    """The changes an adopted act made, as njt attributes them: every unit of
    the post-amendment state whose footnote names ``act_doc``, with its text on
    both sides and the act's own provision that changed it."""
    cited: dict[str, dict] = {}
    for u in after:
        if not u["jh"]:
            continue
        for fn in u.get("fn") or []:
            for link in fn.get("links") or []:
                if link.get("doc") != act_doc:
                    continue
                c = cited.setdefault(u["jh"], {"jh": u["jh"], "provisions": [], "ops": []})
                prov = njt.unit_label(link.get("jh") or "")
                if prov and prov not in c["provisions"]:
                    c["provisions"].append(prov)
                kind = _fn_kind(link)
                if kind not in c["ops"]:
                    c["ops"].append(kind)
    out = []
    for c in _collapse(cited):
        jh = c["jh"]
        b, a = njt.flatten(before, jh), njt.flatten(after, jh)
        if b == a:
            continue
        out.append({**c, "ref": njt.unit_label(jh), "before": b, "after": a})
    return out
