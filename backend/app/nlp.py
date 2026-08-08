"""HuSpaCy lemmatization + entity extraction for the word cloud (WCLOUD-2).

The sitting-day word cloud ranks topical words by TF·IDF. Raw surface forms make a
poor unit of meaning in Hungarian — a richly inflected, agglutinative language —
because *törvény*, *törvényt*, *törvényben*, *törvények* are all the same concept
yet would be counted (and IDF-weighted) as four different words, diluting the
signal and scattering a day's real theme across a dozen inflections. So instead of
the regex tokenizer we lemmatize with a HuSpaCy model — the transformer
(``hu_core_news_trf``) by default, typically offloaded to Modal GPU workers; a
lighter CPU model (``hu_core_news_md``) can be swapped in via
``PARLAMONITOR_HUSPACY_MODEL`` — collapsing inflected forms to their dictionary
lemma, and use its named-entity recognizer to keep multi-word entities ("Orbán
Viktor", "Európai Unió") together as single terms instead of splitting them into
low-value fragments ("orbán", "viktor").

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

# model name → loaded pipeline (or None after a failed load attempt). Two models
# can be active in one build since archive cycles may use a cheaper model than
# the current cycle (see settings.huspacy_model_archive).
_pipelines: dict[str, object | None] = {}

# Pipeline components we never need: we feed already-split sentences (senter) and
# nothing downstream reads dependency arcs (parser). The trf model's dependency
# parser is a spacy-experimental biaffine pair — it must be disabled ALONG WITH
# senter, since with senter off it would crash on the missing sentence
# boundaries (E030). Intersected with the model's actual pipe names at load.
_DISABLED_PIPES = ("parser", "senter",
                   "experimental_arc_predicter", "experimental_arc_labeler")


def get_nlp(model: str | None = None):
    """Lazily load (once per model) a HuSpaCy model, or ``None`` if unavailable.
    ``model`` defaults to the configured primary (``settings.huspacy_model``).

    Only the components the word cloud needs run — the dependency parser and the
    sentence segmenter are disabled (``_DISABLED_PIPES``, intersected with the
    model's actual pipeline since the component set and names vary across HuSpaCy
    models), leaving the embedding/transformer, tagger/morphologizer, lemmatizer
    and NER. A missing model or import is logged once and degrades to ``None`` so
    callers can fall back to the regex tokenizer.
    """
    model = model or settings.huspacy_model
    if model in _pipelines:
        return _pipelines[model]
    try:
        import spacy
        pipe = spacy.load(model)
        for name in _DISABLED_PIPES:
            if name in pipe.pipe_names:
                pipe.disable_pipe(name)
        logger.info("Loaded HuSpaCy model %s (pipes: %s)",
                    model, ", ".join(pipe.pipe_names))
    except Exception as exc:  # ImportError, OSError (model not installed), …
        pipe = None
        logger.warning("HuSpaCy model %s unavailable (%s); word cloud falls back "
                       "to the regex tokenizer", model, exc)
    _pipelines[model] = pipe
    return pipe


def available(model: str | None = None) -> bool:
    """Whether the HuSpaCy model can be loaded (so the loader can pick a backend)."""
    return get_nlp(model) is not None


def method_tag(model: str | None = None) -> str:
    """A short identifier of the extraction method for ``model`` (default: the
    configured primary), embedded in the processing cache so that swapping the
    model / changing the logic busts it."""
    return f"huspacy:{model or settings.huspacy_model}:v{_LOGIC_VERSION}"


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


def _span_key(ent, min_len: int) -> str | None:
    """Shared normaliser behind every span keyer: the span's tokens' lemmas joined,
    which removes inflection ("Orbán Viktornak" → "Orbán Viktor") and keeps a
    multi-word name in one piece. ``None`` when the result is shorter than
    ``min_len`` bare characters, is a bare stop-word, or carries no letter at all."""
    key = " ".join(t.lemma_ for t in ent if not t.is_space).strip()
    bare = key.replace(" ", "")
    if len(bare) < min_len or key.lower() in STOPWORDS:
        return None
    if not any(ch.isalpha() for ch in key):
        return None
    return key


def _person_key(ent) -> str | None:
    """Normalized key for a PERSON entity span. Looser than ``_entity_key`` — a
    name may be short (e.g. "Áder") — but still requires an alphabetic
    multi-character token so pronouns/noise don't leak in."""
    return _span_key(ent, 3)


