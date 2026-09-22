"""CAP policy-topic classification of speech paragraphs (TOPIC-1..7).

What a speech is *about*, in the vocabulary political scientists already use: the
21 major topics of the `Comparative Agendas Project
<https://www.comparativeagendas.net/pages/master-codebook>`_ master codebook, plus
"Other" for talk that carries no policy content at all.

The model is `classla/ParlaCAP-Topic-Classifier
<https://huggingface.co/classla/ParlaCAP-Topic-Classifier>`_ — XLM-R-large further
pretrained on parliamentary proceedings (``classla/xlm-r-parla``) and fine-tuned on
29 ParlaMint 4.1 corpora, **ParlaMint-HU among them**. Hungarian plenary speech is
therefore in-domain for it, not merely covered by a multilingual tokenizer. It is a
single-label classifier: one topic and one confidence per input.

Why paragraph-sized blocks
--------------------------
The unit is a **block of roughly paragraph size**, not the speech and not the
sentence, and the choice is forced from both directions:

* *Not the speech.* The model truncates at 512 tokens, and 58 % of this corpus's
  speeches are longer than that — the truncated ones run to a median of 1096
  tokens, so less than half the text would ever reach the model. Worse, the half it
  would see is the opening, which in Hungarian plenary style is greetings and
  framing ("Köszönöm a szót, elnök úr. Tisztelt Ház!") rather than argument.
  Measured on this corpus, head-truncation and averaging over the whole speech
  disagree on 19.5 % of long speeches, so feeding whole speeches would decide a
  fifth of the long ones on their salutations.
* *Not the sentence.* A single sentence rarely carries enough context to place a
  topic, and the model was fitted on speech-length input.

The obvious unit would be the transcript's own paragraph (``sentence.paragraph``),
and where that column is trustworthy it is exactly what a block turns out to be.
But it is trustworthy in only two of the corpus's five cycles, so blocks are
*assembled* to a word budget that prefers paragraph boundaries rather than trusting
them — see :func:`build_blocks`, which is where that mess is documented.

Why the threshold is not applied here
-------------------------------------
Every block's raw prediction — the winning label, its probability, and the
runner-up — is stored as-is. The confidence threshold that decides which
blocks are trustworthy enough to count is applied at **read** time
(:func:`aggregate`, driven by ``settings.parlacap_threshold``), never at write
time. That is deliberate: the threshold is a presentation policy, not a
measurement, and the operator must be able to retune it — the accuracy/coverage
trade-off below is a curve, not a fact — by changing one setting and restarting,
with no reclassification and not even a rebuild. Consequently the threshold is
**not** part of :func:`method_tag`, so changing it cannot invalidate the cache.

Measured on this corpus (1501 held-out paragraphs coded with the CAP minor
codebook by Gemini, mapped up to major topics), against the model's top-1 label:

===========  ==========  ==========
 threshold    coverage    accuracy
===========  ==========  ==========
 none         1.00        0.709
 0.60         0.90        0.741
 0.80         0.79        0.774
 **0.90**     **0.69**    **0.809**
 0.95         0.62        0.832
===========  ==========  ==========

The default is 0.90: roughly four in five surviving labels are right, at the cost
of saying nothing about the remaining third of paragraphs. Saying nothing is the
cheap error here — an unlabelled speech costs a reader a filter, a wrongly
labelled one costs them trust (TRUST-1).

Procedural speeches are excluded entirely, exactly as they are from the statistics
and the word cloud (STAT-1). This is not only consistency: the model's worst
failure mode is reading the Speaker's chairing announcements as policy, because it
keys on the *title of the bill being announced* ("Most soron következik a
rendvédelmi feladatokat ellátó szervek… törvényjavaslat" → Defense, 0.91). Those
rows carry ``speech.procedural = 1`` already, so the filter removes the failure
mode structurally rather than hoping a threshold catches it.

Running it, and not running it
------------------------------
Classification needs ``torch`` + ``transformers`` and realistically a GPU; the web
server has neither and must never need them. So the pass is split the way every
other expensive pass here is: results are cached on disk per sitting
(``parlacap-cache.json``), keyed by a fingerprint of the sitting's text *and* the
method, and a host that misses the cache without a usable model simply leaves
those speeches unlabelled rather than failing the build.

Two ways to fill a miss, and production uses both:

* **locally**, with torch installed — how the archive was classified, once, on a
  GPU box whose ``parlacap-cache.json`` is then copied to the server;
* **on Modal** (``app/parlacap_modal.py``, service in ``parlacap_modal_app.py``) —
  how the handful of *new* sittings a week get labelled, without the server ever
  installing torch. Metered, so ``PARLAMONITOR_MODAL_CYCLES`` (default ``latest``)
  keeps everything but the newest cycle off it; a backfill is for the GPU box.

Both run this same module against the same pinned model, so their output is
identical and :func:`method_tag` does **not** record which was used — where a
sitting was classified is not a property of the prediction, and the two must share
one cache. See DEPLOYMENT.md.
"""

