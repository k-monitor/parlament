"""Settlement mentions in the transcript — the Települések module (§6D, TEL-1..3).

Parliament argues about places. The transcript names them, but only as words inside
sentences, so the corpus cannot answer *has the House ever mentioned my town* — nor
its sharper inverse, *which of Hungary's 3 177 settlements has it never mentioned*
(TEL-7). This module is the extraction half of that answer: it turns transcript text
into **mentions of settlement entities**.

**Why a gazetteer and not a model.** The set of settlements is a closed, official
list, so the question is not the open-vocabulary one ("is this token a place?") but
the far easier and fully auditable one ("is this token one of *these* names, in one
of Hungarian's case forms?"). That buys three properties the site's other neural
passes cannot have: it needs no model, no GPU and no network, so it runs in every
install and every rebuild (SCR-6); it is deterministic, so a count is reproducible;
and it is unit-testable with plain strings (TIM-4's discipline). The named-entity
layer the corpus already carries (§4.1) is used, but as a **veto**, not a detector —
see below.

**Morphology.** Hungarian almost never names a place in the bare nominative when it
can inflect it, so the matcher strips the case endings a place takes (*-ban/-ben,
-on/-en/-ön, -ra/-re, -ból/-ből, -ig, -ott*, …), undoes the stem lengthening those
endings induce (*Kalocsa → Kalocsán*) and the *-val/-vel* assimilation (*Szeged →
Szegeddel*); and it matches the **adjectival/demonym** form (*szegedi*, *a
szegediek*, *nyíregyházi*), which is how a speaker most often refers to a place at
all — *"a kaposvári kórház"* names Kaposvár as surely as *"Kaposváron"* does.

**Precision over recall (TEL-3), because the headline claim is about silence.** A
blind spot is a statement that no one ever named a real place; one false positive
does not add noise to it, it deletes it. And Hungarian settlement names collide with
ordinary speech on a scale that makes naïve matching useless: *Baj* is "trouble",
*Alap* "fund", *Hét* "seven", *Bár* "although", *Pápa* "the Pope"; *Varga*, *Gyula*
and *Szabolcs* are also sitting MPs; *Balaton*, *Zala* and *Hernád* name a lake, a
county and a river far more often than their namesake villages. So a match must
survive four gates, in order:

1. **The entity layer vetoes.** A candidate overlapping a recognized PER/ORG mention
   is dropped — which is what tells *Varga Mihály* from the village of Varga and the
   *Nemzeti Foglalkoztatási Alap* from the village of Alap, with no per-name rule.
2. **Ambiguity is derived from the corpus**, not guessed (see :func:`build`): a name
   whose lower-case form is a frequent lemma in the corpus's own word statistics, a
   stop-word, or the name of a person in the register is ambiguous *by measurement*,
   so the policy maintains itself as the corpus grows.
3. **An ambiguous name must earn its match** — a place cue in the sentence
   (*község, város, polgármester, önkormányzat, határában*, …) for the strongly
   ambiguous, at least a place-marking case ending for the weakly ambiguous. A bare
   capitalized token is not evidence, least of all sentence-initially, where
   capitalization means nothing.
4. **A small reviewed table** (:data:`REVIEWED`) carries only what measurement
   cannot see — the lake, the river, the summit named after a town — each entry
   naming the homonym it exists for (the §6C/MIN-3 pattern: a checked-in table
   seeded from the data beats a runtime guess).

Rejections are counted by reason so the policy's effect is observable in the load
log rather than taken on faith.

This module is pure: it holds no database or network access. The loader supplies the
register (from :mod:`app.valasztas`, TEL-5) and the corpus statistics; the request
path never runs any of it (TEL-11).
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, NamedTuple

# ---------------------------------------------------------------------------
# The capital (TEL-5)
#
# The official register knows only Budapest's 23 districts, while speakers say
# "Budapest" — by a wide margin the most-named place in the corpus (12 784 mentions
# over cycles 39–43). Dropping it would gut the feature; folding it into a district
# would invent a claim. So the capital is its own entity beside its districts, with
# a synthetic id in Budapest's county (`maz` 01, which has no `taz` 000).
# ---------------------------------------------------------------------------
BUDAPEST_ID = "01/000"
BUDAPEST_NAME = "Budapest"
BUDAPEST_POINT = (47.4979, 19.0402)      # Deák Ferenc tér, the conventional centre

# ---------------------------------------------------------------------------
# Morphology
# ---------------------------------------------------------------------------

# Endings that put a place *somewhere* — in it, onto it, out of it, as far as it.
# These vouch for a place reading on their own, which is what a weakly ambiguous
# name needs (gate 3): "Bajon" is the village of Baj, "Baj" is trouble.
PLACE_SUFFIXES: tuple[str, ...] = (
    "ban", "ben",          # inessive:    Szegedben
    "ból", "ből",          # elative:     Szegedből
    "hoz", "hez", "höz",   # allative:    Szegedhez
    "ról", "ről",          # delative:    Szegedről
    "tól", "től",          # ablative:    Szegedtől
    "ott", "ett", "ött",   # locative:    Pécsett, Győrött, Vácott
    "ba", "be",            # illative:    Szegedbe
    "ra", "re",            # sublative:   Szegedre
    "on", "en", "ön",      # superessive: Szegeden
    "ig",                  # terminative: Szegedig
    "n",                   # superessive after a vowel: Kalocsán
)

# Endings that inflect a place without locating it. They confirm the word is being
# used as a noun (so the stem is worth testing) but say nothing about *place*, so
# they do not satisfy gate 3 on their own.
OTHER_SUFFIXES: tuple[str, ...] = (
    "ként",                        # formal:       Szegedként
    "nak", "nek",                  # dative:       Szegednek
    "ért",                         # causal:       Szegedért
    "at", "ot", "et", "öt", "t",   # accusative:   Szegedet, Budát
    "ul", "ül",                    # essive:       Szegedül
)
# The instrumental *-val/-vel* is not in that list because it does not survive
# contact with the stem: its ``v`` assimilates to the stem's final consonant, which
# doubles instead (*Szeged → Szegeddel*, *Győr → Győrrel*). It is undone separately,
# by :func:`_undo_instrumental`.

# A Hungarian digraph doubles by repeating only its first letter, so undoing the
# assimilation is not simply "drop one character": *Pécs → Péccsel*,
# *Hódmezővásárhely → Hódmezővásárhellyel*.
_DIGRAPH_DOUBLES = {"ccs": "cs", "ssz": "sz", "zzs": "zs", "ggy": "gy",
                    "nny": "ny", "tty": "ty", "lly": "ly", "ddz": "dz"}


def _undo_instrumental(token: str) -> str | None:
    """The stem behind an assimilated instrumental, or ``None`` if the token is not
    one. ``Szegeddel → Szeged``, ``Péccsel → Pécs``, ``Győrrel → Győr``."""
    if len(token) < 5 or not token.endswith(("al", "el")):
        return None
    stem = token[:-2]
    for doubled, single in _DIGRAPH_DOUBLES.items():
        if stem.endswith(doubled):
            return stem[: -len(doubled)] + single
    if stem[-1] == stem[-2]:
        return stem[:-1]
    return None

# Deliberately absent: the plurals (-k/-ok/-ek/-ök). A settlement is not pluralized
# in practice, and stripping a bare "k" turned every mention of the surname *Novák*
# into the Zala village of Nova (356 of them in a 300 000-sentence sample).
_SUFFIXES: tuple[str, ...] = tuple(
    sorted(PLACE_SUFFIXES + OTHER_SUFFIXES, key=len, reverse=True))
_PLACE_SET = frozenset(PLACE_SUFFIXES)

# The superessive (*-on/-en/-ön/-n*) is the one place ending that is not by itself
# evidence of a place, because it is also how Hungarian forms an adverb from an
# adjective: *Sima* is a village in Borsod, and "simán" means "easily". So a weakly
# ambiguous name in the superessive still needs its position or a cue to vouch for
# it, while the endings that can only locate something — *-ban/-ból/-ra/-ról/-tól/
# -hoz/-ig/-ott* — vouch for it on their own, wherever in the sentence it sits.
_SUPERESSIVE = frozenset({"on", "en", "ön", "n"})
_LOCATIVE_ONLY = _PLACE_SET - _SUPERESSIVE

# A suffix lengthens a final a/e of the stem: Kalocsa → Kalocsán, Bicske → Bicskét.
_SHORTEN = {"á": "a", "é": "e"}

# A capitalized word, and a lower-case one. Hungarian settlement names are
# single-token throughout the register (the only names with a space are the Budapest
# districts, and none has a hyphen), so a token *is* the candidate — no phrase
# matching, and no risk of matching inside a longer word: *Pécsvárad* is its own
# name and never reduces to *Pécs*, because "várad" is not a suffix.
_TOKEN = re.compile(r"[A-ZÁÉÍÓÖŐÚÜŰ][a-záéíóöőúüű]+")
_LOWER = re.compile(r"[a-záéíóöőúüű]+")

# Tails the demonym takes: "a szegediek", "a szegedieknek", "szegediként".
_ADJ_TAILS: tuple[str, ...] = tuple(sorted(
    ("", "ak", "ek", "aknak", "eknek", "akat", "eket", "akkal", "ekkel",
     "nak", "nek", "val", "vel", "ként", "t", "ban", "ben", "ra", "re",
     "ról", "ről"), key=len, reverse=True))

# The demonym is only generated for names long enough that the derived form cannot
# be an everyday word. Below this floor it is catastrophic: *Aka* (a village of 300)
# yields "aki" — the relative pronoun "who" — which alone produced 4 903 false
# matches in a 300 000-sentence sample.
_ADJ_MIN_LENGTH = 5
# The variant that drops a final a/e (*Nyíregyháza → nyíregyházi*) is standard only
# for the long compounds; on a short name it manufactures common words.
_ADJ_ELISION_MIN_LENGTH = 8

# ---------------------------------------------------------------------------
# Context cues
# ---------------------------------------------------------------------------

# Words that say the neighbouring token is a place. Used to let an ambiguous name
# through (gate 3) — the sentence itself vouches for it.
PLACE_CUES: frozenset[str] = frozenset({
    "község", "községben", "községet", "községi", "községnek", "községe",
    "nagyközség", "város", "városban", "városa", "városát", "városi",
    "városnak", "városát", "városban", "település", "településen",
    "települést", "településnek", "települése", "falu", "faluban", "falut",
    "faluja", "kistelepülés", "kerület", "kerületben", "kerületi",
    "polgármester", "polgármestere", "polgármesterét", "polgármesterének",
    "önkormányzat", "önkormányzata", "önkormányzatának", "képviselőtestülete",
    "határában", "határa", "belterületén", "külterületén",
    "lakosai", "lakói", "lakosa", "lakossága", "lakosság", "lakóinak",
    "térség", "térségben", "térségének", "térsége", "járás", "járásban",
    "járása", "vasútállomás", "vasútállomása",
})

# A name that is also a county's name is not the settlement when the sentence goes
# on to say *megye* — "Veszprém megyére" is the county, not the town of Veszprém.
# Looked for a little way ahead, not only in the very next word: upstream writes
# "Veszprém, Győr-Moson-Sopron és Vas megyére".
_COUNTY_AHEAD = re.compile(r"^[^.!?]{0,44}?(vár)?megy", re.IGNORECASE)

# The counties whose names a settlement shares (or begins). Only these pay the
# look-ahead cost.
COUNTY_HOMONYMS: frozenset[str] = frozenset({
    "Bács", "Baranya", "Békés", "Borsod", "Csongrád", "Fejér", "Győr", "Hajdú",
    "Heves", "Komárom", "Nógrád", "Pest", "Somogy", "Szabolcs", "Tolna", "Vas",
    "Veszprém", "Zala",
})

# ---------------------------------------------------------------------------
# The reviewed table (gate 4)
#
# Only what the corpus-derived ambiguity test cannot see: these names collide with
# something that is *also* a proper noun, so no lemma frequency and no stop-word list
# betrays them. Each is held to the strict standard (an explicit place cue), never
# dropped outright — the villages are real, and a sentence that says "Balaton
# község" should still count. The note is the homonym the entry exists for.
# ---------------------------------------------------------------------------
REVIEWED: dict[str, str] = {
    "Balaton": "the lake — 2 316 corpus mentions against a village of 1 000",
    "Velence": "Venice, and the lake Velence",
    "Bodrog": "the river",
    "Hernád": "the river",
    "Sajó": "the river",
    "Zala": "the river and the county",
    "Tisza": "the river (and now a party)",
    "Hortobágy": "the puszta and the national park",
    "Visegrád": "the V4 — 'visegrádi négyek/nyilatkozat' dominates the corpus",
    "Erzsébet": "a given name, and the Erzsébet-program",
    "Kisbér": "commonly the historic district, not the town",
}


# ---------------------------------------------------------------------------
# Budapest's districts (TEL-5)
#
# The register spells them "Budapest 09. kerület", a form nobody has ever spoken
# aloud. Left at that, all 23 districts of the capital would be permanent blind
# spots — the module asserting that the House has never mentioned Csepel — which is
# exactly the false claim TEL-3 exists to prevent. So the two forms people actually
# use are recognised:
#
#   * the **roman numeral** ("IX. kerület", "a XVI. kerületben"), 2 890 occurrences
#     in the corpus and unambiguous: no other numbering in Hungarian public life is
#     written this way. An arabic numeral is accepted only with "Budapest" in front
#     of it, because a bare "9. kerület" is as often a *választókerület*.
#   * the **district's own name** ("Zugló", "Csepel", "Józsefváros"), which is how a
#     speaker names their own district. Several are also football clubs — Ferencváros
#     and Újpest above all — so every one of these is held to the weak-ambiguity
#     standard (a place-marking ending or a cue): "Csepelen" counts, "a Csepel SC"
#     does not.
# ---------------------------------------------------------------------------
_ROMAN = ("XXIII", "XXII", "XXI", "XVIII", "XVII", "XVI", "XIX", "XV", "XIV",
          "XIII", "XII", "XI", "VIII", "VII", "VI", "IX", "IV", "X", "V", "III",
          "II", "I")
_ROMAN_VALUE = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
                "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12, "XIII": 13,
                "XIV": 14, "XV": 15, "XVI": 16, "XVII": 17, "XVIII": 18,
                "XIX": 19, "XX": 20, "XXI": 21, "XXII": 22, "XXIII": 23}
# Longest-first alternation, so "XVIII" is never read as "XVII" followed by "I".
_DISTRICT_PHRASE = re.compile(
    r"(?:Budapest\s+(?P<arabic>\d{1,2})\.?\s*kerület"
    r"|(?<![A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű])(?P<roman>" + "|".join(_ROMAN) + r")\.\s*kerület)"
    r"[a-záéíóöőúüű]*")

# The register's own spelling of a district, from which its number is taken — the
# number is never assumed from the key, it is read off the name (as REP-10 does).
_DISTRICT_NAME = re.compile(r"^Budapest (\d{1,2})\. kerület$")

# District number → the names it is commonly called by. Hand-reviewed, and every
# one of them held to the weak-ambiguity standard (see above).
DISTRICT_ALIASES: dict[int, tuple[str, ...]] = {
    3: ("Óbuda",), 4: ("Újpest",), 5: ("Belváros", "Lipótváros"),
    8: ("Józsefváros",), 9: ("Ferencváros",), 10: ("Kőbánya",),
    12: ("Hegyvidék",), 13: ("Angyalföld",), 14: ("Zugló",),
    17: ("Rákosmente",), 19: ("Kispest",), 20: ("Pesterzsébet",),
    21: ("Csepel",), 22: ("Budafok", "Tétény"), 23: ("Soroksár",),
}


class Mention(NamedTuple):
    """One settlement mention found in one text."""
    settlement_id: str
    name: str            # the register's own spelling of the settlement
    surface: str         # exactly as it appears in the text
    start: int           # char offsets into the text handed to `scan`
    end: int
    form: str            # 'name' (inflected proper noun) | 'demonym' (-i adjective)


@dataclass(frozen=True)
class Gazetteer:
    """The matcher: an index of settlement names plus the ambiguity policy.

    Built by :func:`build` from the official register and the corpus's own
    statistics. Holds no I/O, so tests construct one from a handful of names.
    """

    by_name: dict[str, str]              # register spelling (or alias) → settlement id
    demonyms: dict[str, str]             # "szegedi" → "Szeged"
    need_cue: frozenset[str]             # strongly ambiguous: require a place cue
    need_suffix: frozenset[str]          # weakly ambiguous: require a place ending
    reasons: dict[str, str] = field(default_factory=dict)   # name → why ambiguous
    districts: dict[int, str] = field(default_factory=dict)  # 9 → id of district IX

    # -- candidate generation ------------------------------------------------

    def _stems(self, token: str):
        """``(stem, suffix)`` readings of a capitalized token, longest suffix first.

        The bare token comes first so an exact register name always wins over any
        suffix-stripped reading of it.
        """
        yield token, ""
        for suffix in _SUFFIXES:
            if len(token) <= len(suffix) + 1 or not token.endswith(suffix):
                continue
            stem = token[: -len(suffix)]
            yield stem, suffix
            if stem[-1] in _SHORTEN:                 # Kalocsán → Kalocsa
                yield stem[:-1] + _SHORTEN[stem[-1]], suffix
        instrumental = _undo_instrumental(token)
        if instrumental is not None:
            # Classified as a non-place ending: "Szegeddel" says the town is a
            # participant, not that anything happened there.
            yield instrumental, "val"

    def _match_name(self, token: str) -> tuple[str | None, str]:
        for stem, suffix in self._stems(token):
            if stem in self.by_name:
                return stem, suffix
        return None, ""

    def _match_demonym(self, token: str) -> str | None:
        for tail in _ADJ_TAILS:
            if tail and not token.endswith(tail):
                continue
            stem = token[: len(token) - len(tail)] if tail else token
            found = self.demonyms.get(stem)
            if found is not None:
                return found
        return None

    # -- scanning ------------------------------------------------------------

    def scan(self, text: str,
             blocked: Iterable[tuple[int, int]] = (),
             rejected: dict[str, int] | None = None) -> list[Mention]:
        """Every settlement mention in ``text``, in document order.

        ``blocked`` is the sentence's PER/ORG entity spans (gate 1): a candidate
        overlapping one is not a place, it is a person or an institution that
        happens to contain the name. ``rejected``, when given, is incremented per
        rejection reason so the caller can log the policy's effect (TEL-3).
        """
        if not text:
            return []
        spans = [s for s in blocked if s[0] is not None and s[1] is not None]

        def drop(reason: str) -> None:
            if rejected is not None:
                rejected[reason] = rejected.get(reason, 0) + 1

        out: list[Mention] = []
        # Budapest's districts first, because a district phrase *contains* the word
        # "Budapest" ("Budapest 9. kerületében"): matched first, it claims the span,
        # and the token pass below skips inside it — so the sentence is counted as
        # one mention of the district, not also one of the capital.
        claimed: list[tuple[int, int]] = []
        for m in _DISTRICT_PHRASE.finditer(text):
            number = (int(m.group("arabic")) if m.group("arabic")
                      else _ROMAN_VALUE[m.group("roman")])
            sid = self.districts.get(number)
            if sid is None:
                continue
            if any(s < m.end() and m.start() < e for s, e in spans):
                drop("per/org")
                continue
            claimed.append(m.span())
            out.append(Mention(sid, f"Budapest {number:02d}. kerület", m.group(0),
                               m.start(), m.end(), "district"))

        for m in _TOKEN.finditer(text):
            name, suffix = self._match_name(m.group(0))
            if name is None:
                continue
            start, end = m.span()
            if any(s <= start and end <= e for s, e in claimed):
                continue        # inside a district phrase already counted
            if any(s < end and start < e for s, e in spans):
                drop("per/org")
                continue
            ahead = text[end:end + 48]
            if name in COUNTY_HOMONYMS and _COUNTY_AHEAD.match(ahead):
                drop("county")
                continue
            cue = self._has_cue(text, start, ahead)
            # Sentence-initial capitalization is not evidence of a proper noun, so a
            # bare nominative there needs the sentence to vouch for it. ("Hét
            # évtizeddel ezelőtt…" is not the village of Hét.)
            initial = start == 0 or text[:start].strip()[-1:] in (".", "!", "?", "")
            if name in self.need_cue:
                if not cue:
                    drop("need-cue")
                    continue
            elif name in self.need_suffix:
                located = (suffix in _LOCATIVE_ONLY
                           or (suffix in _SUPERESSIVE and not initial))
                if not cue and not located:
                    drop("need-suffix")
                    continue
            elif not suffix and initial and not cue:
                drop("initial-bare")
                continue
            out.append(Mention(self.by_name[name], name, m.group(0),
                               start, end, "name"))

        for m in _LOWER.finditer(text):
            name = self._match_demonym(m.group(0))
            if name is None:
                continue
            if any(s <= m.start() and m.end() <= e for s, e in claimed):
                continue
            out.append(Mention(self.by_name[name], name, m.group(0),
                               m.start(), m.end(), "demonym"))

        out.sort(key=lambda mention: mention.start)
        return out

    @staticmethod
    def _has_cue(text: str, start: int, ahead: str) -> bool:
        """Whether a place-cue word sits either side of the candidate."""
        after = ahead.lstrip(" ,").split(" ")[0].strip(".,;:!?()\"'–—-").lower()
        before = (text[max(0, start - 28):start].rstrip().split(" ")[-1]
                  .strip(".,;:!?()\"'–—-").lower())
        return after in PLACE_CUES or before in PLACE_CUES


# ---------------------------------------------------------------------------
# Building the gazetteer
# ---------------------------------------------------------------------------

# A name whose lower-case form is a lemma in this many of the corpus's sitting days
# is treated as strongly ambiguous (needs a cue); at least three days makes it
# weakly ambiguous (needs a place ending). Both thresholds are deliberately low:
# under-restricting costs a false blind spot, over-restricting costs a mention the
# module never claimed to have found (TEL-12).
STRONG_DOC_FREQ = 20
WEAK_DOC_FREQ = 3


def build(names: dict[str, str], *,
          lemma_doc_freq: dict[str, int] | None = None,
          stopwords: Iterable[str] = (),
          person_names: Iterable[str] = ()) -> Gazetteer:
    """A :class:`Gazetteer` over ``names`` (register spelling → settlement id).

    ``lemma_doc_freq`` is the corpus's own lemma → number-of-sitting-days table
    (``word_doc_freq``, already built for the word cloud): it is what makes the
    ambiguity policy *measured* rather than hand-written (gate 2). ``person_names``
    are the name parts of everyone in the register, so a settlement that is also a
    surname is held to the strict standard even in the sentences where the NER layer
    missed the person (gate 1 catches only the ones it found).
    """
    freq = lemma_doc_freq or {}
    stops = {s.casefold() for s in stopwords}
    people = {p for p in person_names if len(p) > 2}

    # Budapest's districts, keyed by the number read off the register's own spelling,
    # plus the names they are commonly called by (see DISTRICT_ALIASES).
    districts: dict[int, str] = {}
    for name, settlement_id in names.items():
        match = _DISTRICT_NAME.match(name)
        if match:
            districts[int(match.group(1))] = settlement_id
    aliases: dict[str, str] = {}
    for number, alternates in DISTRICT_ALIASES.items():
        settlement_id = districts.get(number)
        if settlement_id is None:
            continue
        for alias in alternates:
            aliases.setdefault(alias, settlement_id)
    names = {**names, **aliases}

    need_cue: dict[str, str] = dict(REVIEWED)
    need_suffix: dict[str, str] = {
        # Every district alias earns its match: over half of them are also the name
        # of a football club, and "a Ferencváros" is a team while "Ferencvárosban" is
        # a place.
        alias: "a Budapest district's colloquial name (also a club name for several)"
        for alias in aliases}
    for name in names:
        if name in need_cue or name in need_suffix:
            continue
        lower = name.lower()
        docs = freq.get(lower, 0)
        if lower in stops:
            need_cue[name] = f"'{lower}' is a Hungarian stop-word"
        elif name in people:
            need_cue[name] = "also the name of a person in the register"
        elif docs >= STRONG_DOC_FREQ:
            need_cue[name] = f"'{lower}' is an everyday word ({docs} sitting days)"
        elif docs >= WEAK_DOC_FREQ:
            need_suffix[name] = f"'{lower}' also occurs as a common word ({docs} days)"

    ambiguous = set(need_cue) | set(need_suffix)
    demonyms: dict[str, str] = {}
    for name in names:
        # An ambiguous name gets no demonym at all: the derived form carries no
        # place ending to vouch for it and no cue rule can be applied to a single
        # adjective, so *Polgár* would claim every "polgári" in the corpus.
        if len(name) < _ADJ_MIN_LENGTH or name in ambiguous or " " in name:
            continue
        lower = name.lower()
        demonyms.setdefault(lower + "i", name)
        if lower[-1] in "ae" and len(name) >= _ADJ_ELISION_MIN_LENGTH:
            demonyms.setdefault(lower[:-1] + "i", name)
    # Eger's demonym elides the stem vowel (egri), which no general rule predicts.
    if "Eger" in names and "Eger" not in ambiguous:
        demonyms.setdefault("egri", "Eger")

    return Gazetteer(by_name=dict(names), demonyms=demonyms,
                     need_cue=frozenset(need_cue), need_suffix=frozenset(need_suffix),
                     reasons={**need_cue, **need_suffix}, districts=districts)


# ---------------------------------------------------------------------------
# Segmented map: H3 binning (TEL-15)
#
# The point map answers "which places"; binning answers "where". H3 is used rather
# than a square grid or an administrative unit because its cells are near-equal-area
# and near-equal-shape at a given resolution, they nest hierarchically, and hexagons
# have uniform adjacency — a square grid's diagonal neighbours sit 1.41× further away
# than its edge ones, which distorts every cluster a reader thinks they see.
#
# The library is an ordinary dependency and its absence is not an error: without it
# the cell table is simply never built and the segmented view reports itself
# unavailable (EXT-6), exactly as the neural passes degrade elsewhere.
# ---------------------------------------------------------------------------

try:                                        # pragma: no cover - import shape
    import h3
except ImportError:                         # pragma: no cover - optional dep
    h3 = None

# Which resolutions the site offers. Over Hungary's 93 000 km²: 4 ≈ 50 cells
# (regional), 5 ≈ 370 (about a district), 6 ≈ 2 600 (finer than the settlement
# pattern itself). Configurable without a code change (OPS-4).
H3_RESOLUTIONS: tuple[int, ...] = tuple(sorted({
    int(r) for r in (os.environ.get("PARLAMONITOR_H3_RESOLUTIONS") or "4,5,6")
    .replace(" ", "").split(",") if r.strip().lstrip("-").isdigit()
    and 0 <= int(r) <= 12
})) or (4, 5, 6)

# The default when a request names none: the middle of the offered set, which is the
# reading most people want — a region, not a county and not a village.
H3_DEFAULT_RESOLUTION = H3_RESOLUTIONS[len(H3_RESOLUTIONS) // 2]


def h3_available() -> bool:
    """Whether the segmented map can be built/served at all (TEL-15)."""
    return h3 is not None


def h3_cell(lat: float, lon: float, resolution: int) -> str | None:
    """The H3 cell a settlement's centre point falls in, or ``None`` when the library
    is missing or the coordinate is unusable."""
    if h3 is None or lat is None or lon is None:
        return None
    try:
        return h3.latlng_to_cell(float(lat), float(lon), int(resolution))
    except (ValueError, TypeError):          # pragma: no cover - upstream junk
        return None


def h3_polygon(cell: str) -> dict | None:
    """A cell's boundary as a **GeoJSON** ``Polygon``.

    h3 hands back ``(lat, lng)`` pairs and leaves the ring open; GeoJSON wants
    longitude first and the ring closed — the same two conversions
    :func:`app.valasztas._ring` makes for the election office's polygons, so the
    frontend receives one geometry format from this whole feature and never a bespoke
    one.
    """
    if h3 is None:
        return None
    try:
        boundary = h3.cell_to_boundary(cell)
    except (ValueError, TypeError):           # pragma: no cover - bad cell id
        return None
    # Rounded to five decimals — about a metre, which is far finer than any hexagon
    # edge this is drawn at. h3 returns full doubles, and eighteen significant digits
    # per coordinate is a third of the payload spent on noise.
    ring = [[round(lon, 5), round(lat, 5)] for lat, lon in boundary]
    if len(ring) < 3:
        return None
    ring.append(list(ring[0]))
    return {"type": "Polygon", "coordinates": [ring]}


def h3_cell_area(resolution: int) -> float | None:
    """Average cell area in km², so the UI can label a resolution with the size it
    means instead of a bare number (TEL-15).

    Rounded to whole km² once past ten, because the figure is shown as an
    approximation ("about 253 km²") and a decimal on an approximation reads as a
    precision that is not being claimed.
    """
    if h3 is None:
        return None
    try:
        area = h3.average_hexagon_area(int(resolution), "km^2")
    except (ValueError, TypeError):           # pragma: no cover
        return None
    return round(area) if area >= 10 else round(area, 2)


def fold(value: str | None) -> str:
    """Accent- and case-folded form, for the settlement search box (§4B FOLD-1).

    Matching against the transcript is deliberately **not** folded — the official
    record is correctly accented Hungarian, and folding would merge *Komló* with
    *Kömlő* and *Komoró* with *Kömörő*, the register's only two collisions.
    """
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
