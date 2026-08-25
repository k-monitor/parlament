"""FTS5 query construction for Hungarian sentence search (SEA-1/SEA-2).

We translate a user's free-text query into a safe FTS5 MATCH expression:

* `"quoted phrases"` become exact FTS phrases (SEA-2 exact-phrase support);
* bare words become **prefix** terms (``word*``) so Hungarian morphology
  (agglutinative suffixes) is matched stemming-free — searching *költségvetés*
  also hits *költségvetését*, *költségvetésről*, … — but only from
  ``settings.min_prefix_len`` characters up: below it a prefix term stops being a
  search and becomes a corpus scan (``a*`` matches two thirds of every sentence
  in the House's history), so short terms are matched exactly instead;
* accent/case folding is handled by the index tokenizer
  (``unicode61 remove_diacritics 2``), so the query text needs no normalization.

All terms are quoted before being handed to FTS, which neutralizes FTS operator
characters — the user cannot inject raw FTS syntax.
"""

from __future__ import annotations

import re

from .config import settings

_TOKEN_RE = re.compile(r'"([^"]+)"|(\S+)')
# Strip characters that are meaningless inside an FTS string literal.
_CLEAN_RE = re.compile(r'["]')


def build_match(query: str) -> str | None:
    """Return an FTS5 MATCH string, or None if the query has no usable terms."""
    if not query or not query.strip():
        return None
    parts: list[str] = []
    for m in _TOKEN_RE.finditer(query):
        phrase, word = m.group(1), m.group(2)
        if phrase is not None:
            cleaned = _CLEAN_RE.sub(" ", phrase).strip()
            if cleaned:
                parts.append(f'"{cleaned}"')          # exact phrase
        elif word is not None:
            cleaned = _CLEAN_RE.sub("", word).strip()
            if cleaned:
                # Prefix term for anything long enough to narrow the corpus;
                # exact for the stubs that would not (see settings.min_prefix_len).
                star = "*" if len(cleaned) >= settings.min_prefix_len else ""
                parts.append(f'"{cleaned}"{star}')
    if not parts:
        return None
    return " ".join(parts)                              # implicit AND