from __future__ import annotations

import logging
import re

from .config import settings

logger = logging.getLogger("parlamonitor.parlacap")

MODEL_ID = "classla/ParlaCAP-Topic-Classifier"

# The model's own label order (``config.json`` id2label), pinned here so the cache
# can store a label *index* per block — tens of megabytes instead of hundreds —
# and so reading the cache back never requires the model to be present. Verified
# against the loaded model before any classification (:func:`_verify_labels`); a
# mismatch aborts rather than silently renaming every topic in the corpus.
LABELS = (
    "Education", "Technology", "Health", "Environment", "Housing", "Labor",
    "Defense", "Government Operations", "Social Welfare", "Other",
    "Macroeconomics", "Domestic Commerce", "Civil Rights", "International Affairs",
    "Transportation", "Immigration", "Law and Crime", "Agriculture",
    "Foreign Trade", "Culture", "Public Lands", "Energy",
)

# The one label that is not a policy topic. CAP's "Other" is procedural, rhetorical
# and interpersonal speech — points of order, greetings, jokes, arguments between
# members. It is a real and useful prediction (it is how the model says "there is no
# policy here"), but it can never be a speech's *subject*, so it is excluded from
# the dominant-topic vote and reported separately instead (:func:`aggregate`).
NON_POLICY = "Other"

# CAP major topic numbers, for callers that want the canonical code alongside the
# name (the codebook numbers are what comparative research joins on, and 12/21/23
# are deliberately non-contiguous — CAP has no majors 11 or 22).
CAP_CODES = {
    "Macroeconomics": 1, "Civil Rights": 2, "Health": 3, "Agriculture": 4,
    "Labor": 5, "Education": 6, "Environment": 7, "Energy": 8, "Immigration": 9,
    "Transportation": 10, "Law and Crime": 12, "Social Welfare": 13, "Housing": 14,
    "Domestic Commerce": 15, "Defense": 16, "Technology": 17, "Foreign Trade": 18,
    "International Affairs": 19, "Government Operations": 20, "Public Lands": 21,
    "Culture": 23, "Other": 0,
}

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Bump when anything that changes a stored prediction changes: the model, the
# truncation length, the block assembly, or the minimum length. NOT the threshold —
# that is applied at read time and must not invalidate the cache.
_METHOD_VERSION = "v1"

# The same, for the document pass (TOPIC-8): bump when paragraph recovery or the
# over-long-paragraph split changes what a stored document prediction would be.
_DOC_METHOD_VERSION = "v1"


def word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def method_tag(model: str | None = None) -> str:
    """Identifies what produced a stored prediction, and is hashed into each
    sitting's cache fingerprint so entries from different methods never collide."""
    return (f"parlacap:{model or settings.parlacap_model}"
            f":len{settings.parlacap_max_length}"
            f":min{settings.parlacap_min_words}"
            f":blk{settings.parlacap_target_words}-{settings.parlacap_max_words}"
            f":{_METHOD_VERSION}")


