"""Parse a committee **jegyzőkönyv** (minutes) PDF into a structured record (BIZ-15).

The committee registry has always held only a *link* to the minutes — the
``jegyzokonyvPath`` upstream prints beside each meeting (BIZ-9/BIZ-12). That is
enough to cite a sitting and nothing more: who actually spoke in committee, what
they said and which agenda point they said it under is the one part of the
House's record that exists only inside those PDFs. The plenary has a JSON API
behind it; committee work has this.

The documents are far more tractable than that makes them sound. Measured over a
spread of sixteen sittings drawn from cycles 40–43, every one of them has the
same five parts in the same order, and the structural markers below are present
in **16 of 16**:

1. a **cover** — the registration number, the sitting's own two numberings, then
   four lines that never vary: the committee (in the genitive), the date, the
   weekday and the start time, the room, and a ``megtartott …`` line that says
   whether the sitting was open;
2. a **Tartalomjegyzék**, each entry ending in its page number, sub-entries
   indented by two — which is what makes the body's headings recoverable at all;
3. a **Napirendi javaslat** — the numbered agenda with each item's iromány
   number, its submitters and the procedural notes in parentheses;
4. **Az ülés résztvevői** — the members present, who chaired, who gave a proxy to
   whom, the secretariat, and the invited guests with their office;
5. the **body**, which is speeches: a speaker line in capitals, then prose until
   the next one, with the agenda headings from part 2 interleaved.

The input is ``pdftotext -layout`` output, as the napirend parser takes
(:mod:`parlamonitor.aktualis.nr`). The layout matters less here than it does
there — minutes are single-column prose — but the indentation is what separates a
heading from the paragraph under it and a sub-entry from its parent, so it is
kept rather than thrown away.

**Nothing here raises on a surprising document** (SCR-5). A part that is missing
is simply absent from the result: a sitting with no guests parses to an empty
guest list, one whose body never matches a heading parses to a single unheaded
section, and a scanned PDF that yields no text at all parses to a record that
says so. The caller records what was found; it is never asked to handle an
exception.
"""

from __future__ import annotations

import re
import unicodedata

MONTHS = {
    "január": 1, "február": 2, "március": 3, "április": 4, "május": 5,
    "június": 6, "július": 7, "augusztus": 8, "szeptember": 9, "október": 10,
    "november": 11, "december": 12,
}

# --- cover -----------------------------------------------------------------

_IKT_RE = re.compile(r"^Ikt\.\s*sz\.?:\s*(.+?)\s*$")
# "TAB-16/2026. sz. ülés" and, in brackets under it, the term-wide numbering
# "(TAB-16/2026-2030. sz. ülés)". Both are upstream's own labels for the same
# sitting; which one a reader recognises depends on which listing they came from.
_SESSNO_RE = re.compile(r"^\(?\s*([A-ZÁÉÍÓÖŐÚÜŰ]{2,5}-[\d/–-]+)\.?\s*sz\.\s*ülés\s*\)?\s*$")
_TITLE_RE = re.compile(r"^Jegyzőkönyv$")
# "az Országgyűlés Mezőgazdasági bizottságának" — but a vizsgálóbizottság's
# cover breaks the line after "az Országgyűlés" and sets the body's name under
# it, so what follows on this line has to be allowed to be nothing.
_COMMITTEE_RE = re.compile(r"^az Országgyűlés\s*(.*?)\s*$")
# "2026. szeptember 3-án, csütörtökön, 14 óra 07 perckor". Almost everything in
# it is optional: the comma before the weekday (cycle 40 omits it), the weekday
# itself (9 of cycle 43's 130 sittings print none), and the minute — which is
# written either "14 óra 07 perckor" or "10.00 órakor". The line can also end
# "…perckor kezdődően".
_WHEN_RE = re.compile(
    # The day takes "-án/-én", except the first of the month, which takes
    # "-jén". The weekday may be bracketed ("13-án (hétfőn) 9 óra") and may
    # carry a stray space before its comma ("kedden , 10 óra"). The hour ends
    # "…kor" for a sitting held when it was called and "…ra"/"…re" for one only
    # *called* for that time ("11 óra 30 percre … összehívott üléséről").
    # The slack at each seam is for the clerks' own typos, which are the last
    # thing standing between six of cycle 40's sittings and a date: an en dash
    # for the hyphen ("27–én"), a stray full stop before the weekday
    # (".szerdán") or inside the time ("13 óra 08.perckor").
    r"^(\d{4})\.\s*([a-záéíóöőúüű]+)\s+(\d{1,2})\s*[-–—]\s*(?:j?[aáeé])n\s*,?\s*"
    r"(?:\(?\.?([a-záéíóöőúüű]+)\)?\s*,?\s*)?"
    r"(\d{1,2})(?:[.:](\d{2}))?\s*"
    r"ór(?:a\s*(?:(\d{1,2})\s*\.?\s*perc)?(?:kor|re|től)|á(?:ra|kor|tól))\b")
# The weekday is printed with the suffix that puts the sitting *on* that day.
# There are seven of them, so they are simply listed rather than stemmed — a
# general rule would turn "csütörtökön" into "csütörtökö".
WEEKDAYS = {
    "hétfőn": "hétfő", "kedden": "kedd", "szerdán": "szerda",
    "csütörtökön": "csütörtök", "pénteken": "péntek", "szombaton": "szombat",
    "vasárnap": "vasárnap",
}
# "megtartott üléséről", but a sitting held away from the House is "összehívott
# üléséről" — and that line is what bounds the cover, so missing it loses the
# venue and the closedness with it.
# Whose minutes these are is stated in the genitive, so a cover line that is
# NOT in the genitive is not the body — it is the parent named above it.
_GENITIVE_RE = re.compile(r"n[ae]k\s*$")
# Searched rather than anchored: where the venue is short enough, it and this
# phrase share a line ("az Országház Esterházy János tanácstermében (földszint
# 1.) megtartott ülésének / 2. napirendi pontjáról"). Anchoring it lost the
# venue, the closedness, and — because the body then began in the wrong place —
# on occasion the whole debate. The search is safe because it only ever runs
# over the cover, which ends at the first section heading.
_HELD_RE = re.compile(r"(?:megtartott|összehívott)\s+(.+?)\s*$")

