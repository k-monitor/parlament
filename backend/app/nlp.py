"""HuSpaCy lemmatization + entity extraction for the word cloud (WCLOUD-2).

The sitting-day word cloud ranks topical words by TF·IDF. Raw surface forms make a
poor unit of meaning in Hungarian — a richly inflected, agglutinative language —
because *törvény*, *törvényt*, *törvényben*, *törvények* are all the same concept
yet would be counted (and IDF-weighted) as four different words, diluting the
signal and scattering a day's real theme across a dozen inflections. So instead of
the regex tokenizer we lemmatize with HuSpaCy's smallest model
(``hu_core_news_md``), collapsing inflected forms to their dictionary lemma, and
use its named-entity recognizer to keep multi-word entities ("Orbán Viktor",
"Európai Unió") together as single terms instead of splitting them into low-value
fragments ("orbán", "viktor").

This neural pipeline is expensive (~200 sentences/s on one core), so it is run
**once at load time** and the per-session results are cached on disk and
precomputed into the DB (see ``loader.rebuild_session_word_counts``); the request
path never invokes spaCy. When the model is not installed the loader degrades to
the dependency-free regex tokenizer (``wordfreq``) so the build still succeeds —
controlled by the ``wordcloud_backend`` setting (OPS-4).

The lemma casing rule keeps the unit stable across a word's appearances:
proper-noun lemmas (and named entities, which are mostly proper nouns) keep their
natural capitalization ("Magyarország", "Orbán Viktor") while every other lemma is
lower-cased — and crucially a proper noun produces the *same* key whether it is
recognized as a named entity in one sentence or seen as a bare ``PROPN`` token in
another, so the two never fragment the count.
"""

from __future__ import annotations

import logging
import os

from collections import Counter

from .config import settings
from .wordfreq import MIN_LENGTH, STOPWORDS

logger = logging.getLogger("parlamonitor.nlp")

# Parts of speech that carry topical meaning. Verbs/adverbs/function words are
# dropped: the cloud answers "what was talked about", which lives in the nouns,
# proper nouns and adjectives. Configurable (OPS-4) without a code change.
CONTENT_POS: frozenset[str] = frozenset(
    (os.environ.get("PARLAMONITOR_WORDCLOUD_POS") or "NOUN,PROPN,ADJ")
    .replace(" ", "").split(","))

# Named-entity labels worth keeping as multi-word terms. PER/LOC/ORG are the
# meaningful named entities; MISC is deliberately excluded — it mostly catches
# noise (stray acronyms, capitalised common words) that the lemma path handles
# better. Configurable (OPS-4).
ENT_LABELS: frozenset[str] = frozenset(
    (os.environ.get("PARLAMONITOR_WORDCLOUD_ENT_LABELS") or "PER,LOC,ORG")
    .replace(" ", "").split(","))

# Bump this when the extraction logic changes in a way that should invalidate the
# on-disk processing cache even if the model and text are unchanged.
_LOGIC_VERSION = 2

_nlp = None
_load_attempted = False


def get_nlp():
    """Lazily load (once) the HuSpaCy model, or ``None`` if it is unavailable.

    Only the components the word cloud needs run — the dependency parser and the
    sentence segmenter are disabled (we feed already-split sentences), leaving the
    tagger/morphologizer, lemmatizer and NER. A missing model or import is logged
    once and degrades to ``None`` so callers can fall back to the regex tokenizer.
    """
    global _nlp, _load_attempted
    if _load_attempted:
        return _nlp
    _load_attempted = True
    try:
        import spacy
        _nlp = spacy.load(settings.huspacy_model, disable=["parser", "senter"])
        logger.info("Loaded HuSpaCy model %s (pipes: %s)",
                    settings.huspacy_model, ", ".join(_nlp.pipe_names))
    except Exception as exc:  # ImportError, OSError (model not installed), …
        _nlp = None
        logger.warning("HuSpaCy model %s unavailable (%s); word cloud falls back "
                       "to the regex tokenizer", settings.huspacy_model, exc)
    return _nlp


def available() -> bool:
    """Whether the HuSpaCy model can be loaded (so the loader can pick a backend)."""
    return get_nlp() is not None


def method_tag() -> str:
    """A short identifier of the active extraction method, embedded in the
    processing cache so that swapping the model / changing the logic busts it."""
    return f"huspacy:{settings.huspacy_model}:v{_LOGIC_VERSION}"


def _lemma_key(tok) -> str | None:
    """The cloud key for a single token, or ``None`` to drop it.

    Keeps proper-noun lemmas capitalised, lower-cases everything else, and applies
    the same length / stop-word / numeric filters as the regex path (WCLOUD-2)."""
    if tok.pos_ not in CONTENT_POS:
        return None
    if tok.is_stop or tok.is_punct or tok.is_space or tok.like_num:
        return None
    lemma = tok.lemma_.strip()
    if not lemma:
        return None
    key = lemma if tok.pos_ == "PROPN" else lemma.lower()
    if len(key) < MIN_LENGTH or key.lower() in STOPWORDS:
        return None
    if not any(ch.isalpha() for ch in key):
        return None
    return key


