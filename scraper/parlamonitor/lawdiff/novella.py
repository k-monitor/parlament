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

A reference names one provision or a list of them — across several §s
(``348. § (3) bekezdésében, 350. § (4) bekezdésében …``), as a range
(``(26)–(29) bekezdése``, read off the law so a (26a) inserted since is in it),
or as an enumeration whose items share what precedes them (``1. § (4) bekezdés
a) f) pontjában …, b) g) pontja``). In a repeal the grammatical case tells a
provision repealed whole ("bekezdése") from one a fragment is repealed in
("bekezdésében az „X” szövegrész").

The target of every instruction is an njt unit id (:mod:`.njt`), so applying a
bill is editing a list of units. Measured against njt's own post-amendment
states (:mod:`.evaluate`) on the 35 adopted bills of cycle 43 whose text read
is the one adopted: of 3,445 instructions, 87% applied exactly as njt has them,
7% known failures (listed), 0.3% wrong without saying so, 6% not judgeable.
"""

from __future__ import annotations

import re

from . import njt

# Bumped whenever parsing/applying changes, so cached per-bill results are redone.
# 12: references listed across sections, enumerations with a shared lead,
#     whole/fragment repeals told apart by case, subtitle and chapter titles,
#     ranges read off the law, mistyped quote marks, wrapped bill headings,
#     one sentence's text edits made at once.
PARSER_VERSION = 12

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
# The bill's own annex ("1. melléklet a 2026. évi ….. törvényhez" — the act has
# no number yet); an annex of an amended law names a real one, in a quote.
_OWN_ANNEX = r"(\d+)\. (?:számú )?melléklet a \d{4}\. évi [.…]+ ?törvényhez"
_OWN_ANNEX_LINE = re.compile(rf"^{_OWN_ANNEX}$")


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
# A quote mark typed wrong where a quote ends: „X„ szövegrész, „X" szöveg —
# inside a quote, an opening or straight mark right before the instruction's
# "szöveg(rész)" (or a line end) can only be the closing one.
_STRAY_CLOSE = re.compile(r"[,.;:]?\s*(?:szöveg|\n|$)")


def _quote_marks(text: str) -> str:
    """An opening „ typed as two commas (",,") — which prose never has."""
    return text.replace(",,", "„")


def _quote_step(text: str, i: int, depth: int, last: str = "") -> int:
    """The quote depth after ``text[i]`` (see :func:`tokenize`); ``last`` is the
    last non-space character before it — a straight quote right after "…
    lép:" opens the new text the bill quotes (some bills are typed so)."""
    ch = text[i]
    if ch == "„" and not (depth and _STRAY_CLOSE.match(text, i + 1)):
        return depth + 1
    if ch == '"' and not depth and last == ":":
        return 1
    if depth and (ch == "”" or (ch in "„\"“" and _STRAY_CLOSE.match(text, i + 1))):
        return depth - 1
    return depth


def _margin(lines: list[dict]) -> float:
    """The body's left margin: where most lines start."""
    xs: dict[int, int] = {}
    for r in lines:
        k = round(r.get("x", 0))
        xs[k] = xs.get(k, 0) + 1
    return float(max(xs, key=xs.get)) if xs else 0.0


def _heading_lines(lines: list[dict]) -> set[int]:
    """The bill's own subtitles ("2. A nemzetközi bűnügyi jogsegélyről szóló …
    törvény módosítása") right before one of its own ``N. §`` lines. A heading
    is centred, so a long one wraps over several lines each set in from the
    margin — but so wide that it can start near the margin too, which is why
    a numbered line or two right before the § is taken even at the margin.
    Neither reaches into a quote or across a line that ends a sentence."""
    out: set[int] = set()
    margin = _margin(lines)
    depth, start_depth, last = 0, [], ""
    for r in lines:
        start_depth.append(depth)
        t = _quote_marks(r["text"]) + "\n"
        for i in range(len(t)):
            depth = _quote_step(t, i, depth, last)
            last = t[i] if not t[i].isspace() else last
    for i, r in enumerate(lines):
        if not _OWN_SECTION_LINE.match(r["text"]) or start_depth[i]:
            continue
        for start in (i - 1, i - 2):
            if start < 0 or not _HEADING_START.match(lines[start]["text"]):
                continue
            span = range(start, i)
            if all(not lines[k]["text"].rstrip().endswith((":", ",")) for k in span):
                out.update(span)
                break
        # a longer one: the first line numbered and set in from the margin,
        # none inside a quote or ending a sentence or a list item
        for j in range(i - 1, max(i - 7, -1), -1):
            ln = lines[j]
            if start_depth[j] or "„" in ln["text"] \
                    or re.search(r"[.:;]$", ln["text"].rstrip()) \
                    or (j == i - 1 and ln["text"].rstrip().endswith(",")):
                break
            if _HEADING_START.match(ln["text"]) and ln.get("x", 0) > margin + 3:
                out.update(range(j, i))
                break
    return out


def tokenize(lines: list[dict]) -> list[tuple[str, str]]:
    """The body as alternating ``("out", text)`` / ``("q", text)`` segments.

    Quotes are „…”; nested ones are »…« in Hungarian, so a „ inside a quote is
    still counted, in case a source nests them anyway — unless it stands where
    the quote must end (a mistyped closing mark, :data:`_STRAY_CLOSE`), or one
    typo would swallow the rest of the bill. Centred lines outside a quote are
    the bill's own headings (``3. Záró rendelkezések``) and are dropped; the
    bill's own ``N. §`` and annex title lines are kept, or the annexes would
    read as part of the last section. A line ending in a real hyphen joins the
    next one without a space."""
    headings = _heading_lines(lines)
    segs: list[tuple[str, str]] = []
    cur: list[str] = []
    kind, depth, last = "out", 0, ""
    for li, r in enumerate(lines):
        t = _quote_marks(r["text"])
        if depth == 0 and (li in headings or (r.get("x", 0) > 110
                                              and not _OWN_SECTION_LINE.match(t)
                                              and not _OWN_ANNEX_LINE.match(t)
                                              and "„" not in t)):
            continue
        t += "" if re.search(r"[\w§)]-$", t) else "\n"
        for i, ch in enumerate(t):
            after = _quote_step(t, i, depth, last)
            last = ch if not ch.isspace() else last
            if depth == 0 and after:
                segs.append((kind, "".join(cur))); cur = []; kind = "q"
            elif depth and not after:
                segs.append((kind, "".join(cur))); cur = []; kind = "out"
            else:
                cur.append(ch)      # text, or the marks of a nested quote
            depth = after
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


_SEC_RE = re.compile(rf"(?m)^(?:(\d+(?:/[A-Z])?)\. §|{_OWN_ANNEX})\s*$")
# A paragraph of the bill — not a line that starts with a reference to some
# ("(1e) és (1f) bekezdéssel egészül ki").
_PAR_RE = re.compile(r"(?m)^\((\d+[a-z]?)\) "
                     r"(?!(?:(?:és|,|–|-)\s*\(\d+[a-z]?\)\s*)*bekezdés)")
# An annex's own points: "1. Az Ftv. 1. mellékletében foglalt táblázat …"
_ANNEX_POINT_RE = re.compile(r"(?m)^(\d+)\. (?=[A-ZÁÉÍÓÖŐÚÜŰ])")


