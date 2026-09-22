"""Offline tests for the committee jegyzőkönyv parser (BIZ-15).

The parser's whole job is to survive the variation in a corpus of 3 220 PDFs
written by different clerks over ten years, so the fixtures here are not one
tidy document: they are the shapes that actually broke it, each reduced to the
few lines that carry the difference. Every one of them was found by running the
parser over real sittings and reading what came out.
"""

from __future__ import annotations

import textwrap

from parlamonitor.committees import minutes


def doc(body: str) -> str:
    """A fixture as ``pdftotext -layout`` would emit it (indentation is data)."""
    return textwrap.dedent(body).lstrip("\n")


# The shape every sitting has: cover, contents, agenda, participants, body.
FULL = doc("""
                                          Ikt. sz.: TAB-43/34-2/2026.
                                               TAB-16/2026. sz. ülés
                                        (TAB-16/2026-2030. sz. ülés)

               Jegyzőkönyv

   az Országgyűlés Törvényalkotási Bizottságának
  2026. szeptember 3-án, csütörtökön, 14 óra 07 perckor
az Országház Apponyi Albert gróf termében (főemelet 58.)
                   megtartott üléséről
                                2

                       Tartalomjegyzék

Az ülés megnyitása, a napirend elfogadása                5
A megye elnevezéséről szóló T/405. számú törvényjavaslat
(A bizottság eljárása a HHSZ 46. §-a alapján)             5
  Határozathozatalok                                      6
                                      3

Napirendi javaslat

1.   A megye elnevezéséről szóló törvényjavaslat (T/405. szám)
     (Kormány - vidékfejlesztési miniszter)
     (A bizottság eljárása a HHSZ 46. §-a alapján)
2.   Egyebek
                                       4

Az ülés résztvevői
A bizottság részéről
  Megjelent
  Elnököl: Dr. Rák Richárd (TISZA), a bizottság elnöke

            Dr. Bilisics Zita (TISZA), a bizottság alelnöke
            Berki Ákos (TISZA)

  Helyettesítési megbízást adott
            Dr. Simon Krisztián Márk (TISZA) dr. Bilisics Zitának (TISZA)

A bizottság titkársága részéről
            Dr. Pór-Zselinszky Eszter

Meghívott
  Hozzászóló
            Dr. Stumpf Péter       államtitkár   (Vidékfejlesztési
            Minisztérium)
                                           5

                    (Az ülés kezdetének időpontja: 14 óra 07 perc)

      Az ülés megnyitása, a napirend elfogadása
       DR. RÁK RICHÁRD (TISZA), a bizottság elnöke, a továbbiakban ELNÖK: Jó
napot kívánok! Köszöntöm a bizottság tagjait.
       Ismertetem a helyettesítések rendjét. Megállapítom, hogy a bizottság
határozatképes.
      A megye elnevezéséről szóló T/405. számú törvényjavaslat
      (A bizottság eljárása a HHSZ 46. §-a alapján)
        Soron következik 1. napirendi pontunk.
       DR. STUMPF PÉTER államtitkár (Vidékfejlesztési Minisztérium): Köszönöm
a szót, elnök úr.
       ELNÖK: Köszönöm. Berekesztem az ülést.

                    (Az ülés befejezésének időpontja: 14 óra 41 perc)

                                                            Dr. Rák Richárd
                                                             a bizottság elnöke
Jegyzőkönyvvezető: Szűcs Dóra
""")


def test_cover_reads_every_fact_it_states():
    c = minutes.parse(FULL)["cover"]
    assert c["registryNumber"] == "TAB-43/34-2/2026."
    assert (c["meetingLabel"], c["termLabel"]) == ("TAB-16/2026",
                                                   "TAB-16/2026-2030")
    # Kept in the genitive on purpose: "bizottság" and "bizottsága" collapse to
    # one genitive, so un-inflecting it would be a guess.
    assert c["committeeLabel"] == "Törvényalkotási Bizottságának"
    assert (c["date"], c["weekday"], c["startsAt"]) == (
        "2026-09-03", "csütörtök", "14:07")
    assert c["venue"] == "az Országház Apponyi Albert gróf termében (főemelet 58.)"
    assert c["closed"] is False
    assert (c["openedAt"], c["closedAt"]) == ("14:07", "14:41")