def build_blocks(sentences, target: int | None = None,
                 maximum: int | None = None) -> list[tuple[int, int, str]]:
    """Group a speech's sentences into classification blocks.

    ``sentences`` is ``[(paragraph_index, cleaned_text), …]`` in speech order; the
    result is ``[(block_ordinal, first_paragraph_index, text), …]``.

    Why this is not simply "group by ``sentence.paragraph``". The corpus spans five
    electoral cycles that were transcribed and ingested differently, and the
    paragraph column means something different in each:

    ==========  ===========================================================
     cycle       what ``sentence.paragraph`` holds
    ==========  ===========================================================
     42, 43      real paragraphs (median ~65 words) — usable as-is
     41          NULL throughout; the whole speech is one block (p90 = 1274
                 words, four times what the model can read)
     39, 40      the source transcript's *line* breaks, so rows are line
                 fragments ("Köszönöm a" / "szót, elnök úr.") and 18 % of
                 "paragraphs" are under 12 words
    ==========  ===========================================================

    Trusting the column would therefore give cycle 43 clean paragraph labels, cycle
    41 truncated speeches, and cycle 39 a stream of confidently-labelled fragments —
    the annotation's quality would silently depend on which cycle you were reading.

    So paragraph marks are treated as *preferred* split points rather than
    authoritative ones. A block accumulates sentences and closes at a paragraph
    boundary once it has reached ``target`` words, or unconditionally at ``maximum``
    words (the model's token limit, in words). Where the marks are real and
    paragraphs are of ordinary length this closes on every one of them and the
    blocks *are* the paragraphs; where they are missing or spurious it falls back to
    a word budget, which is the only thing left that means the same in every cycle.

    A trailing runt below ``settings.parlacap_min_words`` is merged back into the
    previous block rather than emitted: it would not be classified on its own, and
    dropping it would silently lose a speech's closing sentences from the vote.
    """
    target = settings.parlacap_target_words if target is None else target
    maximum = settings.parlacap_max_words if maximum is None else maximum

    blocks: list[tuple[int, int, str]] = []
    cur: list[str] = []
    cur_words = 0
    cur_para: int | None = None

    def _close():
        nonlocal cur, cur_words, cur_para
        if cur:
            blocks.append((len(blocks), cur_para or 0, " ".join(cur).strip()))
        cur, cur_words, cur_para = [], 0, None

    prev_para = None
    for para, text in sentences:
        if not text:
            continue
        words = word_count(text)
        # A paragraph boundary is where this sentence's paragraph index differs
        # from the previous one. Both tests close the block *before* appending, so
        # the boundary falls between blocks and the cap is an upper bound rather
        # than a threshold the last sentence overshoots — a block that closed only
        # once it was already over would run to `maximum` plus a whole sentence,
        # which is how cycle 41 ended up past the token limit it was meant to
        # respect. A single sentence longer than `maximum` still forms its own
        # oversized block: there is no split point inside it that isn't arbitrary.
        if cur and ((prev_para is not None and para != prev_para and cur_words >= target)
                    or cur_words + words > maximum):
            _close()
        if cur_para is None:
            cur_para = para
        cur.append(text)
        cur_words += words
        prev_para = para
    _close()

    if len(blocks) > 1 and word_count(blocks[-1][2]) < settings.parlacap_min_words:
        tail = blocks.pop()[2]
        ordinal, first_para, text = blocks[-1]
        blocks[-1] = (ordinal, first_para, f"{text} {tail}".strip())
    return blocks


# ---------------------------------------------------------------------------
# document text (TOPIC-8): the same classifier over iromány PDFs
# ---------------------------------------------------------------------------
# A speech reaches us as sentences with a paragraph column; an iromány reaches us
# as whatever `pdftotext` made of a PDF — hard-wrapped lines, a blank line between
# paragraphs, and a scanned cover sheet's worth of stamp noise at the top. So the
# unit has to be recovered from the layout before the same block assembly can run
# over it. Everything downstream (the word budget, the threshold, the aggregation)
# is deliberately shared with the speech path: a topic must mean the same thing
# whichever kind of text it was read off.

_PARA_SPLIT_RE = re.compile(r"\n\s*\n")
# A sentence-ish boundary, used only to break up a paragraph that is too long for
# the model to read whole. Requires the next line to start like a new sentence, so
# the legal-citation full stops that fill this corpus ("2013. évi XXXVI. törvény",
# "1. §") do not split it into fragments.
_SENT_SPLIT_RE = re.compile(r'(?<=[.!?:])\s+(?=[A-ZÁÉÍÓÖŐÚÜŰ„"(\[])')


def document_paragraphs(text: str) -> list[str]:
    """Recover an iromány's paragraphs from extracted PDF text.

    ``pdftotext`` hard-wraps to the PDF's own line breaks and separates paragraphs
    with a blank line, so a paragraph is a run of non-blank lines joined back into
    one string. Nothing is filtered here: the cover sheet's stamp fragments
    ("Országgyűlés Hivatala", "Érkezett:", the OCR noise around a signature) come
    through as very short paragraphs and are handled downstream, where
    ``parlacap_min_words`` already declines to classify them on their own."""
    out: list[str] = []
    for para in _PARA_SPLIT_RE.split(text or ""):
        joined = " ".join(l.strip() for l in para.splitlines() if l.strip())
        joined = re.sub(r"\s+", " ", joined).strip()
        if joined:
            out.append(joined)
    return out