def own_sections(tmpl: str) -> list[tuple[str, str]]:
    """The bill's own sections as ``("1. §", text)`` and its annexes as
    ``("1. melléklet", text)``; text before the first one (the title and
    preamble) as ``("", text)``."""
    marks = list(_SEC_RE.finditer(tmpl))
    out = [("", tmpl[:marks[0].start()] if marks else tmpl)]
    for i, m in enumerate(marks):
        key = f"{m.group(1)}. §" if m.group(1) else f"{m.group(2)}. melléklet"
        out.append((key, tmpl[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(tmpl)]))
    return out


def own_provisions(tmpl: str) -> list[tuple[str, str]]:
    """The bill's own paragraphs: ``("2. § (1)", text)`` — or the whole section
    where it has no numbered paragraphs. An annex amending a law does so in
    numbered points, one instruction each: ``("1. melléklet 2. pont", text)``."""
    out = []
    for sec, text in own_sections(tmpl)[1:]:
        if sec.endswith("melléklet"):
            pts = list(_ANNEX_POINT_RE.finditer(text))
            if not pts:
                out.append((sec, text.strip()))
            for j, p in enumerate(pts):
                out.append((f"{sec} {p.group(1)}. pont",
                            text[p.end(): pts[j + 1].start() if j + 1 < len(pts) else len(text)].strip()))
            continue
        pars = list(_PAR_RE.finditer(text))
        if not pars:
            out.append((sec, text.strip()))
            continue
        for j, p in enumerate(pars):
            out.append((f"{sec} ({p.group(1)})",
                        text[p.end(): pars[j + 1].start() if j + 1 < len(pars) else len(text)].strip()))
    return out


# --- references ---------------------------------------------------------------

_LIST = r"(?:\s*(?:,|és|–|-)\s*{0})*"
# a § number, with its book in a code divided into books ("6:105. §", the Ptk.)
_SZ_NUM = r"(?:\d+:)?\d+(?:/[A-Z])?\.?"
# A point is a letter, or a Hungarian digraph past z ("zs)", "dzs)"), or a
# number ("23.", "1a.", "23/f."); a sub-point is lettered ("aa)", "zsa)", or
# "a)" under a numbered point). The first number of a range may go without
# its dot ("24–28. pont").
_PO_LAB = r"(?:[a-z]{1,3}\)|\d+(?:/?[a-z])?\.)"
_PO_FROM = r"(?:\d+(?:/?[a-z])?\.?\s*[–-]\s*)?"
_AP_LAB = r"(?:[a-z]{1,4}\)|\d+\.)"
_TOKEN_RE = re.compile(
    r"(?P<fe>[IVXLCDM]+(?:/[A-Z])?)\. Fejezet|"
    r"(?P<re>[A-ZÁÉÍÓÖŐÚÜŰ]{4,}) (?:Rész|RÉSZ)|"
    r"(?P<pr>preambulum)|"
    r"(?P<acq>⟦\d+⟧) alcím|"
    r"(?P<act>^[A-ZÁÉÍÓÖŐÚÜŰ][^⟦§()\d,;]{2,120}?) alcím|"
    rf"(?P<ac>{_SZ_NUM}{_LIST.format(_SZ_NUM)}) ?alcím|"
    r"(?P<mel>\d+(?:/[A-Z])?)\. (?:számú )?melléklet|(?P<mel0>(?<!\w)melléklet)|"
    rf"(?P<sz>{_SZ_NUM}{_LIST.format(_SZ_NUM)}) ?§|"
    r"(?P<be>\(\d+[a-z]?\)(?:\s*(?:,|és|–|-)\s*\(\d+[a-z]?\))*)\s*bekezdés|"
    rf"(?P<ap>{_PO_FROM}{_AP_LAB}{_LIST.format(_AP_LAB)})\s*alpont|"
    rf"(?P<po>{_PO_FROM}{_PO_LAB}{_LIST.format(_PO_LAB)})\s*pont|"
    r"(?P<ny>nyitó szövegrész)|(?P<za>záró szövegrész)")
# How deep each kind of unit sits in an njt id. A §, a subtitle, a chapter, a
# part or an annex is addressed on its own (njt does not nest a § under its
# chapter).
_LEVEL = {"fe": 0, "re": 0, "pr": 0, "ac": 0, "acq": 0, "act": 0, "mel": 0, "mel0": 0, "sz": 0,
          "be": 1, "po": 2, "ap": 3}
# What an annex reference takes in after it ("1. melléklet I. Fejezet 4. pontja",
# "melléklete preambulumának 2. pontja") is inside the annex, not the law's own.
_IN_ANNEX = {"fe", "re", "pr", "be", "po", "ap"}


def _is_range(spec: str) -> bool:
    return bool(re.search(r"–|-", spec)) and not re.search(r",|és", spec)


def _expand(spec: str, kind: str) -> list[str]:
    """``(2) és (3)`` → ``['2', '3']``; ``b)–d)`` → ``['b', 'c', 'd']``."""
    if kind == "be":
        items = re.findall(r"\((\d+[a-z]?)\)", spec)
    elif kind == "po":
        items = [x.replace("/", "") for x in
                 re.findall(r"([a-z]{1,3}|\d+(?:/?[a-z])?)(?=[\).]|\s*[–-])", spec)]
    else:
        items = re.findall(r"([a-z]{1,4}|\d+)(?=[\).]|\s*[–-])", spec)
    if len(items) == 2 and _is_range(spec):
        a, b = items
        if len(a) == len(b) == 1 and a.isalpha() and b.isalpha():
            items = [chr(c) for c in range(ord(a), ord(b) + 1)]
        elif a.isdigit() and b.isdigit():
            items = [str(i) for i in range(int(a), int(b) + 1)]
        elif len(a) == len(b) == 2 and a[0] == b[0] and a.isalpha():
            items = [a[0] + chr(c) for c in range(ord(a[1]), ord(b[1]) + 1)]
        else:
            # (4a)–(4c), (4)–(4b): one number, a run of letters
            ma, mb = re.fullmatch(r"(\d+)([a-z]?)", a), re.fullmatch(r"(\d+)([a-z]?)", b)
            if ma and mb and ma.group(1) == mb.group(1) and mb.group(2) \
                    and (ma.group(2) or "a") <= mb.group(2):
                n = ma.group(1)
                items = [n] * (not ma.group(2)) + [
                    n + chr(c) for c in range(ord(ma.group(2) or "a"), ord(mb.group(2)) + 1)]
    return items


def _sections(spec: str) -> list[str]:
    """``18/C–18/E.`` → ``['18C', '18D', '18E']``; ``3. és 4.`` → ``['3', '4']``;
    ``6:105.`` → ``['6:105']``. A range across numbers with letters at an end
    (``17/A–19.``) cannot be enumerated without the law, so it keeps its ends
    and the plain numbers (and the applier reads the law between its ends)."""
    items = re.findall(r"(\d+:)?(\d+)(?:/([A-Z]))?", spec)
    if len(items) == 2 and _is_range(spec):
        (ba, a, la), (bb, b, lb) = items
        book = ba or bb
        if a == b and lb:  # 18–18/B. § = 18, 18/A, 18/B
            first = ord(la) if la else ord("A")
            return [book + a] * (not la) + [book + a + chr(c) for c in range(first, ord(lb) + 1)]
        if int(a) < int(b):
            # "45–47/A." runs through 47 to 47/A
            mid = [book + str(n) for n in range(int(a) + 1, int(b) + (1 if lb else 0))]
            return [book + a + la] + mid + [book + b + lb]
    return [bk + n + let for bk, n, let in items]


