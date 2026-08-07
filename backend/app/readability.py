"""Per-speech readability (LIX/RIX) and lexical diversity (TTR/MATTR) — READ-1..7.

Two numbers per speech, both from **saphes**
(https://github.com/crow-intelligence/saphes), a dependency-free package that
computes LIX and TTR/MATTR with the parameters other implementations hardcode:

* **Readability — LIX** ``= A/B + 100·C/A`` (words per sentence, plus the share of
  long words). Its companion **RIX** ``= C/B`` travels along for free.
* **Lexical diversity — TTR / MATTR**, how much of the vocabulary is distinct.

saphes' central contract is that **the two metrics need opposite token streams**:
LIX must see *surface forms* (word length is the signal; ``házakban`` is 8
characters, its lemma ``ház`` is 3) while diversity must see *lemmas* (in
Hungarian ``ház / házak / házban / házakat`` is one word inflected four ways, not
four words — un-lemmatised TTR reports morphology as vocabulary). Feeding one
stream to both yields no error, just a plausible wrong number, so this module
keeps them strictly separate: :func:`readability` takes the transcript text and
:func:`diversity` takes the HuSpaCy lemma stream (``app.nlp.lemma_streams``) and
nothing else. When no lemmatizer is reachable the diversity half is simply
**omitted** rather than computed on surface forms (READ-4).

**The long-word threshold is 8, not Björnsson's 6.** The Swedish default
saturates in Hungarian: at 6 roughly 42 % of running tokens in this corpus count
as "long" against a Germanic norm near 25 %, so the index stops discriminating.
saphes ships an equipercentile calibration for Hungarian
(``recommended_threshold("hu") == 8``), and its caveat says to verify the share on
your own register before trusting it. Verified here on the corpus itself — 1.1 M
tokens of plenary transcript:

===========  ==========================
 threshold    share of tokens "long"
===========  ==========================
 6            0.418
 7            0.339
 **8**        **0.262**
 9            0.196
===========  ==========================

Threshold 8 puts parliamentary Hungarian at 0.262, essentially on top of the
Swedish reference share (0.257) that Björnsson's 6 selects — so the second LIX
term means the same thing here as it does in the original. (``tests/
test_readability.py`` re-checks this against the calibration.) The threshold, the
length policy and the MATTR window are all env-configurable (OPS-4).

**Björnsson's difficulty labels are therefore unavailable**: they are calibrated
for Swedish prose at threshold 6, so at 8 the score is off their scale and saphes
correctly returns ``band = None``. Rather than mislabel a Hungarian number with a
Swedish word, speeches are banded **relative to the corpus itself** — the quintile
cut points of every measured speech (``metric_distribution``), so "nehéz" means
"harder to read than 80 % of what is said in this House", which is both honest and
the comparison a reader actually wants (READ-6).

What is measured is the **spoken text only**: the transcript's leading speaker
attribution ("TUZSON BENCE (Fidesz):") and the stenographer's parenthetical stage
directions ("(Taps a kormánypárti oldalon.)", heckles) are stripped first — they
are not the speaker's words, and they would otherwise both inflate the token count
and invent sentences. This mirrors what the reader sees, since the frontend lifts
exactly the same segments out of the flowing transcript (``format.js``).
"""

from __future__ import annotations

import os
import re

from saphes import (
    hungarian_letter_count,
    lexical_diversity,
    lix,
    recommended_threshold,
)
from saphes import __version__ as SAPHES_VERSION
from saphes import words as saphes_words

# ---------------------------------------------------------------------------
# configuration (OPS-4)
# ---------------------------------------------------------------------------

# The calibrated Hungarian long-word threshold (a long word is *longer than*
# this, so 8 means nine letters or more). Sourced from saphes' calibration rather
# than hard-coded, so a future recalibration arrives with a package upgrade.
try:
    HU_THRESHOLD: int = int(recommended_threshold("hu").threshold)