def test_agenda_carries_its_bill_number_and_notes():
    items = minutes.parse(FULL)["agenda"]
    assert [i["ordinal"] for i in items] == [1, 2]
    assert items[0]["billNumber"] == "T/405"
    assert items[0]["notes"] == ["Kormány - vidékfejlesztési miniszter",
                                 "A bizottság eljárása a HHSZ 46. §-a alapján"]
    assert items[1]["title"] == "Egyebek"


def test_participants_split_by_the_document_s_own_labels():
    p = minutes.parse(FULL)["participants"]
    assert [c["name"] for c in p["chairs"]] == ["Dr. Rák Richárd"]
    # The chair is present too, and counted once.
    assert [m["name"] for m in p["present"]] == [
        "Dr. Rák Richárd", "Dr. Bilisics Zita", "Berki Ákos"]
    assert [s["name"] for s in p["staff"]] == ["Dr. Pór-Zselinszky Eszter"]
    # A guest's office and the body they hold it in, split — and the line wraps.
    assert p["guests"] == [{"name": "Dr. Stumpf Péter", "faction": None,
                            "title": "államtitkár",
                            "org": "Vidékfejlesztési Minisztérium"}]


def test_proxy_holder_is_read_back_out_of_the_dative():
    proxies = minutes.parse(FULL)["participants"]["proxies"]
    assert proxies[0]["absent"]["name"] == "Dr. Simon Krisztián Márk"
    # Written "dr. Bilisics Zitának"; nothing in the register is in the dative.
    assert proxies[0]["heldBy"]["name"] == "dr. Bilisics Zita"


def test_speeches_are_split_by_speaker_and_keep_their_section():
    out = minutes.parse(FULL)
    speeches = out["speeches"]
    assert [s["name"] for s in speeches] == [
        "Dr. Rák Richárd", "Dr. Rák Richárd", "Dr. Stumpf Péter",
        "Dr. Rák Richárd"]
    # The chair introduces themselves once; every later bare "ELNÖK:" is them.
    assert speeches[-1]["chair"] is True
    assert speeches[-1]["name"] == "Dr. Rák Richárd"
    # "a továbbiakban ELNÖK" is stripped off the role it was printed in.
    assert speeches[0]["role"] == "a bizottság elnöke"
    assert speeches[0]["faction"] == "TISZA"
    # A guest's organisation is the trailing bracket, not a faction.
    assert (speeches[2]["faction"], speeches[2]["org"]) == (
        None, "Vidékfejlesztési Minisztérium")
    # The chair carried on past the agenda heading without being named again.
    assert speeches[1]["continued"] is True
    assert speeches[1]["section"] == 1
    assert [s["title"] for s in out["sections"]] == [
        "Az ülés megnyitása, a napirend elfogadása",
        "A megye elnevezéséről szóló T/405. számú törvényjavaslat "
        "(A bizottság eljárása a HHSZ 46. §-a alapján)"]


def test_the_closing_furniture_is_not_part_of_the_last_speech():
    last = minutes.parse(FULL)["speeches"][-1]["text"]
    assert last == "Köszönöm. Berekesztem az ülést."
    assert "Jegyzőkönyvvezető" not in last


def test_paragraphs_come_from_the_indent_not_from_blank_lines():
    # The minutes carry no blank lines inside a speech; the indent is the break.
    text = minutes.parse(FULL)["speeches"][0]["text"]
    assert text.split("\n\n") == [
        "Jó napot kívánok! Köszöntöm a bizottság tagjait.",
        "Ismertetem a helyettesítések rendjét. Megállapítom, hogy a bizottság "
        "határozatképes."]


# --- the variants that broke it --------------------------------------------

def test_a_single_agenda_point_carries_no_number():
    """34 of cycle 43's 130 sittings: the House drops the "1." when there is
    only one point, and requiring an ordinal read every one as having none."""
    out = minutes.parse(doc("""
        Napirendi javaslat

            Az általános forgalmi adóról szóló törvényjavaslat (T/504. szám)
            (Kormány - pénzügyminiszter)
            (Részletes vita)
                                        4
        Az ülés résztvevői
        """))
    assert len(out["agenda"]) == 1
    assert out["agenda"][0]["ordinal"] is None
    assert out["agenda"][0]["billNumber"] == "T/504"
    assert out["agenda"][0]["notes"] == ["Kormány - pénzügyminiszter",
                                         "Részletes vita"]