# --- section headings ------------------------------------------------------

_CONTENTS_RE = re.compile(r"^Tartalomjegyzék$")
_AGENDA_RE = re.compile(r"^Napirendi javaslat$")
_PARTICIPANTS_RE = re.compile(r"^Az ülés résztvevői$")
# A contents entry ends in its page number; the same words standing alone are the
# real heading further down. That difference is the only thing that separates
# "Napirendi javaslat ... 3" in the table of contents from the section itself.
_PAGE_TAIL_RE = re.compile(r"^(.*?)\s{2,}(\d{1,4})$")
# A page number standing alone between the pages. Most are bare ("4"), but a
# minority are dashed ("- 4 -"), and those are not digits-only — left in, they
# glue onto whatever text straddles the page break, which for an agenda is its
# last item's title (63 of the corpus's 12 152).
_PAGENO_RE = re.compile(r"^-?\s*\d{1,4}\s*-?$")

# --- the participants block ------------------------------------------------

_BLOCKS = (
    ("members", re.compile(r"^A bizottság részéről$")),
    ("present", re.compile(r"^Megjelent(ek)?$")),
    ("proxies", re.compile(r"^Helyettesítési megbízást adott$")),
    ("absent", re.compile(r"^(Nem vett részt|Távol volt|Távolmaradását)")),
    ("staff", re.compile(r"^A bizottság titkársága részéről$")),
    ("guests", re.compile(r"^Meghívott(ak)?$")),
    ("speaking_guests", re.compile(r"^Hozzászóló(k|i)?$")),
    ("attending_guests", re.compile(r"^(Megjelent(ek)?|Jelen van(nak)?)$")),
)
_CHAIRS_RE = re.compile(r"^Elnököl:\s*(.+?)\s*$")
# "Dr. Simon Krisztián Márk (TISZA) dr. Bilisics Zitának (TISZA)" — a proxy row
# is two names, the second in the dative. Split on the first ")" so a hyphenated
# or multi-part surname on either side stays whole.
_PROXY_RE = re.compile(r"^(.*?\))\s+(.+)$")

# --- the body --------------------------------------------------------------

# "(Az ülés kezdetének időpontja: 14 óra 07 perc)" — but a partly closed sitting
# times the open part instead ("A nyílt ülés kezdetének időpontja: …"), and the
# opening bracket is sometimes a line further up, so neither the article nor the
# bracket can be required.
# The time runs to the closing bracket, not to the first full stop: it is
# written "13 óra 00 perc" but also "13.00 óra", and stopping at a dot reads the
# second one as the bare hour.
_OPEN_RE = re.compile(r"\(?(?:Az|A nyílt) ülés kezdetének időpontja:?\s*([^)\n]+)")
_CLOSE_RE = re.compile(r"\(?(?:Az|A nyílt) ülés befejezésének időpontja:?\s*([^)\n]+)")
_BODY_START_RE = re.compile(r"^\(?(?:Az|A nyílt) ülés kezdetének időpontja")
# Where the debate stops. Everything after it is the closing furniture — the
# chair's printed signature, their title again, and the clerk who took the
# minutes — which reads as more speech if the body is walked to the end of the
# file, and lands inside whatever the chair said last.
_BODY_END_RE = re.compile(r"^\(?(?:Az|A nyílt) ülés befejezésének időpontja")
# "14 óra 07 perc", but also "13.00 óra" and "13:00" — three spellings of a
# clock that appear across the corpus, and a missed one loses the sitting's
# closing time rather than failing loudly.
_HM_RE = re.compile(r"(\d{1,2})(?:[.:](\d{2})\s*óra|\s*óra(?:\s*(\d{1,2})\s*perc)?)")

# A speaker line: an ALL-CAPS name, optionally a faction in brackets, optionally
# an office in lower case, then a colon. The capitals are what makes it findable
# — committee minutes set every speaker's name in them and nothing else — but
# capitals alone would also catch a sentence opening on an acronym, so the name
# must be **at least two whole words** of two characters or more, each without a
# single lower-case letter in it. That rejects "A NET-COACH-csal kapcsolatban:"
# (one capital word, then lower case) while keeping "SOLYMÁR KÁROLY BALÁZS".
_UPPER = r"A-ZÁÉÍÓÖŐÚÜŰ"
_SPEAKER_RE = re.compile(
    r"^(?P<name>[%s][%s.'\u2019-]*(?:\s+[%s][%s.'\u2019-]*)*)"
    # The colon is as often the last thing on the line as it is followed by the
    # first words of the speech, so what must come after it is "not more text" —
    # a space or the end of the line — rather than a space.
    r"(?P<rest>[^:]{0,300}?):(?!\S)" % (_UPPER, _UPPER, _UPPER, _UPPER))