def _token_ids(m: re.Match) -> list[str]:
    k = m.lastgroup
    if k == "fe":
        return [f"FE{m.group('fe').replace('/', '')}"]
    if k == "re":
        return [f"RE{m.group('re').upper()}"]
    if k == "pr":
        return ["PR"]
    if k == "ac":
        return [f"AC{n}" for n in _sections(m.group("ac"))]
    if k == "acq":      # a subtitle named by its title: resolved once quotes are known
        return [f"ACQ{m.group('acq')[1:-1]}"]
    if k == "act":      # … or by its title unquoted, at the head of an item
        return [f"AC={norm(m.group('act'))}"]
    if k == "mel":
        return [f"MEL{m.group('mel').replace('/', '')}"]
    if k == "mel0":     # a law's only annex: "melléklete"
        return ["MEL"]
    if k == "sz":
        return [f"SZ{n}" for n in _sections(m.group("sz"))]
    prefix = {"be": "BE", "po": "PO", "ap": "AP"}[k]
    return [f"{prefix}{x.upper()}" for x in _expand(m.group(k), k)]


_WORD_END = re.compile(r"[\w-]*")
_TITLE_WORD = re.compile(r"\s+(cím\w*)")
# A reference that only says where something is: "a 394. §-át követő alcím".
_LOCATOR = re.compile(r"\s*(?:követő|megelőző|előtti|utáni)(?!\w)")
_PLACED_HEADING = re.compile(r"\s*(követő|megelőző)\s+(alcím\w*)")
# What may stand between two references of one list.
_FILLER = re.compile(r"(?:[\s,;–-]+|(?:és|valamint|illetve|továbbá|a|az|helyébe|szöveg\w*|lép\w*)"
                     r"(?!\w)|⟦\d+⟧|[a-z]{1,2}\)|\d{1,3}\.)*")


def ref_groups(text: str) -> list[dict]:
    """A reference phrase → its references, grouped as they are written.

    One phrase can name several provisions — ``348. § (3) bekezdésében, 350. §
    (4) bekezdésében és 357. § (5) és (6) bekezdésében`` — and each new § (or a
    unit no deeper than the one before it: ``(2) bekezdés a) pontja és (3)
    bekezdése``) starts another reference, keeping what it shares with the one
    before (the §). A group is what one stretch of words names: ``{refs: [(id,
    part)], end, word, range, locator}`` where part is ``"ny"`` (the opening
    text), ``"za"`` (the closing text), ``"cim"`` (the title) or None (the
    whole unit); ``word`` is the word the group ends on ("bekezdésében",
    "pontja") — its case tells a provision repealed whole from one a fragment
    is repealed in; ``range`` says the refs are a range written by its ends
    ("(26)–(29) bekezdése", which takes in a (26a) the law has between them);
    ``locator`` marks a reference that only places another ("a 394. §-át
    követő alcím")."""
    groups: list[dict] = []
    cur: list[list[tuple[int, str]]] = []
    part, end, word, rng, start = None, 0, "", False, 0

    def flush():
        nonlocal cur, part, rng
        if cur:
            refs = [("@".join(x for _, x in p), part) for p in cur]
            groups.append({"refs": refs, "start": start, "end": end, "word": word,
                           "range": rng and len(cur) > 1,
                           "locator": bool(_LOCATOR.match(text, end))})
            # "a 42/B. §-t megelőző alcím címében": the subtitle placed by it
            mh = _PLACED_HEADING.match(text, end)
            if mh:
                e, w, prt = mh.end(), mh.group(2), None
                mt = _TITLE_WORD.match(text, e)
                if mt:
                    e, w, prt = mt.end(), mt.group(1), "cim"
                side = "<" if mh.group(1) == "megelőző" else ">"
                groups.append({"refs": [(f"AC{side}{r}", prt) for r, _ in refs], "start": end,
                               "end": e, "word": w, "range": False, "locator": False})
        cur, part, rng = [], None, False

    for m in _TOKEN_RE.finditer(text):
        k = m.lastgroup
        w = _WORD_END.match(text, m.end()).group(0)
        if k in ("ny", "za"):
            if cur:
                part, end, word = k, m.end() + len(w), "szövegrész" + w
            continue
        ids = _token_ids(m)
        lvl = _LEVEL[k]
        spans = len(ids) > 1 and _is_range(m.group(k))
        if cur and cur[-1][0][1].startswith("MEL") and k in _IN_ANNEX:
            cur = [p + [(len(p), x)] for p in cur for x in ids]
            rng = False
        elif (k != "fe" and len(cur) == 1 and cur[0][-1][1].startswith("FE")
                and re.fullmatch(r"\s*", text[end:m.start()])):
            cur = [[(lvl, x)] for x in ids]   # "XIII. Fejezet 47. alcíme": context only
            rng = spans
        elif cur and lvl > cur[-1][-1][0]:
            rng = spans and len(cur) == 1
            cur = [p + [(lvl, x)] for p in cur for x in ids]
        elif cur and lvl > 0 and _FILLER.fullmatch(text[end:m.start()]):
            # the next of a list: it keeps what it shares with the one before
            prefix = [s for s in cur[-1] if s[0] < lvl]
            flush()
            cur = [prefix + [(lvl, x)] for x in ids]
            rng, start = spans, m.start()
        else:
            # A new reference — or one cut off from the one before by words
            # that name something else ("… 61. pontja, és b) melléklete XXII.
            # fejezet 1. pontjának …"): left bare, so it resolves to nothing
            # rather than to a point of the wrong provision.
            flush()
            cur = [[(lvl, x)] for x in ids]
            rng, start = spans, m.start()
        end, word = m.end() + len(w), m.group(0).split()[-1] + w
        mc = _TITLE_WORD.match(text, end)
        if mc:      # "XIII. Fejezet címe", "40/H. alcím címében": only its title
            part, end, word = "cim", mc.end(), mc.group(1)
    flush()
    return groups


def parse_refs(text: str) -> list[tuple[str, str | None]]:
    """Every reference of a phrase, in order: ``[(njt unit id, part), …]``."""
    return [r for g in ref_groups(text) if not g["locator"] for r in g["refs"]]


def parse_ref(text: str) -> tuple[list[str], str | None]:
    """A reference phrase → (njt unit ids, part) — the part of the first one
    (see :func:`ref_groups`)."""
    refs = parse_refs(text)
    return [r for r, _ in refs], (refs[0][1] if refs else None)


def _with_groups(groups: list[dict]) -> dict:
    """An instruction's targets: ``refs``, each one's ``parts`` when they are
    not all the same, and the ``ranges`` (first, last index into ``refs``)
    written by their ends."""
    refs: list[tuple[str, str | None]] = []
    ranges: list[list[int]] = []
    for g in groups:
        if g["locator"]:
            continue
        if g["range"]:
            ranges.append([len(refs), len(refs) + len(g["refs"]) - 1])
        refs += g["refs"]
    out = {"refs": [r for r, _ in refs], "part": refs[0][1] if refs else None}
    if len({p for _, p in refs}) > 1:
        out["parts"] = [p for _, p in refs]
    if ranges:
        out["ranges"] = ranges
    return out


