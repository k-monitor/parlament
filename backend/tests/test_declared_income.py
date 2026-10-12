"""Declared income (REP-19): reading the EVNYR "havi átlagos jövedelem" texts.

The field is free text, so every case below is a way a declarant actually wrote
it, or one the form invites. The readings that matter most are the ones where a
careless parser invents a fact: a year read as a second income, "nettó 1.746 Ft"
read as one forint and change, two incomes read as one, net reported as gross.
Where the text is genuinely ambiguous the reading must say so and keep the text,
never pick one.
"""

from __future__ import annotations

import pytest

from app.declared_income import for_person, income_entries, parse_amount


def _ok(text):
    r = parse_amount(text)
    assert r["status"] == "ok", r
    assert r["raw"] == text.strip()
    return r


# --- one amount --------------------------------------------------------------

@pytest.mark.parametrize("text, huf", [
    # From the 2026-10-08 snapshot, as filed.
    ("2380000 (2025-ben)", 2_380_000),
    ("90000", 90_000),
    ("745 000 HUF", 745_000),
    ("5 017 810", 5_017_810),
    ("nettó 3.461.192 Ft", 3_461_192),
    ("nettó 685.393 Ft", 685_393),
    # Dot-grouped thousands, not a decimal: there are no fractional forints.
    ("nettó 1.746 Ft", 1_746),
    ("nettó 34.654 Ft", 34_654),
    # Forms the field invites.
    ("500.000,- Ft", 500_000),
    ("1 200 000 forint", 1_200_000),
    ("2.5M Ft", 2_500_000),
    ("kb. 1,5 millió Ft", 1_500_000),
    ("500 eFt", 500_000),
    ("800 ezer Ft", 800_000),
])
def test_reads_the_amounts_declarants_write(text, huf):
    r = _ok(text)
    assert r["amount_huf"] == huf and r["currency"] == "HUF"
    assert r["period"] == "month" and not r["period_stated"]


def test_a_bare_number_is_forints_and_says_it_was_not_stated():
    """The one reading the text does not spell out, so it is flagged."""
    assert _ok("90000")["currency_stated"] is False
    assert _ok("745 000 HUF")["currency_stated"] is True


@pytest.mark.parametrize("text", [
    "2380000 (2025-ben)",
    "2025. évi átlag 2 380 000 Ft",
    "2026. augusztus 29-től 2 380 000 Ft",
    "2026.09.01-től 2 380 000 Ft",
    "2 380 000 Ft (2025)",
    "december 2 380 000 Ft",
])
def test_a_date_is_never_read_as_a_second_amount(text):
    r = _ok(text)
    assert r["amount_huf"] == 2_380_000
    # "2025. évi" names the year, it does not make the amount yearly.
    assert r["period"] == "month"


def test_net_and_gross_only_when_the_text_says():
    assert _ok("nettó 3.461.192 Ft")["basis"] == "net"
    assert _ok("300 000 Ft/hó bruttó")["basis"] == "gross"
    # Neither is assumed: the published fee is gross, a bare declared figure
    # may be either, and the card must not imply they compare.
    assert _ok("3942565")["basis"] is None


def test_a_period_other_than_the_month_is_reported_not_converted():
    r = _ok("évi 6 000 000 Ft")
    assert (r["amount_huf"], r["period"], r["period_stated"]) == (6_000_000, "year", True)
    assert _ok("3000 Ft/óra")["period"] == "hour"
    assert _ok("alkalmanként 50 000 Ft")["period"] == "occasion"
    assert _ok("havi 450 000 Ft")["period_stated"] is True


def test_a_count_beside_the_amount_is_not_money():
    assert _ok("12 havi átlag: 450 000 Ft")["amount_huf"] == 450_000
    assert _ok("50%-os üzletrész után 120 000 Ft")["amount_huf"] == 120_000


def test_approximate_and_ranges():
    assert _ok("kb. 1,5 millió Ft")["approx"] is True
    assert _ok("1 500 000 Ft")["approx"] is False
    r = _ok("1-2 millió Ft")
    assert (r["amount_huf"], r["amount_huf_max"]) == (1_000_000, 2_000_000)
    r = _ok("100 000 Ft-tól 200 000 Ft-ig")
    assert (r["amount_huf"], r["amount_huf_max"]) == (100_000, 200_000)


def test_a_foreign_amount_stays_in_its_currency():
    r = _ok("1 500 PLN")
    assert (r["amount"], r["currency"], r["amount_huf"]) == (1500, "PLN", None)
    assert _ok("EUR 2 000")["currency"] == "EUR"
    # ...unless the declarant gave the forints too, which are then theirs.
    r = _ok("211448,29 EUR (77083474 HUF)")
    assert (r["amount"], r["currency"], r["amount_huf"]) == (211448.29, "EUR", 77_083_474)


def test_one_income_stated_two_ways_leads_with_the_gross_or_the_month():
    r = _ok("bruttó 1 309 493 Ft, nettó 870 813 Ft")
    assert (r["amount_huf"], r["basis"]) == (1_309_493, "gross")
    assert r["alt"] == {"amount": 870_813, "currency": "HUF", "basis": "net", "period": None}
    r = _ok("havi 500 000 Ft (évi 6 000 000 Ft)")
    assert (r["amount_huf"], r["period"], r["alt"]["period"]) == (500_000, "month", "year")


def test_several_incomes_in_one_field_are_not_picked_from_or_added_up():
    r = parse_amount("osztalék 100 000 Ft, bérleti díj 200 000 Ft")
    assert r["status"] == "multiple" and r["amount"] is None and r["amount_huf"] is None
    assert [v["amount"] for v in r["values"]] == [100_000, 200_000]