# The chair speaks as "ELNÖK:" for the rest of the sitting after introducing
# themselves once; these are the single-word speakers that are still speakers.
_BARE_SPEAKERS = {"ELNÖK", "ELNÖKASSZONY", "ELNÖKÚR", "JEGYZŐ"}
# "…, a bizottság elnöke, a továbbiakban ELNÖK:" — the line that says who every
# later bare "ELNÖK:" is. A sitting whose chair is handed over prints a second
# one, so the name is tracked as the body is walked rather than read once.
_HENCEFORTH_RE = re.compile(r"a továbbiakban\s+ELNÖK\b", re.I)
# How far a paragraph's first line is indented. The minutes wrap continuations
# back to the margin, so anything indented at all is a new paragraph — but a
# page break can dedent a wrapped line by a space or two, so the test is a small
# indent rather than "not zero".
_PARA_INDENT = 3
_FACTION_RE = re.compile(r"^\s*\((?P<faction>[^()]{1,40})\)")
_ORG_RE = re.compile(r"\((?P<org>[^()]{2,120})\)\s*$")

# Honorifics dropped from a speaker's name so the loader's person index (which
# holds "Rák Richárd", not "DR. RÁK RICHÁRD") can match it.
_HONORIFIC_RE = re.compile(r"^(?:dr|prof|ifj|id|özv|dr\.-ne|ifj\.)\.?$", re.I)


def _title_case(name: str) -> str:
    """A speaker's ALL-CAPS name as the register spells it.

    Only the case is changed, never the tokens: "DR. SASI-NAGY EDIT" becomes
    "Dr. Sasi-Nagy Edit", with the parts of a hyphenated surname each
    capitalised. Matching to a person is the loader's job and is done on a
    folded form, so this exists purely so the page reads as prose rather than
    as shouting."""
    def _word(w: str) -> str:
        return "-".join(p[:1].upper() + p[1:].lower() if p else p
                        for p in w.split("-"))
    return " ".join(_word(w) for w in name.split())


def _fold(text: str) -> str:
    """Accent- and case-folded, whitespace-collapsed form two strings are
    compared by — how a heading printed in the body is recognised as the entry
    the table of contents already listed."""
    flat = re.sub(r"\s+", " ", text or "").strip()
    decomposed = unicodedata.normalize("NFKD", flat)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _clean(text: str) -> list[tuple[int, str]]:
    """The document as ``(indent, text)`` pairs, page furniture removed.

    Tabs become spaces (``pdftotext`` emits them for wide gaps), trailing space
    goes, and a line that is nothing but a number is dropped — that is the page
    number ``-layout`` centres between the pages, and left in it would land in
    the middle of whatever speech straddles the break."""
    out: list[tuple[int, str]] = []
    for raw in (text or "").replace("\t", " ").replace("\xa0", " ").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if _PAGENO_RE.match(stripped):
            continue
        out.append((len(line) - len(line.lstrip()) if stripped else 0, stripped))
    return out


def _find(lines: list[tuple[int, str]], pattern: re.Pattern, start: int = 0,
          *, bare: bool = False) -> int:
    """Index of the first line matching ``pattern``, or ``-1``.

    ``bare`` additionally demands that the line carry no trailing page number,
    which is what distinguishes a real section heading from its own entry in the
    table of contents a page earlier."""
    for i in range(start, len(lines)):
        text = lines[i][1]
        if not pattern.match(text):
            continue
        if bare and _PAGE_TAIL_RE.match(text):
            continue
        return i
    return -1


# ---------------------------------------------------------------------------
# The cover
# ---------------------------------------------------------------------------

def _parse_cover(lines: list[tuple[int, str]], end: int) -> dict:
    """The five facts the cover states: the registration number, the sitting's
    two numberings, the committee, when and where it sat, and whether it was
    open.

    The committee is kept **as printed** — in the genitive, "Mezőgazdasági
    bizottságának" — and deliberately not turned back into a nominative. The
    two endings collapse ("bizottság" and "bizottsága" are both "bizottságának"
    in the genitive), so un-inflecting it would be a guess, and the identity of
    the committee is not in doubt anyway: the record this is parsed for came
    from that committee's own meeting listing.

    The venue has no marker of its own — it is simply what stands between the
    date and the "megtartott …" line — so it is read by position, which is what
    the layout is preserved for. It can wrap over two lines, and it is not
    reliably indented (the cover is centred, so a line wide enough starts at
    column 0), which is why neither is required.
    """
    out: dict = {}
    body = [(i, indent, text) for i, (indent, text) in enumerate(lines[:end])
            if text]
    when_at = held_at = None
    held_prefix = ""
    for i, indent, text in body:
        m = _IKT_RE.match(text)
        if m:
            out.setdefault("registryNumber", m.group(1))
            continue
        m = _SESSNO_RE.match(text)
        if m:
            # The bracketed one is the term-wide count, the bare one the year's.
            key = "termLabel" if text.startswith("(") else "meetingLabel"
            out.setdefault(key, m.group(1))
            continue
        m = _COMMITTEE_RE.match(text)
        if m and "committeeLabel" not in out:
            name = m.group(1).strip()
            nxt = next((t for j, _, t in body if j > i), "").strip()
            if not name:
                # "az Országgyűlés" alone on its line, the body's name under it
                # — how every vizsgálóbizottság's cover is set.
                name = nxt
            elif not _GENITIVE_RE.search(name) and nxt \
                    and not _WHEN_RE.match(nxt):
                # A subcommittee names its parent first and itself second:
                # "az Országgyűlés Gazdasági Bizottsága" / "Fogyasztóvédelmi
                # Albizottságának". Only the second line is in the genitive, and
                # taking the first alone labels every subcommittee's minutes
                # with its parent's name — the same trap the registry sets with
                # its two-level listing (BIZ-1).
                name = nxt
            if name:
                out["committeeLabel"] = name
            continue
        m = _WHEN_RE.match(text)
        if m:
            year, month, day, weekday, hour, dotmin, permin = m.groups()
            if month.lower() in MONTHS:
                out["date"] = "%s-%02d-%02d" % (year, MONTHS[month.lower()],
                                                int(day))
            if weekday:
                out["weekday"] = WEEKDAYS.get(weekday.lower(), weekday)
            out["startsAt"] = "%02d:%02d" % (int(hour),
                                             int(dotmin or permin or 0))
            when_at = i
            continue
        m = _HELD_RE.search(text)
        # Only once the date has been read: "megtartott" can appear in the
        # registration block above it, and the venue is located by position
        # between the two.
        if m and held_at is None and "date" in out:
            if m.start():
                # What precedes the phrase on the same line is the venue's tail.
                held_prefix = text[:m.start()].strip()
            held = m.group(1)
            out["heldLabel"] = held
            # "megtartott üléséről" vs "megtartott részben zárt ülésének nyílt
            # napirendi pontjáról": a partly closed sitting publishes only the
            # open points, and a reader counting speeches needs to know that
            # what they are counting is not the whole meeting.
            out["closed"] = "zárt" in held
            held_at = i

    if when_at is not None and held_at is not None and held_at >= when_at:
        venue = " ".join(
            [t for j, _, t in body if when_at < j < held_at] + [held_prefix])
        if venue.strip():
            out["venue"] = re.sub(r"\s+", " ", venue).strip()
    return out