def _part_of(ins: dict, k: int) -> str | None:
    return ins["parts"][k] if ins.get("parts") else ins.get("part")


def ref_label(jh: str) -> str | None:
    """The label a unit is introduced with in a quoted block: ``BE14`` → ``(14)``."""
    last = jh.split("@")[-1]
    m = re.match(r"^(SZ|BE|PO|AP)((?:\d+:)?\d*)([A-Z]*)$", last)
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


def _split_raw(text: str, jhs: list[str]) -> list[str]:
    """Split a quoted block into one chunk per target, at each target's label —
    a label at the start of a line, not one quoted in running text (``a (4a),
    (4b) és (4d) bekezdés``). A target in another § than the one before it
    starts at that §'s number ("27. § (1) …"). Chunks keep their line breaks."""
    if len(jhs) < 2:
        return [text]
    cuts, pos, prev = [0], 0, jhs[0]
    for jh in jhs[1:]:
        sec = jh.split("@")[0]
        lab = ref_label(sec if "@" in jh and sec != prev.split("@")[0] else jh)
        m = re.compile(r"(?m)^\s*" + re.escape(lab) + r"\s").search(text, pos + 1) if lab else None
        if not m:
            return [text] + [""] * (len(jhs) - 1)
        cuts.append(m.start())
        pos, prev = m.start(), jh
    cuts.append(len(text))
    return [text[cuts[k]:cuts[k + 1]] for k in range(len(jhs))]


def split_units(text: str, jhs: list[str]) -> list[str]:
    """:func:`_split_raw`, each chunk as one line."""
    return [norm(c) for c in _split_raw(text, jhs)]


# --- instructions --------------------------------------------------------------

_ABBR_RE = re.compile(r"(\d{4})\. évi ([IVXLCDM]+)\. törvény\w*\s*\(a továbbiakban: ([^)]+?)\)")
_CIT_RE = re.compile(r"(\d{4})\. évi ([IVXLCDM]+)\. törvény")
_AMEND_HINT = re.compile(r"helyébe|egészül ki|[Hh]atályát veszti|szövegrész|[Nn]em lép hatályba")
# An enumeration item's label, before what an item can start with: a reference
# ("a) 20. § …", "1. 23. § …", "b) (2) bekezdésében", "f) ÖTÖDIK Rész címében"),
# a point of the provision the list hangs off ("… 1. § (4) bekezdés a) f)
# pontjában …, b) g) pontja") or a quote ("c) az „X” szövegrész helyébe …").
_ITEM_RE = re.compile(r"(?<![^\s,])(?:([a-z]{1,2})\)|(\d{1,3})\.) "
                      r"(?=\d|[IVXLCDM]+\. |(?:az? )?⟦|\(\d|[a-z]{1,3}\) |[A-ZÁÉÍÓÖŐÚÜŰ]{3,}\b"
                      r"|[A-ZÁÉÍÓÖŐÚÜŰ][^,;⟦§\d]{0,120}? alcím)")
# A law named inside another act's title is not the one amended: "a … szóló
# 2011. évi CLXX. törvény módosításáról szóló 2013. évi CXVIII. törvény
# 10/A. §-a" is about the 2013 act. The title runs on from the inner mention
# without an article up to the outer act's "szóló YYYY. évi N. törvény"; the
# next law of an enumeration ("… törvény és a … szóló …") starts with one, and
# a reference (§, a quote, a list item) is past the title.
_OUTER_RE = re.compile(r"(?=\s)(?!\s*(?:(?:és|valamint|illetve|továbbá)\s+)?az?\s)"
                       r"[^§⟦()]{0,250}?\sszóló (\d{4})\. évi ([IVXLCDM]+)\. törvény\w*")
_TAIL_RE = re.compile(r"\w*\s*\(a továbbiakban: [^)]+\)")


def abbreviations(tmpl: str) -> dict[str, str]:
    """``Szkt.`` → ``2019-80-00-00`` for every law the bill names with one."""
    out = {}
    for m in _ABBR_RE.finditer(norm(tmpl)):
        out[m.group(3).strip()] = njt.doc_id(m.group(1), roman(m.group(2)))
    return out


def _law_of(text: str, abbr: dict[str, str]) -> tuple[str | None, int | None]:
    """The amended law named first in an instruction, and where its mention
    ends — the outermost act when the first one named is inside its title."""
    best = None
    for ab, doc in abbr.items():
        m = re.search(r"(?<![\w.])" + re.escape(ab) + r"(?!\w)", text)
        if m and (best is None or m.start() < best[1]):
            best = (doc, m.start(), m.end())
    m = _CIT_RE.search(text)
    if m and (best is None or m.start() < best[1]):
        end = m.end()
        tail = _TAIL_RE.match(text, end)
        best = (njt.doc_id(m.group(1), roman(m.group(2))), m.start(), tail.end() if tail else end)
    if not best:
        return None, None
    doc, end = best[0], best[2]
    while m := _OUTER_RE.match(text, end):
        doc = njt.doc_id(m.group(1), roman(m.group(2)))
        tail = _TAIL_RE.match(text, m.end())
        end = tail.end() if tail else m.end()
    return doc, end


# A new § in a quoted block: its number at a line start, then its first
# paragraph, its title or its text — not a reference that a wrapped line happens
# to start with ("79. § (2) bekezdése szerint", "79. §-a", "79. § rendelkezéseit").
_NEW_SECTION = re.compile(r"(?m)^\s*((?:\d+:)?\d+(?:/[A-Z])?)\. §"
                          r"(?=[ \t]*(?:$|\(1\)|\[|[A-ZÁÉÍÓÖŐÚÜŰ]))")


def _new_heading(new: str, what: str) -> list[list]:
    """The heading a new subtitle or chapter opens with, as units
    (``[jh, cls, label, text]``): "22/A. A még elégséges szolgáltatás …",
    "XLV/A. Fejezet KÜLFÖLDI ELEM ESETÉN …"."""
    head = _NEW_SECTION.split(new, 1)[0]
    lines = [ln.strip() for ln in head.split("\n") if ln.strip()]
    if not lines:
        return []
    m = re.match(r"^([IVXLCDM]+(?:/[A-Z])?)\. Fejezet\s*(.*)$", lines[0])
    if m and re.search(r"fejezett", what, re.I):
        fe = "FE" + m.group(1).replace("/", "")
        # the chapter's title is in capitals; a subtitle of its own may follow
        title = [m.group(2)] + [ln for ln in lines[1:] if ln == ln.upper()]
        return [[fe, "fejezet", "", f"{m.group(1)}. Fejezet"],
                [f"{fe}@CIM", "fejezetCim", "", norm(" ".join(title))]]
    if re.search(r"alcímm", what, re.I):
        first = _NEW_SECTION.search(new)
        return _heading_units(lines, first.group(1) if first else "")
    return []


