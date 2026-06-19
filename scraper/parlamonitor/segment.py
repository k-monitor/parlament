"""Speech-text cleanup and Hungarian sentence segmentation.

The Felicitas proceedings query returns each speech as an HTML fragment
(``<div class="felszolalas-szoveg"><p><span>…</span></p>…</div>``). This module
strips that to plain text, normalises whitespace, and splits it into sentences
— the unit of search and of video seeking (requirements §3.1, DB-2).

Segmentation prefers HuSpaCy's blank Hungarian tokenizer + sentencizer (no model
download needed); if spaCy is unavailable it falls back to a punctuation-based
splitter so the pipeline (and its tests) still run with no extra dependency
(SCR-6: the only sentence-segmentation dependency is optional).
"""

from __future__ import annotations

import logging
import re
from html import unescape

logger = logging.getLogger(__name__)

# Lazily-initialised spaCy sentencizer; None until first use, False if spaCy
# could not be loaded (then the regex fallback is used for the process lifetime).
_NLP = None

# Fallback splitter: candidate break after sentence-final punctuation followed
# by whitespace and an uppercase/quote/paren start.
_SENT_RE = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÁÉÍÓÖŐÚÜŰ„“(])")

# Common Hungarian abbreviations that end in "." but do not end a sentence —
# parlament.hu ALL-CAPS speaker prefixes ("DR. SULYOK TAMÁS …"), so the bare
# uppercase-after-period heuristic would wrongly split on them.
_ABBREV = {
    "dr", "prof", "id", "ifj", "özv", "stb", "pl", "ún", "vö", "kb", "ld",
    "sz", "u", "ún", "min", "max", "ill", "vmint", "kt", "art", "ún",
    "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
}
_LAST_TOKEN_RE = re.compile(r"([\wÁÉÍÓÖŐÚÜŰáéíóöőúüű]+)\.\s*$")


def _is_false_boundary(left: str) -> bool:
    """True if ``left`` (text before a candidate break) ends in an abbreviation
    or a single initial, so the break should be suppressed."""
    m = _LAST_TOKEN_RE.search(left)
    if not m:
        return False
    tok = m.group(1)
    return tok.lower() in _ABBREV or len(tok) == 1

_TAG_RE = re.compile(r"<[^>]+>")
# Paragraph/line breaks become spaces, but we mark them so a sentence never
# spills across a clear paragraph boundary even without final punctuation.
_PARA_BREAK_RE = re.compile(r"</p\s*>|<br\s*/?>", re.I)


def html_to_text(html: str) -> str:
    """Strip an HTML speech fragment to clean running text."""
    if not html:
        return ""
    text = _PARA_BREAK_RE.sub("\n", html)
    text = _TAG_RE.sub("", text)
    text = unescape(text)
    return clean_text(text)


def clean_text(t: str) -> str:
    if not t:
        return ""
    t = t.replace("\xa0", " ")
    # Collapse intra-line whitespace but keep newlines as soft paragraph marks.
    t = re.sub(r"[^\S\n]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t)
    return t.strip()


def _get_nlp():
    global _NLP
    if _NLP is None:
        try:
            from spacy.lang.hu import Hungarian
            nlp = Hungarian()
            nlp.add_pipe("sentencizer")
            _NLP = nlp
        except Exception as e:  # ModuleNotFoundError or any spaCy init failure
            logger.warning("spaCy Hungarian unavailable (%s); using regex "
                           "sentence splitter", type(e).__name__)
            _NLP = False
    return _NLP


def split_sentences(text: str) -> list[dict]:
    """Split ``text`` into a list of ``{"text": ...}`` sentence dicts."""
    text = clean_text(text)
    if not text:
        return []
    nlp = _get_nlp()
    if nlp:
        # Feed paragraph by paragraph so a missing terminal period at a
        # paragraph end does not glue two sentences together.
        out: list[dict] = []
        for para in text.split("\n"):
            para = para.strip()
            if not para:
                continue
            out.extend({"text": str(s).strip()} for s in nlp(para).sents
                       if str(s).strip())
        return out
    flat = text.replace("\n", " ")
    # Split on candidate boundaries, then re-join across false boundaries (an
    # abbreviation or single initial before the period).
    pieces = _SENT_RE.split(flat)
    merged: list[str] = []
    for piece in pieces:
        if merged and _is_false_boundary(merged[-1]):
            merged[-1] = f"{merged[-1]} {piece}"
        else:
            merged.append(piece)
    return [{"text": s.strip()} for s in merged if s.strip()]
