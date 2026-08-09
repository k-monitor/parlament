"""Portfolio (*tárca*) resolution — the government side of the corpus (§6C).

Everything the House puts to the executive is addressed to a **tárca**, and
everything the government lays before the House comes back through one. The
corpus records that relationship in four places, but only ever as a free-text
office label on a row:

  1. ``bill_event.related_label``  — who answered a kérdés / interpelláció
  2. ``bill_sponsor.label``        — ``kormány (belügyminiszter)``, the submitter
  3. ``speech.speaker_office``     — the office a speaker spoke in
  4. ``person_office.title``       — the office-holder registry's dated terms

The same tárca therefore appears in up to four surface forms — *"Belügyminisztérium
államtitkára"*, *"belügyminiszter"*, *"belügyminisztériumi politikai államtitkár"*,
*"kormány (belügyminiszter)"* — which no reader can be expected to collate by eye
across 67 000 irományok. This module is the one place that says which label means
which tárca (MIN-3).

**It is a table, not an inference.** Hungarian morphology *almost* gets there
(``belügyminiszter`` → ``Belügyminisztérium``) and then fails exactly where it
matters: *"innovációért és technológiáért felelős miniszter"* heads the
*Innovációs és Technológiai Minisztérium*, and no rule derives one from the other.
A wrong guess here silently misfiles thousands of questions under the wrong
ministry, so every label is enumerated and the mapping is auditable at a glance
(TRUST-1). The table was drafted from the corpus's own label counts and then
reviewed; the runtime does dict lookup and nothing else.

Consequences of that choice, all deliberate:

* A label **not in the table stands alone** as its own portfolio, named after
  itself (:func:`resolve` returns ``None`` and the caller keeps the raw label).
  An unmapped tárca is a visible gap to fix here — never silently folded into a
  neighbour, and never dropped.
* **Renames are not merged.** *Nemzeti Erőforrás Minisztérium* and *Emberi
  Erőforrások Minisztériuma* are separate entries even though the second is the
  first renamed, and *Külügyminisztérium* is not *Külgazdasági és
  Külügyminisztérium*. Continuity across a machinery-of-government change is a
  claim the source does not make, so the site does not make it either (MIN-9).
* **Personal commissions are excluded** (:data:`EXCLUDED_LABELS`): a
  *miniszterelnöki biztos* or *kormánymegbízott* is one person's brief, attached
  to no tárca the source names.

Configurable per OPS-4: ``PARLAMONITOR_PORTFOLIO_MAP`` points at a JSON file
whose entries are added to (or override, by slug) the table below.
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

logger = logging.getLogger(__name__)

# Display order of the kinds on the listing page (MIN-5): the ministries first,
# the independent bodies last, so the page reads as what it is.
KINDS = ("ministry", "pm", "no-portfolio", "other", "body")


@dataclass(frozen=True)
class Portfolio:
    slug: str                    # stable id, used in URLs and stored on rows
    name: str                    # canonical Hungarian name shown in the UI
    kind: str                    # one of KINDS
    aliases: tuple[str, ...]     # every label form the corpus uses for it




def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def normalize_label(label: str | None) -> str:
    """The bare office inside a corpus label.

    Government submitters arrive wrapped (``kormány (belügyminiszter)``) and the
    registry's titles carry a leading article (``a Miniszterelnöki Hivatal
    államtitkára``); both name the same office as the answer events' plain
    ``belügyminiszter``. Whitespace is collapsed because upstream has at least one
    double-spaced title (``tárca nélküli miniszter  (nemzeti vagyon…)``)."""
    s = re.sub(r"\s+", " ", (label or "").strip())
    m = re.match(r"^kormány\s*\((.*)\)$", s, re.S)
    if m:
        s = m.group(1).strip()
    return re.sub(r"^(a|az)\s+", "", s, flags=re.I)


def _fold(label: str | None) -> str:
    """Accent- and case-folded lookup key (§4B FOLD-3): the corpus writes the same
    tárca capitalised as an institution and lowercased as a title."""
    return _strip_accents(normalize_label(label).lower())



# A label that names no tárca at all: a commission held by one person, attached
# to no ministry the source states. Mapping these to a ministry would be a guess,
# so they resolve to nothing and never create a portfolio of their own.
EXCLUDED_LABELS: frozenset[str] = frozenset(
    _fold(x) for x in (
        "kormányhivatalt vezető kormánymegbízott",
        "miniszterelnöki biztos",
        "miniszterelnöki megbízott",
        "miniszteri biztos",
        "a miniszterelnök politikai igazgatója",
        "a miniszterelnök kabinetfőnöke",
        "a köztársasági elnök feladat- és hatásköreit gyakorló OGY elnök",
    )
)


# The table. One entry per tárca, listing every label the corpus uses for it;
# the trailing comment is how many rows in the corpus carry that tárca's labels,
# which is what the review was ordered by.
PORTFOLIOS: tuple[Portfolio, ...] = (

    # --- Ministries and the offices that head them.
    Portfolio("emberi-eroforrasok", "Emberi Erőforrások Minisztériuma", "ministry", (
        "Emberi Erőforrások Minisztériumának államtitkára",
        "emberi erőforrások minisztere",
    )),  # 11776
    Portfolio("belugy", "Belügyminisztérium", "ministry", (
        "Belügyminisztérium államtitkára",
        "belügyminiszter",
        "belügyminisztériumi politikai államtitkár",
    )),  # 8095
    Portfolio("nemzetgazdasagi", "Nemzetgazdasági Minisztérium", "ministry", (
        "Nemzetgazdasági Minisztérium államtitkára",
        "nemzetgazdasági miniszter",
    )),  # 6421
    Portfolio("nemzeti-fejlesztesi", "Nemzeti Fejlesztési Minisztérium", "ministry", (
        "Nemzeti Fejlesztési Minisztérium államtitkára",
        "nemzeti fejlesztési miniszter",
    )),  # 4633
    Portfolio("miniszterelnokseg", "Miniszterelnökség", "ministry", (
        "Miniszterelnökséget vezető miniszter",
        "Miniszterelnökség államtitkára",
        "Miniszterelnökséget vezető államtitkár",
    )),  # 3921
    Portfolio("kulgazdasagi-es-kulugy", "Külgazdasági és Külügyminisztérium", "ministry", (
        "Külgazdasági és Külügyminisztérium államtitkára",
        "külgazdasági és külügyminiszter",
    )),  # 2967
    Portfolio("agrar", "Agrárminisztérium", "ministry", (
        "agrárminiszter",
        "Agrárminisztérium államtitkára",
    )),  # 2550
    Portfolio("innovacios-es-technologiai", "Innovációs és Technológiai Minisztérium", "ministry", (
        "Innovációs és Technológiai Minisztérium államtitkára",
        "innovációért és technológiáért felelős miniszter",
    )),  # 2531
    Portfolio("honvedelmi", "Honvédelmi Minisztérium", "ministry", (
        "honvédelmi miniszter",
        "Honvédelmi Minisztérium államtitkára",
        "honvédelmi minisztériumi politikai államtitkár",
    )),  # 2328
    Portfolio("kozigazgatasi-es-igazsagugyi", "Közigazgatási és Igazságügyi Minisztérium", "ministry", (
        "közigazgatási és igazságügyi minisztériumi államtitkár",
        "közigazgatási és igazságügyi miniszter",
        "Közigazgatási és Igazságügyi Minisztérium",
    )),  # 2238
    Portfolio("videkfejlesztesi", "Vidékfejlesztési Minisztérium", "ministry", (
        "vidékfejlesztési miniszter",
        "vidékfejlesztési minisztériumi államtitkár",
        "Vidékfejlesztési Minisztérium",
    )),  # 2044
    Portfolio("foldmuvelesugyi", "Földművelésügyi Minisztérium", "ministry", (
        "földművelésügyi miniszter",
        "Földművelésügyi Minisztérium államtitkára",
        "földművelésügyi minisztériumi politikai államtitkár",
    )),  # 1801
    Portfolio("igazsagugyi", "Igazságügyi Minisztérium", "ministry", (
        "Igazságügyi Minisztérium államtitkára",
        "igazságügyi miniszter",
        "igazságügyi minisztériumi politikai államtitkár",
        "igazságügy-miniszter",
    )),  # 1750
    Portfolio("nemzeti-eroforras", "Nemzeti Erőforrás Minisztérium", "ministry", (
        "nemzeti erőforrás minisztériumi államtitkár",
        "nemzeti erőforrás miniszter",
    )),  # 1745
    Portfolio("penzugy", "Pénzügyminisztérium", "ministry", (
        "Pénzügyminisztérium államtitkára",
        "pénzügyminiszter",
        "pénzügyminisztériumi politikai államtitkár",
        "pénzügyminisztériumi államtitkár",
    )),  # 1659
    Portfolio("miniszterelnoki-kabinetiroda", "Miniszterelnöki Kabinetiroda", "ministry", (
        "Miniszterelnöki Kabinetiroda államtitkára",
        "Miniszterelnöki Kabinetirodát vezető miniszter",
    )),  # 1612
    Portfolio("energiaugyi", "Energiaügyi Minisztérium", "ministry", (
        "Energiaügyi Minisztérium államtitkára",
        "energiaügyi miniszter",
    )),  # 1214
    Portfolio("epitesi-es-kozlekedesi", "Építési és Közlekedési Minisztérium", "ministry", (
        "Építési és Közlekedési Minisztérium államtitkára",
        "építési és közlekedési miniszter",
    )),  # 1054
    Portfolio("kulturalis-es-innovacios", "Kulturális és Innovációs Minisztérium", "ministry", (
        "Kulturális és Innovációs Minisztérium államtitkára",
        "kultúráért és innovációért felelős miniszter",
    )),  # 684
    Portfolio("kulugy", "Külügyminisztérium", "ministry", (
        "külügyminiszter",
        "külügyminisztériumi államtitkár",
        "Külügyminisztérium államtitkára",
        "külügyminisztériumi politikai államtitkár",
    )),  # 617
    Portfolio("technologiai-es-ipari", "Technológiai és Ipari Minisztérium", "ministry", (
        "Technológiai és Ipari Minisztérium államtitkára",
        "technológiai és ipari miniszter",
    )),  # 399
    Portfolio("miniszterelnoki-kormanyiroda", "Miniszterelnöki Kormányiroda", "ministry", (
        "Miniszterelnöki Kormányiroda államtitkára",
    )),  # 397
    Portfolio("gazdasagfejlesztesi", "Gazdaságfejlesztési Minisztérium", "ministry", (
        "Gazdaságfejlesztési Minisztérium államtitkára",
        "gazdaságfejlesztési miniszter",
    )),  # 279
    Portfolio("kozigazgatasi-es-teruletfejlesztesi", "Közigazgatási és Területfejlesztési Minisztérium", "ministry", (
        "Közigazgatási és Területfejlesztési Minisztérium államtitkára",
        "közigazgatási és területfejlesztési miniszter",
    )),  # 210
    Portfolio("kozlekedesi-es-beruhazasi", "Közlekedési és Beruházási Minisztérium", "ministry", (
        "közlekedési és beruházási miniszter",
        "Közlekedési és Beruházási Minisztérium államtitkára",
    )),  # 111
    Portfolio("europai-unios-ugyek", "Európai Uniós Ügyek Minisztériuma", "ministry", (
        "Európai Uniós Ügyek Minisztériumának államtitkára",
        "európai uniós ügyekért felelős miniszter",
    )),  # 86
    Portfolio("egeszsegugyi", "Egészségügyi Minisztérium", "ministry", (
        "egészségügyi miniszter",
        "Egészségügyi Minisztérium államtitkára",
        "egészségügyi minisztériumi államtitkár",
        "egészségügyi minisztériumi politikai államtitkár",
    )),  # 79
    Portfolio("agrar-es-elelmiszergazdasagi", "Agrár- és Élelmiszergazdasági Minisztérium", "ministry", (
        "Agrár- és Élelmiszergazdasági Minisztérium államtitkára",
        "agrár- és élelmiszergazdaságért felelős miniszter",
    )),  # 69
    Portfolio("gazdasagi-es-energetikai", "Gazdasági és Energetikai Minisztérium", "ministry", (
        "gazdasági és energetikai miniszter",
        "Gazdasági és Energetikai Minisztérium államtitkára",
    )),  # 64
    Portfolio("videk-es-telepulesfejlesztesi", "Vidék- és Településfejlesztési Minisztérium", "ministry", (
        "vidék- és településfejlesztési miniszter",
        "Vidék- és Településfejlesztési Minisztérium államtitkára",
    )),  # 61
    Portfolio("tarsadalmi-kapcsolatokert-es-kulturaert-felelos", "Társadalmi Kapcsolatokért és Kultúráért Felelős Minisztérium", "ministry", (
        "Társadalmi Kapcsolatokért és Kultúráért Felelős Minisztérium államtitkára",
        "társadalmi kapcsolatokért és kultúráért felelős miniszter",
    )),  # 60
    Portfolio("epitesi-es-beruhazasi", "Építési és Beruházási Minisztérium", "ministry", (
        "Építési és Beruházási Minisztérium államtitkára",
        "építési és beruházási miniszter",
    )),  # 53
    Portfolio("miniszterelnoki-hivatal", "Miniszterelnöki Hivatal", "ministry", (
        "Miniszterelnöki Hivatal politikai államtitkára",
        "Miniszterelnöki Hivatal államtitkára",
        "Miniszterelnöki Hivatalt vezető miniszter",
        "Miniszterelnöki Hivatal általános politikai államtitkára",
    )),  # 48
    Portfolio("oktatasi-es-gyermekugyi", "Oktatási és Gyermekügyi Minisztérium", "ministry", (
        "Oktatási és Gyermekügyi Minisztérium államtitkára",
        "oktatási és gyermekügyi miniszter",
    )),  # 48
    Portfolio("szocialis-es-csaladugyi", "Szociális és Családügyi Minisztérium", "ministry", (
        "szociális és családügyi miniszter",
        "Szociális és Családügyi Minisztérium államtitkára",
        "szociális és családügyi minisztériumi politikai államtitkár",
    )),  # 48
    Portfolio("elo-kornyezetert-felelos", "Élő Környezetért Felelős Minisztérium", "ministry", (
        "élő környezetért felelős miniszter",
        "Élő Környezetért Felelős Minisztérium államtitkára",
    )),  # 35
    Portfolio("tudomanyos-es-technologiai", "Tudományos és Technológiai Minisztérium", "ministry", (
        "tudományos és technológiai miniszter",
        "Tudományos és Technológiai Minisztérium államtitkára",
    )),  # 29
    Portfolio("foldmuvelesugyi-es-videkfejlesztesi", "Földművelésügyi és Vidékfejlesztési Minisztérium", "ministry", (
        "földművelésügyi és vidékfejlesztési minisztériumi államtitkár",
        "földművelésügyi és vidékfejlesztési miniszter",
        "földművelésügyi és vidékfejlesztési minisztériumi politikai államtitkár",
    )),  # 22
    Portfolio("teruletfejlesztesi", "Területfejlesztési Minisztérium", "ministry", (
        "területfejlesztési miniszter",
    )),  # 17
    Portfolio("gazdasagi-es-kozlekedesi", "Gazdasági és Közlekedési Minisztérium", "ministry", (
        "gazdasági és közlekedési minisztériumi politikai államtitkár",
        "gazdasági és közlekedési miniszter",
        "gazdasági és közlekedési minisztériumi államtitkár",
    )),  # 14
    Portfolio("kornyezetvedelmi-es-vizugyi", "Környezetvédelmi és Vízügyi Minisztérium", "ministry", (
        "környezetvédelmi és vízügyi miniszter",
        "környezetvédelmi és vízügyi minisztériumi politikai államtitkár",
        "környezetvédelmi és vízügyi minisztériumi államtitkár",
    )),  # 13
    Portfolio("igazsagugyi-es-rendeszeti", "Igazságügyi és Rendészeti Minisztérium", "ministry", (
        "igazságügyi és rendészeti miniszter",
        "igazságügyi és rendészeti minisztériumi államtitkár",
    )),  # 12
    Portfolio("nemzeti-kulturalis-orokseg", "Nemzeti Kulturális Örökség Minisztériuma", "ministry", (
        "nemzeti kulturális örökség minisztere",
        "Nemzeti Kulturális Örökség Minisztériumának politikai államtitkára",
    )),  # 11
    Portfolio("szocialis-es-munkaugyi", "Szociális és Munkaügyi Minisztérium", "ministry", (
        "szociális és munkaügyi minisztériumi államtitkár",
        "szociális és munkaügyi miniszter",
        "Szociális és Munkaügyi Minisztérium",
    )),  # 11
    Portfolio("muvelodesi-es-kozoktatasi", "Művelődési és Közoktatási Minisztérium", "ministry", (
        "művelődési és közoktatási minisztériumi politikai államtitkár",
        "művelődési és közoktatási miniszter",
    )),  # 10
    Portfolio("oktatasi-es-kulturalis", "Oktatási és Kulturális Minisztérium", "ministry", (
        "oktatási és kulturális miniszter",
        "oktatási és kulturális minisztériumi államtitkár",
    )),  # 10
    Portfolio("oktatasi", "Oktatási Minisztérium", "ministry", (
        "oktatási minisztériumi politikai államtitkár",
        "oktatási miniszter",
    )),  # 9
    Portfolio("munkaugyi", "Munkaügyi Minisztérium", "ministry", (
        "munkaügyi miniszter",
        "munkaügyi minisztériumi politikai államtitkár",
    )),  # 8
    Portfolio("nepjoleti", "Népjóléti Minisztérium", "ministry", (
        "népjóléti miniszter",
        "népjóléti minisztériumi politikai államtitkár",
    )),  # 8
    Portfolio("egeszsegugyi-szocialis-es-csaladugyi", "Egészségügyi, Szociális és Családügyi Minisztérium", "ministry", (
        "egészségügyi, szociális és családügyi minisztériumi politikai államtitkár",
        "egészségügyi, szociális és családügyi miniszter",
    )),  # 7
    Portfolio("ipari-es-kereskedelmi", "Ipari és Kereskedelmi Minisztérium", "ministry", (
        "ipari és kereskedelmi miniszter",
        "ipari és kereskedelmi politikai államtitkár",
    )),  # 7
    Portfolio("kornyezetvedelmi", "Környezetvédelmi Minisztérium", "ministry", (
        "környezetvédelmi miniszter",
        "környezetvédelmi minisztériumi politikai államtitkár",
    )),  # 7
    Portfolio("kozlekedesi-hirkozlesi-es-energiaugyi", "Közlekedési, Hírközlési és Energiaügyi Minisztérium", "ministry", (
        "közlekedési, hírközlési és energiaügyi minisztériumi államtitkár",
        "közlekedési, hírközlési és energiaügyi miniszter",
        "Közlekedési, Hírközlési és Energiaügyi Minisztérium",
    )),  # 7
    Portfolio("nemzeti-fejlesztesi-es-gazdasagi", "Nemzeti Fejlesztési és Gazdasági Minisztérium", "ministry", (
        "nemzeti fejlesztési és gazdasági minisztériumi államtitkár",
        "nemzeti fejlesztési és gazdasági miniszter",
    )),  # 7
    Portfolio("kozlekedesi-hirkozlesi-es-vizugyi", "Közlekedési, Hírközlési és Vízügyi Minisztérium", "ministry", (
        "közlekedési, hírközlési és vízügyi miniszter",
        "közlekedési, hírközlési és vízügyi minisztériumi politikai államtitkár",
    )),  # 6
    Portfolio("foglalkoztataspolitikai-es-munkaugyi", "Foglalkoztatáspolitikai és Munkaügyi Minisztérium", "ministry", (
        "foglalkoztatáspolitikai és munkaügyi miniszter",
        "foglalkoztatáspolitikai és munkaügyi minisztériumi politikai államtitkár",
    )),  # 5
    Portfolio("onkormanyzati", "Önkormányzati Minisztérium", "ministry", (
        "önkormányzati miniszter",
        "önkormányzati minisztériumi államtitkár",
    )),  # 5
    Portfolio("gazdasagi", "Gazdasági Minisztérium", "ministry", (
        "gazdasági miniszter",
        "gazdasági minisztériumi politikai államtitkár",
    )),  # 4
    Portfolio("gyermek-ifjusagi-es-sport", "Gyermek-, Ifjúsági és Sportminisztérium", "ministry", (
        "gyermek-, ifjúsági és sport miniszter",
        "gyermek-, ifjúsági és sport minisztériumi politikai államtitkár",
    )),  # 4
    Portfolio("ifjusagi-csaladugyi-szocialis-es-eselyegyenlosegi", "Ifjúsági, Családügyi, Szociális és Esélyegyenlőségi Minisztérium", "ministry", (
        "ifjúsági, családügyi, szociális és esélyegyenlőségi minisztériumi politikai államtitkár",
        "ifjúsági, családügyi, szociális és esélyegyenlőségi miniszter",
    )),  # 4
    Portfolio("informatikai-es-hirkozlesi", "Informatikai és Hírközlési Minisztérium", "ministry", (
        "informatikai és hírközlési miniszter",
        "informatikai és hírközlési minisztériumi politikai államtitkár",
    )),  # 4
    Portfolio("ipari-kereskedelmi-es-idegenforgalmi", "Ipari, Kereskedelmi és Idegenforgalmi Minisztérium", "ministry", (
        "ipari, kereskedelmi és idegenforgalmi miniszter",
        "ipari, kereskedelmi és idegenforgalmi politikai államtitkár",
    )),  # 3
    Portfolio("kornyezetvedelmi-es-teruletfejlesztesi", "Környezetvédelmi és Területfejlesztési Minisztérium", "ministry", (
        "környezetvédelmi és területfejlesztési miniszter",
        "környezetvédelmi és területfejlesztési minisztériumi politikai államtitkár",
    )),  # 3
    Portfolio("onkormanyzati-es-teruletfejlesztesi", "Önkormányzati és Területfejlesztési Minisztérium", "ministry", (
        "önkormányzati és területfejlesztési miniszter",
        "önkormányzati és területfejlesztési minisztériumi államtitkár",
    )),  # 3
    Portfolio("ifjusagi-es-sport", "Ifjúsági és Sportminisztérium", "ministry", (
        "ifjúsági és sport miniszter",
        "ifjúsági és sport minisztériumi politikai államtitkár",
    )),  # 2
    Portfolio("kozlekedesi-es-hirkozlesi", "Közlekedési és Hírközlési Minisztérium", "ministry", (
        "közlekedési és hírközlési miniszter",
        "közlekedési és hírközlési minisztériumi politikai államtitkár",
    )),  # 2
    Portfolio("nemzetkozi-gazdasagi-kapcsolatok", "Nemzetközi Gazdasági Kapcsolatok Minisztériuma", "ministry", (
        "nemzetközi gazdasági kapcsolatok minisztere",
        "nemzetközi gazdasági kapcsolatok minisztériumi politikai államtitkár",
    )),  # 2
    Portfolio("kozlekedesi-es-vizugyi", "Közlekedési és Vízügyi Minisztérium", "ministry", (
        "közlekedési és vízügyi miniszter",
    )),  # 1

    # --- The Prime Minister in person.
    Portfolio("miniszterelnok", "Miniszterelnök", "pm", (
        "miniszterelnök",
    )),  # 1373

    # --- Ministers without portfolio — the remit, not a ministry, is what
    # questions are addressed to.
    Portfolio("tarca-nelkuli-nemzetpolitikaert-felelos", "Tárca nélküli miniszter (nemzetpolitikáért felelős)", "no-portfolio", (
        "tárca nélküli miniszter (nemzetpolitikáért felelős)",
    )),  # 391
    Portfolio("nemzetpolitikaert-egyhazpolitikaert-felelos-miniszter", "Nemzetpolitikáért, nemzetiségpolitikáért, egyházpolitikáért és egyházdiplomáciáért felelős miniszter", "no-portfolio", (
        "nemzetpolitikáért, nemzetiségpolitikáért, egyházpolitikáért és egyházdiplomáciáért felelős miniszter",
    )),  # 169
    Portfolio("tarca-nelkuli-paks", "Tárca nélküli miniszter (a Paksi Atomerőmű két új blokkja tervezéséért, megépítéséért és üzembe helyezéséért felelős)", "no-portfolio", (
        "tárca nélküli miniszter (a Paksi Atomerőmű két új blokkja tervezéséért, megépítéséért és üzembe helyezéséért felelős)",
    )),  # 164
    Portfolio("tarca-nelkuli-miniszter", "Tárca nélküli miniszter", "no-portfolio", (
        "tárca nélküli miniszter",
    )),  # 93
    Portfolio("tarca-nelkuli-csaladokert-felelos", "Tárca nélküli miniszter (családokért felelős)", "no-portfolio", (
        "tárca nélküli miniszter (családokért felelős)",
    )),  # 76
    Portfolio("tarca-nelkuli-megyei-jogu-varosok", "Tárca nélküli miniszter (a megyei jogú városok fejlesztéséért felelős)", "no-portfolio", (
        "tárca nélküli miniszter (a megyei jogú városok fejlesztéséért felelős)",
    )),  # 39
    Portfolio("tarca-nelkuli-nemzeti-vagyon", "Tárca nélküli miniszter (nemzeti vagyon kezeléséért felelős)", "no-portfolio", (
        "tárca nélküli miniszter (nemzeti vagyon kezeléséért felelős)",
    )),  # 30
    Portfolio("tarca-nelkuli-nemzetkozi-penzugyi-szervezetek", "Egyes nemzetközi pénzügyi szervezetekkel való kapcsolattartásért felelős tárca nélküli miniszter", "no-portfolio", (
        "Egyes nemzetközi pénzügyi szervezetekkel való kapcsolattartásért felelős tárca nélküli miniszter",
    )),  # 15
    Portfolio("tarca-nelkuli-nemzetbiztonsagi-szolgalatok", "Polgári nemzetbiztonsági szolgálatokat irányító tárca nélküli miniszter", "no-portfolio", (
        "polgári nemzetbiztonsági szolgálatokat irányító tárca nélküli miniszter",
        "polgári nemzetbiztonsági szolgálatok irányításában közreműködő államtitkár",
        "polgári nemzetbiztonsági szolgálatokat irányító tárca nélküli miniszter politikai államtitkára",
    )),  # 14
    Portfolio("tarca-nelkuli-europai-ugyekert", "Európai ügyekért felelős tárca nélküli miniszter", "no-portfolio", (
        "európai ügyekért felelős politikai államtitkár",
        "európai ügyekért felelős tárca nélküli miniszter",
    )),  # 3
    Portfolio("tarca-nelkuli-tarsadalompolitika", "Társadalompolitika összehangolásáért felelős tárca nélküli miniszter", "no-portfolio", (
        "társadalompolitika összehangolásáért felelős tárca nélküli miniszter",
        "társadalompolitika összehangolásáért felelős tárca nélküli miniszter államtitkára",
    )),  # 3
    Portfolio("tarca-nelkuli-eselyegyenlosegi", "Esélyegyenlőségi tárca nélküli miniszter", "no-portfolio", (
        "esélyegyenlőségi tárca nélküli miniszter",
    )),  # 2
    Portfolio("tarca-nelkuli-kutatas-fejlesztesert", "Kutatás-fejlesztésért felelős tárca nélküli miniszter", "no-portfolio", (
        "kutatás-fejlesztésért felelős tárca nélküli miniszter",
        "kutatás-fejlesztésért felelős tárca nélküli miniszter tevékenységében közreműködő államtitkár",
    )),  # 2
    Portfolio("tarca-nelkuli-privatizacioert", "Privatizációért felelős tárca nélküli miniszter", "no-portfolio", (
        "privatizációért felelős tárca nélküli miniszter",
    )),  # 2
    Portfolio("tarca-nelkuli-regionalis-fejlesztes", "Regionális fejlesztésért és felzárkóztatásért felelős tárca nélküli miniszter", "no-portfolio", (
        "regionális fejlesztésért és felzárkóztatásért felelős politikai államtitkár",
        "regionális fejlesztésért és felzárkóztatásért felelős tárca nélküli miniszter",
    )),  # 2
    Portfolio("tarca-nelkuli-europai-integracio", "Európai integrációs ügyek koordinációjáért felelős tárca nélküli miniszter", "no-portfolio", (
        "európai integrációs ügyek koordinációjáért felelős tárca nélküli miniszter",
    )),  # 1
    Portfolio("tarca-nelkuli-kormanyzati-igazgatas", "Kormányzati igazgatás összehangolásáért felelős tárca nélküli miniszter", "no-portfolio", (
        "kormányzati igazgatás összehangolásáért felelős tárca nélküli miniszter",
    )),  # 1
    Portfolio("tarca-nelkuli-phare", "PHARE program koordinációjáért felelős tárca nélküli miniszter", "no-portfolio", (
        "PHARE program koordinációjáért felelős tárca nélküli miniszter",
    )),  # 1

    # --- Government offices that are neither a ministry nor a portfolio.
    Portfolio("miniszterelnok-helyettes", "Miniszterelnök-helyettes", "other", (
        "miniszterelnök-helyettes",
    )),  # 149

    # --- Independent bodies that answer to the House but are not part of the
    # government: their answers are in the record exactly like a ministry's.
    Portfolio("legfobb-ugyeszseg", "Legfőbb Ügyészség", "body", (
        "legfőbb ügyész",
        "legfőbb ügyész helyettese",
    )),  # 1471
    Portfolio("magyar-nemzeti-bank", "Magyar Nemzeti Bank", "body", (
        "Magyar Nemzeti Bank elnöke",
        "Magyar Nemzeti Bank alelnöke",
    )),  # 355
    Portfolio("allami-szamvevoszek", "Állami Számvevőszék", "body", (
        "Állami Számvevőszék elnöke",
        "Állami Számvevőszék alelnöke",
    )),  # 111
    Portfolio("alapveto-jogok-biztosa", "Alapvető Jogok Biztosa", "body", (
        "alapvető jogok biztosa",
        "alapvető jogok biztosának a Magyarországon élő nemzetiségek jogainak védelmét ellátó helyettese",
        "alapvető jogok biztosának a jövő nemzedékek érdekeinek védelmét ellátó helyettese",
    )),  # 93
)


@lru_cache(maxsize=1)
def _index() -> tuple[dict[str, Portfolio], dict[str, Portfolio]]:
    """``(by folded alias, by slug)`` over the table plus any configured override."""
    entries = list(PORTFOLIOS)
    path = os.environ.get("PARLAMONITOR_PORTFOLIO_MAP")
    if path:
        try:
            extra = json.loads(open(path, encoding="utf-8").read())
            override = {e["slug"] for e in extra}
            entries = [p for p in entries if p.slug not in override] + [
                Portfolio(e["slug"], e["name"], e.get("kind", "ministry"),
                          tuple(e.get("aliases") or ()))
                for e in extra]
            logger.info("Portfolio map: %d entries overridden/added from %s",
                        len(extra), path)
        except (OSError, ValueError, KeyError) as e:
            # A broken override must not take the built-in table down with it.
            logger.warning("PARLAMONITOR_PORTFOLIO_MAP %s unusable (%s); "
                           "using the built-in table", path, e)
    by_alias: dict[str, Portfolio] = {}
    by_slug: dict[str, Portfolio] = {}
    for p in entries:
        by_slug[p.slug] = p
        for a in p.aliases:
            by_alias.setdefault(_fold(a), p)
    return by_alias, by_slug


def resolve(label: str | None) -> Portfolio | None:
    """The tárca a corpus label names, or ``None`` when the table doesn't cover it
    (an unmapped label stands alone — MIN-3) or the label names no tárca at all."""
    key = _fold(label)
    if not key or key in EXCLUDED_LABELS:
        return None
    return _index()[0].get(key)


def is_excluded(label: str | None) -> bool:
    """Whether the label names no tárca at all (a personal commission), as
    distinct from one the table simply hasn't got yet. The two look identical
    through :func:`resolve` — both ``None`` — but mean opposite things: an
    excluded label is a decision, an unmapped one is a gap that must still
    surface as its own portfolio."""
    return _fold(label) in EXCLUDED_LABELS


def standalone(label: str | None) -> Portfolio | None:
    """The one-off portfolio an **unmapped** label stands as (MIN-3): named after
    itself, so a tárca the table doesn't know yet is visible in the site rather
    than silently dropped or folded into a neighbour. Returns ``None`` for a label
    that is excluded or already resolves."""
    key = _fold(label)
    if not key or key in EXCLUDED_LABELS or key in _index()[0]:
        return None
    name = normalize_label(label)
    slug = "x-" + re.sub(r"[^a-z0-9]+", "-", _strip_accents(key)).strip("-")[:56]
    return Portfolio(slug, name[:1].upper() + name[1:], "other", (name,))


def by_slug(slug: str) -> Portfolio | None:
    return _index()[1].get(slug)


def all_portfolios() -> tuple[Portfolio, ...]:
    return tuple(_index()[1].values())