# ---------------------------------------------------------------------------
# Table of contents — the body's headings, named in advance
# ---------------------------------------------------------------------------

def _parse_contents(lines: list[tuple[int, str]], start: int,
                    end: int) -> list[dict]:
    """The table of contents as ``{title, page, level}`` entries.

    Every entry ends in a page number, and an entry too long for one line wraps
    with the number on its last line — so lines are accumulated until one
    carries a number. ``level`` is 1 for the sub-entries the document indents
    ("Határozathozatalok" under the bill they were taken on), 0 for the rest;
    the body uses that to tell an agenda point from a stage within one."""
    entries: list[dict] = []
    buffer: list[str] = []
    indent0: int | None = None
    for indent, text in lines[start:end]:
        if not text:
            continue
        m = _PAGE_TAIL_RE.match(text)
        if indent0 is None:
            indent0 = indent
        if m:
            head = m.group(1).strip()
            buffer.append(head)
            title = " ".join(p for p in buffer if p)
            if title:
                entries.append({"title": title, "page": int(m.group(2)),
                                "level": 1 if indent > (indent0 or 0) else 0})
            buffer = []
        else:
            buffer.append(text)
    return entries


# ---------------------------------------------------------------------------
# Napirendi javaslat — the numbered agenda
# ---------------------------------------------------------------------------

_ITEM_RE = re.compile(r"^(\d{1,3})\.\s*(.*)$")
# A wrapped line can open on a number and a full stop without being an item:
# "…a HHSZ 92. § (4) bekezdése alapján)" reads exactly like "92." opening one.
# Two guards, because either alone is escapable: the numbering has to RUN (an
# item is the previous one plus one), and what follows the number must not be a
# word that only ever continues a sentence. The same trap the napirend parser
# guards (`nr._NOT_ORDINAL_RE`), in a different document.
_NOT_ORDINAL_RE = re.compile(
    r"^(?:§|évi|melléklet|cikk|oldal|bekezdés|pont|szám|sz\.)\b", re.I)
# "(T/10267. szám)" / "(T/405. szám)" — the iromány the point is about.
_BILLNO_RE = re.compile(r"\(([A-ZÁÉÍÓÖŐÚÜŰ]/\d+(?:/\d+)?)\.?\s*szám[^)]*\)")
_NOTE_RE = re.compile(r"^\((.+)\)$")


def _opens_item(number: int, rest: str, expected: int | None) -> bool:
    """Whether "``number``." at the start of a line opens an agenda point.

    ``expected`` is the ordinal the next item must carry — ``None`` before the
    first one, which may be any small number (an agenda does not always start at
    1, but it never starts at 92)."""
    if _NOT_ORDINAL_RE.match(rest.strip()):
        return False
    return number == expected if expected is not None else number <= 3


def _balanced(text: str) -> bool:
    """Whether every bracket opened in ``text`` was closed in it."""
    return text.count("(") == text.count(")")


def _parse_agenda(lines: list[tuple[int, str]], start: int,
                  end: int) -> list[dict]:
    """The proposed agenda: one entry per numbered point, with its iromány
    number, the parenthetical notes under it and its submitters.

    A note is any line that is *entirely* one parenthetical — the House writes
    the submitter, the procedural basis and the urgency that way, each on its
    own line — and they are kept verbatim rather than classified, because the
    vocabulary is open and a note nobody anticipated is still worth printing."""
    items: list[dict] = []
    current: dict | None = None
    expected: int | None = None
    for indent, text in lines[start:end]:
        if not text:
            continue
        m = _ITEM_RE.match(text)
        if m and _opens_item(int(m.group(1)), m.group(2), expected):
            expected = int(m.group(1)) + 1
            current = {"ordinal": int(m.group(1)), "title": m.group(2).strip(),
                       "notes": [], "billNumber": None}
            items.append(current)
            continue
        if current is None:
            # A sitting with a **single** agenda point does not number it — the
            # House prints the title straight under the heading. 34 of cycle
            # 43's 130 sittings are of that shape, and requiring an ordinal read
            # every one of them as having no agenda at all.
            current = {"ordinal": None, "title": text, "notes": [],
                       "billNumber": None}
            items.append(current)
            continue
        note = _NOTE_RE.match(text)
        if note and current["title"]:
            current["notes"].append(note.group(1).strip())
        elif note:
            current["title"] = text
        elif current["notes"] and not _balanced(current["notes"][-1]):
            # A note too long for one line. Only an *unclosed* one continues:
            # a line after a note whose brackets are balanced is a new remark
            # of its own ("Nemzetiségi napirendi pont!"), not more of the last.
            current["notes"][-1] += " " + text
        elif current["notes"]:
            current["notes"].append(text)
        else:
            current["title"] += " " + text
    for item in items:
        m = _BILLNO_RE.search(item["title"])
        if m:
            item["billNumber"] = m.group(1)
        item["title"] = re.sub(r"\s+", " ", item["title"]).strip()
        item["notes"] = [re.sub(r"\s+", " ", n).strip() for n in item["notes"]]
    return items