def test_a_remark_after_a_closed_note_is_its_own_note():
    out = minutes.parse(doc("""
        Napirendi javaslat

          Magyarország Alaptörvényének módosítása (T/324. szám)
          (Sürgős eljárás keretében)
          Nemzetiségi napirendi pont!
                                     4
        Az ülés résztvevői
        """))
    assert out["agenda"][0]["notes"] == ["Sürgős eljárás keretében",
                                         "Nemzetiségi napirendi pont!"]


def test_cover_variants_that_carry_no_weekday_or_a_dotted_time():
    def cover(line):
        return minutes._parse_cover([(0, "Jegyzőkönyv"), (0, line)], 2)
    # No weekday at all (9 of cycle 43's 130).
    c = cover("2026. szeptember 7-én, 10.00 órakor")
    assert (c["date"], c["startsAt"]) == ("2026-09-07", "10:00")
    assert "weekday" not in c
    # The first of the month takes "-jén", not "-én".
    assert cover("2026. július 1-jén, szerdán, 10 óra 02 perckor")["date"] == \
        "2026-07-01"
    # No minute at all.
    assert cover("2019. július 3-án, szerdán 12 órakor")["startsAt"] == "12:00"
    # A sitting held away from the House: "órára … összehívott", not "órakor".
    assert cover("2026. június 18-án, csütörtökön, 11 órára")["startsAt"] == "11:00"


def test_a_partly_closed_sitting_is_flagged():
    c = minutes._parse_cover([
        (0, "Jegyzőkönyv"),
        (0, "az Országgyűlés Mentelmi Bizottságának"),
        (0, "2024. szeptember 30-án, hétfőn, 12 óra 45 perckor"),
        (0, "az Országház Vázsonyi Vilmos termében (félemelet 15.)"),
        (0, "megtartott részben zárt ülésének nyílt napirendi pontjáról")], 5)
    assert c["closed"] is True
    assert c["venue"] == "az Országház Vázsonyi Vilmos termében (félemelet 15.)"


def test_a_vizsgalobizottsag_names_itself_on_the_next_line():
    c = minutes._parse_cover([
        (0, "Jegyzőkönyv"),
        (23, "az Országgyűlés"),
        (0, "A Végrehajtási Visszaéléseket Feltáró Vizsgálóbizottságának"),
        (7, "2026. szeptember 9-én, szerdán, 13 óra 06 perckor"),
        (8, "az Országház Nagy Imre termében (főemelet 61.)"),
        (22, "megtartott üléséről")], 6)
    assert c["committeeLabel"] == \
        "A Végrehajtási Visszaéléseket Feltáró Vizsgálóbizottságának"


def test_a_wrapped_speaker_line_is_still_one_speaker():
    """A long office with an organisation after it does not fit on one line, so
    the colon falls to the next. Unjoined, the speech is credited to nobody and
    the name is swallowed into the previous speaker's text."""
    out = minutes.parse(doc("""
        (Az ülés kezdetének időpontja: 10 óra)

               ELNÖK: Öné a szó.
               ARANYOSNÉ DR. BÖRCS JANKA főigazgató (Nemzeti Média- és Hírközlési
        Hatóság): Köszönöm szépen. Tisztelt Elnök Úr!
        """))
    assert [s["name"] for s in out["speeches"]] == ["Elnök",
                                                    "Aranyosné Dr. Börcs Janka"]
    assert out["speeches"][1]["org"] == "Nemzeti Média- és Hírközlési Hatóság"
    assert out["speeches"][1]["text"] == "Köszönöm szépen. Tisztelt Elnök Úr!"


def test_capitals_alone_do_not_make_a_speaker():
    """A sentence can open on an acronym; a speaker is two whole capitalised
    words (or the chair's own bare "ELNÖK:")."""
    out = minutes.parse(doc("""
        (Az ülés kezdetének időpontja: 10 óra)

               ELNÖK: Egy dolog van hátra.
        A NET-COACH-csal kapcsolatban: a pályázat lezárult.
        """))
    assert len(out["speeches"]) == 1
    assert "NET-COACH" in out["speeches"][0]["text"]


