"""Parse a parlament.hu speaker string into name / faction / role / context.

parlament.hu renders a speaker as ``"Vezetéknév Keresztnév (Frakció)"`` —
surname-first, parliamentary group in parentheses — sometimes wrapped in an
office phrase (``DR. KÖVÉR LÁSZLÓ, az Országgyűlés elnöke``). This module
splits that into a clean label, a faction label, an optional role, and a
speaker ``context`` from a small controlled vocabulary.

Ported and adapted from the reference HU ``parsers/common.py``; kept here so the
new scraper has no runtime dependency on the reference tree (SRC-1).
"""

from __future__ import annotations

import re

# Office/role phrases (lowercased, accents kept) → speaker context.
_ROLE_TO_CONTEXT = {
    "korelnök": "interim-president",
    "ülést vezető elnök": "president",
    "levezető elnök": "president",
    "alelnök": "vice-president",
    "elnök": "president",
}

# Honorary prefixes stripped from a label so the media- and proceedings-side
# names collapse to one canonical person.
_TITLE_PREFIX_RE = re.compile(r"^(?:dr\.?|prof\.?|ifj\.?|id\.?|özv\.?)\s+", re.I)


def split_speaker(raw: str) -> tuple[str, str, str | None]:
    """Split a raw speaker string into ``(label, faction, role)``.

        "Kövér László (Fidesz)"                     -> ("Kövér László", "Fidesz", None)
        "DR. KÖVÉR LÁSZLÓ, az Országgyűlés elnöke"  -> ("Kövér László", "", "president")
        "Varga Mihály pénzügyminiszter (Fidesz)"    -> ("Varga Mihály", "Fidesz", "miniszter")
    """
    if not raw:
        return "", "", None
    text = re.sub(r"\s+", " ", raw).strip()

    faction = ""
    m = re.search(r"\(([^()]*)\)\s*$", text)
    if m:
        faction = m.group(1).strip()
        text = text[: m.start()].strip()

    role = None
    # Office after a comma: "..., az Országgyűlés elnöke", "..., a Ház jegyzője".
    if "," in text:
        head, tail = text.split(",", 1)
        role = _role_from_phrase(tail)
        if role is not None:
            text = head.strip()

    # A trailing office word with no comma: "Varga Mihály pénzügyminiszter".
    if role is None:
        trailing = _role_from_phrase(text)
        if trailing is not None and trailing != "president":
            text = re.sub(r"\s+\S*miniszter\w*$|\s+jegyző$|\s+háznagy$", "", text).strip()
            role = trailing

    return _clean_name(text), _clean_faction(faction), role


def _role_from_phrase(phrase: str) -> str | None:
    p = phrase.strip().lower()
    if not p:
        return None
    for key, context in _ROLE_TO_CONTEXT.items():
        if key in p:
            return context
    if "jegyző" in p or "háznagy" in p:
        return "speaker"
    if "miniszter" in p:  # pénzügyminiszter, miniszterelnök, államtitkár-style
        return "miniszter"
    return None


def _clean_name(name: str) -> str:
    name = _TITLE_PREFIX_RE.sub("", name.strip())
    name = re.sub(r"\s+", " ", name).strip()
    # parlament.hu ALL-CAPS the name in office lines; title-case only when the
    # whole string is upper, to preserve real mixed casing elsewhere.
    if name and name == name.upper():
        name = name.title()
    return name


def _clean_faction(faction: str) -> str:
    if not faction:
        return ""
    return re.sub(r"\s+", " ", faction).strip()


def split_name(label: str) -> tuple[str, str]:
    """Best-effort ``(firstname, lastname)`` for a surname-first Hungarian name.

    "Kövér László" -> ("László", "Kövér"). Compound surnames are not reliably
    separable, so the first whitespace token is the surname. Returns ``("", "")``
    for a single-token label.
    """
    if not label:
        return "", ""
    parts = label.split(" ", 1)
    if len(parts) != 2:
        return "", ""
    return parts[1], parts[0]


def context_for(role: str | None, is_main: bool = True) -> str:
    """Map a parsed ``role`` to a speaker context."""
    if role in ("president", "vice-president", "interim-president"):
        return role
    return "main-speaker" if is_main else "speaker"


def person_type_for(role: str | None) -> str:
    """``memberOfGovernment`` for ministerial speakers, else ``memberOfParliament``."""
    return "memberOfGovernment" if role == "miniszter" else "memberOfParliament"


def build_person(speaker_raw: str, *, person_id: str | None = None,
                 office: str | None = None) -> dict:
    """Build a normalised ``people[]`` entry from a raw speaker string.

    ``person_id`` is the Felicitas ``kepviseloId`` when known — it cross-links a
    speech to the representative registry (requirements EXT-2), so the frontend
    can join a speech to an MP profile without name matching.

    ``office`` is the speaker's full government office (*tisztség*) as the source
    reports it per speech — e.g. ``"igazságügyi miniszter"`` or ``"Pénzügy-
    minisztérium államtitkára"`` (Felicitas ``tisztseg``, see
    ``felicitas.speech_text``). Unlike the coarse ``role`` parsed from the
    speaker string (``"miniszter"`` / ``"elnök"``), it names the specific post,
    so a non-MP speaker's profile can show *who they are* (their office) even
    though they carry no faction or constituency.
    """
    label, faction, role = split_speaker(speaker_raw)
    firstname, lastname = split_name(label)
    context = context_for(role, is_main=True)
    person: dict = {
        "type": person_type_for(role),
        "label": label,
        "context": context,
    }
    if person_id:
        person["personID"] = person_id
    if firstname:
        person["firstname"] = firstname
    if lastname:
        person["lastname"] = lastname
    if context in ("president", "vice-president", "interim-president"):
        person["role"] = "elnök"
    elif role == "miniszter":
        person["role"] = "miniszter"
    office = (office or "").strip()
    if office:
        person["office"] = office
    if faction:
        person["faction"] = {"label": faction}
    return person
