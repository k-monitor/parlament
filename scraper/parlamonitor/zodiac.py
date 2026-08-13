"""Astrological signs (csillagjegy) derived from a date of birth.

Two of them, both pure functions of the birth *day*, so both are computed once at
scrape time next to the Wikidata linker that supplies the dates rather than
recomputed by every reader:

* **the sun sign** (Western/tropical — Aries … Pisces), from the month and day;
* **the Chinese zodiac animal** (生肖 — Rat … Pig), from the *lunar* year, which
  is why it needs more than arithmetic (see below).

Western boundaries are the conventional civil-calendar ones used by newspapers and
almanacs (Aries 03-21 … Pisces 02-19). The true boundary is the instant the Sun
enters the sign and drifts by about a day with the year and the time of day, so a
birth *on* a cusp date can disagree with an ephemeris; without a birth time — which
Wikidata does not carry — no more precision is available, and the conventional
table is what a reader would check their own date against.
"""

from __future__ import annotations

import datetime as _dt

# (month, day) each sign starts on, in calendar order. Capricorn appears once, on
# its December start: it also owns January 1–19, which `sign_for` handles as the
# wrap-around case below.
_SIGN_STARTS: tuple[tuple[int, int, str], ...] = (
    (1, 20, "aquarius"),
    (2, 19, "pisces"),
    (3, 21, "aries"),
    (4, 20, "taurus"),
    (5, 21, "gemini"),
    (6, 21, "cancer"),
    (7, 23, "leo"),
    (8, 23, "virgo"),
    (9, 23, "libra"),
    (10, 23, "scorpio"),
    (11, 22, "sagittarius"),
    (12, 22, "capricorn"),
)

#: The twelve sign keys, language-neutral (lowercase Latin names) so the display
#: label stays a UI concern.
SIGNS: tuple[str, ...] = tuple(key for _, _, key in _SIGN_STARTS)


def iso_day(value: str | None) -> str | None:
    """Normalize a date to a plain ``YYYY-MM-DD`` day, or ``None`` if it isn't one.

    Accepts what upstream sources actually hand us — a bare date, a full timestamp
    (``1965-05-31T00:00:00Z``), and Wikidata's leading ``+`` sign. Anything else
    (an empty value, a year-only string, a BCE date) yields ``None`` rather than a
    guess, since a partial date cannot fix a sign."""
    if not value:
        return None
    text = str(value).strip().lstrip("+")
    try:
        return _dt.date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def sign_for(value: str | None) -> str | None:
    """Return the sun-sign key (e.g. ``"taurus"``) for a birth date, else ``None``.

    Takes the same date forms as :func:`iso_day`; an unusable date is simply
    unsigned — never a fallback sign."""
    day = iso_day(value)
    if day is None:
        return None
    date = _dt.date.fromisoformat(day)
    for month, start_day, key in reversed(_SIGN_STARTS):
        if (date.month, date.day) >= (month, start_day):
            return key
    # Before 01-20 — still in the Capricorn that began the previous December.
    return "capricorn"


# ---------------------------------------------------------------------------
# Chinese zodiac (生肖) — the animal of the LUNAR year
# ---------------------------------------------------------------------------
# The animal turns over at Chinese New Year, which falls anywhere from 21 January
# to 20 February, so it cannot be derived from the Gregorian year alone: somebody
# born on 1 February 1965 belongs to the *Rabbit* year that began in February
# 1964, not to the Snake year that began on 2 February 1965. Getting that right
# needs the actual date of each lunar new year, and computing it means new-moon
# and solstice astronomy plus the leap-month rule — deliberately not done here,
# both because the scraper's dependency list is kept minimal (SCR-6) and because a
# subtly wrong ephemeris would silently mislabel February birthdays.
#
# So the dates are tabulated instead, from the **Hong Kong Observatory's** official
# Gregorian-Lunar Calendar conversion tables (hko.gov.hk/en/gts/time/calendar),
# which is one of the bodies that publishes the calendar authoritatively. 1924
# onwards was additionally cross-checked against Wikipedia's sexagenary-cycle table
# — two independent sources, all 120 of those years in agreement, including the
# extremes (1966-01-21 is the earliest new year in the whole range, 1985-02-20 the
# latest). Outside the tabulated range there is no sign rather than a guess.
#
# The range starts well before any sitting MP was born (the earliest birth date on
# record here is 1921) and runs far enough ahead that it needs no attention.