def _split_oversized(para: str, maximum: int) -> list[str]:
    """Break a paragraph the model could not read whole into readable pieces.

    :func:`build_blocks` lets a single over-long *sentence* stand as its own
    oversized block, on the grounds that any split inside it is arbitrary. That is
    the right call for speech, where the unit is a sentence; it is the wrong one
    here, because a document's paragraph routinely runs past the token limit (a
    single recital in a beszámoló can be 400+ words) and letting it stand whole
    means the tokenizer silently drops its second half. Splitting on sentence
    boundaries first, and only then on a word budget, keeps all of the text in
    front of the model."""
    if word_count(para) <= maximum:
        return [para]
    pieces: list[str] = []
    for part in _SENT_SPLIT_RE.split(para):
        if not part.strip():
            continue
        if word_count(part) <= maximum:
            pieces.append(part.strip())
            continue
        words = part.split()
        for i in range(0, len(words), maximum):
            pieces.append(" ".join(words[i:i + maximum]))
    return pieces or [para]


def build_text_blocks(text: str, target: int | None = None,
                      maximum: int | None = None) -> list[tuple[int, int, str]]:
    """Group an iromány's extracted text into classification blocks.

    Returns the same ``[(block_ordinal, first_paragraph_index, text)]`` shape the
    speech path produces, and reaches it through the same :func:`build_blocks`
    word budget — the paragraphs recovered from the PDF simply take the place of
    the transcript's paragraph column, and here they are real rather than a
    per-cycle guess.

    **The cover sheet is deliberately kept.** Every iromány opens with an
    administrative block — the registry stamp, the addressee, and *the subject line*
    — and the obvious instinct is to strip it as boilerplate. Measured over all 356
    cycle-43 documents, dropping the first block changes the verdict on 3 of them
    (none of the three clearly for the better) and **silences 32** outright: for a
    short personnel or procedural iromány the cover sheet is the only text that
    names what the document is about. It stays.
    """
    target = settings.parlacap_target_words if target is None else target
    maximum = settings.parlacap_max_words if maximum is None else maximum
    units: list[tuple[int, str]] = []
    for i, para in enumerate(document_paragraphs(text)):
        for piece in _split_oversized(para, maximum):
            units.append((i, piece))
    return build_blocks(units, target, maximum)


def document_method_tag(model: str | None = None) -> str:
    """:func:`method_tag` for the document pass.

    Distinct from the speech tag even at identical settings: the same model over
    the same words would still be split differently (paragraphs recovered from PDF
    layout, over-long paragraphs sub-split), so a cache entry from one pass must
    never be mistaken for the other's."""
    return method_tag(model) + f":doc{_DOC_METHOD_VERSION}"


def methodology() -> dict:
    """What ``/meta`` publishes about how these labels were produced (TRUST-1).

    The threshold is included because it changes what the reader is being shown —
    at 0.90 a third of paragraphs are deliberately left unlabelled — and a figure
    on the site has to be able to say what produced it (REP-5)."""
    return {
        "model": settings.parlacap_model,
        "scheme": "CAP major topics",
        "scheme_url": "https://www.comparativeagendas.net/pages/master-codebook",
        "unit": "paragraph",
        "threshold": settings.parlacap_threshold,
        "max_length": settings.parlacap_max_length,
        "min_words": settings.parlacap_min_words,
        "labels": list(LABELS),
    }


# ---------------------------------------------------------------------------
# aggregation (pure stdlib — this half runs on the web server)
# ---------------------------------------------------------------------------

