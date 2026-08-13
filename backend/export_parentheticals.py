#!/usr/bin/env python3
"""Export every parenthetical span of the transcripts from the runtime DB (read-only).

Text in parentheses is where the Országgyűlés transcript keeps everything that
is *not* the speech itself: the shorthand writers' editorial stage directions
(applause, heckling, the chair's bell), the wall-clock stamps, the voting
markers, the speaker attributions — and, most interestingly, verbatim heckles
attributed to a named MP, which exist nowhere else in the record.

``export_nlp_datasets.py`` *removes* these to get clean speech text; this script
is its mirror image — it keeps only the parentheticals, with enough provenance
and surrounding context to analyze what the conventions actually are.

Outputs, per requested electoral cycle plus a combined index:

  1. ``cycle<N>-parentheticals.jsonl`` — one line per span, verbatim content,
     position, speaker/agenda provenance, surrounding context, classification.
  2. ``cycle<N>-parentheticals.txt``   — the same spans, text and nothing else,
     one per line in transcript order.
  3. ``parentheticals-frequency.csv``  — one row per distinct span text (case- and
     dash-folded), with counts per cycle: the workhorse for spotting conventions.
  4. ``parentheticals-distinct.txt``   — that CSV's text column alone, same
     frequency order: every distinct bracketed text, once each.

The two ``.txt`` files are for reading, so by default they leave out the
boilerplate that would otherwise be most of the volume — the voting markers, the
clock stamps, the bare statute enumerators, the decree dates and the speaker
attributions' faction tags (``--txt-exclude``, 55% of all spans). The JSONL and
the CSV always keep everything.
  5. ``parentheticals-manifest.json``  — totals, per-category and per-cycle
     counts, what was unbalanced, and the classifier's rule list.

Usage:

    python export_parentheticals.py --db parlamonitor.db --out ../exports
    python export_parentheticals.py --periods 43 --context 200

Extraction notes:

  * Spans are found on the **whole speech text**, not per sentence: a direction
    routinely straddles the segmentation ("(A képviselő feláll." + "Taps.)"),
    and a paren can even split mid-phrase ("(Fidesz, Győr-Moson-Sopron" +
    "megye)"). Joining first recovers those as one span.
  * Nesting is kept, not flattened: an interjection quoted inside a direction
    ("(KISS PÉTER (MSZP): Tisztelt Elnök Úr!)") yields both the outer span and
    the inner faction tag, distinguished by ``depth``.
  * Genuinely unbalanced parentheses (a typo'd or never-closed bracket, ~1.5% of
    speeches) are still emitted, with ``closed: false`` and the content
    truncated at ``--unclosed-cap`` characters. Filter them out with
    ``closed == true`` if they get in the way.
  * A span is split into ``segments`` on the " - " the shorthand writers use to
    chain directions ("Zaj. - Az elnök csenget."); each segment is classified on
    its own, and an attributed heckle ("Nacsa Lőrinc: Ezt már mondtad!") has its
    speaker and utterance pulled apart.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict

from export_nlp_datasets import SPEAKER_PREFIX_RE, split_speaker_prefix

# --- span text normalization ----------------------------------------------

# The transcripts mix U+002D, U+2010/2011 (non-breaking hyphen) and en/em dashes
# interchangeably; fold them so the same direction groups into one CSV row.
DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
WS_RE = re.compile(r"\s+")
# The " - " that chains several directions inside one paren.
SEGMENT_RE = re.compile(r"\s+-\s+")


def norm_text(s: str) -> str:
    """Whitespace- and dash-normalized surface form (case preserved)."""
    return WS_RE.sub(" ", s.translate(DASHES).replace("\xa0", " ")).strip()


def fold_key(s: str) -> str:
    """Grouping key: normalized, case-folded, trailing sentence period dropped."""
    s = unicodedata.normalize("NFC", norm_text(s)).casefold()
    return s.rstrip(".").strip() or s


# --- classification --------------------------------------------------------

FACTIONS = {
    "fidesz", "kdnp", "mszp", "jobbik", "lmp", "dk", "párbeszéd", "momentum",
    "mi hazánk", "tisza", "szdsz", "mdf", "fkgp", "miép", "független",
    "nemzetiségi képviselő", "fidesz-kdnp", "fidesz-mpsz", "fidesz-mpp",
    "fidesz-magyar polgári szövetség",
}

# Wall-clock stamp the transcript inserts every ten minutes: "(13.20)", "(9:50)".
CLOCK_RE = re.compile(r"^\d{1,2}[.:]\d{2}\.?$")
# Bare enumerators from a statute citation: "13. § (2) bekezdése", "(EU) 2016/679".
LEGAL_RE = re.compile(r"^(?:\d{1,3}|[a-zíáéúőóüö]|[IVXLC]{1,6})\)?\.?$", re.IGNORECASE)
# "II. 24." — the day half of a decree number split by the segmenter.
DATE_REF_RE = re.compile(r"^[IVXLC]{1,4}\.\s*\d{1,2}\.$")
ACRONYM_RE = re.compile(r"^[A-ZÁÉÍÓÖŐÚÜŰ][A-ZÁÉÍÓÖŐÚÜŰ0-9.\-]{1,11}\.?$")

# An attributed heckle: "Nacsa Lőrinc: Ezt már mondtad!" — a Hungarian personal
# name (optionally "Dr.", initials, up to four name parts) then a colon then what
# they shouted. Rules above this one in ORDER claim their prefixes first, so
# "Szünet:" and "Közbeszólás az MSZP soraiból:" never reach it.
ATTRIBUTED_RE = re.compile(
    r"""^(?P<who>
            (?:(?:dr|ifj|id|özv|prof)\.\s*)?                      # honorific
            (?:[A-ZÁÉÍÓÖŐÚÜŰ][\wÁÉÍÓÖŐÚÜŰáéíóöőúüű.\-]*\s+){0,3}  # given names
            [A-ZÁÉÍÓÖŐÚÜŰ][\wáéíóöőúüű.\-]{1,}                    # surname
        )\s*:\s*
        (?P<what>\S.*)$""",
    re.VERBOSE | re.IGNORECASE,
)

# Ordered rules — first match wins, so the specific precedes the generic.
# (category, pattern). Patterns are searched case-insensitively.
RULES: list[tuple[str, str]] = [
    ("break",            r"^szünet\b|\bszünet:\s*\d|rövid szünet|az ülés.{0,20}felfüggeszt|folytatás[a-z]*\s+\d"),
    ("chair_change",     r"elnöki széket|az ülés vezetését|átveszi az ülés|elnököl|jegyz[őo]k?:\s"),
    # anchored: a bare "szavaznak" also occurs inside heckles ("Arató Gergely:
    # Imre, de nem szavaznak le rá!"), which are not voting markers
    ("vote",             r"^szavazás|^szavaznak\b|^jelenlét-ellen[őo]rzés|^gépszavazás|^szavazategyenl"),
    ("procedural_response",
                         r"^megtörténik|nincs (?:ilyen )?(?:jelentkez|jelzés|ellenvet|hozzászól)"
                         r"|senki (?:sem|nem) jelentkezik|nem érkezik jelzés|^jelzésre\b|jelzésére:"
                         r"|^nincs\b.{0,25}\.$|^igen\.$|^nem\.$"),
    ("chair_action",     r"csenget|cseng[őo]|jelzi az id|jelzi a (?:hozzászólási|felszólalási)|az elnök .{0,30}jelzi"
                         r"|az elnök közbeszól|az elnök megkopogtat|figyelmezteti"),
    ("reading_out",      r"felolvas|olvassák|olvassa fel|névsorolvasás|a jegyz[őo].{0,25}olvas"),
    ("applause",         r"\btaps|tapsol|éljenz|ováci|helyeslés|ütemes.{0,12}taps"),
    ("laughter",         r"derültség|nevetés|kacag|mosoly|\bnevet\.|\bnevetnek\b"),
    ("heckling",         r"közbeszól|közbekiabál|bekiabál|felkiált|bekiált|felkiabál"),
    ("noise",            r"\bzaj\b|moraj|síp(?:ol|olás)|fütty|hujjog|felzúdulás|pissz|dörömböl"
                         r"|dobog|kiabálás|hangzavar|csend\b"),
    # a cue for *how* or *to whom* the words that follow are said, rather than a
    # description of an event — these characteristically end in a colon
    ("delivery",         r"felé:$|felé fordul|fordulva|irányába (?:néz|fordul|mutat)|^így ejti"
                         r"|hangját (?:fel)?emel|^hangosítás nélkül|ülve folytat"
                         r"|(?:folytatja|mutatva|nézve|hozzátéve|hozzáteszi|megjegyzi):$"),
    ("movement",         r"feláll|állva|helyet foglal|elhagyj|távoz|bevonul|kivonul|visszatér|leül"
                         r"|az emelvény|szónoki emelvény|a terembe|a teremb[őo]l|odalép|megérkez"
                         r"|felmutat|mutatj|kezet fog|magasba emel|feltart"),
    ("gesture",          r"bólint|bólogat|(?:nemet|igent) int|int a fej|fejét (?:csóválja|rázza)"
                         r"|jelentkezik|integet|gesztikulál|vállat von|lapoz|beszélget|suttog"
                         r"|kezével|karját|ujjával|széttárja|összenéz"),
    ("technical",        r"mikrofon|hangosít|kivetít|képerny[őo]|a technika|szavazógép|nem működik"
                         r"|kikapcsol|bekapcsol"),
    ("sic",              r"^sic!?$|^így!?$|^!$|^…$|^\.\.\.$"),
    ("silence",          r"^néma (?:felállás|csend)|egyperces|a himnusz|a szózat|állva énekl"),
]
RULES_C = [(cat, re.compile(pat, re.IGNORECASE)) for cat, pat in RULES]

CATEGORY_ORDER = [
    "faction_tag", "clock", "vote", "procedural_response", "applause", "heckling",
    "attributed_interjection", "chair_action", "chair_change", "noise", "laughter",
    "movement", "gesture", "delivery", "technical", "break", "reading_out", "silence", "sic",
    "legal_ref", "date_ref", "acronym", "other", "mixed", "empty", "stray_close",
]


def classify(segment: str) -> tuple[str, str | None, str | None]:
    """Label one segment; returns (category, heckler_name, heckled_utterance)."""
    s = norm_text(segment)
    if not s:
        return "empty", None, None
    low = s.casefold().rstrip(".").strip()
    if low in FACTIONS or (
        "," in low and low.split(",", 1)[0].strip() in FACTIONS
    ):
        return "faction_tag", None, None            # "(Fidesz)", "(Fidesz, Vas megye)"
    if CLOCK_RE.match(s):
        return "clock", None, None
    for cat, rx in RULES_C:
        if rx.search(s):
            return cat, None, None
    m = ATTRIBUTED_RE.match(s)
    if m:
        return "attributed_interjection", m.group("who").strip(), m.group("what").strip()
    if s.endswith(":"):
        return "delivery", None, None      # a colon with nothing after it cues what follows
    if LEGAL_RE.match(s):
        return "legal_ref", None, None
    if DATE_REF_RE.match(s):
        return "date_ref", None, None
    if ACRONYM_RE.match(s):
        return "acronym", None, None
    return "other", None, None


def analyze(content: str) -> dict:
    """Split a span into its chained segments and classify each."""
    parts = [p for p in SEGMENT_RE.split(norm_text(content)) if p.strip()]
    if not parts:
        return {"category": "empty", "categories": ["empty"], "segments": []}
    segs = []
    for p in parts:
        cat, who, what = classify(p)
        seg = {"text": p, "category": cat}
        if who:
            seg["speaker"] = who
            seg["utterance"] = what
        segs.append(seg)
    cats = sorted({s["category"] for s in segs}, key=CATEGORY_ORDER.index)
    return {
        "category": cats[0] if len(cats) == 1 else "mixed",
        "categories": cats,
        "segments": segs,
    }


# --- span extraction -------------------------------------------------------


SENT_END = ".!?…"


def find_spans(text: str, unclosed_cap: int) -> list[dict]:
    """All parenthetical spans, innermost and outermost alike, with nesting depth.

    ``close_kind`` records how each span's right edge was determined:

    ``explicit``
        a matching ")" in the source — the normal case.
    ``implicit``
        the source dropped the ")". The older transcripts do this often
        ("(Taps a Jobbik soraiban. (16.50)" — one ")" short), and a naive stack
        parser would then report every later span in the speech as nested inside
        the leaked one. So when a "(" arrives while an open span *already reads
        as a finished direction* (its text ends in sentence-final punctuation),
        the open one is closed here instead of nesting. Real nesting — an inner
        paren mid-phrase, "(KISS PÉTER (MSZP): …)" — is untouched by this,
        because a name is not sentence-final punctuation.
    ``unterminated``
        a "(" that never closed and never hit the rule above; content is
        truncated at ``unclosed_cap`` and ``truncated`` says whether it was cut.
    ``stray``
        a ")" with nothing open — recorded as a zero-length marker so the
        imbalance stays visible and countable.
    """
    spans: list[dict] = []
    stack: list[int] = []
    for i, ch in enumerate(text):
        if ch == "(":
            while stack:
                inner = text[stack[-1] + 1:i].rstrip()
                if not inner or inner[-1] not in SENT_END:
                    break
                start = stack.pop()
                spans.append({
                    "start": start, "end": start + 1 + len(inner),
                    "content": inner, "depth": len(stack),
                    "closed": False, "close_kind": "implicit",
                })
            stack.append(i)
        elif ch == ")":
            if stack:
                start = stack.pop()
                spans.append({
                    "start": start, "end": i + 1,
                    "content": text[start + 1:i],
                    "depth": len(stack), "closed": True, "close_kind": "explicit",
                })
            else:
                spans.append({
                    "start": i, "end": i + 1, "content": "",
                    "depth": 0, "closed": False, "close_kind": "stray",
                })
    for start in stack:                       # never closed
        end = min(len(text), start + 1 + unclosed_cap)
        spans.append({
            "start": start, "end": end,
            "content": text[start + 1:end],
            "depth": 0, "closed": False, "close_kind": "unterminated",
            "truncated": end < len(text),
        })
    spans.sort(key=lambda s: (s["start"], -s["end"]))
    return spans


# --- DB access -------------------------------------------------------------

SPEECH_SQL = """
SELECT sp.uid, sp.origin_id, sp.session_id, s.date, s.sitting, sp.period_number,
       sp.speech_index, sp.felszolalas_tipus, sp.procedural,
       sp.person_id, sp.speaker_label, sp.speaker_office,
       f.label AS faction,
       sp.agenda_item_id, ai.title AS agenda_title,
       ai.type AS agenda_type, ai.native_type AS agenda_native_type
