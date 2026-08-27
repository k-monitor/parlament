"""Parse the 1994–98 (cycle 35) irományok off parlament.hu's static archive.

The Felicitas ``iromany`` API that feeds :mod:`parlamonitor.bills.scrape` knows
nothing about the 35th cycle: the same query that returns 19 408 documents for
cycle 37 returns **zero** for 1994–98. What survives from that term is the
original static site the House published at the time — plain hand-generated HTML
under ``/iromany/``, still served, and the only source there is:

    irom.htm                index of the listings
    tvossz.htm              every törvényjavaslat / határozati javaslat (1 659)
    intossz.htm             every interpelláció / kérdés / azonnali kérdés (3 987)
    <NNNNN>ir.htm           one document's adatlap (submitters, status, events)
    mod/<NNNNN>imo.htm      its non-self-standing motions (módosítók)
    felsz/<NNNNN>npl.htm    the speeches held on it, numbered per sitting day
    fulltext/<NNNNN>txt.htm its full text

The two "Összes" listings together cover the numbering with no gaps (1 659 +
3 987 = 5 646, the highest iromány number of the cycle), so they are the whole
corpus; the per-status listings (``tvkih.htm``, ``tvelut.htm``, …) are just
filtered views of ``tvossz.htm`` — they cover only 1 298 of its rows and add no
field the adatlap doesn't carry, so this module ignores them.

Everything is mapped onto the record shape :func:`parlamonitor.bills.scrape.
fetch_bills` produces, so the registry this writes is a drop-in ``bills-35.json``
that the existing loader ingests with no special-casing. What the era simply did
not record (the stage diagram, vote tallies, deadlines) stays empty rather than
being guessed at.

**Encoding.** The pages are ISO-8859-2 (Latin-2), as the one page that bothers to
declare a charset says. Almost every accented letter is an HTML entity rather
than a raw byte, and the entity *names* were picked for the byte values, not the
characters: ``&otilde;``/``&ucirc;`` are Latin-1 õ/û, whose code points hold ő/ű
in Latin-2. Unescaping alone therefore renders "mûködése" where the page means
"működése", so :func:`decode` folds those four long-umlaut vowels back — see
:data:`_LATIN1_HUNGARIAN`.
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from ..http_client import CaptchaWall

logger = logging.getLogger(__name__)

CYCLE = 35
BASE = "https://www.parlament.hu/iromany"
# The two "Összes" listings, named rather than discovered off irom.htm: the
# archive is frozen, so the index can only ever tell us what is hard-coded here.
LIST_PAGES = ("tvossz.htm", "intossz.htm")

SOURCE = "parlament-hu-iromany-static-1994-98"

# --- encoding --------------------------------------------------------------
# The four vowels whose Latin-1 entity name (õ û Õ Û) stands for the Latin-2
# character at the same code point (ő ű Ő Ű). Every other entity on these pages
# (&aacute;, &ouml;, &uuml;, …) denotes the same letter in both charsets, so
# only these need folding — which is also why a plain html.unescape() looks
# *almost* right and is quietly wrong on exactly the letters that make Hungarian
# Hungarian.
_LATIN1_HUNGARIAN = str.maketrans({"õ": "ő", "û": "ű", "Õ": "Ő", "Û": "Ű"})


def decode(raw: bytes) -> str:
    """Decode one archive page to correctly-accented Hungarian text."""
    return html.unescape(raw.decode("iso-8859-2", "replace")).translate(
        _LATIN1_HUNGARIAN)


# --- small text helpers ----------------------------------------------------

_TAG_RE = re.compile(r"<[^>]*>")
_SPLIT_P_RE = re.compile(r"<p>", re.I)
_LI_RE = re.compile(r"<li>", re.I)
_BR_RE = re.compile(r"<br>", re.I)
_MP_HREF_RE = re.compile(r"elet/([0-9a-z]+)\.html?", re.I)
_HREF_RE = re.compile(r"<a\s[^>]*href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>", re.I | re.S)
_DATE_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})\.?")
# A vote link's caption: "98.03.16.11:51:32" — two-digit year, local wall clock.
_VOTE_STAMP_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{2})\.(\d{2}):(\d{2}):(\d{2})")


def text_of(fragment: str) -> str:
    """Tags stripped, whitespace collapsed — the visible text of a fragment."""
    return " ".join(_TAG_RE.sub(" ", fragment or "").split())


def _iso_date(raw: str | None) -> str | None:
    """``"1998.03.16."`` → ``"1998-03-16"``; ``None`` if there is no date in it.

    Date-only on purpose: the archive records days, not timestamps, and the
    loader only ever reads ``submitted_date[:10]``."""
    m = _DATE_RE.search(raw or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _iso_vote_stamp(caption: str) -> str | None:
    """A vote link's ``"98.03.16.11:51:32"`` caption → a UTC ISO timestamp.

    The caption is Budapest wall-clock time, while every date the Felicitas API
    emits is UTC; converting keeps the two sources comparable (and makes the
    site render the minute the vote was actually held). Without a tz database
    the offset is unknowable, so the naive local time is emitted rather than a
    wrong instant."""
    m = _VOTE_STAMP_RE.search(caption or "")
    if not m:
        return None
    yy, mo, dd, hh, mi, ss = (int(g) for g in m.groups())
    try:
        # The whole archive is 1994-98; a two-digit year needs no wider guess.
        local = datetime(1900 + yy, mo, dd, hh, mi, ss)
    except ValueError:
        logger.warning("Unusable vote timestamp %r", caption)
        return None
    try:
        from zoneinfo import ZoneInfo
        return local.replace(tzinfo=ZoneInfo("Europe/Budapest")).astimezone(
            timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:  # no tz database on this host — keep the local reading
        logger.debug("No tz database; keeping local time for vote stamp %r", caption)
        return local.isoformat(timespec="seconds")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _abs(href: str, base: str = BASE + "/") -> str:
    """One page's relative href made absolute.

    Links are relative to the *page's own* directory, and the archive has three:
    the listings and adatlapok sit in ``/iromany/`` while the motion and speaker
    listings sit a level down in ``/iromany/mod/`` and ``/iromany/felsz/``, from
    where ``../`` reaches back into ``/iromany/`` and ``../../`` to the site root
    (MP CVs, roll-call sheets, the naplo). Passing the wrong base silently yields
    a plausible-looking URL that 404s, so every caller states its own."""
    return urljoin(base, href)


def _page_base(*segments: str) -> str:
    """The base URL of an archive sub-directory, e.g. ``_page_base("mod")``."""
    return "/".join((BASE, *segments)) + "/"


# Committee block headers are set in the fixed-width all-caps of the 1998 report
# generator ("MEZŐGAZDASÁGI BIZOTTSÁG"), which is typography, not the name; the
# Hungarian convention capitalises only the first word. Every one of the ~17
# committee names seen across the cycle is a word rather than an acronym, so
# lower-casing the tail cannot mangle one.
def _committee_name(raw: str) -> str | None:
    name = " ".join((raw or "").split())
    if not name:
        return None
    return name.capitalize() if name.isupper() else name


# --- the listings ----------------------------------------------------------
# One row per document, wrapped in <p> and laid out as four ---separated fields:
#
#   <p>  <a href='05638ir.htm'>H/5638</a>  ---  1998.03.16.  ---  személyi  ---
#        <EM>A rádió és televízió testületek…</EM>
#
# The interpellation listing breaks the same four fields across lines and drops
# the <EM>, so the fields are read off the tag-stripped text rather than off the
# markup.

_ROW_RE = re.compile(
    r"<a\s[^>]*href=['\"](?P<href>(?P<num>\d+)ir\.html?)['\"]>(?P<number>[^<]+)</a>"
    r"(?P<rest>.*)", re.S | re.I)


def parse_listing(page: str) -> list[dict]:
    """Rows of one "Összes" listing: number, submission date, type, title."""
    rows: list[dict] = []
    for chunk in _SPLIT_P_RE.split(page):
        m = _ROW_RE.search(chunk)
        if not m:
            continue
        fields = [f.strip() for f in text_of(m.group("rest")).split("---")]
        # fields[0] is the empty run before the first separator.
        number = " ".join(m.group("number").split())
        rows.append({
            "number": number,
            "num": m.group("num"),
            "href": m.group("href"),
            "submittedDate": _iso_date(fields[1]) if len(fields) > 1 else None,
            "type": fields[2] or None if len(fields) > 2 else None,
            "title": " --- ".join(f for f in fields[3:] if f) or None,
        })
    return rows


# --- the adatlap (<NNNNN>ir.htm) -------------------------------------------
# Head: a <UL> of up to four resource links (full text / motions / vote /
# speakers, each a bare label when the resource doesn't exist), then a run of
# <p>-separated "Label (date): value" lines. Tail: <EM>-introduced blocks — one
# "ESEMÉNYEK" block of plenary events and one block per committee, each a run of
# <br>-separated "date --- event [--- related]" lines.

# "T/4754 -- törvényjavaslat", sometimes with a remark after a second dash run:
# a document replaced by a new version says so right here ("--- Új változat 45.
# számon."). The remark separator is split on separately rather than folded into
# this pattern, because a subtype can itself contain a hyphen ("határozati
# javaslat (hat. n. szerz.-ről)") — but never a *spaced* dash run.
_HEADER_LINE_RE = re.compile(r"^(?P<number>\S+/\d+)\s*--\s*(?P<rest>.+)$")
_HEADER_REMARK_RE = re.compile(r"\s+---+\s+")
_LABELLED_RE = re.compile(
    r"^(?P<label>[^:(]{1,40}?)(?:\s*\((?P<date>[^)]*)\))?\s*:\s*(?P<value>.*)$")
_MOTION_COUNT_RE = re.compile(r"\((\d+)\s*db\)")
_MK_NUMBER_RE = re.compile(r"Magyar Közlöny\s*(\d+)")
_EM_RE = re.compile(r"<em>", re.I)
_FIRST_PLACE_RE = re.compile(r"első helyen kijelölt", re.I)

# The resource links in the head <UL>, keyed by the label the row starts with.
_UL_LABELS = {
    "Teljes szöveg": "textUrl",
    "Módosítók": "motionsUrl",
    "Felszólalók": "speakersUrl",
}


def _sponsors(fragment: str) -> list[dict]:
    """Submitters out of a "Benyújtó(k) (date):" value.

    One submitter is inline; several are an ``<ol>`` of ``<li>``. A submitter is
    either an MP — linked to their static CV page, whose stem (``s322``) *is* the
    ``personID`` the cycle-35 MP registry uses, so the join needs no name
    matching (EXT-2) — or an unlinked institution ("földművelésügyi miniszter",
    "kormány"). The faction in trailing parentheses is kept in the label, the way
    the API renders it, and split out for the faction link."""
    items = _LI_RE.split(fragment)[1:] or [fragment]
    out: list[dict] = []
    for item in items:
        label = text_of(item)
        if not label:
            continue
        m = _MP_HREF_RE.search(item)
        fx = re.search(r"\(([^()]*)\)\s*$", label)
        out.append({
            "personID": m.group(1) if m else None,
            "factionId": None,
            "factionLabel": fx.group(1).strip() if fx else None,
            "committeeId": None,
            "label": label,
        })
    return out


# The archive abbreviates its event names to fit a fixed-width column, and puts
# them in the active voice where the modern API uses the passive: "kérdést
# megválaszolja" is the same event the API calls "kérdés megválaszolva". For the
# events that name a **responding tárca** that difference is not cosmetic — the
# §6C portfolio derivation matches event names exactly, so left as they are the
# cycle's ~4 000 answered questions would contribute nothing to it. Only those
# four are renamed, enumerated rather than pattern-matched so the list stays
# auditable; the archive's own wording is kept on the event's `remark`.
_EVENT_NAMES = {
    "kérdést megválaszolja": "kérdés megválaszolva",
    "kérdést írásban megválaszolja": "kérdés írásban megválaszolva",
    "interpellációt szóban megvál.": "interpelláció szóban megválaszolva",
    "interpellációt írásban megvál.": "interpelláció írásban megválaszolva",
}


def _event_line(line: str) -> dict | None:
    """One ``"1997.09.09.  ---  elnök bejelenti az indítványt"`` history line.

    A third field, where present, names the person or body the event relates to
    ("interpellációt szóban megvál. --- földművelésügyi miniszter") — the same
    thing the API calls ``relatedLabel``, which is what the §6C portfolio
    derivation reads a responding ministry off."""
    parts = [p.strip() for p in text_of(line).split("---")]
    date = _iso_date(parts[0]) if parts else None
    name = parts[1] if len(parts) > 1 else None
    if not date and not name:
        return None
    canonical = _EVENT_NAMES.get(name)
    return {
        "date": date,
        "name": canonical or name or None,
        "personID": None,
        "committeeId": None,
        "relatedLabel": " ".join(parts[2:]).strip() or None if len(parts) > 2 else None,
        "speechNumber": None,
        "speechId": None,
        "voteId": None,
        "remark": name if canonical else "",
    }


def parse_adatlap(page: str) -> dict:
    """One document's adatlap page, as far as it is stated on it.

    Returns the raw reading — resource links, the labelled header lines, the
    plenary event history, and one entry per committee block. Mapping it onto
    the API record shape is :func:`_record`'s job."""
    out: dict = {
        "titleFull": None, "subtype": None, "submittedDate": None,
        "sponsors": [], "status": None, "statusDate": None,
        "negotiationMode": None, "committeeStatus": None, "addressee": None,
        "remark": None,
        "promulgationNumber": None, "mkNumber": None,
        "textUrl": None, "motionsUrl": None, "motionCount": 0,
        "speakersUrl": None, "votes": [],
        "events": [], "committeeEvents": [], "committees": [],
    }

    m = re.search(r"<h2>(.*?)</h2>", page, re.I | re.S)
    if m:
        out["titleFull"] = _strip_final_dot(text_of(m.group(1))) or None

    # --- head <UL>: the resource links ------------------------------------
    ul = re.search(r"<ul>(.*?)</ul>", page, re.I | re.S)
    if ul:
        for item in _LI_RE.split(ul.group(1))[1:]:
            flat = text_of(item)
            if flat.startswith("Szavazás"):
                # Zero or more roll-call links, captioned with the wall-clock
                # time the vote was taken; the tallies were never published here.
                for href, caption in _HREF_RE.findall(item):
                    out["votes"].append({
                        "voteId": re.sub(r"\.html?$", "", href.rsplit("/", 1)[-1]),
                        "date": _iso_vote_stamp(text_of(caption)),
                        "subject": None, "yes": None, "no": None,
                        "abstain": None, "result": None,
                        "sourceUrl": _abs(href),
                    })
                continue
            for prefix, key in _UL_LABELS.items():
                if not flat.startswith(prefix):
                    continue
                if key == "motionsUrl":
                    cnt = _MOTION_COUNT_RE.search(flat)
                    out["motionCount"] = int(cnt.group(1)) if cnt else 0
                links = _HREF_RE.findall(item)
                if links:
                    out[key] = _abs(links[0][0])
                break

    # --- head: the labelled lines -----------------------------------------
    head = _EM_RE.split(page, maxsplit=1)[0]
    head = re.split(r"</ul>", head, maxsplit=1, flags=re.I)[-1]
    for chunk in _SPLIT_P_RE.split(head):
        flat = text_of(chunk)
        if not flat:
            continue
        hm = _HEADER_LINE_RE.match(flat)
        if hm:
            parts = _HEADER_REMARK_RE.split(hm.group("rest"), maxsplit=1)
            out["subtype"] = parts[0].strip() or None
            out["remark"] = (parts[1].strip() if len(parts) > 1 else None) or None
            continue
        lm = _LABELLED_RE.match(flat)
        if not lm:
            continue
        label = lm.group("label").strip()
        value = lm.group("value").strip()
        when = _iso_date(lm.group("date"))
        if label.startswith("Benyújtó"):          # "Benyújtó" / "Benyújtók"
            out["submittedDate"] = when
            # The raw chunk, not `value`: the submitters are links.
            out["sponsors"] = _sponsors(chunk.split(":", 1)[-1])
        elif label.startswith("Eljárás"):
            out["negotiationMode"] = value or None
        elif label.startswith("Plenáris állapot"):
            # "kihirdetve --- XXVII.tv, Magyar Közlöny 28.szám"
            status, _, tail = value.partition("---")
            out["status"] = status.strip() or None
            out["statusDate"] = when
            tail = tail.strip()
            if tail:
                out["promulgationNumber"] = tail.split(",")[0].strip() or None
                mk = _MK_NUMBER_RE.search(tail)
                out["mkNumber"] = mk.group(1) if mk else None
        elif label.startswith("Bizottsági állapot"):
            out["committeeStatus"] = value or None
        elif label.startswith("Kihez szól"):
            out["addressee"] = value or None
        else:
            logger.debug("Unrecognised adatlap line: %r", flat)

    # --- tail: the <EM> blocks --------------------------------------------
    for block in _EM_RE.split(page)[1:]:
        lines = _BR_RE.split(block)
        header = text_of(lines[0])
        events = [e for e in (_event_line(l) for l in lines[1:]) if e]
        if "ESEMÉNYEK" in header.upper():
            out["events"].extend(events)
            continue
        if not header.startswith("="):
            logger.debug("Unrecognised adatlap block: %r", header[:80])
            continue
        name, _, status = header.strip("= ").partition(":")
        committee = _committee_name(name)
        if not committee:
            continue
        out["committees"].append({
            "committee": committee,
            "committeeId": None,
            # The archive never states the negotiating role as such; the
            # designation event does ("első helyen kijelölt bizottságként
            # tárgyalja" vs "az elnök kijelöli a bizottságot"), so it is read
            # off there rather than left blank.
            "role": ("első helyen kijelölt bizottság"
                     if any(_FIRST_PLACE_RE.search(e["name"] or "") for e in events)
                     else "kijelölt bizottság"),
            "reference": None,
            "parts": None,
            # Per-committee status ("bizottsági tárgyalás befejezve"). Kept in
            # the scrape output but not loaded: the modern schema has no column
            # for it, and the block's own last event already says as much.
            "status": status.strip() or None,
        })
        for e in events:
            out["committeeEvents"].append({
                "date": e["date"], "name": e["name"], "committee": committee,
                "committeeId": None, "personID": None,
                # A committee line never carried a third field in any document
                # sampled, but if one does it names a person, as on the plenary
                # events — kept rather than dropped on the strength of a sample.
                "personLabel": e["relatedLabel"],
                "amendment": None, "overreachingAmendment": None, "report": None,
            })
    return out