# ---------------------------------------------------------------------------
# Az ülés résztvevői — who was in the room
# ---------------------------------------------------------------------------

_PERSON_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<faction>[^()]{1,40})\)\s*"
                        r"(?:,\s*)?(?P<role>.*)$")
# A proxy is written "X (F) Y-nak (F)": the member who gave it, then the member
# who holds it **in the dative**. Hungarian marks only the last element of a
# name, and lengthens a final -a/-e before the suffix, so undoing it is the
# suffix and that one vowel — "Bilisics Zitának" → "Bilisics Zita",
# "Tiba Istvánnak" → "Tiba István". Worth undoing rather than storing as
# written: the loader matches these names against the register, and nothing in
# it is spelled in the dative.
_DATIVE_RE = re.compile(r"(n[ae]k)$")
_LENGTHENED = {"á": "a", "é": "e"}


def _undative(name: str) -> str:
    """A name written in the dative, back in the nominative."""
    words = (name or "").split(" ")
    if not words:
        return name
    last = words[-1]
    stem = _DATIVE_RE.sub("", last)
    if stem == last or len(stem) < 3:
        return name
    if stem[-1] in _LENGTHENED:
        stem = stem[:-1] + _LENGTHENED[stem[-1]]
    return " ".join(words[:-1] + [stem])


def _person(text: str) -> dict:
    """One name from the participants block, split into name / faction / role.

    Three shapes appear and all three are one pattern: "Berki Ákos (TISZA)",
    "Dr. Bilisics Zita (TISZA), a bizottság alelnöke" and, for a guest who holds
    no seat, "Dr. Stumpf Péter államtitkár (Vidék- és Településfejlesztési
    Minisztérium)". The bracket carries a faction in the first two and an
    organisation in the third; which it is is decided by the caller, which knows
    whose block the name stood in."""
    flat = re.sub(r"\s+", " ", text).strip().rstrip(",")
    m = _PERSON_RE.match(flat)
    if not m:
        return {"name": flat, "faction": None, "role": None}
    return {"name": m.group("name").strip(),
            "faction": m.group("faction").strip(),
            "role": (m.group("role") or "").strip() or None}


def _guest(text: str) -> dict:
    """A guest: their name, the faction or office they hold, and the body they
    hold it in.

    The office is not bracketed and the organisation is, so what is left of the
    line once the trailing bracket is taken off it is "name + title" — and the
    title always begins at the first lower-case word, since names are
    capitalised and offices are not.

    A guest is not always an outsider, though. An MP invited to a committee they
    do not sit on is listed here as "Dr. Latorcai Csaba (KDNP) országgyűlési
    képviselő", and that bracket is their **faction**, not an organisation: it
    stands directly after the name rather than at the end of an office. Reading
    it as an organisation left 62 of cycle 43's 583 guest rows with a closing
    bracket stuck on the end of the person's name.
    """
    flat = re.sub(r"\s+", " ", text).strip().rstrip(",")
    org = None
    m = _ORG_RE.search(flat)
    if m:
        org = m.group("org").strip()
        flat = flat[:m.start()].strip()
    words = flat.split(" ")
    cut = len(words)
    for i, w in enumerate(words):
        if i and w[:1].islower() and not _HONORIFIC_RE.match(w):
            cut = i
            break
    name = " ".join(words[:cut]).strip().rstrip(",")
    title = " ".join(words[cut:]).strip() or None
    faction = None
    m = re.search(r"\(([^()]{1,40})\)\s*$", name)
    if m:
        # "Dr. Latorcai Csaba (KDNP) országgyűlési képviselő" — a bracket that
        # sits between the name and an office is the faction.
        faction = m.group(1).strip()
        name = name[:m.start()].strip().rstrip(",")
    elif org and not title:
        # "Polgár György (TISZA)" — nothing follows the bracket at all, so what
        # was taken for an organisation a moment ago is the only thing it can
        # be: the faction of an MP sitting in as a guest.
        faction, org = org, None
    return {"name": name, "faction": faction, "title": title, "org": org}


# How many lines one participant may run to. A guest with a long office and a
# long organisation takes two; nothing in the corpus takes three, and a cap is
# what stops an unclosed bracket from swallowing the rest of the block.
_ENTRY_MAX_LINES = 3