FROM speech sp
JOIN session s           ON s.id = sp.session_id
LEFT JOIN faction f      ON f.id = sp.faction_id
LEFT JOIN agenda_item ai ON ai.id = sp.agenda_item_id
WHERE sp.period_number = ?
ORDER BY sp.session_id, sp.speech_index
"""

SENTENCE_SQL = """
SELECT se.speech_id, se.ord, se.paragraph, se.text
FROM sentence se
JOIN speech sp ON sp.uid = se.speech_id
WHERE sp.period_number = ?
ORDER BY se.speech_id, se.ord
"""


def build_speech_text(rows) -> tuple[str, list[tuple[int, int, int, int]]]:
    """Rejoin a speech's sentences; return the text and a sentence offset map.

    Map entries are (start, end, ord, paragraph) into the returned text.
    Sentences are joined with a space inside a paragraph, paragraphs with a
    blank line — the same reconstruction ``export_nlp_datasets.py`` uses, so
    offsets are comparable between the two exports.
    """
    chunks: list[str] = []
    smap: list[tuple[int, int, int, int | None]] = []
    pos = 0
    last_p = None
    for i, r in enumerate(rows):
        # cycles 41-42 predate the paragraph migration: paragraph is NULL
        # throughout, so the whole speech is one block (and stays NULL in the
        # output rather than being reported as paragraph 0).
        p = 0 if r["paragraph"] is None else r["paragraph"]
        if i:
            sep = "\n\n" if p != last_p else " "
            chunks.append(sep)
            pos += len(sep)
        chunks.append(r["text"])
        smap.append((pos, pos + len(r["text"]), r["ord"], r["paragraph"]))
        pos += len(r["text"])
        last_p = p
    return "".join(chunks), smap


def speaker_prefix_end(first_sentence: str) -> int:
    """Length of the speech-opening attribution, 0 when there is none."""
    prefix, _rest = split_speaker_prefix(first_sentence)
    if prefix is None:
        return 0
    m = SPEAKER_PREFIX_RE.match(first_sentence)
    return m.end() if m else 0


# --- record building -------------------------------------------------------


def speaker_obj(sp: dict) -> dict:
    return {
        "person_id": sp["person_id"],
        "label": sp["speaker_label"],
        "faction": sp["faction"],
        "office": sp["speaker_office"],
    }


def build_records(sp: dict, rows, context: int, unclosed_cap: int):
    text, smap = build_speech_text(rows)
    if "(" not in text and ")" not in text:
        return
    prefix_end = speaker_prefix_end(rows[0]["text"]) if rows else 0

    for n, span in enumerate(find_spans(text, unclosed_cap)):
        start, end = span["start"], span["end"]
        touched = [s for s in smap if s[0] < end and s[1] > start] or [smap[0]]
        first, last = touched[0], touched[-1]
        rec = {
            "id": f"{sp['uid']}#{n}",
            "speech_uid": sp["uid"],
            "origin_id": sp["origin_id"],
            "session_id": sp["session_id"],
            "date": sp["date"],
            "sitting": sp["sitting"],
            "period_number": sp["period_number"],
            "speech_index": sp["speech_index"],
            "speech_type": sp["felszolalas_tipus"],
            "procedural": bool(sp["procedural"]),
            "speaker": speaker_obj(sp),
            "agenda": {
                "id": sp["agenda_item_id"],
                "type": sp["agenda_type"],
                "native_type": sp["agenda_native_type"],
                "title": sp["agenda_title"],
            },
            "paragraph": first[3],
            "sentence_ord": first[2],
            "sentence_ord_end": last[2],
            "n_sentences_spanned": len(touched),
            "char_start": start,
            "char_end": end,
            "text": span["content"],
            "text_norm": norm_text(span["content"]),
            "text_key": fold_key(span["content"]),
            "depth": span["depth"],
            "closed": span["closed"],
            "close_kind": span["close_kind"],
            "truncated": bool(span.get("truncated")),
            # the span is the entire sentence — an editorial-only sentence that
            # export_nlp_datasets.py drops from the speech text altogether
            "whole_sentence": len(touched) == 1
                              and norm_text(text[first[0]:first[1]]) == norm_text(text[start:end]),
            # sits inside the transcript's speaker attribution ("NAGY J. (Fidesz):")
            "in_speaker_prefix": end <= prefix_end,
        }
        rec.update(analyze(span["content"]))
        if span["close_kind"] == "stray":
            rec.update(category="stray_close", categories=["stray_close"], segments=[])
        if context:
            rec["context_before"] = norm_text(text[max(0, start - context):start])
            rec["context_after"] = norm_text(text[end:end + context])
        yield rec


# --- main ------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("PARLAMONITOR_DB", "parlamonitor.db"))
    ap.add_argument("--periods", default="all",
                    help="comma-separated cycle numbers, or 'all' (default)")
    ap.add_argument("--out", default="../exports")
    ap.add_argument("--context", type=int, default=140,
                    help="chars of surrounding text to keep either side (0 = none)")
    ap.add_argument("--unclosed-cap", type=int, default=300,
                    help="chars to keep for a never-closed '('")
    ap.add_argument("--min-count", type=int, default=1,
                    help="frequency CSV: drop rows seen fewer than this many times")
    ap.add_argument("--txt-exclude", default="vote,clock,legal_ref,date_ref,faction_tag",
                    help="categories to leave out of the .txt files only (the JSONL "
                         "and the CSV always keep everything); '' keeps all")
    args = ap.parse_args()

    txt_exclude = {c.strip() for c in args.txt_exclude.split(",") if c.strip()}
    unknown = txt_exclude - set(CATEGORY_ORDER)
    if unknown:
        ap.error(f"unknown --txt-exclude categories: {', '.join(sorted(unknown))}\n"
                 f"known: {', '.join(CATEGORY_ORDER)}")

    os.makedirs(args.out, exist_ok=True)
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    all_periods = [r[0] for r in con.execute(
        "SELECT DISTINCT period_number FROM speech WHERE period_number IS NOT NULL "
        "ORDER BY period_number")]
    periods = all_periods if args.periods == "all" else [
        int(p) for p in args.periods.split(",") if p.strip()]

    build = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM build_meta")}

    # frequency table, accumulated across every requested cycle
    freq: dict[str, dict] = {}
    per_cycle: dict[int, dict] = {}
    hecklers: Counter = Counter()

    for period in periods:
        speeches = [dict(r) for r in con.execute(SPEECH_SQL, (period,))]
        sents: dict[str, list] = defaultdict(list)
        for r in con.execute(SENTENCE_SQL, (period,)):
            sents[r["speech_id"]].append(r)

        path = os.path.join(args.out, f"cycle{period}-parentheticals.jsonl")
        txt_path = os.path.join(args.out, f"cycle{period}-parentheticals.txt")
        cats: Counter = Counter()
        kinds: Counter = Counter()
        stats = Counter()
        with open(path, "w", encoding="utf-8") as fh, \
                open(txt_path, "w", encoding="utf-8") as tfh:
            for sp in speeches:
                rows = sents.get(sp["uid"])
                if not rows:
                    continue
                for rec in build_records(sp, rows, args.context, args.unclosed_cap):
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    if rec["text_norm"] and rec["category"] not in txt_exclude:
                        # plain-text companion: the bracketed text and nothing
                        # else, one span per line, in transcript order. Uses
                        # text_norm because a verbatim span can span paragraphs
                        # and so contain the newline that separates the lines.
                        tfh.write(rec["text_norm"] + "\n")
                        stats["text_lines"] += 1
                    cats[rec["category"]] += 1
                    kinds[rec["close_kind"]] += 1
                    stats["spans"] += 1
                    stats["nested"] += rec["depth"] > 0
                    stats["sentence_straddling"] += rec["n_sentences_spanned"] > 1
                    stats["whole_sentence"] += rec["whole_sentence"]
                    stats["in_speaker_prefix"] += rec["in_speaker_prefix"]
                    for seg in rec["segments"]:
                        if seg.get("speaker"):
                            hecklers[seg["speaker"]] += 1

                    if not rec["text_key"]:
                        continue        # "()" and stray ")" carry no text to count
                    e = freq.setdefault(rec["text_key"], {
                        "text": Counter(), "category": Counter(),
                        "count": 0, "speeches": set(), "periods": Counter(),
                        "first_date": rec["date"], "last_date": rec["date"],
                        "example": rec["id"],
                    })
                    e["text"][rec["text_norm"]] += 1
                    # text_key folds case and the trailing period, so one key can
                    # hold surface forms that classify differently ("IX. 30." is
                    # a date_ref, "IX. 30" is not) — let the majority decide.
                    e["category"][rec["category"]] += 1
                    e["count"] += 1
                    e["speeches"].add(rec["speech_uid"])
                    e["periods"][period] += 1
                    if rec["date"]:
                        if not e["first_date"] or rec["date"] < e["first_date"]:
                            e["first_date"] = rec["date"]
                        if not e["last_date"] or rec["date"] > e["last_date"]:
                            e["last_date"] = rec["date"]

        per_cycle[period] = {
            "file": os.path.basename(path),
            "text_file": os.path.basename(txt_path),
            "speeches": len(speeches),
            "spans": stats["spans"],
            "size_bytes": os.path.getsize(path),
            "text_lines": stats["text_lines"],
            "by_close_kind": dict(kinds),
            "nested": stats["nested"],
            "sentence_straddling": stats["sentence_straddling"],
            "whole_sentence": stats["whole_sentence"],
            "in_speaker_prefix": stats["in_speaker_prefix"],
            "by_category": {c: cats[c] for c in CATEGORY_ORDER if cats[c]},
        }
        print(f"cycle {period}: {stats['spans']:>7,} spans  ->  {path}")

    # --- frequency CSV + its plain-text companion ---
    csv_path = os.path.join(args.out, "parentheticals-frequency.csv")
    distinct_path = os.path.join(args.out, "parentheticals-distinct.txt")
    cols = ["text", "text_key", "category", "count", "n_speeches",
            *[f"cycle{p}" for p in periods], "first_date", "last_date", "example_id"]
    written = distinct_written = 0
    with open(csv_path, "w", encoding="utf-8", newline="") as fh, \
            open(distinct_path, "w", encoding="utf-8") as dfh:
        w = csv.writer(fh)
        w.writerow(cols)
        for key, e in sorted(freq.items(), key=lambda kv: (-kv[1]["count"], kv[0])):
            if e["count"] < args.min_count:
                continue
            surface = e["text"].most_common(1)[0][0]
            category = e["category"].most_common(1)[0][0]
            w.writerow([
                surface, key, category, e["count"],
                len(e["speeches"]),
                *[e["periods"].get(p, 0) for p in periods],
                e["first_date"], e["last_date"], e["example"],
            ])
            written += 1
            if category not in txt_exclude:
                dfh.write(surface + "\n")     # text only, same order as the CSV
                distinct_written += 1
    print(f"frequency: {written:,} distinct texts  ->  {csv_path}")
    print(f"           {distinct_written:,} lines kept       ->  {distinct_path}")

    total_cats: Counter = Counter()
    total_kinds: Counter = Counter()
    for c in per_cycle.values():
        total_cats.update(c["by_category"])
        total_kinds.update(c["by_close_kind"])

    manifest = {
        "generated_from": os.path.abspath(args.db),
        "db_data_updated_at": build.get("data_updated_at"),
        "periods": periods,
        "totals": {
            "spans": sum(c["spans"] for c in per_cycle.values()),
            "distinct_texts": len(freq),
            "by_category": {c: total_cats[c] for c in CATEGORY_ORDER if total_cats[c]},
            "by_close_kind": dict(total_kinds),
            "nested": sum(c["nested"] for c in per_cycle.values()),
            "sentence_straddling": sum(c["sentence_straddling"] for c in per_cycle.values()),
            "whole_sentence": sum(c["whole_sentence"] for c in per_cycle.values()),
            "in_speaker_prefix": sum(c["in_speaker_prefix"] for c in per_cycle.values()),
        },
        "attributed_interjections": {
            "total": sum(hecklers.values()),
            "distinct_speakers": len(hecklers),
            "top": hecklers.most_common(40),
        },
        "by_cycle": per_cycle,
        "frequency_csv": {
            "file": os.path.basename(csv_path),
            "rows": written,
            "min_count": args.min_count,
        },
        "text_files": {
            "distinct_file": os.path.basename(distinct_path),
            "distinct_rows": distinct_written,
            "lines": sum(c["text_lines"] for c in per_cycle.values()),
            "excluded_categories": sorted(txt_exclude),
            "excluded_occurrences": sum(
                total_cats[c] for c in txt_exclude) + total_cats["stray_close"]
                + total_cats["empty"],
        },
        "context_chars": args.context,
        "classifier_rules": [
            {"category": "faction_tag", "rule": "exact match against the faction list "
             "(optionally + constituency)"},
            {"category": "clock", "rule": CLOCK_RE.pattern},
            *[{"category": c, "rule": p} for c, p in RULES],
            {"category": "attributed_interjection", "rule": "Name: utterance"},
            {"category": "legal_ref", "rule": LEGAL_RE.pattern},
            {"category": "date_ref", "rule": DATE_REF_RE.pattern},
            {"category": "acronym", "rule": ACRONYM_RE.pattern},
            {"category": "mixed", "rule": "segments of a chained span disagree"},
            {"category": "other", "rule": "no rule matched"},
        ],
    }
    man_path = os.path.join(args.out, "parentheticals-manifest.json")
    with open(man_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(json.dumps({k: manifest[k] for k in
                      ("totals", "attributed_interjections", "periods")},
                     ensure_ascii=False, indent=2)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