# --- the motions listing (mod/<NNNNN>imo.htm) ------------------------------
# One <LI> per non-self-standing motion (módosító javaslat, bizottsági ajánlás,
# kapcsolódó módosító, …), laid out as:
#
#   <LI> <a href='../04754/0030mod.htm'>4754/30</a> (módosító javaslat) ---
#        1998.02.16. --- 30/2c,30/8a-b,30/12 visszavonva
#        <a href='../04754/0030txt.htm'>Teljes szöveg</a>
#        -- BENYÚJTÓ: <a …>Juhász Pál</a> (SZDSZ)
#        -- BENYÚJTÓ: <a …>Dr. Kiss Róbert</a> (SZDSZ)
#        **SZAVAZÁS: -
#
# A motion with several submitters repeats the BENYÚJTÓ line; one voted on in
# several parts repeats the SZAVAZÁS marker (+ carried / - fell), which is all
# the archive says about it — there are no tallies here.

_MOTION_HEAD_RE = re.compile(r"^(?P<number>\S+?/\d+)\s*\((?P<type>[^)]*)\)\s*$")
_SPONSOR_SPLIT_RE = re.compile(r"--\s*BENYÚJTÓ\s*:", re.I)
_VOTE_MARK_RE = re.compile(r"\*\*\s*SZAVAZÁS\s*:\s*([+-])")
_FULLTEXT_CAPTION_RE = re.compile(r"\s*Teljes szöveg\s*$", re.I)
_MOTIONS_SPLIT_RE = re.compile(r"NEM ÖNÁLLÓ INDÍTVÁNYOK", re.I)