_FIRST_LUNAR_YEAR = 1901
_RAT_YEAR = 1924  # anchor of the twelve-year animal cycle

#: Chinese New Year as ``MMDD``, one entry per year from ``_FIRST_LUNAR_YEAR``.
_LUNAR_NEW_YEAR = (
    "0219 0208 0129 0216 0204 0125 0213 0202 0122 0210 "  # 1901–1910
    "0130 0218 0206 0126 0214 0203 0123 0211 0201 0220 "  # 1911–1920
    "0208 0128 0216 "                                     # 1921–1923
    "0205 0124 0213 0202 0123 0210 0130 0217 0206 0126 "  # 1924–1933
    "0214 0204 0124 0211 0131 0219 0208 0127 0215 0205 "  # 1934–1943
    "0125 0213 0202 0122 0210 0129 0217 0206 0127 0214 "  # 1944–1953
    "0203 0124 0212 0131 0218 0208 0128 0215 0205 0125 "  # 1954–1963
    "0213 0202 0121 0209 0130 0217 0206 0127 0215 0203 "  # 1964–1973
    "0123 0211 0131 0218 0207 0128 0216 0205 0125 0213 "  # 1974–1983
    "0202 0220 0209 0129 0217 0206 0127 0215 0204 0123 "  # 1984–1993
    "0210 0131 0219 0207 0128 0216 0205 0124 0212 0201 "  # 1994–2003
    "0122 0209 0129 0218 0207 0126 0214 0203 0123 0210 "  # 2004–2013
    "0131 0219 0208 0128 0216 0205 0125 0212 0201 0122 "  # 2014–2023
    "0210 0129 0217 0206 0126 0213 0203 0123 0211 0131 "  # 2024–2033
    "0219 0208 0128 0215 0204 0124 0212 0201 0122 0210 "  # 2034–2043
).split()

#: The twelve animals in cycle order, anchored on ``_RAT_YEAR``.
CHINESE_SIGNS: tuple[str, ...] = (
    "rat", "ox", "tiger", "rabbit", "dragon", "snake",
    "horse", "goat", "monkey", "rooster", "dog", "pig",
)


def lunar_new_year(year: int) -> _dt.date | None:
    """The Gregorian date Chinese New Year fell on in ``year``, if tabulated."""
    index = year - _FIRST_LUNAR_YEAR
    if not 0 <= index < len(_LUNAR_NEW_YEAR):
        return None
    mmdd = _LUNAR_NEW_YEAR[index]
    return _dt.date(year, int(mmdd[:2]), int(mmdd[2:]))


def chinese_sign_for(value: str | None) -> str | None:
    """Return the Chinese zodiac animal (e.g. ``"dragon"``) for a birth date.

    Takes the same date forms as :func:`iso_day`. ``None`` for an unusable date, or
    for one outside the tabulated lunar-new-year range — a January birthday in an
    untabulated year genuinely cannot be placed, and the wrong animal is worse than
    none."""
    day = iso_day(value)
    if day is None:
        return None
    date = _dt.date.fromisoformat(day)
    new_year = lunar_new_year(date.year)
    if new_year is None:
        return None
    # Born before the new year began → still the previous lunar year's animal.
    lunar_year = date.year - 1 if date < new_year else date.year
    return CHINESE_SIGNS[(lunar_year - _RAT_YEAR) % 12]