def _joined_entries(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """The participants block with each entry on one line again.

    A participant is one line unless their brackets say otherwise: "Dr. Stumpf
    Péter államtitkár (Vidék- és Településfejlesztési" continues on the next
    line with "Minisztérium)". Split, that is two entries — a guest whose office
    ends mid-name and a guest called "Minisztérium)" — so lines are accumulated
    until the brackets balance. A line with no bracket at all is already
    balanced and emits immediately, which is every member row.
    """
    out: list[tuple[int, str]] = []
    pending: list[str] = []
    indent0 = 0
    for indent, text in lines:
        if not text:
            continue
        if not pending:
            indent0 = indent
        pending.append(text)
        joined = " ".join(pending)
        if _balanced(joined) or len(pending) >= _ENTRY_MAX_LINES:
            out.append((indent0, re.sub(r"\s+", " ", joined).strip()))
            pending = []
    if pending:
        out.append((indent0, re.sub(r"\s+", " ", " ".join(pending)).strip()))
    return out


def _parse_participants(lines: list[tuple[int, str]], start: int,
                        end: int) -> dict:
    """The participants block, cut into its named sub-lists.

    The document's own labels are the structure — "A bizottság részéről",
    "Megjelent", "Helyettesítési megbízást adott", "A bizottság titkársága
    részéről", "Meghívott" — and they nest: "Megjelent" under the committee
    means members present, the same word under "Meghívott" means guests who
    attended without speaking. So the labels are read in order and the two that
    repeat are resolved by which side of "Meghívott" they fall on.

    ``chairs`` is pulled out of "Elnököl:", which names who presided. It is the
    only participant line the body needs: every later bare "ELNÖK:" is one of
    these people."""
    out: dict = {"chairs": [], "present": [], "proxies": [], "absent": [],
                 "staff": [], "guests": []}
    bucket = "present"
    in_guests = False
    for indent, text in _joined_entries(lines[start:end]):
        m = _CHAIRS_RE.match(text)
        if m:
            out["chairs"].append(_person(m.group(1)))
            # Whoever chairs is also present; the document does not list them
            # twice, and a roster that omitted the chair would be wrong.
            out["present"].append(_person(m.group(1)))
            continue
        label = next((name for name, pat in _BLOCKS if pat.match(text)), None)
        if label is not None:
            if label == "guests":
                in_guests = True
                bucket = "guests"
            elif label == "members":
                in_guests = False
                bucket = "present"
            elif label in ("speaking_guests", "attending_guests"):
                bucket = "guests"
            elif label == "present":
                bucket = "guests" if in_guests else "present"
            else:
                bucket = label
            continue
        if bucket == "proxies":
            flat = re.sub(r"\s+", " ", text).strip()
            m = _PROXY_RE.match(flat)
            if m:
                held = _person(m.group(2))
                held["name"] = _undative(held["name"])
                out["proxies"].append({"absent": _person(m.group(1)),
                                       "heldBy": held})
            else:
                out["proxies"].append({"absent": _person(flat), "heldBy": None})
        elif bucket == "guests":
            out["guests"].append(_guest(text))
        elif bucket == "staff":
            out["staff"].append(_guest(text))
        else:
            out[bucket].append(_person(text))
    # The chair is appended to `present` above; a document that also lists them
    # in the roster would then carry them twice.
    seen: set[str] = set()
    out["present"] = [p for p in out["present"]
                      if not (_fold(p["name"]) in seen
                              or seen.add(_fold(p["name"])))]
    return out


# ---------------------------------------------------------------------------
# The body — speeches under headings
# ---------------------------------------------------------------------------

class _Headings:
    """Recognises a heading printed in the body as one the contents listed.

    A heading wraps across lines exactly as it did in the table of contents but
    breaks in different places, so it cannot be matched line by line. Instead
    each candidate line is tested as the **start** of a known heading; while the
    text accumulated so far is still a prefix of one, more lines are taken, and
    when it becomes equal to one the heading is complete. That both finds the
    heading and says how many lines it spanned, without ever needing the two
    documents to agree on where the line breaks fall."""

    def __init__(self, entries: list[dict]):
        self.exact: dict[str, dict] = {}
        self.prefixes: set[str] = set()
        for e in entries:
            key = _fold(e["title"])
            if not key:
                continue
            self.exact.setdefault(key, e)
            for i in range(1, len(key) + 1):
                self.prefixes.add(key[:i])

    def match(self, lines: list[tuple[int, str]], i: int,
              limit: int = 6) -> tuple[dict, int] | None:
        """``(entry, lines_consumed)`` if a heading starts at ``i``."""
        acc = ""
        for n in range(limit):
            if i + n >= len(lines):
                return None
            text = lines[i + n][1]
            if not text:
                return None
            acc = (acc + " " + text).strip() if acc else text
            key = _fold(acc)
            if key in self.exact:
                return self.exact[key], n + 1
            if key not in self.prefixes:
                return None
        return None


def _split_speaker(prefix: str, rest: str) -> dict:
    """The speaker of one speech, from the capitals and whatever follows them.

    The bracket directly after the name is a faction ("(TISZA)", "(független)");
    a bracket at the very end of a lower-case office is the organisation the
    office is held in ("államtitkár (Igazságügyi Minisztérium)"). Between them
    sits the office itself, kept as written."""
    tail = re.sub(r"\s+", " ", rest or "").strip()
    faction = None
    m = _FACTION_RE.match(tail)
    if m:
        faction = m.group("faction").strip()
        tail = tail[m.end():].strip()
    org = None
    m = _ORG_RE.search(tail)
    if m:
        org = m.group("org").strip()
        tail = tail[:m.start()].strip()
    role = tail.strip(" ,") or None
    return {"name": _title_case(re.sub(r"\s+", " ", prefix).strip()),
            "faction": faction, "role": role, "org": org}


# A capitalised name run with no colon after it: the start of a speaker line
# whose office was too long to fit, so the colon fell to the next line. Twenty-
# five of them in a sixteen-sitting sample — every long title with an
# organisation after it ("ARANYOSNÉ DR. BÖRCS JANKA főigazgató (Nemzeti Média-
# és Hírközlési / Hatóság):").
_WRAPPED_RE = re.compile(r"^[%s][%s.'\u2019-]*(?:\s+[%s][%s.'\u2019-]*)+"
                         % (_UPPER, _UPPER, _UPPER, _UPPER))


def _speaker_at(line: str) -> tuple[dict, str] | None:
    """``(speaker, first line of what they said)`` if ``line`` opens a speech.

    The capitals are the signal, but capitals alone are not enough — a sentence
    can open on an acronym — so a name must be two whole capitalised words, with
    the chair's own bare "ELNÖK:" allowed as the one single-word exception."""
    m = _SPEAKER_RE.match(line)
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group("name")).strip()
    words = [w for w in name.split(" ") if w]
    solid = [w for w in words if len(w.rstrip(".")) >= 2]
    if len(solid) < 2 and name.replace(".", "") not in _BARE_SPEAKERS:
        return None
    return _split_speaker(name, m.group("rest")), line[m.end():]