def _strip_final_dot(s: str) -> str:
    """Drop one trailing full stop. The adatlap's ``<H2>`` ends its title with a
    period where the listing does not; dropping it makes the two agree."""
    return re.sub(r"\.$", "", s or "")


def parse_motions(page: str, *, parent_number: str) -> tuple[list[dict], list[dict]]:
    """``(motions, motionSummary)`` for one document's non-self-standing motions.

    ``motionSummary`` is derived here rather than read: the archive prints no
    per-type totals, but the API's summary panel is exactly a count per motion
    type, so it is counted off the parsed rows."""
    base = _page_base("mod")
    body = _MOTIONS_SPLIT_RE.split(page, maxsplit=1)
    if len(body) < 2:
        return [], []
    motions: list[dict] = []
    for item in _LI_RE.split(body[1])[1:]:
        # Each <LI> runs to the <p> that introduces the next one (and, for the
        # last motion, to the page footer).
        item = _SPLIT_P_RE.split(item, maxsplit=1)[0]
        head, *sponsor_parts = _SPONSOR_SPLIT_RE.split(item)
        links = _HREF_RE.findall(head)
        text_url = next((_abs(h, base) for h, cap in links
                         if _FULLTEXT_CAPTION_RE.match(text_of(cap))), None)
        fields = [f.strip() for f in text_of(head).split("---")]
        hm = _MOTION_HEAD_RE.match(fields[0]) if fields else None
        number = hm.group("number") if hm else (fields[0] if fields else "")
        if not number:
            continue
        note = _FULLTEXT_CAPTION_RE.sub("", " ".join(fields[2:])).strip()
        sponsors: list[dict] = []
        votes = 0
        for part in sponsor_parts:
            votes += len(_VOTE_MARK_RE.findall(part))
            sponsors.extend(_sponsors(_VOTE_MARK_RE.split(part)[0]))
        votes += len(_VOTE_MARK_RE.findall(head))
        motions.append({
            "iromanyId": f"{CYCLE}-{parent_number}-{number.rsplit('/', 1)[-1]}",
            "billNumber": number,
            "billNumberSort": _motion_sort(number),
            "mainType": "módosító",
            "type": " ".join((hm.group("type") if hm else "").split()) or None,
            "submittedDate": _iso_date(fields[1] if len(fields) > 1 else None),
            "textUrl": text_url,
            "textCaption": "teljes szöveg" if text_url else None,
            "noText": text_url is None,
            "hasVote": votes > 0,
            "note": note or None,
            "sponsors": sponsors,
        })
    summary: dict[str, int] = {}
    for m in motions:
        summary[m["type"] or ""] = summary.get(m["type"] or "", 0) + 1
    return motions, [{"type": t or None, "valid": None, "withdrawn": None,
                      "total": str(n)} for t, n in summary.items()]