def _heading_units(lines: list[str], before: str) -> list[list]:
    """A subtitle's lines as a heading unit: numbered ("22/A. Az …") as njt's
    ``alcim``, unnumbered ("Átmeneti szabályok") as its ``tagoloCim`` — whose
    id njt makes up, so it is named here after the § it stands before."""
    m = re.match(r"^(\d+(?:/[A-Z])?)\.\s+(.+)$", lines[0])
    if m:
        return [["AC" + m.group(1).replace("/", ""), "alcim", f"{m.group(1)}.",
                 norm(" ".join([m.group(2)] + lines[1:]))]]
    return [[f"TA~{before.replace('/', '')}", "tagoloCim", "", norm(" ".join(lines))]]


def _section_heading(chunk: str) -> tuple[str, list[str]]:
    """A new § as quoted, and the heading lines that close it — the subtitle of
    the § after it ("… révén.\n155/B. Különös szabályok"): lines after its last
    sentence that end none."""
    lines = chunk.rstrip("\n").split("\n")
    k = len(lines)
    while k > 1 and lines[k - 1].strip() and not re.search(r"[.:;,!?”)\]]$", lines[k - 1].strip()):
        k -= 1
    if k == len(lines) or not re.search(r"[.:;!?”]$", lines[k - 1].strip()):
        return chunk, []
    return "\n".join(lines[:k]), [ln.strip() for ln in lines[k:] if ln.strip()]


_KIND_LEVEL = {"SZ": 0, "BE": 1, "PO": 2, "AP": 3}


def _parent_for(ref: str, new: str) -> str:
    """Where a unit new to the law goes when the bill names it beside another
    ("… f) pontja helyébe … lép, és e bekezdés a következő g) ponttal egészül
    ki"): under the part of ``ref`` above the new unit's kind."""
    lvl = _KIND_LEVEL.get(new[:2], 0)
    return "@".join(p for p in ref.split("@") if _KIND_LEVEL.get(p[:2], -1) < lvl)


def _next_label(lab: str) -> set[str]:
    if lab.isdigit():
        return {str(int(lab) + 1)}
    if lab == "z":
        return {"zs", "za"}
    return {lab[:-1] + chr(ord(lab[-1]) + 1)}


def _items(text: str) -> list[tuple[str | None, str, str]]:
    """An enumerated tail ``a) X, b) Y`` → ``[('a', lead, 'X'), ('b', lead, 'Y')]``.

    ``lead`` is what comes before the first item — the provision the items
    point into when they name only its points (``1. § (4) bekezdés a) f)
    pontjában …, b) g) pontja``). Only a run of labels counted from ``a)`` or
    ``1.`` is an enumeration, so a label that merely looks like one is not
    taken for an item."""
    picked, want = [], {"a", "1"}
    for m in _ITEM_RE.finditer(text):
        lab = m.group(1) or m.group(2)
        if lab in want:
            picked.append(m)
            want = _next_label(lab)
    if len(picked) < 2:
        return [(None, "", text)]
    lead = text[:picked[0].start()].strip()
    return [(m.group(1) or m.group(2), lead,
             text[m.end(): picked[i + 1].start() if i + 1 < len(picked) else len(text)])
            for i, m in enumerate(picked)]


# A fragment named by its words: "az „X” szövegrész", "az „X” és az „Y” szövegrész".
# (The article is now and then left out: "… alpontjában „X” szövegrész".)
_FRAG_RE = re.compile(r"(?:(?:az?|a) )?⟦\d+⟧(?:(?:,\s*|\s+(?:és|valamint|illetve)\s+)(?:(?:az?|a) )?⟦\d+⟧)*"
                      r"\s+szövegrész\w*")
_INESSIVE = re.compile(r"(?:ban|ben)$")


def parse_instruction(tmpl: str, quotes: list[str], abbr: dict[str, str],
                      at: str) -> list[dict]:
    """One paragraph of the bill → its instructions (``op`` + targets + text).

    ``op`` is one of ``replace``, ``insert``, ``replace_text``, ``delete_text``,
    ``insert_text``, ``append_sentence``, ``repeal``, ``repeal_law``; a
    paragraph that amends something we cannot read is ``unparsed`` (with a
    ``why``), and one that amends nothing is ``none``."""
    out = _instructions(tmpl, quotes, abbr, at)
    for ins in out:
        # a subtitle named by its title ("a „Záró rendelkezések” alcím
        # címében") is found by that title in the law
        titled = lambda r: f"AC={norm(quotes[int(r[3:])])}" if r.startswith("ACQ") else r  # noqa: E731
        if ins.get("refs"):
            ins["refs"] = [titled(r) for r in ins["refs"]]
        if ins.get("parent"):
            ins["parent"] = titled(ins["parent"])
    return out


