"""Accidental haikus hidden in the parliamentary record (SOC-4).

A haiku here follows the Hungarian rules:

  * three lines of 5, 7 and 5 syllables (17 syllables in all);
  * in Hungarian the number of syllables equals the number of vowels — every
    syllable has exactly one vowel nucleus and the language has no diphthongs,
    so we simply count the 14 vowels ``a á e é i í o ó ö ő u ú ü ű``.
    Digraphs (sz, gy, cs, …) are irrelevant: only vowels are counted;
  * a line break must fall on a word boundary — a word is never split.

A sentence from the transcript qualifies when its words can be grouped, in order,
into runs of 5 + 7 + 5 syllables. By default the *whole* sentence must be exactly a
haiku; ``partial=True`` also accepts a 5-7-5 run inside a longer sentence.

Sentences are skipped when the syllable count would be unreliable: those with
digits (numbers are spelled out when spoken), with editorial stage directions in
parentheses/brackets ("(Taps.)"), or containing a lettered token that has no vowel
(abbreviations like "DR.", spelled-out initials).

Two callers share the rules from here so they cannot drift: ``find_haikus.py``,
the corpus-wide exploration CLI, and ``app/social.py``, which posts the ones a
representative said on a freshly published sitting day.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# The 14 Hungarian vowels (short + long, both cases). One vowel == one syllable.
VOWELS = frozenset("aáeéiíoóöőuúüűAÁEÉIÍOÓÖŐUÚÜŰ")

# Characters that make a sentence's spoken syllable count unreliable.
_STAGE_CHARS = frozenset("()[]{}")

_CLAUSE_END = tuple(",.;:!?—–")


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


def poem_lines(words: list[Word], span: tuple[int, int, int, int]) -> list[str]:
    start, a, b, end = span
    return [
        " ".join(w.surface for w in words[start:a]),
        " ".join(w.surface for w in words[a:b]),
        " ".join(w.surface for w in words[b:end]),
    ]


def naturalness(lines: list[str], whole: bool) -> int:
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
    faction: str | None = field(default=None)

    @property
    def key(self) -> str:
        """Stable identity of this poem, for "have I posted it already?".

        The speech it was said in plus the poem itself: a re-scan of the same
        sitting recognises it, while the same words from another speaker (or the
        same speaker on another day) count as a new find. A re-segmented sentence
        that changes the wording is a different poem by this key — which is the
        safe direction for a scan, but means an upstream transcript correction can
        surface a near-duplicate."""
        h = hashlib.sha1(("\n".join([self.uid, *self.lines])).encode("utf-8"))
        return h.hexdigest()


def _rows_to_haikus(rows, *, partial: bool, base_url: str) -> list[Haiku]:
    """Scan ``(text, ord, uid, speaker, date, sitting, period, faction)`` rows.

    Returns the poems in transcript order; callers sort by what they care about.
    Identical poems repeated inside one speech are reported once."""
    results: list[Haiku] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    scanned = 0
    for text, ord_, uid, speaker, day, sitting, period, faction in rows:
        scanned += 1
        if not text:
            continue
        words = tokenize(text)
        if not words or len(words) < 3:
            continue
        for span in find_haikus(words, partial):
            lines = poem_lines(words, span)
            whole = span[0] == 0 and span[3] == len(words)
            key = (uid, tuple(lines))
            if key in seen:
                continue
            seen.add(key)
            results.append(Haiku(
                lines=lines,
                speaker=speaker or "ismeretlen",
                uid=uid,
                sentence_ord=ord_,
                date=day,
                sitting=sitting,
                period=period,
                whole=whole,
                score=naturalness(lines, whole),
                link=f"{base_url}/proceedings/{uid}?s={ord_}" if base_url
                     else f"/proceedings/{uid}?s={ord_}",
                faction=faction,
            ))
    logger.info("scanned %d sentences, found %d haiku(s)", scanned, len(results))
    return results


# The row shape `_rows_to_haikus` expects, in that order.
_COLUMNS = ("s.text, s.ord, sp.uid, sp.speaker_label, ss.date, ss.sitting, "
            "ss.period_number, f.label")

_FROM = ("FROM sentence s "
         "JOIN speech sp ON s.speech_id = sp.uid "
         "JOIN session ss ON sp.session_id = ss.id "
         "LEFT JOIN faction f ON sp.faction_id = f.id")


def scan(conn: sqlite3.Connection, period: int | None, partial: bool,
         base_url: str) -> list[Haiku]:
    """Every haiku in the corpus (optionally one electoral cycle), best first.

    The exploration CLI's entry point — it reads every sentence, so it is a
    minutes-long pass over a full corpus, not something to run per update."""
    sql = f"SELECT {_COLUMNS} {_FROM}"
    params: list = []
    if period is not None:
        sql += " WHERE ss.period_number = ?"
        params.append(period)
    results = _rows_to_haikus(conn.execute(sql, params), partial=partial,
                              base_url=base_url)
    results.sort(key=lambda h: (h.score, h.date or ""), reverse=True)
    return results


def haikus_in_session(conn: sqlite3.Connection, session_id: str, *,
                      partial: bool = False, base_url: str = "",
                      mps_only: bool = True) -> list[Haiku]:
    """The haikus said on ONE sitting day, best first (SOC-4).

    Scoped to what "a representative said a haiku" means: a speech attributed to a
    known person, with a transcript, that is not the chair's procedural/session-
    running boilerplate (STAT-1) — announcing a vote result in 5-7-5 is an accident
    of the formula, not of anybody's speech. With ``mps_only`` the speaker must also
    hold a mandate in the roster, so ministers and nationality advocates are left
    out; clear it to include everyone who spoke.

    One sitting day is a few thousand sentences, so this is milliseconds — the
    announcer runs it per newly published day, never over the corpus."""
    sql = (f"SELECT {_COLUMNS} {_FROM} "
           "LEFT JOIN person p ON sp.person_id = p.person_id "
           "WHERE sp.session_id = ? AND sp.has_text = 1 "
           "  AND COALESCE(sp.procedural, 0) = 0 "
           "  AND sp.person_id IS NOT NULL "
           + ("  AND COALESCE(p.is_mp, 0) = 1 " if mps_only else "")
           + "ORDER BY sp.speech_index, s.ord")
    results = _rows_to_haikus(conn.execute(sql, (session_id,)), partial=partial,
                              base_url=base_url)
    # Best-reading first: the announcer posts the top one or two of a day.
    results.sort(key=lambda h: (h.score, -h.sentence_ord), reverse=True)
    return results