def _org_key(ent) -> str | None:
    """Normalized key for an ORGANISATION/institution span ("a Magyar Nemzeti
    Bankban" → "Magyar Nemzeti Bank"). Allows a short bare form (min 2 chars) so
    institution acronyms — "EU", "MNB", "NAV" — survive. Unmatched keys never
    render (they link to nothing), so being permissive here only risks harmless
    invisible rows."""
    return _span_key(ent, 2)


def _loc_key(ent) -> str | None:
    """Normalized key for a LOCATION span ("Brüsszelben" → "Brüsszel"). Kept at the
    same permissive 2-char floor as ORG so short place names and abbreviations
    ("Bp.", "USA") survive."""
    return _span_key(ent, 2)


def _misc_key(ent) -> str | None:
    """Normalized key for a MISC span — HuSpaCy's catch-all for named things that
    are neither person, place nor organisation (laws, events, products, works).
    Noisier than the other three, which is why it is stored but never linked."""
    return _span_key(ent, 2)


# Every NER label HuSpaCy emits, with its key normaliser: all four are extracted
# and stored as mentions, so the corpus keeps a complete entity layer (places in
# particular are wanted for later work). Only ``LINKABLE_LABELS`` are ever
# resolved to a destination and rendered inline — LOC/MISC rows exist in the DB
# for analysis and stay invisible in the UI.
_SPAN_KEYERS = {"PER": _person_key, "ORG": _org_key,
                "LOC": _loc_key, "MISC": _misc_key}

# The subset of extracted kinds that gets resolved to inline destinations
# (``entity_link``) and therefore shows up on the site. Consumers filter on this
# rather than on "every kind in the table".
LINKABLE_LABELS: frozenset[str] = frozenset({"PER", "ORG"})


def entity_spans(texts, *, batch_size: int = 128, n_process: int = 1,
                 model: str | None = None):
    """Per-text named-entity mentions — every label the model emits (NEL).

    Yields, for each input text in order, a list of
    ``(surface, char_start, char_end, key, kind)`` tuples — one per recognized
    span, where ``surface`` is the exact substring in the text (offsets relative to
    that text), ``key`` is its inflection-normalized name (the join key for
    ``entity_link``) and ``kind`` is the NER label: ``"PER"``, ``"ORG"``, ``"LOC"``
    or ``"MISC"``. Persisting all four keeps the entity layer complete; only the
    ``LINKABLE_LABELS`` subset is ever linked and shown."""
    nlp = get_nlp(model)
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


def _diversity_lemma(tok) -> str | None:
    """The lexical-diversity token for one spaCy token, or ``None`` to drop it.

    Deliberately **not** the word cloud's filter: diversity asks "how much of the
    vocabulary is distinct", so it counts the running text — function words
    included — and only drops what is not vocabulary at all (punctuation,
    whitespace, bare numerals, and anything with no letter in it). Case folding is
    left to saphes, which records that it happened."""
    if tok.is_punct or tok.is_space or tok.like_num:
        return None
    lemma = tok.lemma_.strip()
    if not lemma or not any(ch.isalpha() for ch in lemma):
        return None
    return lemma


def lemma_streams(texts, *, batch_size: int = 128, n_process: int = 1,
                  model: str | None = None):
    """Per-text **ordered lemma streams**, the input lexical diversity requires.

    Yields, for each input text in order, the list of its lemmas (see
    ``_diversity_lemma`` for what is kept). Order matters — MATTR slides a window
    over the sequence — so unlike :func:`analyze_counts` nothing is aggregated
    here; the caller (``app.readability.diversity``) groups sentences into speeches
    and measures each one.

    Surface forms would be the wrong stream: in Hungarian *ház / házak / házban /
    házakat* is one word inflected four ways, and counting them as four types
    reports morphology as vocabulary (see ``app/readability.py``). That is why this
    needs the model at all, and why the diversity half is omitted rather than
    approximated when no model is reachable."""
    nlp = get_nlp(model)
    if nlp is None:
        raise RuntimeError("HuSpaCy model not available")
    for doc in nlp.pipe([t or "" for t in texts], batch_size=batch_size,
                        n_process=n_process):
        yield [lemma for lemma in (_diversity_lemma(t) for t in doc) if lemma]


def analyze_counts(texts, *, batch_size: int = 128, n_process: int = 1,
                   model: str | None = None):
    """Lemmatized + entity-aware term frequencies for ``texts``.

    Returns ``(counts, entity_words)`` where ``counts`` is a ``Counter`` of
    term→occurrences and ``entity_words`` is the subset that came from a named
    entity (so the caller can tag them). A token that is part of a kept entity is
    counted *only* as that entity, never also on its own, to avoid double counting.
    """
    nlp = get_nlp(model)
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
