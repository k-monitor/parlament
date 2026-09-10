"""Interjections in the transcript — the Közbeszólások module (§6E, INT-1..3).

Hungarian plenary debate is not a sequence of monologues. Members shout across the
chamber while someone else holds the floor, and the shorthand writers record the
loudest of it verbatim, in parentheses, inside the speech that was interrupted:

    …hogy ön fél (Balla György: Úgy van!); ön fél attól, hogy…

That parenthetical is the only place in the record where those words exist — the
heckler has no speech of their own for them, so they appear in no speech count, no
speaking-time total, and no search facet keyed on a speaker. This module lifts them
out and turns them into what they actually are: a **directed relation between two
representatives** — who shouts, and over whose speech.

**The rules are the reader's rules.** The sitting-day transcript and the viewer
already lift these parentheticals out of the body text and italicise them, and
already split a ``Name:`` attribution off the front so the name can be linked to a
profile (``frontend/src/format.js``). Counting them by a *different* definition
would mean the graph disagrees with the page the reader can check it against, so
this module deliberately ports those rules rather than inventing its own:

  * a parenthetical is ``(`` up to the next ``)`` — no nesting, as in the reader;
  * one parenthetical can bundle several interjections, separated by a **spaced**
    dash (``Szavazás. - Novák Előd: Nagykoalíció! - Gulyás Gergely közbeszól.``);
    the flanking spaces are what keep *Ruszin-Szendi* and *2028-ig* in one piece;
  * an interjection is attributed when its text opens with **two to four
    title-case name tokens** (optionally behind *Dr.*/*ifj.*/…) and a colon.

**Precision over recall, because the claim is about people.** An edge on this graph
says *this named member heckled that named member*, which is a stronger claim than a
word frequency: a false one puts words in someone's mouth. So the gates are strict
and everything that does not pass them is stored **unattributed** rather than
guessed at:

1. The two-token name rule alone drops the transcript's own look-alikes without a
   single hand-written exception — ``Elnök:`` (one token, the chair), ``Igen:``,
   ``Közbeszólás:``, ``Moraj a kormánypárti oldalon:`` (lowercase words), and
   ``A táblán megjelenő eredmény:`` (the voting display, 398 occurrences in cycle 42).
2. **The name must resolve to one person in the register** (:func:`resolve_name`),
   which is the real filter: a name nobody in the House bears buys no edge.
3. **Ambiguity is never broken by guessing.** Hungarian surnames collide hard — the
   register holds five *Kovács László*s — so a name matching several people is
   resolved only when the electoral cycle the words were spoken in leaves exactly
   one of them in the House, and is otherwise left unattributed. Cycles 37 and 39
   each contain a genuinely undecidable pair (two sitting *Tóth István*s, two
   *Dr. Varga László*s); those stay unattributed, and the module's coverage figure
   says so out loud rather than quietly crediting the wrong member.

**What is deliberately not counted.** The transcript also records interruptions it
does not quote — *"Gulyás Gergely közbeszól."* names the heckler but not the words,
and *"Közbeszólások a Fidesz padsoraiból: Nem!"* quotes the words but not the
heckler. Both are real events, and neither is an edge: the first has nothing to put
behind the arrow when a reader clicks it, and the second has no one to draw it from.
Counting the first would also double-count, since the same shout is often quoted in
full a line later. So this module counts the ~99 000 interruptions that are *both*
attributed and quoted, and leaves the rest to the transcript.

This module is pure — no database, no network, no I/O. It sees text and a name
index; the loader owns the corpus and the register (§6E/INT-4), and the request path
runs none of it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Iterator, Mapping, Sequence

# ---------------------------------------------------------------------------
# Parsing (ported from frontend/src/format.js — see the module docstring)
# ---------------------------------------------------------------------------

# The separator between several interjections bundled in one parenthetical: a dash
# *flanked by whitespace*. The dash class covers U+2010–U+2015, the minus sign
# U+2212 and a plain hyphen; requiring the spaces is what keeps hyphenated names
# (Ruszin-Szendi, Turi-Kovács) and ranges (2028-ig) from being split.
_SEP_RE = re.compile(r"\s+[‐-―−-]\s+")
# The same dashes clinging to an edge of a part, left behind when a bundle was cut
# across a sentence boundary and the separator lost one of its flanking spaces.
_EDGE_DASH_RE = re.compile(r"^[\s‐-―−-]+|[\s‐-―−-]+$")

# A parenthetical that is a reference the speaker dictated, not an aside: a bare
# enumerator ("(2)") or the Hungarian legal date form ("(V. 9.)"). Recognised
# structurally — only digits, Roman-numeral letters, dots and space, with at least
# one digit — so a real interjection, which has lowercase words, never matches.
_REFERENCE_RE = re.compile(r"^[IVXLCDM\d.\s]+$")

# An honorific that may precede the name inside an attribution.
_HONORIFIC_RE = re.compile(r"^(?:dr|prof|ifj|id|özv)\.?$", re.IGNORECASE)
# One name token: a capitalised word (accents, apostrophes and hyphens allowed, e.g.
# "Ruszin-Szendi") or a bare initial ("Z." in "Z. Kárpát Dániel").
_NAME_TOKEN_RE = re.compile(
    r"^[A-ZÁÉÍÓÖŐÚÜŰ](?:[A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű'’\-]*|\.)$")

# How far into an aside the attributing colon may sit, and how many name tokens the
# attribution may hold. Both are the reader's numbers (format.js).
_MAX_COLON = 60
_MIN_NAME_TOKENS = 2
_MAX_NAME_TOKENS = 4


@dataclass(frozen=True)
class Interjection:
    """One attributed interjection, as written.

    ``speaker`` is the attributed name with its honorific dropped — the same form
    the transcript reader lifts out of the aside, so the two agree on what the name
    *is* before either tries to resolve it to a person; ``text`` is what was
    shouted, with the attribution stripped; ``start`` is the offset of the
    parenthetical that carried it, into the text handed to :func:`extract`.
    """

    speaker: str
    text: str
    start: int


def iter_parentheticals(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(start, inner)`` for every parenthetical, in reading order.

    Non-nesting, and an unclosed ``(`` runs to the end of the text — both matching
    what the transcript reader does with the same characters. A reference the
    speaker dictated ("(2)", "(V. 9.)") is skipped: it is part of the speech.
    """
    idx = 0
    n = len(text)
    while idx < n:
        open_at = text.find("(", idx)
        if open_at < 0:
            return
        close_at = text.find(")", open_at + 1)
        inner = text[open_at + 1:] if close_at < 0 else text[open_at + 1:close_at]
        idx = n if close_at < 0 else close_at + 1
        stripped = inner.strip()
        if stripped and not (any(c.isdigit() for c in stripped)
                             and _REFERENCE_RE.match(stripped)):
            yield open_at, inner