def _instructions(tmpl: str, quotes: list[str], abbr: dict[str, str],
                  at: str) -> list[dict]:
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
        if not label:
            return at
        return f"{at} {label}." if label.isdigit() else f"{at} {label})"

    def quote_of(label, item, lead=""):
        """What to quote of an enumerated instruction: its own item, with the
        law named (and what the items hang off) — not the whole enumeration
        again for every item."""
        if not label:
            return t
        head = f"{law_mention} {lead}".strip()
        return f"{head} {label}{'.' if label.isdigit() else ')'} {item.strip()}"

    # "Nem lép hatályba a … 2025. évi CXI. törvény 14. § a) pontja": a provision
    # not yet in force — nearly always of an amending act — is called off. What
    # that leaves out is in the law the provision would have amended, which the
    # bill does not name, so it is shown rather than applied.
    if re.match(r"^Nem lép hatályba", t) or re.search(r"nem lép hatályba\.?$", rest):
        return [{**base, "op": "unparsed", "why": "hatálybalépés elmarad", "doc": doc}]

    # repeal (whole units, or a fragment of one). A reference in the
    # nominative ("… (2) bekezdése", "… §-a") is repealed whole; one in the
    # inessive ("… (2) bekezdésében", "… pontjában") is where a fragment named
    # after it is repealed — one list mixes both.
    if re.match(r"^Hatályát vesz(?:t)?i", t) or re.search(r"hatályát vesz(?:t)?i\.?$", rest):
        body_ = re.sub(r"\s*hatályát vesz(?:t)?i\.?$", "", rest).rstrip(".")
        if not body_.strip():
            return [{**base, "op": "repeal_law", "doc": doc}]
        for label, lead, it in _items(body_):
            ctx = f"{lead} {it}" if lead else it
            frags = list(_FRAG_RE.finditer(ctx))
            found: list[tuple[int, dict]] = []
            pending: dict[int, list] = {}
            item = {**base, "at": at_item(label), "src": quote_of(label, it, lead), "doc": doc}
            groups = [g for g in ref_groups(ctx) if not g["locator"]]
            for gi, g in enumerate(groups):
                if _INESSIVE.search(g["word"]):
                    # every fragment named after it, up to the next provision
                    # ("… pontjában az „X” szövegrész és az „Y” szövegrész")
                    until = groups[gi + 1]["start"] if gi + 1 < len(groups) else len(ctx)
                    mine = [k for k, f in enumerate(frags) if g["end"] <= f.start() < until]
                    if not mine:
                        # a provision listed before the fragments it shares
                        # with the next one ("(3) és (4) bekezdésében az „X” …")
                        nxt = next((k for k, f in enumerate(frags) if f.start() >= g["end"]), None)
                        mine = [nxt] if nxt is not None else []
                    for k in mine:
                        pending.setdefault(k, []).append(g)
                    if not mine:    # a fragment we cannot read: shown, not dropped
                        found.append((g["end"], {**item, "op": "unparsed",
                                                 "why": "ismeretlen szerkezet"}))
                    continue
                found.append((g["end"], {**item, "op": "repeal", **_with_groups([g])}))
            for fi, groups in pending.items():
                every = bool(re.search(r"szövegrész(?:ek|ei)$", frags[fi].group(0)))
                for qn in re.findall(r"⟦(\d+)⟧", frags[fi].group(0)):
                    found.append((frags[fi].start(), {**item, "op": "delete_text",
                                                      **_with_groups(groups),
                                                      "old": norm(q(qn)), "all": every}))
            out += [e for _, e in sorted(found, key=lambda x: x[0])]
        return out or [{**base, "op": "unparsed", "why": "hatályon kívül helyezés", "doc": doc}]

    if re.search(r"melléklete? helyébe az? \d+\. melléklet lép", rest) \
            or re.search(r"melléklettel egészül ki", rest):
        return [{**base, "op": "unparsed", "why": "melléklet", "doc": doc}]

    # replace whole provision(s)
    m = re.match(r"^(?P<ref>.+?) helyébe a következő (?:rendelkezés|rendelkezések|szöveg)"
                 r"\s+(?:lép|lépnek)\s*:?\s*(?P<tail>.*)$", rest)
    if m:
        tg = _with_groups(ref_groups(m.group("ref")))
        qs = re.findall(r"⟦(\d+)⟧", m.group("tail"))
        if tg["refs"] and qs:
            new = "\n".join(q(n) for n in qs)
            # "… f) pontja helyébe a következő rendelkezés lép, és e bekezdés a
            # következő g) ponttal egészül ki: „f) …” „g) …”" — both at once
            both = re.match(r"^,?\s*(?:és|valamint)\s+(?:[ae]z?\s+(?:§|bekezdés|pont)\w*\s+)?"
                            r"a következő (?P<what>.+?)\s+egészül ki", m.group("tail"))
            newrefs = [r for r, _ in parse_refs(both.group("what"))] if both else []
            if newrefs:
                parent = _parent_for(tg["refs"][-1], newrefs[0])
                full = [nr if nr.startswith("SZ") or not parent else f"{parent}@{nr}"
                        for nr in newrefs]
                chunks = _split_raw(new, tg["refs"] + full)
                if all(c.strip() for c in chunks):
                    n = len(tg["refs"])
                    return [{**base, "op": "replace", "doc": doc, **tg,
                             "new": "\n".join(chunks[:n])},
                            {**base, "op": "insert", "doc": doc, "parent": parent,
                             "newrefs": newrefs, "new": "\n".join(chunks[n:])}]
            return [{**base, "op": "replace", "doc": doc, **tg, "new": new}]

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
            if any("MEL" in p for p in parent):
                # an annex is a table on njt, not read as units (yet)
                return [{**base, "op": "unparsed", "why": "melléklet", "doc": doc}]
            newrefs, _ = parse_ref(what)
            quoted = list(dict.fromkeys(f"SZ{n.replace('/', '')}" for n in _NEW_SECTION.findall(new)))
            if newrefs and all(r.startswith("SZ") for r in newrefs) and quoted \
                    and (quoted[0], quoted[-1]) == (newrefs[0], newrefs[-1]):
                # "17/A–19. §": the §s between are the ones the quote brings
                newrefs = quoted
            heading: list[list] = []
            if all(r.startswith(("FE", "AC", "RE")) for r in newrefs) \
                    and re.search(r"alcímm|fejezett|címmel|résszel", what, re.I):
                # A new subtitle or chapter: what enters the law is its
                # heading and the sections it contains, each under its own
                # number.
                newrefs = quoted
                heading = _new_heading(new, what)
                if heading:     # the §s' own text starts at the first of them
                    new = new[_NEW_SECTION.search(new).start():]
                parent = []
            if newrefs:
                return [{**base, "op": "insert", "doc": doc,
                         "parent": parent[0] if parent else "", "newrefs": newrefs,
                         "new": new, **({"heading": heading} if heading else {})}]

    # a fragment inserted after an existing one
    for mm in re.finditer(r"(?P<ref>[^⟦]*?)(?:az?|a) ⟦(?P<a>\d+)⟧ szövegrész(?:t|e)? követően "
                          r"(?:az?|a) ⟦(?P<b>\d+)⟧ szöveg(?:gel|résszel)", rest):
        tg = _with_groups(ref_groups(mm.group("ref")))
        if tg["refs"]:
            out.append({**base, "op": "insert_text", "doc": doc, **tg,
                        "after": norm(q(mm.group("a"))), "new": norm(q(mm.group("b")))})

    # text replacements, possibly enumerated and chained; an item that names no
    # provision of its own is in the one the list hangs off (its lead)
    last = None
    for label, lead, it in _items(rest):
        prev_end = 0
        for mm in re.finditer(r"(?:(?:az?|a) )?⟦(\d+)⟧ szövegrész(?:ek)? helyébe (?:az? |a )?⟦(\d+)⟧ "
                              r"szöveg(?:ek)?", it):
            # the references since the last replacement, read with what came
            # before them ("… 5. § (2) bekezdésében …, (3) bekezdésében …")
            tg = _with_groups([g for g in ref_groups(it[:mm.start()]) if g["end"] > prev_end])
            if not tg["refs"] and not prev_end and lead:
                tg = _with_groups(ref_groups(lead))
            if tg["refs"]:
                last = tg
            elif last is None:
                continue
            out.append({**base, "at": at_item(label), "src": quote_of(label, it, lead),
                        "op": "replace_text", "doc": doc, **last,
                        "old": norm(q(mm.group(1))), "new": norm(q(mm.group(2))),
                        "all": "szövegrészek" in mm.group(0)})
            prev_end = mm.end()
    if out:
        return out
    if "helyett" in rest and "szöveggel" in rest:
        why = "módosított szöveggel hatályba lépés"
    elif re.search(r"\d+(?:/[A-Z])?\. (?:számú )?mellékle", rest.split("⟦", 1)[0]):
        # "… 1. mellékletében foglalt táblázat a következő 14. sorral egészül ki"
        why = "melléklet"
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


_HEADING_CLASSES = ("alcim", "tagoloCim", "tagolo")


def resolve(units: list[dict], ref: str) -> str | None:
    if ref.startswith(("AC<", "AC>")):
        # the subtitle right before (or after) a §
        anchor = resolve(units, ref[3:])
        if not anchor:
            return None
        at = [i for i, u in enumerate(units)
              if u["jh"] == anchor or (u["jh"] or "").startswith(anchor + "@")]
        steps = range(at[0] - 1, -1, -1) if ref[2] == "<" else range(at[-1] + 1, len(units))
        hit = next((k for k in steps if units[k]["cls"] in _HEADING_CLASSES
                    or units[k]["cls"] == "szakasz"), None)
        return units[hit]["jh"] if hit is not None and units[hit]["cls"] != "szakasz" else None
    if ref.startswith("AC="):
        # a subtitle, numbered or not ("XI. Az állam …" keeps its number in
        # its text), by its title
        title = ref[3:].casefold()
        return next((u["jh"] for u in units if u["cls"] in ("alcim", "tagoloCim", "tagolo")
                     and u["jh"] and re.sub(r"^[IVXLCDM\d]+\.\s*", "", norm(u["text"])).casefold()
                     == title), None)
    ids = {u["jh"] for u in units}
    if ref in ids:
        return ref
    # njt ids are canonical (:func:`.njt.canonical`): a § without numbered
    # paragraphs has its points straight under the §, its body under BE0
    alt = re.sub(r"^([^@]+)@BE0@", r"\1@", ref)
    if alt in ids:
        return alt
    return _by_label(units, alt)


