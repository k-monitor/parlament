"""Agenda-item type classification for the Hungarian National Assembly.

Two-tier model, kept compatible with the reference output:

* ``nativeType`` — a parliament-specific ``HU-*`` token.
* ``type`` — a small cross-parliament enum (the ``CORE_*`` constants) that
  search / frontend filtering can rely on without learning HU's vocabulary.

parlament.hu publishes no structured agenda-type enum; the signal is the
free-text agenda-act name (*esemenyfajtaNev*, e.g. "Napirend előtti
felszólalások") and the per-speech type token (*felszolalasTipusa*, e.g.
"interpelláció"). The classifier scans whichever string(s) the caller has.

Ported from the reference ``optv/shared/agenda_types.py`` (HU subset only) so
the new scraper carries no runtime dependency on the reference tree (SRC-1).
"""

from __future__ import annotations

import re

CORE_REGULAR = "regular"
CORE_QA = "qa"
CORE_GOVERNMENT_QUESTIONING = "questioning_of_the_government"
CORE_CURRENT_AFFAIRS = "current_affairs"
CORE_GOVERNMENT_DECLARATION = "government_declaration"
CORE_BUDGET = "budget"
CORE_ELECTION = "election"
CORE_VOTING = "voting"
CORE_OATH = "oath"
CORE_REPORT = "report"
CORE_OPENING = "opening"
CORE_CLOSING = "closing"
CORE_CONDOLENCE = "condolence"
CORE_RULES_OF_PROCEDURE = "rules_of_procedure"
CORE_PROCEDURAL = "procedural"
CORE_OTHER = "other"

# Ordered (regex, native, core); first match wins, so specific procedural
# markers precede the broad debate catch-alls.
_HU_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\bnapirend\s+előtti?\b", re.I), "HU-pre_agenda", CORE_REGULAR),
    (re.compile(r"\bnapirend\s+utáni?\b", re.I), "HU-post_agenda", CORE_REGULAR),
    (re.compile(r"\bazonnali\s+kérdés", re.I), "HU-immediate_question", CORE_QA),
    (re.compile(r"\binterpell", re.I), "HU-interpellation", CORE_GOVERNMENT_QUESTIONING),
    (re.compile(r"\bkérdés(?:ek)?\s+órája\b|\bkérdés(?:ek)?\s+és\s+válasz", re.I),
     "HU-question_hour", CORE_QA),
    (re.compile(r"\bkérdés\b", re.I), "HU-question", CORE_QA),
    (re.compile(r"\b(?:miniszterelnöki|kormány)\s+expozé\b|\bnapirend\s+előtti\s+kormány",
                re.I), "HU-government_statement", CORE_GOVERNMENT_DECLARATION),
    (re.compile(r"\bkormány\s*nyilatkozat|\bkormányfői\s+expozé\b|\bpolitikai\s+vita\b",
                re.I), "HU-government_statement", CORE_GOVERNMENT_DECLARATION),
    (re.compile(r"\bköltségvetés", re.I), "HU-budget", CORE_BUDGET),
    (re.compile(r"\bzárószavazás\b|\bszavazás\b|\bhatározathozatal\b|\bdöntés\b",
                re.I), "HU-voting", CORE_VOTING),
    (re.compile(r"\b(?:ünnepélyes\s+)?eskü(?:tétel)?\b|\bfogadalomtétel\b", re.I),
     "HU-oath", CORE_OATH),
    (re.compile(r"\bválaszt(?:ás|ja|juk|unk)\b|\bszemélyi\s+(?:javaslat|döntés)", re.I),
     "HU-election", CORE_ELECTION),
    (re.compile(r"\bházszabály", re.I), "HU-rules_of_procedure", CORE_RULES_OF_PROCEDURE),
    (re.compile(r"\bmegemlékezés\b|\bgyász\b|\bnekrológ\b|\bemlékezés\b", re.I),
     "HU-commemoration", CORE_CONDOLENCE),
    (re.compile(r"\bbeszámoló\b|\btájékoztató\b|\bjelentés\b", re.I),
     "HU-report", CORE_REPORT),
    (re.compile(r"\bülés(?:nap)?\s+megnyitás|\bmegnyitom\b|\bmegnyitó\b", re.I),
     "HU-opening", CORE_OPENING),
    (re.compile(r"\bülés\s+(?:bezárás|berekesztés)|\bberekesztem\b|\bbezárom\s+az\s+ülés",
                re.I), "HU-closing", CORE_CLOSING),
    (re.compile(r"\bnapirend(?:i\s+pont|\s+megállapítás|\s+elfogadás|\s+kiegészítés)|"
                r"\büléssel\s+kapcsolatos\s+ügyrendi\b|\bügyrendi\b|\bülésvezetés\b", re.I),
     "HU-procedural", CORE_PROCEDURAL),
    # Substantive debate stages — broadest, so last.
    (re.compile(r"\b(?:általános|részletes|összevont)\s+vita\b|\bvezérszónok|"
                r"\bvita\b|\btárgyalás", re.I), "HU-debate", CORE_REGULAR),
]


def classify(*signals: str | None) -> tuple[str | None, str]:
    """Classify from one or more free-text signals (agenda-act name and/or
    per-speech type). Returns ``(native_type, core_type)``; falls back to
    ``(None, CORE_REGULAR)`` when nothing matches."""
    haystack = " ".join(s for s in signals if s)
    if not haystack:
        return None, CORE_REGULAR
    for pat, native, core in _HU_PATTERNS:
        if pat.search(haystack):
            return native, core
    return None, CORE_REGULAR


def annotate(agenda_item: dict, *signals: str | None) -> dict:
    """Set ``type`` / ``nativeType`` on an agendaItem dict in place (non-empty
    existing values are preserved). Returns the dict for convenience."""
    native, core = classify(*signals)
    if not agenda_item.get("type"):
        agenda_item["type"] = core
    if native and not agenda_item.get("nativeType"):
        agenda_item["nativeType"] = native
    return agenda_item


# A bill reference code embedded in an agenda title, e.g. "T/360", "H/1234".
_BILL_CODE_RE = re.compile(r"\b[A-ZÁÉÍÓÖŐÚÜŰ]/\d+")


def split_topic_and_bills(title: str) -> tuple[str, list[str]]:
    """Split an agenda title into ``(topic, bill_references)``.

    The topic is the human-readable title with the bill codes removed; bill
    references are the codes themselves (kept for the Bills module, EXT-2).

    The code is NOT always a trailing suffix — it often sits mid-title inside
    parentheses, e.g. ``"Interpelláció megtárgyalása (I/112) Folytatódik-e a
    panelprogram?"``. Truncating at the first code (the old behaviour) left a
    dangling ``"("`` and threw away the descriptive part after it, so we strip
    the codes wherever they occur and tidy up any parentheses/separators left
    empty, keeping the whole title intact."""
    if not title:
        return "", []
    codes = _BILL_CODE_RE.findall(title)
    if not codes:
        return title.strip(), []
    topic = _BILL_CODE_RE.sub("", title)          # drop the codes in place
    topic = re.sub(r"\([\s,;]*\)", "", topic)     # remove now-empty "(…)"
    topic = re.sub(r"\s{2,}", " ", topic)          # collapse the gaps left behind
    topic = topic.strip(" ,;–—-")                  # trim dangling separators
    return topic, codes