def _motion_sort(number: str) -> int | None:
    """``"4754/30"`` → ``4754030``, matching how the API packs a motion's parent
    and own number into one sortable integer (``"15790/16017"`` → 15806017)."""
    parts = number.split("/")
    if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
        return None
    return int(parts[0]) * 1000 + int(parts[1])


# --- the speakers listing (felsz/<NNNNN>npl.htm) ---------------------------
# One <LI> per speech held on the document:
#
#   <LI>1997.09.09. <a href="/naplo35/295/2950249.htm">249. </a> felszólaló
#       <a href="../../kepviselo/elet/n287.htm">Dr. Nagy Frigyes</a> [01:10 sec]
#
# The naplo link gives the sitting day and the speech's number within it, which
# is the same ordinal `speech.speech_index` carries — so `sessionId`/`uid` below
# address the very speech already in the database. Nothing in the current schema
# holds a bill→speech link that isn't hung off an event, so this is parsed and
# saved but not loaded; hence `--speakers` is opt-in rather than default.

_NAPLO_RE = re.compile(r"/naplo(?P<cycle>\d+)/(?P<sitting>\d+)/(?P<file>\d+)\.html?", re.I)
_DURATION_RE = re.compile(r"\[\s*(\d+):(\d+)\s*sec\s*\]", re.I)
# Every row on these pages gives its speaker the one role "felszólaló". Knowing
# it matters because a speaker who was not an MP — a minister, the chief
# prosecutor, the MNB governor — has no CV page in the archive to link to, so
# their name is plain text sitting right after the role word, and only the known
# role tells the two apart.
_SPEAKER_ROLES = ("felszólaló",)