def aggregate(rows, threshold: float | None = None) -> dict | None:
    """Roll a speech's paragraph predictions up into one topic (TOPIC-4).

    ``rows`` are this speech's stored paragraphs as
    ``(paragraph, label, score, runner_up, runner_score, words)``; the return value
    is ``None`` when the speech has no paragraph confident enough to say anything
    about, which is the honest answer for roughly a third of them.

    Three decisions are worth stating, because none is forced by the data:

    * **Weight by words, not by paragraph count.** A speech's subject is what it
      spends its words on. Counting paragraphs would let a one-line aside outvote
      three long paragraphs of argument.
    * **Only paragraphs at or above the threshold vote.** Below it the model's
      label is barely better than a guess (0.709 accuracy overall against 0.809 at
      0.90), so those paragraphs are counted as *coverage lost*, not as evidence.
    * **"Other" cannot win.** It is not a policy topic (see :data:`NON_POLICY`);
      a speech that opens with two paragraphs of greetings and then argues about
      hospital funding is about health care, not about greetings. Its weight is
      reported as ``other_share`` so the reader can see how much of the speech was
      procedural, and a speech with *nothing* but confident "Other" gets ``None``.

    ``breakdown`` carries every competing policy topic, so the UI can show what the
    dominant label won against rather than asserting it unopposed.
    """
    if threshold is None:
        threshold = settings.parlacap_threshold
    rows = list(rows)
    if not rows:
        return None

    classified = sum(r[5] for r in rows)                 # words with a prediction
    confident = [r for r in rows if r[2] is not None and r[2] >= threshold]
    if not confident:
        return None

    confident_words = sum(r[5] for r in confident)
    other_words = sum(r[5] for r in confident if r[1] == NON_POLICY)
    policy = [r for r in confident if r[1] != NON_POLICY]
    if not policy:
        return None

    policy_words = sum(r[5] for r in policy)
    by_label: dict[str, dict] = {}
    for _para, label, score, _ru, _rus, words in policy:
        e = by_label.setdefault(label, {"words": 0, "paragraphs": 0, "weighted": 0.0})
        e["words"] += words
        e["paragraphs"] += 1
        e["weighted"] += (score or 0.0) * words

    breakdown = sorted(
        ({"label": lab,
          "code": CAP_CODES.get(lab),
          "share": e["words"] / policy_words if policy_words else 0.0,
          "paragraphs": e["paragraphs"],
          "confidence": e["weighted"] / e["words"] if e["words"] else 0.0}
         for lab, e in by_label.items()),
        # Words first; paragraph count breaks a tie between two labels that split
        # the speech evenly, and the label name breaks it after that so the order
        # is deterministic across servers rather than dict-insertion dependent.
        key=lambda d: (-d["share"], -d["paragraphs"], d["label"]))

    top = breakdown[0]
    return {
        "label": top["label"],
        "code": top["code"],
        "share": top["share"],
        "confidence": top["confidence"],
        "paragraphs": top["paragraphs"],
        "breakdown": breakdown,
        # How much of the speech the label actually speaks for. `coverage` is the
        # share of classified words that cleared the threshold at all — the reader's
        # cue that a label rests on a quarter of a long speech rather than all of it.
        "other_share": other_words / confident_words if confident_words else 0.0,
        "coverage": confident_words / classified if classified else 0.0,
        "threshold": threshold,
    }


def topic_mix(rows, dominant: dict, coverage: dict | None = None) -> dict:
    """Roll a whole slice of the corpus up into its topic mix (TOPIC-9).

    ``rows`` are ``(period, label, confident, words, blocks)`` groups — one scan of
    a block table, grouped by calendar period, label, and whether the block cleared
    the threshold. ``dominant`` maps a label to how many *items* (speeches,
    irományok) it is the subject of, by the rule in :func:`aggregate` expressed as
    :data:`DOMINANT_TOPIC_SQL`. ``coverage`` is whatever the caller can say about
    the population behind the numbers; its keys travel through untouched.

    Both halves of the Témák page go through this one function on purpose. The
    speech blocks and the document blocks live in different tables with different
    scoping columns, but the chart puts their shares side by side, and a share
    computed one way for speeches and another way for irományok would be a
    comparison of nothing — the same reason the two are read off one model at one
    threshold in the first place (TOPIC-8).

    Three decisions it inherits from :func:`aggregate`, so the page and the chip
    cannot disagree:

    * **words, not blocks**, are the weight — a topic's share is of the classified
      *policy text*, so a long argument outweighs a one-line aside;
    * **"Other" never counts as a topic** (:data:`NON_POLICY`). It is reported as
      ``other_words`` — how much of the confident text carried no policy at all —
      and is excluded from every share;
    * **below the threshold is not evidence**. Those blocks are counted only into
      ``blocks`` (against ``confident_blocks``), which is what lets the page say
      how much of the corpus it is *not* speaking for.
    """
    words: dict[str, int] = {}
    blocks: dict[str, int] = {}
    by_period: dict[str, dict[str, int]] = {}
    other_words = 0
    all_blocks = 0
    confident_blocks = 0

    for period, label, confident, w, b in rows:
        all_blocks += b
        if not confident:
            continue
        confident_blocks += b
        if label == NON_POLICY:
            other_words += w
            continue
        words[label] = words.get(label, 0) + w
        blocks[label] = blocks.get(label, 0) + b
        if period:
            bucket = by_period.setdefault(period, {})
            bucket[label] = bucket.get(label, 0) + w

    policy_words = sum(words.values())
    topics = sorted(
        ({"label": lab,
          "code": CAP_CODES.get(lab),
          "words": w,
          "blocks": blocks.get(lab, 0),
          "share": w / policy_words if policy_words else 0.0,
          "count": dominant.get(lab, 0)}
         for lab, w in words.items()),
        # Words first, the label name after — the same deterministic order the
        # per-item breakdown uses, so two servers answer identically.
        key=lambda d: (-d["words"], d["label"]))

    return {
        "threshold": settings.parlacap_threshold,
        "coverage": {**(coverage or {}),
                     "blocks": all_blocks,
                     "confident_blocks": confident_blocks,
                     "policy_words": policy_words,
                     "other_words": other_words},
        "topics": topics,
        # One bucket per period that has any confident policy text, each carrying
        # its own total so a share can be taken against the period rather than
        # against the corpus — the agenda's *shape* in a quiet year is the point,
        # not that the year was quiet.
        "trend": {"granularity": "year",
                  "buckets": [{"period": p,
                               "policy_words": sum(by_period[p].values()),
                               "labels": by_period[p]}
                              for p in sorted(by_period)]},
    }