def test_a_document_with_nothing_in_it_parses_to_nothing():
    """SCR-5: a scan with no text layer is a finding, not an exception."""
    out = minutes.parse("")
    assert out["speeches"] == [] and out["agenda"] == []
    assert out["stats"]["speeches"] == 0
    assert minutes.parse("Csak egy sor, semmi más.")["stats"]["chars"] == 0


def test_a_wrapped_guest_line_is_one_guest_not_two():
    """A long office plus a long organisation does not fit on one line. Split,
    it is a guest whose office ends mid-name and a guest called
    "Minisztérium)" — 156 of cycle 43's guest rows were that shape."""
    out = minutes.parse(doc("""
        Az ülés résztvevői
        Meghívott
          Hozzászóló
                    Dr. Stumpf Péter       államtitkár   (Vidék-   és   Településfejlesztési
                    Minisztérium)
        (Az ülés kezdetének időpontja: 10 óra)
        """))
    assert out["participants"]["guests"] == [
        {"name": "Dr. Stumpf Péter", "faction": None, "title": "államtitkár",
         "org": "Vidék- és Településfejlesztési Minisztérium"}]


def test_an_mp_appearing_as_a_guest_keeps_their_faction():
    """A guest is not always an outsider. The bracket after an MP's name is
    their faction, not the body they represent."""
    out = minutes.parse(doc("""
        Az ülés résztvevői
        Meghívott
          Hozzászóló
                    Dr. Latorcai Csaba (KDNP) országgyűlési képviselő, előterjesztő
                    Polgár György (TISZA)
        (Az ülés kezdetének időpontja: 10 óra)
        """))
    assert out["participants"]["guests"] == [
        {"name": "Dr. Latorcai Csaba", "faction": "KDNP",
         "title": "országgyűlési képviselő, előterjesztő", "org": None},
        {"name": "Polgár György", "faction": "TISZA", "title": None,
         "org": None}]


def test_a_subcommittee_is_not_labelled_with_its_parent():
    """A subcommittee's cover names its parent first and itself second, and only
    the second line is in the genitive. Taking the first labels every
    subcommittee's minutes with its parent — the same trap the registry's
    two-level listing sets (BIZ-1)."""
    c = minutes._parse_cover([
        (0, "Jegyzőkönyv"),
        (0, "az Országgyűlés Gazdasági Bizottsága"),
        (0, "Fogyasztóvédelmi Albizottságának"),
        (0, "2025. június 10-én, kedden, 8 óra 30 percre"),
        (0, "az Országház Tisza Kálmán termébe (főemelet 37.)"),
        (0, "összehívott üléséről")], 6)
    assert c["committeeLabel"] == "Fogyasztóvédelmi Albizottságának"
    assert (c["date"], c["startsAt"]) == ("2025-06-10", "08:30")


def test_the_weekday_may_be_bracketed_or_carry_a_stray_space():
    def cover(line):
        return minutes._parse_cover([(0, "Jegyzőkönyv"), (0, line)], 2)
    assert cover("2023. november 13-án (hétfőn) 9 óra 34 perckor")["weekday"] == \
        "hétfő"
    assert cover("2023. október 3-án, kedden , 10 óra 31 perckor")["date"] == \
        "2023-10-03"
    # A sitting only *called* for a time reads "percre", not "perckor".
    assert cover("2023. szeptember 25-én, hétfőn, 11 óra 30 percre")["startsAt"] \
        == "11:30"


def test_a_start_time_given_as_from_rather_than_at():
    """"10 óra 06 perctől kezdődően" — the Nemzetbiztonsági bizottság's own
    wording, and the only thing standing between its sittings and a date."""
    c = minutes._parse_cover(
        [(0, "Jegyzőkönyv"),
         (0, "2018. október 9-én, kedden 10 óra 06 perctől kezdődően")], 2)
    assert (c["date"], c["startsAt"]) == ("2018-10-09", "10:06")


def test_the_venue_may_share_its_line_with_the_megtartott_phrase():
    """Anchoring that phrase to the start of a line lost the venue, the
    closedness, and — because the body then began in the wrong place — the whole
    debate."""
    c = minutes._parse_cover([
        (0, "Jegyzőkönyv"),
        (0, "az Országgyűlés Nemzeti összetartozás bizottságának"),
        (0, "2019. október 16-án, szerdán, 13 óra 29 perckor"),
        (0, "az Országház Esterházy János tanácstermében (földszint 1.) "
            "megtartott ülésének"),
        (0, "2. napirendi pontjáról")], 5)
    assert c["venue"] == "az Országház Esterházy János tanácstermében (földszint 1.)"
    assert c["heldLabel"] == "ülésének"
    assert c["date"] == "2019-10-16"