def parse_speakers(page: str) -> list[dict]:
    """The speeches held on one document, in the order the archive lists them."""
    out: list[dict] = []
    base = _page_base("felsz")
    listing = re.split(r"</ol>", page, maxsplit=1, flags=re.I)[0]
    for item in _LI_RE.split(listing)[1:]:
        flat = text_of(item)
        if not flat:
            continue
        mp = _MP_HREF_RE.search(item)
        naplo = _NAPLO_RE.search(item)
        dur = _DURATION_RE.search(flat)
        number = session = uid = None
        if naplo:
            session = f"{naplo.group('cycle')}{int(naplo.group('sitting')):03d}"
            # "2950249" = the sitting number followed by the speech's ordinal.
            number = str(int(naplo.group("file")[len(naplo.group("sitting")):]))
            uid = f"{session}-{number}"
        # Whatever is left once the links (speech ordinal, MP name), the date and
        # the duration are removed is the role the archive gives the speaker.
        rest = _HREF_RE.sub(" ", item)
        rest = " ".join(
            _DURATION_RE.sub(" ", _DATE_RE.sub(" ", text_of(rest))).split())
        role, label = rest or None, _speaker_label(item)
        if label is None:
            for known in _SPEAKER_ROLES:
                if rest.lower().startswith(known):
                    role, label = known, rest[len(known):].strip() or None
                    break
        out.append({
            "date": _iso_date(flat),
            "role": role,
            "personID": mp.group(1) if mp else None,
            "label": label,
            "speechNumber": number,
            "sessionId": session,
            "speechUid": uid,
            "durationSeconds": (int(dur.group(1)) * 60 + int(dur.group(2)))
                               if dur else None,
            "sourceUrl": _abs(naplo.group(0), base) if naplo else None,
        })
    return out


