"""Word-frequency aggregation for the sitting-day word cloud (WCLOUD-1/2/5).

A small, dependency-free tokenizer + Hungarian stop-word list used to turn a
sitting's transcript into a top-N word→count list. Kept separate from the API
router so the stop-word set is a single, configurable knob (WCLOUD-2 / OPS-4) and
the logic is unit-testable on plain strings.

The cloud surfaces *topical* words, so we strip grammatical filler: a standard
Hungarian function-word list (articles, pronouns, conjunctions, auxiliaries,
common adverbs) plus the parliamentary address fillers that recur in nearly every
speech ("tisztelt", "elnök", "képviselő úr/asszony", "köszönöm") and would
otherwise dominate — and be identical across — every day's cloud, defeating the
point of distinguishing one day's themes from another's.
"""

from __future__ import annotations

import math
import re
from collections import Counter

# Token = a run of Hungarian/Latin letters (incl. the accented set). Numbers and
# punctuation are split out; pure-digit tokens never survive this pattern.
_WORD_RE = re.compile(r"[a-zà-öø-ÿőűŐŰ]+", re.IGNORECASE)

MIN_LENGTH = 4  # drop very short tokens (mostly function words / noise)

# Hungarian stop-words: function words first, then parliamentary address fillers.
# Configurable (WCLOUD-2 / OPS-4) — extend the set, no code change needed.
STOPWORDS: frozenset[str] = frozenset(
    # articles, conjunctions, particles
    "a az egy és vagy hogy nem is de mert ha sem se hát csak már még meg ki be fel le "
    "el át rá ról ről ban ben bal jobb majd vagyis tehát illetve azaz mint amely amelyek "
    "amelynek amelyet aki akik akit ami amit ahol ahogy amikor amennyiben miszerint "
    # pronouns
    "én te ő mi ti ők ön önök maga maguk engem téged minket titeket őket ez ezt ezek "
    "ezeket az azt azok azokat itt ott ide oda innen onnan ilyen olyan ezen azon "
    "magam magad magát magunk ennek annak ebben abban erről arról ehhez ahhoz "
    "valaki valami valamint senki semmi minden mindenki mindez "
    # common verbs / auxiliaries
    "van vannak volt voltak lesz lenne legyen lehet kell kellene szabad fog fogja "
    "lett való lévő nincs nincsenek "
    # prepositional / adverbial fillers
    "után előtt alatt között közben során mellett miatt által nélkül felé felől helyett "
    "szerint óta végett ellen körül iránt révén végül szóval pedig azonban viszont "
    "ezért azért így úgy ugyanis tehát szintén egyébként persze valójában éppen akár "
    "amióta mivel hiszen vajon bár noha holott "
    "most akkor mindig soha gyakran néha újra ismét együtt külön nagyon eléggé igen "
    "jól rosszul mindenképpen természetesen "
    "ezt ezzel azzal arra erre ahhoz ehhez "
    # interrogative / relative words (questions, indirect clauses)
    "miért mért hogyan miképpen mikor mióta meddig mennyi mennyit mennyire mennyiben "
    "milyen milyenek hányan hányszor melyik melyek kik kit kinek kivel mibe miben "
    "mihez mitől mivel hová honnan merre ugyan ehhez "
    # parliamentary address fillers (recur in ~every speech — WCLOUD-2)
    "tisztelt elnök elnökasszony elnökúr képviselő képviselőtárs képviselőtársaim úr "
    "asszony hölgyeim uraim ház házelnök köszönöm köszönjük szó szót megadom kérem "
    "kérdés válasz napirend felszólalás államtitkár miniszter kormány képviselőtársam "
    "frakció parlament országgyűlés jegyző alelnök "
    # bracketed stage directions in the transcript ("(Taps a … soraiban.)", …)
    "taps szépen soraiban közbeszólás közbeszólások derültség moraj felzúdulás "
    "közbekiáltás zaj "
    # residual high-frequency fillers
    "hanem illetve volna fogjuk további több".split()
)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens of a transcript fragment (no stop-word filtering)."""
    return [m.group(0).lower() for m in _WORD_RE.finditer(text)]


def count_words(texts, stopwords: frozenset[str] = STOPWORDS,
                min_length: int = MIN_LENGTH) -> Counter:
    """Topical-word frequencies across ``texts`` (the day's term frequencies).

    Stop-words and tokens shorter than ``min_length`` are dropped (WCLOUD-2)."""
    counts: Counter[str] = Counter()
    for text in texts:
        if not text:
            continue
        for tok in tokenize(text):
            if len(tok) < min_length or tok in stopwords:
                continue
            counts[tok] += 1
    return counts


def top_words(texts, limit: int = 80,
              stopwords: frozenset[str] = STOPWORDS,
              min_length: int = MIN_LENGTH) -> list[tuple[str, int]]:
    """Return the ``limit`` most frequent topical words across ``texts``.

    Result is ``[(word, count), …]`` ordered by count desc, then word asc for a
    stable order on ties (deterministic output helps tests/caching)."""
    counts = count_words(texts, stopwords, min_length)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]


def tfidf_scores(tf: dict, df: dict, n_docs: int) -> dict:
    """Score each word by TF·IDF, favouring words frequent today but rare across
    the cycle (the "distinctive to this day" signal — WCLOUD).

    ``tf[word]`` = occurrences on the day; ``df[word]`` = number of sitting days
    in the period containing it; ``n_docs`` = total sitting days in the period.
    IDF is the smoothed ``log((N + 1) / (df + 0.5))`` so a word on *every* day is
    driven toward zero (suppressed) while a word unique to the day gets the full
    ``log(N)`` boost; it never goes negative. A word missing from ``df`` is
    treated as appearing on this day only (``df = 1``)."""
    out: dict = {}
    for word, freq in tf.items():
        d = df.get(word, 1)
        idf = math.log((n_docs + 1) / (d + 0.5))
        out[word] = freq * max(idf, 0.0)
    return out