def _parse_body(lines: list[tuple[int, str]], start: int,
                headings: _Headings,
                chair: str | None = None) -> tuple[list[dict], list[dict]]:
    """The body as ``(sections, speeches)``.

    A section is a heading from the table of contents and everything under it; a
    speech is a speaker line and the prose that follows until the next speaker
    or the next heading. Both carry each other's index, so the page can show a
    sitting either way round — the agenda with its debate under each point, or
    the debate with the point each speech was made under.

    ``chair`` is who the participants block said presided, and it is what a
    bare "ELNÖK:" means in a sitting whose body never introduces them — which is
    most older ones. Without it those speeches are credited to a person called
    "Elnök", and a committee's most prolific speaker becomes nobody.

    Text before the first speaker (the "(Az ülés kezdetének időpontja: …)"
    stage direction, and on rare occasions a note from the clerk) is kept as the
    section's ``preamble`` rather than thrown away or credited to whoever speaks
    next."""
    sections: list[dict] = []
    speeches: list[dict] = []
    current: dict | None = None
    chair_name: str | None = chair
    # Who spoke last before a heading interrupted them. The chair routinely
    # carries straight on after one — they announce the next agenda point and
    # keep talking — without their name being printed again, and dropping that
    # text would silently lose most of what the chair says.
    last_speaker: dict | None = None
    buffer: list[tuple[int, str]] = []
    preamble: list[tuple[int, str]] = []

    def _flush() -> None:
        if current is not None and buffer:
            current["text"] = _paragraphs(buffer)
        buffer.clear()

    i = start
    while i < len(lines):
        indent, text = lines[i]
        if text and _BODY_END_RE.match(text):
            break
        if not text:
            i += 1
            continue

        hit = headings.match(lines, i)
        # A heading is only a heading where it stands on its own: the same words
        # appear inside a speech ("Soron következik 1. napirendi pontunk, a
        # megye és …"), and there they are prose. Standing alone means starting
        # its own line at an indent the body's paragraphs do not use.
        if hit and indent >= 4:
            entry, used = hit
            _flush()
            if current is not None:
                last_speaker = {k: current[k] for k in
                                ("name", "faction", "role", "org", "chair")}
            current = None
            # Unattributed text under the heading that is now closing. Handed to
            # that section before a new one opens — clearing it here instead
            # silently dropped every word of a sitting whose clerk named no
            # speakers at all, which is how a few of them are written.
            if preamble and sections:
                sections[-1]["preamble"] = _paragraphs(preamble)
            preamble = []
            sections.append({"title": entry["title"], "level": entry["level"],
                             "page": entry.get("page"), "preamble": None,
                             "speechCount": 0})
            i += used
            continue

        opened = _speaker_at(text) if indent >= 2 else None
        used_lines = 1
        if opened is None and indent >= 2 and ":" not in text \
                and _WRAPPED_RE.match(text) and i + 1 < len(lines) \
                and lines[i + 1][1] and lines[i + 1][0] < _PARA_INDENT:
            # The speaker line wrapped: the name is here and the colon that ends
            # it is on the next line. Left unjoined, the speech is credited to
            # nobody and this line is swallowed into the previous speaker's text.
            joined = _speaker_at(text + " " + lines[i + 1][1])
            if joined is not None:
                opened, used_lines = joined, 2
        if opened:
            speaker, first = opened
            _flush()
            if _HENCEFORTH_RE.search(speaker.get("role") or ""):
                # "…, a továbbiakban ELNÖK" — every bare ELNÖK after this is
                # this person, until another line says otherwise.
                chair_name = speaker["name"]
                speaker["role"] = re.sub(
                    r",?\s*a továbbiakban\s+ELNÖK\s*$", "",
                    speaker["role"], flags=re.I).strip(" ,") or None
            is_chair = speaker["name"].replace(".", "").upper() in _BARE_SPEAKERS
            if is_chair:
                speaker["name"] = chair_name or speaker["name"]
            if not sections:
                sections.append({"title": None, "level": 0, "page": None,
                                 "preamble": None, "speechCount": 0})
            if preamble:
                sections[-1]["preamble"] = _paragraphs(preamble)
                preamble = []
            current = {"ord": len(speeches), "section": len(sections) - 1,
                       "chair": is_chair or bool(chair_name
                                                 and speaker["name"] == chair_name),
                       "continued": False, "text": "", **speaker}
            last_speaker = None
            speeches.append(current)
            sections[-1]["speechCount"] += 1
            # The speaker line opens the first paragraph, whatever its own
            # indent was — it carried the name, not a continuation.
            buffer.append((_PARA_INDENT, first.strip()))
            i += used_lines
            continue

        if current is None and last_speaker is not None and sections:
            # Prose under a heading with nobody newly introduced: the speaker
            # the heading interrupted is still speaking. It opens a new speech
            # rather than extending the old one, because it belongs to the new
            # agenda point — the same person, contributing again.
            current = {"ord": len(speeches), "section": len(sections) - 1,
                       "continued": True, "text": "", **last_speaker}
            speeches.append(current)
            sections[-1]["speechCount"] += 1
            buffer.append((indent, text))
        elif current is None:
            preamble.append((indent, text))
        else:
            buffer.append((indent, text))
        i += 1

    _flush()
    if preamble and sections:
        sections[-1]["preamble"] = _paragraphs(preamble)
    return sections, speeches


