"""Astrological signs on the profile and in the comparison (REP-16).

The loader side is already covered (test_loader.py); these are the three things that
can go wrong once the signs are *served*:

* the endpoints hand out the **signs and not the birth date** — the date is the input
  they are derived from, and publishing it is a bigger disclosure than the site
  decided to make (§4.1);
* a person with no Wikidata date comes back **null**, so the UI can say "nincs adat"
  rather than inventing a sign; and
* every key the scraper can emit has a **display label in both locales** — a sign
  with no label would render as the raw i18n path `profile.zodiacSign.<key>` on a
  public page, which is the one failure nobody would notice in review.

The last one reaches out of the backend into the scraper and the frontend on purpose:
those three places have to agree on one vocabulary, and nothing else checks that.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def test_profile_serves_the_signs_but_not_the_birth_date(client):
    """k001 is dated in the fixture (1968-08-30 → virgo / monkey)."""
    p = client.get("/api/v1/representatives/k001").json()
    assert p["zodiac_sign"] == "virgo"
    assert p["chinese_zodiac_sign"] == "monkey"
    # The date stays internal — no key spells it, under any name.
    assert not [k for k in p if "birth" in k.lower() or k == "date_of_birth"]


def test_comparison_serves_the_signs_but_not_the_birth_date(client):
    person = client.get("/api/v1/representatives/compare",
                        params=[("id", "k001")]).json()["people"][0]
    assert person["zodiac_sign"] == "virgo"
    assert person["chinese_zodiac_sign"] == "monkey"
    assert not [k for k in person if "birth" in k.lower() or k == "date_of_birth"]


def test_an_undated_person_has_no_sign_rather_than_a_guessed_one(client):
    """n002 has no birth date in the fixture. Null, so the UI says "nincs adat" —
    a sign derived from nothing would be indistinguishable from a real one."""
    for payload in (client.get("/api/v1/representatives/n002").json(),
                    client.get("/api/v1/representatives/compare",
                               params=[("id", "n002")]).json()["people"][0]):
        assert payload["zodiac_sign"] is None
        assert payload["chinese_zodiac_sign"] is None


def _scraper_vocabulary() -> tuple[list[str], list[str]]:
    """The two sign key lists as `scraper/parlamonitor/zodiac.py` defines them,
    read from the source rather than restated here — a copy would drift silently,
    which is the very thing this test exists to catch."""
    src = (REPO / "scraper" / "parlamonitor" / "zodiac.py")
    if not src.exists():
        pytest.skip("scraper package not present in this checkout")
    text = src.read_text(encoding="utf-8")
    # The sun signs are the third element of each _SIGN_STARTS row.
    sun = re.findall(r'^\s*\(\d+,\s*\d+,\s*"(\w+)"\),', text, re.M)
    animals_block = re.search(r"CHINESE_SIGNS[^=]*=\s*\(([^)]*)\)", text)
    assert animals_block, "CHINESE_SIGNS not found — did zodiac.py change shape?"
    animals = re.findall(r'"(\w+)"', animals_block.group(1))
    assert len(sun) == 12 and len(animals) == 12, (sun, animals)
    return sun, animals


def _locale_sign_labels(locale: str, block: str) -> dict[str, str]:
    """The `profile.<block>` label map out of a locale file. Parsed rather than
    imported, since these are ES modules and this is a Python suite."""
    src = (REPO / "frontend" / "src" / "locales" / f"{locale}.js")
    if not src.exists():
        pytest.skip("frontend not present in this checkout")
    text = src.read_text(encoding="utf-8")
    body = re.search(block + r"\s*:\s*\{(.*?)\}", text, re.S)
    assert body, f"{block} not found in {locale}.js"
    return dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", body.group(1)))


@pytest.mark.parametrize("locale", ["hu", "en"])
def test_every_sign_key_has_a_label_in_both_locales(locale):
    """The scraper, the API and the UI share one vocabulary. A key the UI cannot
    name would be printed as `profile.zodiacSign.<key>` to the public."""
    sun, animals = _scraper_vocabulary()
    for keys, block in ((sun, "zodiacSign"), (animals, "chineseSign")):
        labels = _locale_sign_labels(locale, block)
        missing = [k for k in keys if not labels.get(k)]
        assert not missing, f"{locale}.js {block} has no label for: {missing}"
        # And nothing invented on the UI side either, which would be a label for a
        # sign no scraper run can ever produce.
        assert not set(labels) - set(keys)


def test_every_sign_key_has_a_glyph():
    """The glyph map lives in the frontend's zodiac lib, which returns *null* for a
    key it has no glyph for — so a gap there silently drops the sign rather than
    printing a broken one. Cheap to check, invisible when wrong."""
    lib = REPO / "frontend" / "src" / "lib" / "zodiac.js"
    if not lib.exists():
        pytest.skip("frontend not present in this checkout")
    text = lib.read_text(encoding="utf-8")
    sun, animals = _scraper_vocabulary()
    for keys, name in ((sun, "SUN_GLYPHS"), (animals, "ANIMAL_GLYPHS")):
        body = re.search(name + r"\s*=\s*\{(.*?)\}", text, re.S)
        assert body, f"{name} not found in zodiac.js"
        mapped = set(re.findall(r"(\w+)\s*:", body.group(1)))
        assert not [k for k in keys if k not in mapped], \
            f"{name} has no glyph for: {[k for k in keys if k not in mapped]}"


def test_the_dev_corpus_holds_no_sign_the_ui_cannot_name(conn):
    """Belt and braces on real data: whatever keys actually reached the DB must be
    ones the locales can print. On the tiny fixture this is nearly vacuous; run
    against a production copy it is the check that matters."""
    sun, animals = _scraper_vocabulary()
    for column, allowed in (("zodiac_sign", sun), ("chinese_zodiac_sign", animals)):
        found = {r[0] for r in conn.execute(
            f"SELECT DISTINCT {column} FROM person WHERE {column} IS NOT NULL")}
        assert not found - set(allowed), f"unknown {column} values: {found - set(allowed)}"