@pytest.mark.parametrize("text, status", [
    ("nem részesülök díjazásban", "none"),
    ("-", "none"),
    ("nincs", "none"),
    ("változó", "unparsed"),
    ("lásd a PDF-et", "unparsed"),
])
def test_words_without_a_number(text, status):
    r = parse_amount(text)
    assert r["status"] == status and r["amount"] is None and r["raw"] == text


def test_an_empty_field_is_no_reading():
    assert parse_amount(None) is None and parse_amount("  ") is None


# --- a declaration's entries ---------------------------------------------------

def test_entries_are_grouped_by_list_current_first_unpaid_counted():
    content = {
        "jovedelemnyilatkozat.korabbiFoglalkozasok": [
            {"megnevezes": "legfőbb ügyész", "reszesultDijazasban": "igen",
             "haviJovedelem": "nettó 3.461.192 Ft"},
            {"megnevezes": "OKRI Tudományos Tanács tagja", "reszesultDijazasban": "nem"}],
        "jovedelemnyilatkozat.jelenlegiTevekenysegek": [
            {"megnevezes": "professor emeritus",
             "kifizetoSzemelye": "Nemzeti Közszolgálati Egyetem",
             "haviJovedelem": "nettó 255.000 Ft"}],
        "gazdasagiErdekeltseg.tagsagok": [
            {"szervezetNeve": "Acta Humana", "tagsag": "szerkesztőbizottsági tag",
             "reszesulDijazasban": "nem"}],
        "gazdasagiErdekeltseg.tarsasagiErdekeltsegek": [
            {"tarsasagNeve": "Példa Kft.", "erdekeltsegFormaja": "üzletrész",
             "tulajdoniArany": "50%", "reszesulDijazasban": "igen",
             "haviJovedelem": "kb. 120 000 Ft"}],
    }
    groups, unpaid = income_entries(content)
    assert [(g["path"], g["when"]) for g in groups] == [
        ("jovedelemnyilatkozat.jelenlegiTevekenysegek", "current"),
        ("gazdasagiErdekeltseg.tarsasagiErdekeltsegek", "current"),
        ("jovedelemnyilatkozat.korabbiFoglalkozasok", "previous"),
    ]
    assert unpaid == 2
    first = groups[0]["items"][0]
    assert first["payer"] == "Nemzeti Közszolgálati Egyetem"
    assert first["income"]["amount_huf"] == 255_000 and first["income"]["basis"] == "net"
    assert groups[1]["items"][0]["detail"] == "üzletrész · 50%"


def test_a_paid_entry_without_an_amount_is_kept_and_marked_as_such():
    groups, _ = income_entries({"gazdasagiErdekeltseg.tagsagok": [
        {"szervezetNeve": "Alapítvány", "tagsag": "kuratóriumi tag",
         "reszesulDijazasban": "igen"}]})
    item = groups[0]["items"][0]
    assert item["paid"] is True and item["income"] is None


def test_the_mandate_listed_as_an_activity_is_flagged_not_doubled():
    groups, _ = income_entries({"jovedelemnyilatkozat.jelenlegiTevekenysegek": [
        {"megnevezes": "országgyűlési képviselő", "haviJovedelem": "2 618 986 Ft"},
        {"megnevezes": "Ingatlan bérbeadás", "haviJovedelem": "417250"}]})
    assert [i["parliamentary"] for i in groups[0]["items"]] == [True, False]


def test_for_person_reads_the_newest_evnyr_declaration_only():
    older = {"source": "evnyr", "id": "a", "type": "Nyitó", "finalizedAt": "2026-06-02T10:00:00",
             "content": {"jovedelemnyilatkozat.jelenlegiTevekenysegek": [
                 {"megnevezes": "régi", "haviJovedelem": "1"}]}}
    newer = {"source": "evnyr", "id": "b", "type": "Éves", "finalizedAt": "2027-01-31T10:00:00",
             "content": {}}
    adatlap = {"title": "Vagyonnyilatkozat 2025", "url": "https://x/y.pdf"}
    d = for_person([adatlap, older, newer])
    # The newest says nothing about income, and that is what is reported.
    assert d["declaration"]["id"] == "b" and d["groups"] == [] and d["entries"] == 0
    assert for_person([adatlap]) is None and for_person([]) is None


# --- served on the profile ------------------------------------------------------

def test_profile_serves_the_declared_income_beside_the_published_fee(client):
    p = client.get("/api/v1/representatives/k001").json()
    assert p["remuneration"]["amount_huf"] == 2618986
    d = p["declared_income"]
    assert d["declaration"]["type"] == "Nyitó"
    assert d["declaration"]["url"].endswith("/nyilatkozat/1111aaaa-0000-4000-8000-000000000001")
    assert [g["path"] for g in d["groups"]] == [
        "jovedelemnyilatkozat.jelenlegiTevekenysegek",
        "jovedelemnyilatkozat.korabbiFoglalkozasok"]
    mandate, rent = d["groups"][0]["items"]
    assert mandate["parliamentary"] and mandate["income"]["basis"] == "gross"
    assert rent["income"]["amount_huf"] == 180_000 and rent["income"]["basis"] == "net"
    assert rent["income"]["raw"] == "nettó 180.000 Ft (2025-ben)"
    assert (d["unpaid"], d["entries"], d["parsed"]) == (1, 3, 3)
    # No EVNYR declaration, no block.
    assert client.get("/api/v1/representatives/n002").json()["declared_income"] is None