def _label_norm(label: str) -> str:
    return re.sub(r"[\s/]", "", label or "").lower()


def _by_label(units: list[dict], ref: str) -> str | None:
    """A unit found by its labels, step by step under the §: njt's ids do not
    always follow them ("cb)" filed as ``APBB``, "28." and "28/a." both as
    ``PO28``) — the labels are what the bill cites."""
    parts = ref.split("@")
    if len(parts) < 2 or not parts[0].startswith("SZ"):
        return None
    cur = parts[0]
    lo = next((i for i, u in enumerate(units) if u["jh"] == cur), None)
    if lo is None:
        return None
    hi = lo + 1
    while hi < len(units) and (units[hi]["jh"] or "").startswith(cur + "@"):
        hi += 1
    for part in parts[1:]:
        want = _label_norm(ref_label(part) or "")
        depth = cur.count("@") + 1
        hit = next((i for i in range(lo, hi) if (units[i]["jh"] or "").count("@") == depth
                    and (units[i]["jh"] or "").startswith(cur + "@")
                    and _label_norm(units[i]["label"]) == want), None)
        if hit is None or not want:
            return None
        cur = units[hit]["jh"]
        lo, hi = hit, hit + 1
        while hi < len(units) and (units[hi]["jh"] or "").startswith(cur + "@"):
            hi += 1
    return cur


def _targets(units: list[dict], jh: str, part: str | None) -> list[int]:
    """The units an instruction acts on: the unit and all under it, or only its
    opening (``ny``) or closing (``za``) text. A § without numbered paragraphs
    keeps its opening text in its ``@BE0`` unit (the § unit holds its title)."""
    idx = _subtree_idx(units, jh)
    if part == "ny":
        own = [i for i in idx if units[i]["jh"] == jh]
        if "@" not in jh:
            body = [i for i in idx if units[i]["jh"] == jh + "@BE0"]
            return body or own
        return own
    if part == "za":
        return [i for i in idx if units[i]["jh"] == jh + "@ZARO"]
    if part == "cim":
        # a § or a subtitle holds its title itself; a chapter's is its @CIM
        own = [i for i in idx if units[i]["jh"] == jh]
        return [i for i in idx if units[i]["jh"] == jh + "@CIM"] or own
    if not part and jh.startswith(("AC", "FE", "TA")) and "@" not in jh and idx:
        return _heading_span(units, idx[0])
    return idx


# A heading and what it heads: a subtitle runs to the next subtitle or chapter,
# a chapter to the next chapter (njt does not nest a § under either); an
# unnumbered subtitle (njt's tagoló) may stand inside a numbered one.
_HEADS = {"alcim": {"alcim", "fejezet", "resz", "focim", "konyv"},
          "tagoloCim": {"tagoloCim", "tagolo", "alcim", "fejezet", "resz", "focim", "konyv"},
          "tagolo": {"tagoloCim", "tagolo", "alcim", "fejezet", "resz", "focim", "konyv"},
          "fejezet": {"fejezet", "resz", "konyv"}}


def _heading_span(units: list[dict], i: int) -> list[int]:
    stop = _HEADS.get(units[i]["cls"])
    if not stop:
        return [i]
    j = i + 1
    while j < len(units) and units[j]["cls"] not in stop:
        j += 1
    return list(range(i, j))


def _label_key(jh: str) -> tuple:
    """Sibling order: 4 < 4a < 5; ``SZ14D`` → (14, 'D')."""
    last = jh.split("@")[-1]
    m = re.match(r"([A-Z]+?)(?:\d+:)?(\d*)([A-Z]*)$", last)
    return (int(m.group(2) or 0), m.group(3)) if m else (0, last)


def _unlabelled(text: str, label: str) -> str:
    """A unit's new wording without the label the bill quotes it with."""
    text = norm(text)
    if label and text.startswith(label):
        return text[len(label):].strip()
    return text


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


def _spread(units: list[dict], ins: dict) -> tuple[list[str], list[str | None]]:
    """An instruction's targets with each range written by its ends ("(26)–(29)
    bekezdése", "43–48. alcíme") read off the law: every unit of that kind it
    has between them, the ones inserted since included ((26a), 47/A.)."""
    refs = list(ins["refs"])
    parts = [_part_of(ins, k) for k in range(len(refs))]
    for i, j in reversed(ins.get("ranges") or []):
        a, b = resolve(units, refs[i]), resolve(units, refs[j])
        if not a or not b:
            continue
        between = _siblings_between(units, a, b)
        if between:
            refs[i:j + 1] = between
            parts[i:j + 1] = [parts[i]] * len(between)
    return refs, parts


def _siblings_between(units: list[dict], a: str, b: str) -> list[str]:
    ia = next(i for i, u in enumerate(units) if u["jh"] == a)
    ib = next(i for i, u in enumerate(units) if u["jh"] == b)
    if ib < ia:
        return []
    cls = units[ia]["cls"]
    prefix = a.rsplit("@", 1)[0] + "@" if "@" in a else ""
    depth = a.count("@")
    out: list[str] = []
    for u in units[ia:ib + 1]:
        jh = u["jh"] or ""
        same = (u["cls"] == cls) if not prefix else (jh.startswith(prefix)
                                                    and jh.count("@") == depth)
        if same and jh and jh not in out:
            out.append(jh)
    return out


_TEXT_OPS = ("replace_text", "delete_text", "insert_text")


def _fragment(old: str, whole: bool) -> str:
    """A fragment to find; ``whole``: not as part of a longer word — "főispán"
    is not in "főispánt", which a bill lists apart when it means it too."""
    pat = re.escape(old)
    if whole and re.match(r"\w", old):
        pat = r"(?<!\w)" + pat
    if whole and re.search(r"\w$", old):
        pat += r"(?!\w)"
    return pat


def _edit_texts(units: list[dict], idx: list[int], edits: list[dict]) -> list[bool]:
    """Several text edits of one sentence of the bill, made at once.

    "a „főispán” szövegrészek helyébe a „kormánymegbízott” szöveg, a „főispánt”
    szövegrészek helyébe a „kormánymegbízottat” szöveg" — each fragment is
    found in the text as it stood, not as the edit before left it (or the
    first would turn "főispánt" into "kormánymegbízottt" and the second find
    nothing; a swap of two words would make them one). A fragment is matched
    as whole words first, and as it stands only where it is not found so.
    "szövegrész" is one occurrence, "szövegrészek" all of them. Returns
    whether each edit found its fragment."""
    found = [False] * len(edits)
    left = [None if e.get("all") else 1 for e in edits]   # None: no limit
    for whole in (True, False):
        todo = sorted((k for k, e in enumerate(edits) if e["old"] and not found[k]),
                      key=lambda k: -len(edits[k]["old"]))
        if not todo:
            break
        pat = re.compile("|".join(f"(?P<e{k}>{_fragment(edits[k]['old'], whole)})" for k in todo))

        def sub(m: re.Match) -> str:
            k = int(m.lastgroup[1:])
            if left[k] == 0:
                return m.group(0)
            if left[k]:
                left[k] -= 1
            found[k] = True
            return edits[k]["new"]

        for i in idx:
            t = units[i]["text"]
            edited = pat.sub(sub, t)
            if edited != t:
                units[i] = {**units[i], "text": norm(edited)}
    return found


