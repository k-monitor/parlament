#!/usr/bin/env python3
"""Find accidental haikus hidden in the parliamentary proceedings.

A haiku here follows the Hungarian rules:

  * three lines of 5, 7 and 5 syllables (17 syllables in all);
  * in Hungarian the number of syllables equals the number of vowels — every
    syllable has exactly one vowel nucleus and the language has no diphthongs,
    so we simply count the 14 vowels ``a á e é i í o ó ö ő u ú ü ű``.
    Digraphs (sz, gy, cs, …) are irrelevant: only vowels are counted;
  * a line break must fall on a word boundary — a word is never split.

A sentence from the transcript qualifies when its words can be grouped, in
order, into runs of 5 + 7 + 5 syllables. By default the *whole* sentence must
be exactly a haiku; ``--partial`` also accepts a 5-7-5 run inside a longer
sentence.

Sentences are skipped when the syllable count would be unreliable: those with
digits (numbers are spelled out when spoken), with editorial stage directions
in parentheses/brackets ("(Taps.)"), or containing a lettered token that has no
vowel (abbreviations like "DR.", spelled-out initials).

Usage:
    python find_haikus.py                 # whole-sentence haikus, pretty output
    python find_haikus.py --partial       # also find 5-7-5 runs inside sentences
    python find_haikus.py --period 43     # only the 43rd electoral cycle
    python find_haikus.py --json out.json # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, asdict

# The 14 Hungarian vowels (short + long, both cases). One vowel == one syllable.
VOWELS = frozenset("aáeéiíoóöőuúüűAÁEÉIÍOÓÖŐUÚÜŰ")

# Characters that make a sentence's spoken syllable count unreliable.
_STAGE_CHARS = frozenset("()[]{}")


def count_syllables(token: str) -> int:
    """Syllables in a Hungarian token = number of vowels in it."""
    return sum(1 for ch in token if ch in VOWELS)


@dataclass
class Word:
    surface: str   # original token, punctuation kept, for rendering the poem
    syllables: int


def tokenize(sentence: str) -> list[Word] | None:
    """Split a sentence into vowel-bearing words.

    Returns ``None`` if the sentence is unfit for syllable counting (digits,
    stage directions, or a lettered token with no vowel).
    """
    for ch in sentence:
        if ch.isdigit() or ch in _STAGE_CHARS:
            return None

    words: list[Word] = []
    for tok in sentence.split():
        has_letter = any(ch.isalpha() for ch in tok)
        if not has_letter:
            continue  # pure punctuation between words — drop it
        syl = count_syllables(tok)
        if syl == 0:
            return None  # a real word with no vowel (abbreviation/initials): unreliable
        words.append(Word(tok, syl))
    return words


def _haiku_boundaries(sylls: list[int], start: int) -> tuple[int, int, int] | None:
    """From word index ``start``, find the 5/7/5 word boundaries.

    Because every word has at least one syllable the running sum is strictly
    increasing, so each target (5, 12, 17) can be hit at most once. Returns the
    absolute word indices ``(a, b, end)`` where line 1 = words[start:a],
    line 2 = words[a:b], line 3 = words[b:end], or ``None`` if no clean split.
    """
    run = 0
    a = b = None
    for j in range(start, len(sylls)):
        run += sylls[j]
        if run == 5:
            a = j + 1
        elif run == 12:
            b = j + 1
        elif run == 17:
            if a is not None and b is not None:
                return (a, b, j + 1)
            return None
        elif run > 17:
            return None
    return None


def find_haikus(words: list[Word], partial: bool) -> list[tuple[int, int, int, int]]:
    """Return haiku spans as ``(start, a, b, end)`` word-index tuples."""
    sylls = [w.syllables for w in words]
    if not partial:
        bounds = _haiku_boundaries(sylls, 0)
        # Whole sentence must BE the haiku (all words consumed).
        if bounds and bounds[2] == len(words):
            return [(0, *bounds)]
        return []

    spans: list[tuple[int, int, int, int]] = []
    for start in range(len(words)):
        bounds = _haiku_boundaries(sylls, start)
        if bounds:
            spans.append((start, *bounds))
    return spans


def _lines(words: list[Word], span: tuple[int, int, int, int]) -> list[str]:
    start, a, b, end = span
    return [
        " ".join(w.surface for w in words[start:a]),
        " ".join(w.surface for w in words[a:b]),
        " ".join(w.surface for w in words[b:end]),
    ]


_CLAUSE_END = tuple(",.;:!?—–")


def _naturalness(lines: list[str], whole: bool) -> int:
    """Small score: haikus whose line breaks land on punctuation read better."""
    score = 2 if whole else 0
    if lines[0].rstrip().endswith(_CLAUSE_END):
        score += 1
    if lines[1].rstrip().endswith(_CLAUSE_END):
        score += 1
    return score


@dataclass
class Haiku:
    lines: list[str]
    speaker: str
    uid: str
    sentence_ord: int
    date: str
    sitting: int
    period: int
    whole: bool
    score: int
    link: str


def scan(conn: sqlite3.Connection, period: int | None, partial: bool,
         base_url: str) -> list[Haiku]:
    sql = (
        "SELECT s.text, s.ord, sp.uid, sp.speaker_label, "
        "       ss.date, ss.sitting, ss.period_number "
        "FROM sentence s "
        "JOIN speech sp ON s.speech_id = sp.uid "
        "JOIN session ss ON sp.session_id = ss.id"
    )
    params: list = []
    if period is not None:
        sql += " WHERE ss.period_number = ?"
        params.append(period)

    results: list[Haiku] = []
    seen: set[tuple[str, tuple[str, str, str]]] = set()
    scanned = 0
    for text, ord_, uid, speaker, date, sitting, period_num in conn.execute(sql, params):
        scanned += 1
        if not text:
            continue
        words = tokenize(text)
        if not words or len(words) < 3:
            continue
        for span in find_haikus(words, partial):
            lines = _lines(words, span)
            whole = span[0] == 0 and span[3] == len(words)
            # Dedupe identical poems said in the same speech.
            key = (uid, tuple(lines))
            if key in seen:
                continue
            seen.add(key)
            link = f"{base_url}/proceedings/{uid}?s={ord_}"
            results.append(Haiku(
                lines=lines,
                speaker=speaker or "ismeretlen",
                uid=uid,
                sentence_ord=ord_,
                date=date,
                sitting=sitting,
                period=period_num,
                whole=whole,
                score=_naturalness(lines, whole),
                link=link,
            ))

    results.sort(key=lambda h: (h.score, h.date or ""), reverse=True)
    print(f"scanned {scanned:,} sentences, found {len(results):,} haiku(s)",
          file=sys.stderr)
    return results


def _print_pretty(haikus: list[Haiku]) -> None:
    for i, h in enumerate(haikus, 1):
        print(f"\n━━━━━━━━━━━━━━━ #{i} ━━━━━━━━━━━━━━━")
        for line in h.lines:
            print(f"  {line}")
        print(f"  — {h.speaker} · {h.period}. ciklus {h.sitting}. ülésnap · {h.date}")
        print(f"    {h.link}")


def main(argv: list[str] | None = None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    default_db = os.environ.get("PARLAMONITOR_DB", os.path.join(here, "parlamonitor.db"))

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=default_db, help=f"SQLite DB path (default: {default_db})")
    ap.add_argument("--period", type=int, default=None, help="restrict to an electoral cycle, e.g. 43")
    ap.add_argument("--partial", action="store_true",
                    help="also find 5-7-5 runs inside longer sentences (default: whole sentences only)")
    ap.add_argument("--limit", type=int, default=None, help="print at most N haikus")
    ap.add_argument("--base-url", default="", help="prefix for the viewer links (e.g. https://…)")
    ap.add_argument("--json", metavar="PATH", help="write results as JSON to PATH")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print(f"error: database not found: {args.db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    try:
        haikus = scan(conn, args.period, args.partial, args.base_url.rstrip("/"))
    finally:
        conn.close()

    if args.limit is not None:
        haikus = haikus[: args.limit]

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump([asdict(h) for h in haikus], fh, ensure_ascii=False, indent=2)
        print(f"wrote {len(haikus)} haiku(s) to {args.json}", file=sys.stderr)
    else:
        _print_pretty(haikus)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