def _paragraphs(buffer: list[tuple[int, str]]) -> str:
    """One speech's lines rejoined into paragraphs.

    ``pdftotext`` breaks prose at the PDF's own line width, so almost every
    newline inside a speech is an artefact and joining on a space is right.
    What is **not** an artefact is the indent: the minutes open every paragraph
    with one and wrap every continuation back to the margin, so the indent is
    the paragraph break, and it is the only one these documents have — they
    carry no blank lines inside a speech at all.

    A centred stage direction ("(Szavazás.)") is indented far past a paragraph
    opener and so stands as a paragraph of its own, which is how it reads on
    the page.
    """
    paras: list[list[str]] = []
    for indent, line in buffer:
        if not line:
            continue
        if indent >= _PARA_INDENT or not paras:
            paras.append([line])
        else:
            paras[-1].append(line)
    return "\n\n".join(
        re.sub(r"\s+", " ", " ".join(p)).strip() for p in paras if p).strip()


# ---------------------------------------------------------------------------
# The whole document
# ---------------------------------------------------------------------------

def parse(text: str) -> dict:
    """One jegyzőkönyv's ``pdftotext -layout`` output as a structured record.

    Never raises on a surprising document (SCR-5): a part that is not there is
    absent from the result, and ``stats`` says what was found, so a caller can
    tell a sitting with no guests from a document this failed to read.
    """
    lines = _clean(text)
    out: dict = {"cover": {}, "contents": [], "agenda": [], "participants": {},
                 "sections": [], "speeches": []}
    if not lines:
        out["stats"] = _stats(out)
        return out

    # The five parts, located by their own headings. Each is bounded by the next
    # one that was actually found, so a document missing a part simply hands its
    # lines to the part that follows (and a missing *last* boundary runs to the
    # end) rather than losing everything after it.
    i_contents = _find(lines, _CONTENTS_RE)
    i_agenda = _find(lines, _AGENDA_RE, max(i_contents, 0), bare=True)
    i_people = _find(lines, _PARTICIPANTS_RE, max(i_agenda, 0), bare=True)
    i_body = _find(lines, _BODY_START_RE, max(i_people, 0))
    if i_body < 0:
        # No opening stage direction: the body still starts where the first
        # person speaks, and without this the participants block would run to
        # the end of the document and swallow every speaker into the guest list.
        # Searched from the line after the participants heading where there is
        # one and from the very first line where there is not — `i_people + 1`
        # over a missing heading (-1) would start at 0, but `max(…, 0) + 1`
        # starts at 1 and loses a document whose first line is its first
        # speaker.
        i_body = _first_speaker(lines, i_people + 1 if i_people >= 0 else 0)

    bounds = [b for b in (i_contents, i_agenda, i_people, i_body) if b >= 0]
    out["cover"] = _parse_cover(lines, bounds[0] if bounds else len(lines))

    if i_contents >= 0:
        stop = next((b for b in (i_agenda, i_people, i_body) if b > i_contents),
                    len(lines))
        out["contents"] = _parse_contents(lines, i_contents + 1, stop)
    if i_agenda >= 0:
        stop = next((b for b in (i_people, i_body) if b > i_agenda), len(lines))
        out["agenda"] = _parse_agenda(lines, i_agenda + 1, stop)
    if i_people >= 0:
        stop = i_body if i_body > i_people else len(lines)
        out["participants"] = _parse_participants(lines, i_people + 1, stop)

    # The body starts at the opening stage direction where there is one, and
    # otherwise at the first speaker line after the participants: a sitting whose
    # clerk left the direction out still has a debate, and dropping it because a
    # bracketed line is missing would lose the whole document (SCR-5).
    if i_body >= 0:
        start = i_body
        out["sections"], out["speeches"] = _parse_body(
            lines, i_body, _Headings(out["contents"] + [
                {"title": a["title"], "level": 0, "page": None}
                for a in out["agenda"]]),
            chair=next((c["name"] for c in
                        (out["participants"].get("chairs") or []) if c.get("name")),
                       None))

    whole = "\n".join(t for _, t in lines)
    m = _OPEN_RE.search(whole)
    if m:
        out["cover"].setdefault("openedAt", _clock(m.group(1)))
    m = _CLOSE_RE.search(whole)
    if m:
        out["cover"]["closedAt"] = _clock(m.group(1))
    out["stats"] = _stats(out)
    return out


def _first_speaker(lines: list[tuple[int, str]], start: int) -> int:
    for i in range(max(start, 0), len(lines)):
        indent, text = lines[i]
        if text and indent >= 2 and _speaker_at(text):
            return i
    return -1


def _clock(raw: str) -> str | None:
    """"14 óra 07 perc" as "14:07"."""
    m = _HM_RE.search(raw or "")
    if not m:
        return None
    return "%02d:%02d" % (int(m.group(1)), int(m.group(2) or m.group(3) or 0))


def _stats(out: dict) -> dict:
    """What the parse found — the numbers a caller records to tell a thin
    document from a failed one."""
    speeches = out["speeches"]
    speakers = {s["name"] for s in speeches if s.get("name")}
    return {
        "agendaItems": len(out["agenda"]),
        "sections": len(out["sections"]),
        "speeches": len(speeches),
        "speakers": len(speakers),
        "chars": sum(len(s.get("text") or "") for s in speeches),
        "present": len((out["participants"] or {}).get("present") or []),
        "guests": len((out["participants"] or {}).get("guests") or []),
        "proxies": len((out["participants"] or {}).get("proxies") or []),
    }