def _speaker_label(item: str) -> str | None:
    """The MP's displayed name in one speakers-list row."""
    for href, caption in _HREF_RE.findall(item):
        if _MP_HREF_RE.search(href):
            return text_of(caption) or None
    return None


# --- assembling the registry -----------------------------------------------

def _record(row: dict, adatlap: dict | None = None, *,
            motions: list[dict] | None = None,
            motion_summary: list[dict] | None = None,
            speakers: list[dict] | None = None,
            faction_ids: dict[str, int] | None = None) -> dict:
    """One listing row (plus whatever detail was fetched for it) as a record in
    the shape :func:`parlamonitor.bills.scrape.fetch_bills` emits.

    Fields the era never recorded stay empty rather than being invented: there is
    no stage diagram behind ``stages``, no tallies behind the votes, and no
    deadlines or background documents anywhere in the archive."""
    a = adatlap or {}
    number = row["number"]
    detail: dict = {
        "header": {
            "subtype": a.get("subtype") or row.get("type"),
            "character": None,
            "negotiationMode": a.get("negotiationMode"),
            "statusType": None,
            "currentEvent": None,
            "promulgationNumber": a.get("promulgationNumber"),
            "mkNumber": a.get("mkNumber"),
            "promulgationDate": (a.get("statusDate")
                                 if a.get("promulgationNumber") or a.get("mkNumber")
                                 else None),
            "remark": a.get("remark"),
            "lastModifier": None,
            # Stated on the adatlap, no column to load it into (the modern
            # scrape leaves the equivalent `cimzettNeve` out for the same
            # reason): the ministry a question is put to. The answering side is
            # loaded — it is named on the answer event's `relatedLabel`.
            "addressee": a.get("addressee"),
            "committeeStatus": a.get("committeeStatus"),
        },
        "events": a.get("events") or [],
        "committeeEvents": a.get("committeeEvents") or [],
        "votes": a.get("votes") or [],
        "deadlines": [],
        "committees": a.get("committees") or [],
        "documents": [],
    }
    if motions is not None:
        detail["motions"] = _with_faction_ids(motions, faction_ids)
        detail["motionSummary"] = motion_summary or []
    if speakers is not None:
        detail["speakers"] = speakers

    sponsors = _with_faction_ids([{**s} for s in (a.get("sponsors") or [])],
                                 faction_ids, top_level=True)
    text_url = a.get("textUrl")
    return {
        "billId": f"{CYCLE}-{row['num']}",
        "billNumber": number,
        "billNumberSort": int(row["num"]),
        "title": a.get("titleFull") or _strip_final_dot(row.get("title") or "") or None,
        "type": row.get("type") or a.get("subtype"),
        "mainType": number.split("/")[0] if "/" in number else None,
        "status": a.get("status"),
        "stages": [],
        "submittedDate": a.get("submittedDate") or row.get("submittedDate"),
        "textUrl": text_url,
        "textCaption": "teljes szöveg" if text_url else None,
        "noText": text_url is None,
        "sponsors": sponsors,
        # The document's own page in the archive — the "view on parlament.hu"
        # target, since the modern portal has no adatlap for this cycle at all.
        "sourceUrl": _abs(row["href"]),
        "detail": detail if adatlap is not None else None,
    }