# The same winner :func:`aggregate` picks, expressed in SQL — so a *filter* on
# topic selects exactly the irományok whose chip shows that topic.
#
# It has to be a query rather than a stored column because the threshold is a
# read-time policy (TOPIC-6): the dominant topic is not a fact about the row, it
# is a fact about the row *and the setting in force*, and an operator must be able
# to move that setting with a restart. So the aggregation is redone per request,
# over an index, for the page being served.
#
# **The ORDER BY is the tie-break in :func:`aggregate`, and the two must not
# drift.** Words first (a document is about what it spends its words on), then
# block count to separate two labels that split it evenly, then the label name so
# the answer is deterministic rather than dependent on scan order. ``share`` there
# is words over the same per-document total, so ordering by ``SUM(words)`` is the
# same ordering. "Other" is excluded here exactly as it is excluded from the vote:
# it is not a policy topic and can never be a document's subject.
#
# ``{scope}`` is where a caller adds cheap pre-filters (a cycle, say) so the scan
# is bounded by an index rather than covering the whole corpus. Measured: 6.6 ms
# over cycle 43 as it stands (10.8k blocks), and ~200 ms against a synthetic
# fully-classified five-cycle archive (1.5M blocks, ~290k in the scanned cycle) —
# paid only when the filter is actually engaged. A covering
# ``(period_number, bill_id, label)`` index removes the GROUP BY's temp B-tree but
# saves ~4 % of that, the rest being the window sort, so it is not worth carrying
# on a table that size; revisit if the archive is ever classified in full.
DOMINANT_TOPIC_SQL = """
SELECT {key}, label FROM (
  SELECT {key}, label,
         ROW_NUMBER() OVER (PARTITION BY {key}
                            ORDER BY SUM(words) DESC, COUNT(*) DESC, label ASC) AS rk
  FROM {table}
  WHERE score >= :topic_threshold AND label <> :topic_non_policy {scope}
  GROUP BY {key}, label
) WHERE rk = 1
"""

# The two tables the rule applies to. It is the *same* rule in both: the topic a
# reader sees on an iromány's chip and the one they see on a speech's are picked
# by one tie-break (:func:`aggregate`), so the analysis that counts either of them
# must not re-invent it — see TOPIC-9.
DOMINANT_TOPIC_TABLES = {
    "bill": ("bill_topic", "bill_id"),
    "speech": ("speech_topic", "speech_id"),
}


def dominant_topic_sql(scope: str = "", kind: str = "bill") -> str:
    """:data:`DOMINANT_TOPIC_SQL` over one of :data:`DOMINANT_TOPIC_TABLES`, with
    ``scope`` spliced in.

    ``scope`` is appended to the inner ``WHERE`` and must be caller-built SQL with
    bound parameters — never interpolated user input. It can only name columns of
    the block table itself: the query has no join to hang anything else on, so a
    speech-side caller scoping by cycle writes a subquery over ``session`` (the
    block rows carry a sitting id, not a cycle number)."""
    table, key = DOMINANT_TOPIC_TABLES[kind]
    return DOMINANT_TOPIC_SQL.format(scope=scope, table=table, key=key)