def _entity_key(ent) -> str | None:
    """The cloud key for a named entity span (its tokens' lemmas joined), or
    ``None`` to drop it. Joining lemmas normalises inflection ("Orbán Viktorral"
    → "Orbán Viktor") and keeps the entity a single multi-word term."""
    if ent.label_ not in ENT_LABELS:
        return None
    key = " ".join(t.lemma_ for t in ent if not t.is_space).strip()
    bare = key.replace(" ", "")
    if len(bare) < MIN_LENGTH or key.lower() in STOPWORDS:
        return None
    if not any(ch.isalpha() for ch in key):
        return None
    return key


def _person_key(ent) -> str | None:
    """Normalized key for a PERSON entity span (lemma-joined, inflection removed:
    "Orbán Viktornak" → "Orbán Viktor"), or ``None`` to drop it. Looser than
    ``_entity_key`` — a name may be short (e.g. "Áder") — but still requires an
    alphabetic multi-character token so pronouns/noise don't leak in."""
    key = " ".join(t.lemma_ for t in ent if not t.is_space).strip()
    bare = key.replace(" ", "")
    if len(bare) < 3 or key.lower() in STOPWORDS:
        return None
    if not any(ch.isalpha() for ch in key):
        return None
    return key


def _org_key(ent) -> str | None:
    """Normalized key for an ORGANISATION/institution span (lemma-joined,
    inflection removed: "a Magyar Nemzeti Bankban" → "Magyar Nemzeti Bank"), or
    ``None`` to drop it. Allows a short bare form (min 2 chars) so institution
    acronyms — "EU", "MNB", "NAV" — survive; anything with no alphabetic character
    or that is a bare stop-word is dropped. Unmatched keys never render (they link
    to nothing), so being permissive here only risks harmless invisible rows."""
    key = " ".join(t.lemma_ for t in ent if not t.is_space).strip()
    bare = key.replace(" ", "")
    if len(bare) < 2 or key.lower() in STOPWORDS:
        return None
    if not any(ch.isalpha() for ch in key):
        return None
    return key


# NER labels linked inline in the transcript, with their key normaliser. PER →
# people, ORG → institutions (LOC/MISC are deliberately excluded: places and the
# MISC noise bucket aren't link-worthy entities here).
_SPAN_KEYERS = {"PER": _person_key, "ORG": _org_key}


def entity_spans(texts, *, batch_size: int = 128, n_process: int = 1):
    """Per-text PERSON + ORGANISATION mentions for inline transcript linking (NEL).

    Yields, for each input text in order, a list of
    ``(surface, char_start, char_end, key, kind)`` tuples — one per recognized PER
    or ORG span, where ``surface`` is the exact substring in the text (offsets
    relative to that text), ``key`` is its inflection-normalized name (the join key
    for ``entity_link``) and ``kind`` is ``"PER"`` or ``"ORG"``."""
    nlp = get_nlp()
    if nlp is None:
        raise RuntimeError("HuSpaCy model not available")
    for doc in nlp.pipe([t or "" for t in texts], batch_size=batch_size, n_process=n_process):
        spans = []
        for ent in doc.ents:
            keyer = _SPAN_KEYERS.get(ent.label_)
            if keyer is None:
                continue
            key = keyer(ent)
            if key is None:
                continue
            spans.append((ent.text, ent.start_char, ent.end_char, key, ent.label_))
        yield spans


def person_spans(texts, *, batch_size: int = 128, n_process: int = 1):
    """PERSON-only span mentions — a thin wrapper over :func:`entity_spans` kept for
    callers that want just people. Yields ``(surface, start, end, key)`` per PER
    span (the ``kind`` is dropped since it is always ``"PER"``)."""
    for spans in entity_spans(texts, batch_size=batch_size, n_process=n_process):
        yield [(s, a, b, k) for (s, a, b, k, kind) in spans if kind == "PER"]


def analyze_counts(texts, *, batch_size: int = 128, n_process: int = 1):
    """Lemmatized + entity-aware term frequencies for ``texts``.

    Returns ``(counts, entity_words)`` where ``counts`` is a ``Counter`` of
    term→occurrences and ``entity_words`` is the subset that came from a named
    entity (so the caller can tag them). A token that is part of a kept entity is
    counted *only* as that entity, never also on its own, to avoid double counting.
    """
    nlp = get_nlp()
    if nlp is None:
        raise RuntimeError("HuSpaCy model not available")
    counts: Counter[str] = Counter()
    entity_words: set[str] = set()
    texts = [t for t in texts if t]
    for doc in nlp.pipe(texts, batch_size=batch_size, n_process=n_process):
        consumed: set[int] = set()
        for ent in doc.ents:
            key = _entity_key(ent)
            if key is None:
                continue
            counts[key] += 1
            entity_words.add(key)
            consumed.update(t.i for t in ent)
        for tok in doc:
            if tok.i in consumed:
                continue
            key = _lemma_key(tok)
            if key is not None:
                counts[key] += 1
    return counts, entity_words