def _steps(instrs: list[dict], doc: str) -> list[list[dict]]:
    """The instructions for one law, the text edits of one sentence (same
    provision, same targets) grouped to be made at once."""
    steps: list[list[dict]] = []
    for ins in instrs:
        if ins.get("doc") != doc:
            continue
        prev = steps[-1][-1] if steps else None
        if prev and ins["op"] in _TEXT_OPS and prev["op"] in _TEXT_OPS and all(
                ins.get(f) == prev.get(f) for f in ("at", "src", "refs", "parts", "part", "ranges")):
            steps[-1].append(ins)
        else:
            steps.append([ins])
    return steps


def apply(units: list[dict], instrs: list[dict], doc: str) -> tuple[list[dict], list[dict]]:
    """Apply a bill's instructions for one law. Returns the edited units and a
    log entry per instruction: ``{ins, ok, touched, why}``."""
    units = [dict(u) for u in units]
    log: list[dict] = []
    for step in _steps(instrs, doc):
        ins = step[0]
        op = ins["op"]
        if op in _TEXT_OPS:
            _apply_text(units, step, log)
            continue
        res = {"ins": ins, "ok": False, "touched": [], "why": None}
        log.append(res)
        if op in ("replace", "repeal", "append_sentence"):
            hits = 0
            refs, parts = _spread(units, ins)
            for k, ref in enumerate(refs):
                part = parts[k]
                jh = resolve(units, ref)
                if not jh:
                    res["why"] = _not_found(ref)
                    continue
                if op == "replace":
                    chunk = split_units(ins["new"], refs)[k]
                    if "@" in jh:   # "27. § (1) …" quoted for a paragraph of § 27
                        chunk = re.sub(r"^(?:\d+:)?\d+(?:/[A-Z])?\. §\s*", "", chunk)
                    idx = _subtree_idx(units, jh) if not part else _targets(units, jh, part)
                    if len(idx) == 1:
                        # one unit (a leaf, or only its opening/closing text or
                        # title): its new wording, its place and label kept —
                        # a title quoted under its heading ("MÁSODIK RÉSZ A …")
                        # without the heading
                        u = units[idx[0]]
                        text = _unlabelled(chunk, u["label"])
                        head = next((h["text"] for h in units if h["jh"] == jh), "")
                        if part == "cim" and head and u["jh"] != jh \
                                and text.casefold().startswith(head.casefold()):
                            text = text[len(head):].strip()
                        units[idx[0]] = {**u, "text": text}
                    elif idx:
                        units[idx[0]] = _new_unit(jh, chunk)
                        for i in reversed(idx[1:]):
                            del units[i]
                    hits += 1
                elif op == "repeal":
                    span = _targets(units, jh, part)
                    for i in span:
                        units[i] = {**units[i], "text": "", "repealed": True}
                    # a subtitle or chapter repealed whole takes its §s with it
                    res["touched"] += [units[i]["jh"] for i in span
                                       if units[i]["cls"] == "szakasz" and units[i]["jh"]]
                    hits += 1
                else:
                    tg = _targets(units, jh, part)
                    if tg:
                        i = tg[-1]
                        units[i] = {**units[i], "text": norm(units[i]["text"] + " " + norm(ins["new"]))}
                        hits += 1
                res["touched"].append(jh)
            res["ok"] = hits > 0 and hits == len(refs)
        elif op == "insert":
            parent = ins["parent"]
            full = [nr if nr.startswith("SZ") or not parent or parent[:2] in ("FE", "AC")
                    else f"{parent}@{nr}" for nr in ins["newrefs"]]
            chunks = _split_raw(ins["new"], full)
            between: dict[str, list[list]] = {}
            if all(jh.startswith("SZ") for jh in full):
                for k in range(len(full) - 1):
                    chunks[k], head = _section_heading(chunks[k])
                    if head:
                        between[full[k + 1]] = _heading_units(head, full[k + 1][2:])
            for jh, chunk in zip(full, chunks):
                _insert_one(units, jh, norm(chunk))
                res["touched"].append(jh)
            for jh, rows in between.items():
                # the subtitle a new § opens, before it
                at = next((i for i, u in enumerate(units) if u["jh"] == jh), None)
                if at is not None:
                    for row in reversed(rows):
                        units.insert(at, {"jh": row[0], "cls": row[1], "label": row[2],
                                          "text": row[3], "fn": []})
                    res["touched"] += [row[0] for row in rows]
            if ins.get("heading"):
                # a new subtitle or chapter: its heading before its first §
                at = next((i for i, u in enumerate(units) if u["jh"] == full[0]), None)
                if at is not None:
                    for row in reversed(ins["heading"]):
                        units.insert(at, {"jh": row[0], "cls": row[1], "label": row[2],
                                          "text": row[3], "fn": []})
                    res["touched"] += [row[0] for row in ins["heading"]]
            res["ok"] = True
        else:
            res["why"] = ins.get("why") or op
    return units, log


def _not_found(ref: str) -> str:
    # Annexes are tables on njt and are not read as units (yet).
    return "melléklet" if "MEL" in ref else f"nem található: {njt.unit_label(ref) or ref}"


def _apply_text(units: list[dict], step: list[dict], log: list[dict]) -> None:
    """One sentence's text edits (:func:`_steps`), on each of its targets."""
    results = [{"ins": ins, "ok": False, "touched": [], "why": None} for ins in step]
    log += results
    edits = []
    for ins in step:
        if ins["op"] == "insert_text":
            edits.append({"old": ins.get("after"), "new": f"{ins.get('after')} {ins.get('new')}"})
        else:
            edits.append({"old": ins.get("old"), "all": ins.get("all"),
                          "new": ins.get("new") if ins["op"] == "replace_text" else ""})
    refs, parts = _spread(units, step[0])
    hits = [0] * len(step)
    for k, ref in enumerate(refs):
        jh = resolve(units, ref)
        if not jh:
            for res in results:
                res["why"] = _not_found(ref)
            continue
        found = _edit_texts(units, _targets(units, jh, parts[k]), edits)
        for res, ok in zip(results, found):
            if ok:
                res["touched"].append(jh)
            else:
                res["why"] = f"a szövegrész nem található: {njt.unit_label(jh)}"
        hits = [h + ok for h, ok in zip(hits, found)]
    for res, h in zip(results, hits):
        res["ok"] = h > 0 and h == len(refs)


def _ref(jh: str, before: list[dict], after: list[dict]) -> str:
    """How a change names its unit; a subtitle without a number by its title."""
    if re.fullmatch(r"TA~?\w+", jh):
        title = next((u["text"] for u in (*before, *after) if u["jh"] == jh and u["text"]), "")
        return f"„{title}” alcím" if title else "alcím"
    return njt.unit_label(jh)


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
        out.append({**c, "ref": _ref(c["jh"], before, after), "before": b, "after": a})
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
        out.append({**c, "ref": _ref(jh, before, after), "before": b, "after": a})
    return out