def topic_params(threshold: float | None = None) -> dict:
    """The bound parameters :data:`DOMINANT_TOPIC_SQL` needs."""
    return {"topic_threshold": (settings.parlacap_threshold
                                if threshold is None else threshold),
            "topic_non_policy": NON_POLICY}


def has_topics(db, kind: str = "bill") -> bool:
    """Whether the (regenerable) DB carries one of the block tables — false on a
    DB built before the pass existed, or on one whose build had neither a model
    nor a cache to replay. Every read path checks it, because the feature has to
    degrade to silence rather than to a 500."""
    table, _key = DOMINANT_TOPIC_TABLES[kind]
    return bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table,)).fetchone())


def topics_for(db, ids, kind: str = "bill") -> dict[str, dict]:
    """Topics for a batch of speeches or irományok, ``{id: topic}``.

    Aggregated **on read** rather than looked up: nothing about an item's topic is
    stored, because the confidence threshold that decides which blocks count is an
    operator setting that must be retunable with a restart rather than a
    reclassification (TOPIC-6). The cost is one indexed query over the batch's
    block rows and a fold in Python.

    One function for both tables because it is one claim, and the two callers that
    used to carry a copy each differed in nothing but the table name — a topic
    must mean the same thing whichever text it was read off (TOPIC-8), down to the
    tie-break. Items with no confident block are simply absent from the result and
    end up with ``topic: null`` and no chip, which is the honest answer for about a
    third of speeches and a tenth of irományok.

    Duplicates in ``ids`` are collapsed: the order paper names the same iromány on
    several days, and its topic is a property of the document, not of the slot.
    """
    ids = list(dict.fromkeys(i for i in ids if i))
    if not ids or not has_topics(db, kind):
        return {}
    table, key = DOMINANT_TOPIC_TABLES[kind]
    out: dict[str, dict] = {}
    # Chunked to stay under SQLite's variable limit on a long sitting day or a
    # full page of irományok.
    for start in range(0, len(ids), 400):
        chunk = ids[start:start + 400]
        rows = db.execute(
            f"SELECT {key} AS item_id, block, label, score, runner_up, "
            f"runner_score, words FROM {table} WHERE {key} IN ("
            + ",".join("?" * len(chunk)) + f") ORDER BY {key}, block",
            chunk).fetchall()
        by_item: dict[str, list] = {}
        for r in rows:
            by_item.setdefault(r["item_id"], []).append(
                (r["block"], r["label"], r["score"], r["runner_up"],
                 r["runner_score"], r["words"]))
        for item_id, block_rows in by_item.items():
            agg = aggregate(block_rows)
            if agg:
                out[item_id] = agg
    return out


# ---------------------------------------------------------------------------
# inference (needs torch + transformers; never imported on the web server)
# ---------------------------------------------------------------------------

_pipeline: object | None = None


def backend(*, modal_ok: bool = True) -> str | None:
    """How this host can classify: ``"modal"``, ``"local"``, or ``None``.

    ``PARLAMONITOR_PARLACAP_BACKEND`` picks it, with the same precedence
    ``loader._nlp_backend`` uses for the word cloud — and the same crucial rule:
    **``auto`` never auto-selects Modal.** Modal is metered, so spending credit is
    always an explicit choice; ``auto`` (the default) uses a local torch install
    if there is one and otherwise declines. ``modal`` opts in and degrades to
    local if the client is missing or unauthenticated; ``local`` pins the local
    path; ``off`` refuses both while leaving the labels the DB already carries
    alone.

    That rule is what makes a GPU box safe to run unconfigured: it has a card and
    it very likely has Modal credentials too (for the HuSpaCy and Whisper
    offloads), and an ``auto`` that preferred Modal would quietly bill a
    600k-block archive backfill to a service that may not even be deployed.

    ``modal_ok=False`` means this sitting's electoral cycle is outside the metered
    scope (``PARLAMONITOR_MODAL_CYCLES``, default ``latest``), so Modal is not an
    option for it however it is configured — the second half of the same guard.
    """
    if not settings.parlacap:
        return None
    choice = (settings.parlacap_backend or "auto").strip().lower()
    if choice == "off":
        return None

    def _local() -> bool:
        try:
            import torch                                 # noqa: F401
            import transformers                          # noqa: F401
        except ImportError:
            return False
        return True

    if choice == "modal":
        if not modal_ok:
            logger.info("sitting is outside the Modal cycle scope (%s); trying a "
                        "local backend for it", settings.modal_cycles)
        else:
            from . import parlacap_modal
            if parlacap_modal.available():
                return "modal"
            logger.warning("parlacap_backend=modal unavailable; trying a local "
                           "torch install")
    if _local():
        return "local"
    if choice == "local":
        logger.warning("parlacap_backend=local but torch/transformers are not "
                       "installed; leaving these sittings unlabelled")
    return None