except KeyError:  # pragma: no cover - only if saphes drops the hu calibration
    HU_THRESHOLD = 8

LONG_WORD_THRESHOLD: int = int(
    os.environ.get("PARLAMONITOR_LIX_THRESHOLD") or HU_THRESHOLD)

# How a word's letters are counted. "nfc" (the default) counts NFC code points and
# is what the shipped Hungarian threshold was calibrated against; "hu-letters"
# collapses the Hungarian digraphs (sz/gy/ny… are single *letters*), which is a
# defensible sensitivity check but shifts the share down (0.236 at threshold 8 on
# this corpus), so it is opt-in.
_LENGTH_POLICIES = {"nfc", "graphemes", "codepoints", "hu-letters"}
LENGTH_POLICY: str = (os.environ.get("PARLAMONITOR_LIX_LENGTH_POLICY")
                      or "nfc").strip().lower()
if LENGTH_POLICY not in _LENGTH_POLICIES:
    LENGTH_POLICY = "nfc"

# MATTR's sliding window, in lemmas. TTR falls as a text grows, so raw TTR across
# speeches of wildly different lengths mostly ranks them by length; MATTR is the
# length-robust number and is the one the UI leads with. A speech shorter than the
# window gets no MATTR at all (saphes would silently degrade it to whole-text TTR,
# which is exactly the incomparable number we are avoiding).
MATTR_WINDOW: int = max(1, int(os.environ.get("PARLAMONITOR_MATTR_WINDOW") or 100))

# Floor below which a speech is not scored: LIX and TTR on a two-sentence remark
# are noise, and a page full of noise is worse than a page with a gap. ~50 words is
# a short but real contribution (a point of order, a one-paragraph question).
MIN_WORDS: int = max(1, int(os.environ.get("PARLAMONITOR_READABILITY_MIN_WORDS") or 50))

# Bump when the cleaning/metric logic changes in a way that should invalidate the
# on-disk cache even though saphes, the model and the text are unchanged.
_LOGIC_VERSION = 1


def _length_policy():
    """The saphes length policy object for :data:`LENGTH_POLICY`."""
    return hungarian_letter_count if LENGTH_POLICY == "hu-letters" else LENGTH_POLICY


def method_tag(lemma_model: str | None = None) -> str:
    """Identifier of the exact measurement, hashed into the on-disk cache so that
    a saphes upgrade, a threshold change or a different lemmatizer busts it.

    ``lemma_model`` is the HuSpaCy model behind the diversity half, or ``None``
    when no lemmatizer was reachable — a readability-only entry, which must not be
    reused once a model becomes available (READ-4)."""
    return (f"saphes:{SAPHES_VERSION}:lix{LONG_WORD_THRESHOLD}:{LENGTH_POLICY}"
            f":mattr{MATTR_WINDOW}:min{MIN_WORDS}"
            f":lemmas:{lemma_model or 'none'}:v{_LOGIC_VERSION}")


# ---------------------------------------------------------------------------
# transcript cleaning — what counts as "the speaker's words"
# ---------------------------------------------------------------------------

# A token whose letters are all uppercase ("TUZSON", "DR.") — the signature of the
# speaker attribution the transcript opens with. Mirrors format.js' isCapsToken:
# it must contain a letter, and none of them may be lowercase.
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def _is_caps_token(tok: str) -> bool:
    return bool(_LETTER_RE.search(tok)) and not any(ch.islower() for ch in tok)


# A parenthetical the speaker dictated rather than a stage direction: a bare
# reference like "(2)" or a Hungarian legal date "(V. 9.)". Structural, so a real
# heckle (which contains lowercase words) never matches. Mirrors format.js.
_REFERENCE_PAREN = re.compile(r"^[IVXLCDM\d.\s]+$")
_DIGIT_RE = re.compile(r"\d")