def _with_faction_ids(items: list[dict], faction_ids: dict[str, int] | None,
                      *, top_level: bool = False) -> list[dict]:
    """Resolve each submitter's ``factionLabel`` to the faction id the loader
    joins on. Without a map (or for a label it doesn't know) the id stays
    ``None`` and only the label — which the display label already carries —
    is lost, never the submitter."""
    if not faction_ids:
        return items
    holders = items if top_level else [s for m in items for s in (m.get("sponsors") or [])]
    for s in holders:
        label = (s.get("factionLabel") or "").strip()
        if label and s.get("factionId") is None:
            s["factionId"] = faction_ids.get(label.casefold())
    return items


def _get(http, url: str) -> str | None:
    """One archive page, decoded. ``None`` when it isn't there (SCR-5)."""
    raw = http.get_bytes(url)
    http.polite_sleep()
    return decode(raw) if raw is not None else None


def _fetch_listings(http) -> list[dict]:
    """Both "Összes" listings, deduplicated, newest iromány number first."""
    rows: dict[str, dict] = {}
    for name in LIST_PAGES:
        page = _get(http, f"{BASE}/{name}")
        if page is None:
            logger.warning("Legacy listing %s is not there; skipping", name)
            continue
        chunk = parse_listing(page)
        logger.info("Legacy listing %s: %d rows", name, len(chunk))
        for row in chunk:
            prev = rows.get(row["num"])
            if prev and prev["number"] != row["number"]:
                logger.warning("Iromány %s listed twice, as %s and %s; keeping %s",
                               row["num"], prev["number"], row["number"],
                               prev["number"])
                continue
            rows[row["num"]] = row
    ordered = sorted(rows.values(), key=lambda r: int(r["num"]), reverse=True)
    logger.info("Legacy irományok listed: %d", len(ordered))
    return ordered


def _fetch_one(http, row: dict, *, with_motions: bool, with_speakers: bool,
               faction_ids: dict[str, int] | None) -> dict:
    """One document: its adatlap, plus the motion and speaker listings it links."""
    page = _get(http, _abs(row["href"]))
    if page is None:
        logger.warning("No adatlap for %s (%s)", row["number"], row["href"])
        return _record(row, faction_ids=faction_ids)
    a = parse_adatlap(page)
    motions = summary = speakers = None
    if with_motions:
        motions, summary = [], []
        # The adatlap states the count, so a document with no motions costs no
        # request at all — which is most of them (every kérdés, for a start).
        if a["motionsUrl"] and a["motionCount"]:
            mpage = _get(http, a["motionsUrl"])
            if mpage is not None:
                motions, summary = parse_motions(
                    mpage, parent_number=str(int(row["num"])))
                if len(motions) != a["motionCount"]:
                    logger.warning("%s: adatlap says %d motions, listing has %d",
                                   row["number"], a["motionCount"], len(motions))
    if with_speakers:
        speakers = []
        if a["speakersUrl"]:
            spage = _get(http, a["speakersUrl"])
            if spage is not None:
                speakers = parse_speakers(spage)
    return _record(row, a, motions=motions, motion_summary=summary,
                   speakers=speakers, faction_ids=faction_ids)


def _load_cache(path) -> dict[str, dict]:
    """Records a previous legacy run wrote, keyed by ``billId``.

    The archive was generated once, on 1998-04-03, and has not changed since —
    so unlike the live API stages there is no fingerprint to compare: a record
    already on file is reused whole, and ``force`` is the only way to re-fetch
    it. A ``bills-35.json`` written by the *API* stage is ignored, so the two
    sources can never be silently mixed."""
    if path is None or not path.exists():
        return {}
    try:
        prev = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        logger.warning("Legacy cache %s unreadable (%s); re-fetching all", path, e)
        return {}
    if (prev.get("meta") or {}).get("source") != SOURCE:
        logger.info("%s was not written from the static archive; re-fetching all",
                    getattr(path, "name", path))
        return {}
    cache = {r["billId"]: r for r in prev.get("data") or []
             if r.get("billId") and r.get("detail")}
    if cache:
        logger.info("Reusing %d records from %s", len(cache),
                    getattr(path, "name", path))
    return cache


def _has_layers(rec: dict, *, motions: bool, speakers: bool) -> bool:
    """Was this cached record scraped with at least the layers now asked for?

    A skipped layer leaves its key out entirely (an *empty* list means the page
    was read and had nothing), so adding ``--speakers`` to a later run re-fetches
    exactly the documents that never had them."""
    detail = rec.get("detail") or {}
    if motions and "motions" not in detail:
        return False
    if speakers and "speakers" not in detail:
        return False
    return True