def test_a_sitting_that_names_no_speaker_keeps_its_text():
    """A few sittings in the corpus print no speaker names at all — the clerk
    ran the chair's words straight under each heading. There is nothing to
    attribute, so the text is kept as the section's preamble rather than
    credited to a guess or thrown away."""
    out = minutes.parse(doc("""
        Tartalomjegyzék

        Határozathozatal                                             5

        (Az ülés kezdetének időpontja: 14 óra 6 perc)

              Határozathozatal
              Van-e hozzászólás, kérdés? (Nincs jelentkező.) Amennyiben nincs, úgy
        kérdezem a bizottságot, hogy ki az, aki elfogadja ezt a javaslatot.
        """))
    assert out["speeches"] == []
    assert [s["title"] for s in out["sections"]] == ["Határozathozatal"]
    assert "Van-e hozzászólás" in out["sections"][0]["preamble"]


def test_unattributed_text_reaches_the_section_it_stood_under():
    """Each section's unattributed text belongs to *that* section. Clearing the
    buffer when the next heading opened instead kept only the last one's, which
    on a sitting that names no speakers at all threw away the whole document
    bar its final paragraph."""
    out = minutes.parse(doc("""
        Tartalomjegyzék

        Első pont                                                    5
        Második pont                                                 6
        Az ülés berekesztése                                         7

        (Az ülés kezdetének időpontja: 14 óra)

              Első pont
              Az első napirendi pont szövege.
              Második pont
              A második napirendi pont szövege.
              Az ülés berekesztése
              A bizottsági ülést bezárom.
        """))
    assert [(s["title"], s["preamble"]) for s in out["sections"]] == [
        ("Első pont", "Az első napirendi pont szövege."),
        ("Második pont", "A második napirendi pont szövege."),
        ("Az ülés berekesztése", "A bizottsági ülést bezárom."),
    ]


def test_a_wrapped_line_opening_on_a_number_is_not_an_agenda_point():
    """"…a HHSZ 92. § (4) bekezdése alapján)" reads exactly like "92." opening
    an item. Two guards catch it: the numbering has to run, and what follows the
    number must not be a word that only ever continues a sentence."""
    out = minutes.parse(doc("""
        Napirendi javaslat

        1.   Magyarország 2018. évi központi költségvetéséről szóló törvényjavaslat
             (T/7556. szám) (Döntés a részletes vitában megtárgyalandó szerkezeti
             egységekről a HHSZ
        92. § (4) bekezdése alapján)
             (Vitához kapcsolódó bizottság)
        2.   Egyebek
                                       4
        Az ülés résztvevői
        """))
    assert [a["ordinal"] for a in out["agenda"]] == [1, 2]
    assert out["agenda"][0]["billNumber"] == "T/7556"
    assert "92. § (4) bekezdése alapján" in out["agenda"][0]["title"]


def test_the_clerks_own_typos_still_yield_a_date():
    """The last thing between six of cycle 40's sittings and a date: an en dash
    for the hyphen, a stray full stop before the weekday or inside the time."""
    def cover(line):
        return minutes._parse_cover([(0, "Jegyzőkönyv"), (0, line)], 2)
    assert cover("2015. november 24-én, kedden 13 óra 08.perckor")["startsAt"] \
        == "13:08"
    assert cover("2015. április 27–én, hétfőn, 9 óra 35 perckor")["date"] \
        == "2015-04-27"
    assert cover("2014. november 5-én, .szerdán 10 óra 31 perckor")["weekday"] \
        == "szerda"


def test_a_dashed_page_number_is_page_furniture_too():
    """Most page numbers are bare ("4"), a minority dashed ("- 4 -"). Left in,
    the dashed ones glue onto whatever text straddles the break — for an agenda,
    its last item's title."""
    out = minutes.parse(doc("""
        Napirendi javaslat

        1.   Az első javaslat (T/104. szám)
        2.   Egyebek
        -4-
        Az ülés résztvevői
        """))
    assert [a["title"] for a in out["agenda"]] == [
        "Az első javaslat (T/104. szám)", "Egyebek"]