def split_attribution(part: str) -> tuple[str, str] | None:
    """Split ``"Balla György: Úgy van!"`` into ``("Balla György", "Úgy van!")``.

    Returns ``None`` when the part carries no name attribution — a stage direction
    ("Taps a kormánypártok soraiból."), a one-word cue ("Közbeszólás: …"), an
    unattributed shout from a bench ("Hangok a DK soraiból: …"), or anything whose
    pre-colon tokens are not all title-case names.
    """
    colon = part.find(":")
    if colon < 1 or colon > _MAX_COLON:
        return None
    said = part[colon + 1:].strip()
    if not said:
        return None
    tokens = part[:colon].split()
    while tokens and _HONORIFIC_RE.match(tokens[0]):
        tokens = tokens[1:]
    if not _MIN_NAME_TOKENS <= len(tokens) <= _MAX_NAME_TOKENS:
        return None
    if not all(_NAME_TOKEN_RE.match(t) for t in tokens):
        return None
    return " ".join(tokens), said


def extract(text: str) -> list[Interjection]:
    """Every attributed interjection in one speech's text, in reading order."""
    found: list[Interjection] = []
    for start, inner in iter_parentheticals(text):
        for part in _SEP_RE.split(inner):
            part = _EDGE_DASH_RE.sub("", part).strip()
            if not part:
                continue
            split = split_attribution(part)
            if split:
                found.append(Interjection(speaker=split[0], text=split[1],
                                          start=start))
    return found


# ---------------------------------------------------------------------------
# Resolving a written name to a person (INT-3)
# ---------------------------------------------------------------------------

def fold_name(name: str) -> str:
    """Accent- and case-folded form a written name is matched by.

    The same normalization ``/api/v1/representatives/resolve`` uses for the inline
    transcript links (``db.fold_text``), so a name the reader sees linked to a
    profile is the name this module credits — plus the leading honorific dropped,
    since the transcript writes *Dr. Vitányi István* where the register has
    *Vitányi István*.
    """
    tokens = re.sub(r"\s+", " ", name or "").strip().split(" ")
    while tokens and _HONORIFIC_RE.match(tokens[0]):
        tokens = tokens[1:]
    decomposed = unicodedata.normalize("NFKD", " ".join(tokens))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def build_name_index(people: Iterable[tuple[str, Sequence[str | None]]]
                     ) -> dict[str, list[str]]:
    """Index ``folded name -> [person_id, …]`` from ``(person_id, names)`` pairs.

    A person is indexed under every spelling the register holds for them (``label``
    and ``label_full``, which differ by the honorific and by middle names), and a
    folded name several people share keeps all of them — that collision is exactly
    what :func:`resolve_name` has to see in order to refuse to guess.
    """
    index: dict[str, list[str]] = {}
    for person_id, names in people:
        for name in names:
            if not name:
                continue
            bucket = index.setdefault(fold_name(name), [])
            if person_id not in bucket:
                bucket.append(person_id)
    return index


def resolve_name(name: str, index: Mapping[str, Sequence[str]],
                 in_house: Sequence[str] | frozenset[str] | None = None) -> str | None:
    """The one person a written name means, or ``None`` when it is not decidable.

    Tried in order, and the first that yields **exactly one** person wins:

    1. the name as written;
    2. its longest resolvable prefix — the transcript adds tokens the register does
       not have, either a middle name (*Hankó Balázs Zoltán* for *Hankó Balázs*, 26
       occurrences in cycle 42), a stage direction that ran into the name
       (*Sebián-Petrovszki László felnevetve*), or the dative-marked member the
       shout was aimed at (*Rétvári Bence Tordai Bencének*). Nothing is dropped
       from the *front*, so a prefix match can never cross to another family.

    ``in_house`` — the people sitting in the electoral cycle the words were spoken
    in — narrows a colliding name, and only that: a name matching several members of
    the same House is left unresolved rather than credited to the likelier one.
    """
    tokens = fold_name(name).split(" ")
    for stop in range(len(tokens), 0, -1):
        candidates = index.get(" ".join(tokens[:stop]))
        if not candidates:
            continue
        if len(candidates) == 1:
            return candidates[0]
        if in_house:
            seated = [p for p in candidates if p in in_house]
            if len(seated) == 1:
                return seated[0]
        return None       # a collision this cycle cannot break: never guess
    return None