def fetch_legacy_bills(http, *, with_detail: bool = True,
                       with_motions: bool = True, with_speakers: bool = False,
                       cache_path=None, force: bool = False,
                       faction_ids: dict[str, int] | None = None,
                       limit: int | None = None) -> dict:
    """Build the cycle-35 irományok registry from the static archive.

    ``with_detail`` (default) fetches each document's adatlap — one request per
    document, ~5 650 of them; without it only the two listings are read, which
    is a whole registry of number/date/type/title in two requests.
    ``with_motions`` (default) additionally reads the motion listing of every
    document that has one. ``with_speakers`` is off by default: the speech list
    it reads has no home in the current schema (see :func:`parse_speakers`), so
    it is a request per document spent on data nothing loads yet.

    ``cache_path`` (the ``bills-35.json`` of an earlier run) makes a re-run
    resumable — an interrupted scrape picks up where it stopped instead of
    starting over; ``force`` re-fetches everything. ``limit`` caps the number of
    documents, for a quick smoke run over the newest few."""
    rows = _fetch_listings(http)
    if limit:
        rows = rows[:limit]
    cache = _load_cache(cache_path) if not force else {}

    records: list[dict] = []
    fetched = reused = 0
    for i, row in enumerate(rows, 1):
        if not with_detail:
            records.append(_record(row, faction_ids=faction_ids))
            continue
        cached = cache.get(f"{CYCLE}-{row['num']}")
        if cached and _has_layers(cached, motions=with_motions,
                                  speakers=with_speakers):
            # The faction map is re-applied rather than trusted: it is pure local
            # computation, so a run made after the MP registry finally landed
            # fills in the links a first pass had to leave empty — without
            # spending 5 646 requests to find that out.
            _with_faction_ids(cached.get("sponsors") or [], faction_ids,
                              top_level=True)
            _with_faction_ids((cached.get("detail") or {}).get("motions") or [],
                              faction_ids)
            records.append(cached)
            reused += 1
            continue
        try:
            records.append(_fetch_one(http, row, with_motions=with_motions,
                                      with_speakers=with_speakers,
                                      faction_ids=faction_ids))
        except CaptchaWall:
            raise
        except Exception:  # one bad document never aborts the run (SCR-5)
            logger.exception("Legacy detail failed for %s", row["number"])
            records.append(_record(row, faction_ids=faction_ids))
        fetched += 1
        if i % 250 == 0:
            logger.info("Legacy irományok: %d/%d (%d fetched, %d reused)",
                        i, len(rows), fetched, reused)

    records.sort(key=lambda r: r.get("billNumberSort") or 0, reverse=True)
    return {
        "meta": {
            "cycle": CYCLE,
            "mainTypes": "all",
            "scrapedAt": _now_iso(),
            "source": SOURCE,
            "sourceUrls": [f"{BASE}/{n}" for n in LIST_PAGES],
            "count": len(records),
            "withDetail": with_detail,
            "withMotions": with_motions,
            "withSpeakers": with_speakers,
            "detailFetched": fetched,
            "detailReused": reused,
        },
        "data": records,
    }


# Parliamentary groups as the 1994-98 archive spells them → the label the MP
# registries use. Only the ones that actually differ: the archive's FKGP, MDF,
# KDNP, MSZP, SZDSZ, Néppárt-MDNP and független all match a registry label
# outright (the lookup is case-insensitive, so plain "FIDESZ" does too), leaving
# only the name Fidesz went by from 1995.
_FACTION_ALIASES = {
    "FIDESZ-MPP": "Fidesz",
}


def faction_ids_from_registries(paths) -> dict[str, int]:
    """``casefolded faction label`` → the faction id the loader joins on, read
    off the ``representatives-<cycle>.json`` files already on disk.

    Every registry is read, not just cycle 35's, because a Felicitas frakcioId is
    global (MSZP is 4 in every cycle) while a registry only names each MP's
    *final* group — so KDNP, a 22-seat group of this very parliament, appears in
    no cycle-35 record at all and would otherwise be unresolvable.

    A submitter whose group is still unknown loads anyway, with the group visible
    in the display label but no link to the faction."""
    paths = list(paths)
    ids: dict[str, int] = {}
    for path in paths:
        try:
            registry = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            logger.warning("Cannot read %s (%s); skipping it for the faction map",
                           path, e)
            continue
        for rec in registry.get("data") or []:
            fx = rec.get("faction") or {}
            if fx.get("label") and fx.get("id") is not None:
                ids.setdefault(str(fx["label"]).casefold(), int(fx["id"]))
    for era, modern in _FACTION_ALIASES.items():
        fid = ids.get(modern.casefold())
        if fid is not None:
            ids.setdefault(era.casefold(), fid)
    logger.info("Faction map: %d labels from %d registry file(s)",
                len(ids), len(paths))
    return ids