def strip_speaker_label(text: str) -> str:
    """Drop the transcript's leading speaker attribution — "TUZSON BENCE (Fidesz):",
    "ELNÖK:", "DR. ÁDER JÁNOS köztársasági elnök:" — from a speech's opening text.

    Recognised structurally so an ordinary sentence is never mutilated: the label
    ends at the first colon, a ``!``/``?`` before it disqualifies the match (real
    speech), and it must be either the lone chair token or open with two ALL-CAPS
    name tokens — so "EU-csúcs volt: …" survives. Same rule as the frontend's
    ``stripSpeakerLabel`` (format.js), so the measured text is the read text."""
    m = re.search(r"[:!?\n]", text)
    if not m or m.group(0) != ":" or m.start() > 140:
        return text
    tokens = text[:m.start()].strip().split()
    is_label = (
        (len(tokens) == 1 and tokens[0].startswith("ELNÖK"))
        or (len(tokens) >= 2 and _is_caps_token(tokens[0]) and _is_caps_token(tokens[1]))
    )
    return text[m.start() + 1:].lstrip(" \t") if is_label else text


def spoken_sentences(texts) -> list[str]:
    """The speaker's own words, one entry per source sentence.

    Strips the leading speaker attribution from the first sentence and removes the
    stenographer's parenthetical asides (stage directions, heckles, applause) —
    keeping dictated references like "(2)" or "(V. 9.)" inline. An open parenthesis
    is carried across the sentence boundary, because a stage direction routinely
    spans several transcript sentences; without the carry its text would be counted
    as speech and its "sentences" as the speaker's.

    Entries that become empty are kept as empty strings so the caller can still
    line results up with the input; saphes ignores blank sentences when counting
    *B*."""
    out: list[str] = []
    in_paren = False
    for i, raw in enumerate(texts):
        block = raw or ""
        if i == 0:
            block = strip_speaker_label(block)
        kept: list[str] = []
        if in_paren:
            close = block.find(")")
            if close < 0:
                out.append("")
                continue
            block = block[close + 1:]
            in_paren = False
        last = idx = 0
        while True:
            open_at = block.find("(", idx)
            if open_at < 0:
                break
            close = block.find(")", open_at + 1)
            if close < 0:
                kept.append(block[last:open_at])
                last = len(block)
                in_paren = True
                break
            inner = block[open_at + 1:close].strip()
            if _DIGIT_RE.search(inner) and _REFERENCE_PAREN.match(inner):
                idx = close + 1          # dictated reference: leave it inline
                continue
            kept.append(block[last:open_at])
            last = idx = close + 1
        if not in_paren:
            kept.append(block[last:])
        out.append(" ".join(part.strip() for part in kept if part.strip()).strip())
    return out


# ---------------------------------------------------------------------------
# the metrics
# ---------------------------------------------------------------------------

def readability(texts) -> dict | None:
    """LIX + RIX for one speech, from its transcript sentences (surface forms).

    ``texts`` is the speech's sentences in order — the raw rows; cleaning
    (:func:`spoken_sentences`) happens here so every caller measures the same
    thing. *B* comes from the transcript's own sentence segmentation rather than a
    splitter guessing at it, which is the more trustworthy count and is recorded as
    ``presegmented`` by saphes.

    Returns ``None`` for a speech below :data:`MIN_WORDS` (or with no usable text):
    a score there would be noise wearing a number's clothes."""
    spoken = [s for s in spoken_sentences(texts) if s]
    if not spoken:
        return None
    # Tokenize once, explicitly, and hand saphes both streams: the surface tokens
    # and the transcript's own sentences. Nothing is inferred, and the MIN_WORDS
    # floor is checked on exactly the tokens the score would be built from.
    tokens = saphes_words(" ".join(spoken))
    if len(tokens) < MIN_WORDS:
        return None
    result = lix(tokens, sentences=spoken,
                 long_word_threshold=LONG_WORD_THRESHOLD,
                 length_policy=_length_policy())
    return {
        "lix": round(result.score, 2),
        "rix": round(result.rix, 2),
        "words": result.words,
        "sentences": result.sentences,
        "long_words": result.long_words,
        "avg_sentence": round(result.avg_sentence_length, 2),
        "long_share": round(result.long_word_share, 4),
    }