def available(*, modal_ok: bool = True) -> bool:
    """Whether this host can classify at all — i.e. whether a cache miss can be
    filled here, or must be left unlabelled for a GPU box to fill later."""
    return backend(modal_ok=modal_ok) is not None


def _verify_labels(model) -> None:
    """Refuse to run against a checkpoint whose label order is not the one the
    cache stores indices against. A silent mismatch would not error anywhere — it
    would just relabel the whole corpus wrongly."""
    got = tuple(model.config.id2label[i] for i in range(model.config.num_labels))
    if got != LABELS:
        raise RuntimeError(
            f"{settings.parlacap_model} label set does not match the pinned one "
            f"(got {len(got)} labels starting {got[:3]}). Refusing to classify: "
            "stored predictions are label *indices* and would be misread.")


def _load():
    """Load the classifier once per process, on GPU in fp16 when one is there.

    fp16 is safe for a 560M-parameter encoder doing 22-way classification (the
    softmax is computed in fp32) and roughly halves both memory and time; the
    device and precision are overridable for a host where that is wrong (OPS-4)."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    device = settings.parlacap_device or ("cuda" if torch.cuda.is_available() else "cpu")
    fp16 = settings.parlacap_fp16 and device.startswith("cuda")
    logger.info("loading %s on %s (%s)", settings.parlacap_model, device,
                "fp16" if fp16 else "fp32")
    tok = AutoTokenizer.from_pretrained(settings.parlacap_model)
    # `dtype` is what transformers >= 4.56 calls this argument; older releases
    # spell it `torch_dtype` and forward an unrecognised `dtype` straight to the
    # model constructor, which raises. This module has to load on three
    # different installs — the pinned Modal image (parlacap_modal_app.py), a GPU
    # box's ad-hoc `pip install torch transformers`, and a CPU box — and only
    # the first is version-pinned, so ask for the current name and fall back
    # instead of tying the project to one release.
    want = torch.float16 if fp16 else torch.float32
    try:
        model = AutoModelForSequenceClassification.from_pretrained(
            settings.parlacap_model, dtype=want)
    except TypeError:
        model = AutoModelForSequenceClassification.from_pretrained(
            settings.parlacap_model, torch_dtype=want)
    _verify_labels(model)
    model.to(device).eval()
    _pipeline = (tok, model, device)
    return _pipeline


def classify(texts: list[str]) -> list[tuple[int, float, int, float] | None]:
    """Classify paragraphs, returning ``(label_idx, score, runner_idx, runner_score)``
    per input — or ``None`` for one that is too short to be worth asking about.

    Batches are formed over **length-sorted** inputs and restored to the caller's
    order afterwards. Padding is to the longest item in each batch, so mixing a
    12-token aside with a 500-token argument would pad the aside forty-fold; on
    this corpus sorting first is worth roughly a 2.5× speedup, which is the
    difference between a one-hour and a three-hour corpus pass.
    """
    import torch

    tok, model, device = _load()
    out: list[tuple[int, float, int, float] | None] = [None] * len(texts)
    todo = [i for i, t in enumerate(texts)
            if word_count(t) >= settings.parlacap_min_words]
    if not todo:
        return out

    # Sort by tokenizer-free length: character count orders these closely enough
    # to pack batches well, and avoids tokenizing the corpus twice.
    todo.sort(key=lambda i: len(texts[i]))
    bs = settings.parlacap_batch_size
    for start in range(0, len(todo), bs):
        idxs = todo[start:start + bs]
        enc = tok([texts[i] for i in idxs], truncation=True,
                  max_length=settings.parlacap_max_length,
                  padding=True, return_tensors="pt")
        enc = {k: v.to(device) for k, v in enc.items()}
        with torch.no_grad():
            probs = model(**enc).logits.float().softmax(-1).cpu()
        top2 = probs.topk(2, dim=-1)
        for row, i in enumerate(idxs):
            out[i] = (int(top2.indices[row][0]), round(float(top2.values[row][0]), 4),
                      int(top2.indices[row][1]), round(float(top2.values[row][1]), 4))
    return out