def diversity(lemmas) -> dict | None:
    """TTR (+ MATTR) for one speech, from its **lemma** stream.

    ``lemmas`` must come from the HuSpaCy lemmatizer (``app.nlp.lemma_streams``),
    never from surface text — see the module docstring. ``mattr`` is ``None`` for a
    speech shorter than :data:`MATTR_WINDOW`, because saphes would otherwise
    silently return the whole-text TTR under a MATTR label, and TTR is not
    comparable across lengths.

    Returns ``None`` for an empty stream."""
    lemmas = [t for t in (lemmas or []) if t]
    if not lemmas:
        return None
    result = lexical_diversity(
        lemmas, unit="lemma", case_fold=True,
        window=MATTR_WINDOW if len(lemmas) >= MATTR_WINDOW else None,
        pos_filter="content tokens (punctuation, whitespace and numerals dropped)")
    return {
        "ttr": round(result.ttr, 4),
        "mattr": None if result.mattr is None else round(result.mattr, 4),
        "types": result.types,
        "tokens": result.tokens,
    }


# ---------------------------------------------------------------------------
# corpus-relative bands (READ-6)
# ---------------------------------------------------------------------------

# Quintile cut points and the band each interval maps to. Björnsson's absolute
# labels do not apply off threshold 6 (see the module docstring), so a speech is
# placed against the corpus it belongs to instead.
BAND_QUANTILES = (20, 40, 60, 80)
LIX_BANDS = ("very-easy", "easy", "average", "hard", "very-hard")
MATTR_BANDS = ("very-low", "low", "average", "high", "very-high")

# Every quantile stored for a metric: the band cut points plus the deciles, so a
# reader-facing "in the top X %" can be derived without re-ranking the corpus.
STORED_QUANTILES = tuple(sorted({10, 20, 30, 40, 50, 60, 70, 80, 90}
                                | set(BAND_QUANTILES)))


def quantiles(values, qs=STORED_QUANTILES) -> dict[int, float]:
    """The requested percentile cut points of ``values`` (nearest-rank).

    Nearest-rank rather than interpolated: the cut points only ever classify, so
    exactness beyond "which fifth is this in" buys nothing, and nearest-rank is
    stable under the ties a rounded score produces."""
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return {}
    n = len(ordered)
    out = {}
    for q in qs:
        rank = max(1, min(n, -(-q * n // 100)))   # ceil(q*n/100), clamped
        out[int(q)] = float(ordered[rank - 1])
    return out


def band_for(value, cuts: dict, labels=LIX_BANDS) -> str | None:
    """Place ``value`` in its corpus quintile band.

    ``cuts`` maps percentile → cut point (as stored in ``metric_distribution``).
    Returns ``None`` when the corpus distribution isn't available, so the UI hides
    the label rather than inventing one."""
    if value is None or not cuts:
        return None
    for label, q in zip(labels, BAND_QUANTILES):
        cut = cuts.get(q)
        if cut is not None and value <= cut:
            return label
    return labels[-1]


def methodology() -> dict:
    """The measurement's parameters, for the API's methodology note (TRUST-1 /
    REP-5). Every number the site shows should be able to say how it was made."""
    return {
        "package": "saphes",
        "version": SAPHES_VERSION,
        "long_word_threshold": LONG_WORD_THRESHOLD,
        "calibrated_threshold": HU_THRESHOLD,
        "length_policy": LENGTH_POLICY,
        "mattr_window": MATTR_WINDOW,
        "min_words": MIN_WORDS,
    }
