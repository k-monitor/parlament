"""Offline tests for the Aktuális page + napirend stage (parlamonitor/aktualis).

Focus: the two things that decide whether this stage is worth having. The NR
parser has to read a **real** order paper — so the fixture below is a trimmed
but otherwise verbatim ``pdftotext -layout`` capture of one (the 2026-09-14
sitting), gutters, page furniture and all, rather than a tidy invention that
would only prove the parser agrees with itself. And the page scraper has to
survive parlament.hu's Liferay markup changing under it, so it keys on hrefs and
Hungarian labels, never on a CSS class.

Nothing here touches the network.
"""

from __future__ import annotations

from datetime import date

from parlamonitor.aktualis import nr, page, scrape
from parlamonitor.config import Paths
from parlamonitor.http_client import CappedFetch


# A real napirend, abridged: the cover, two sitting days of part A (numbered
# items, an unnumbered section heading, a time window, a "Megjegyzés" block),
# and one part B detail sheet.
NR_TEXT = """\
             AZ ORSZÁGGYŰLÉS
        2026. ÉVI ŐSZI ÜLÉSSZAKA
            SZEPTEMBER 14-15-I
               (HÉTFŐ-KEDD)
         ÜLÉSÉNEK NAPIRENDJE

  2026. szeptember 14. 15:45 órai állapot szerint
                      Elfogadott

        SZEPTEMBER 14. HÉTFŐ
   ülésnap kezdete:     13:00 óra
határozathozatalok:     legkorábban 14:45 órától

ülésnap befejezése:     kb. 22:00 óra, illetve a napirendi
                        pontok megtárgyalása
            szünet:     szükség szerint



         SZEPTEMBER 15. KEDD
   ülésnap kezdete:     9:00 óra
határozathozatalok:     legkorábban 9:40 órától
                        és
                        legkorábban 11:30 órától
                        és
                        legkorábban 12:00 órától

ülésnap befejezése:     a napirendi pontok
                        megtárgyalása
            szünet:     szükség szerint




                          1
                                     A)
                 A napirendi pontok tárgyalásának időrendje

                             SZEPTEMBER 14. HÉTFŐ
Ülésnap kezdete: 13:00 óra

Napirend előtt

Napirendi pontok tárgyalási sorrendje


1.      S/...         Bizottsági tisztségviselő(k) és tag(ok) választása
B./1.
                      Megjegyzés:
                      Jelöléstől függően!



2.                    Interpellációk
B./2.
                      Kb. 15:30- kb. 16:30 óráig

                      Megjegyzés:
                      Határozatképesség szükséges!



A határozathozatalokat követően


3.                    Azonnali kérdések és válaszok órája
B./3.
                      Kb. 16:30- kb. 17:30 óráig



A bizottsági jelentések és az összegző módosító javaslatok vitái

5.      T/438.       A szakképzésről szóló 2019. évi LXXX. törvény
B./5.                módosításáról
                     (Kormány - oktatási és gyermekügyi miniszter)
                     Bizottsági jelentések és az összegző módosító javaslat vitája
                     Megjegyzés:
                     A Törvényalkotási Bizottság az előterjesztést megtárgyalta, és
                     összegző jelentését benyújtotta.


6.      T/404.       Az elmúlt rendszer titkosszolgálati tevékenységének
B./6.                teljes körű feltárásáról és az Állambiztonsági
                     Szolgálatok Történeti Levéltáráról

                                SZEPTEMBER 15. KEDD
Ülésnap kezdete: 9:00 óra

Napirend előtt

Megemlékezés:

Demokrácia Nemzetközi Napja
[81/2008. (IX. 18.) OGY határozat alapján]

Napirend előtti felszólalások


Döntések, határozathozatalok
Legkorábban: 9:40 órától határozatképesség szükséges!
A határozathozatalokat követően


Az összevont vita


7.       T/669.      Az üzemanyag-áremelkedésre tekintettel nyújtott
B./11.               támogatásokkal     összefüggő intézkedésekről és
                     kapcsolódó törvények módosításáról
                     (Kormány - pénzügyminiszter)
                     Összevont vita
                     Megjegyzés:
                     Kivételes eljárásban!



Döntések, határozathozatalok
Legkorábban: 11:30 órától határozatképesség szükséges!


8.       T/669.      Az üzemanyag-áremelkedésre tekintettel nyújtott
B./11.               támogatásokkal      összefüggő       intézkedésekről      és

                                         15
                             B)
              A napirendi pontok részletes adatai

1.   S/...    Bizottsági tisztségviselő(k) és tag(ok) választása




5.   T/438.   A szakképzésről szóló 2019. évi LXXX. törvény
              módosításáról
              (Kormány - oktatási és gyermekügyi miniszter)
              A bizottsági jelentések és az összegző módosító javaslat vitája,
              döntés az összegző módosító javaslatról és a zárószavazás
              Benyújtva: 2026.07.28.
              Bizottság kijelölése, kijelölt bizottság:
              2026.07.29. Oktatási Bizottság
              Vitához kapcsolódó bizottság: -
              Képviselői módosító javaslatok benyújtási határideje:
              2026.09.01.16:00
              Képviselői módosító javaslatok (db): 2
              Bizottsági részletesvita-szakasz megnyílása: 2026.08.31.
              A részletes vitát lezáró bizottsági módosító javaslat:
              T/438/5
              A részletes vitáról szóló bizottsági jelentés:
              T/438/6
              A részletes vita lezárása: 2026.09.02.

              Előterjesztői nyilatkozat: T/438/7.
              Összegző módosító javaslat: T/438/8.
              Összegző jelentés: T/438/9.
              Egységes javaslat: T/438/11.

"""

# The Aktuális page, reduced to the markup that matters: the site chrome above
# the content (which links documents of its own), the two headings, the
# documents, the House Committee block, and a footer that repeats the chrome.
PAGE_HTML = """
<html><body>
<nav><a href="/documents/d/guest/szmsz_01-1">Szervezeti felépítés</a></nav>
<div class="title-bar">Plenáris üléshez kapcsolódó információk</div>
<a href="/documents/d/guest/nr_20260914_elfogadott">Napirend</a>
<a href="/documents/d/guest/ut_20260914_elfogadott">Ülésterv</a>
<a href="/documents/d/guest/tajek_benyhatido_20260914">Benyújtási határidők</a>
<div class="title-bar">Aktuális ülésszak</div>
<a href="/documents/d/guest/torvenyalkotasi-program_2026-osz">Törvényalkotási program</a>
<div class="title-bar">A Házbizottság soron következő ülése</div>
<div class="years">
  <a data-year="2025" href="#">Helyszíne: Országház, Pázmándy Dénes terem (főemelet 11.)</a>
  <a data-year="2024" href="#">Időpontja: 2026. szeptember 17. (csütörtök) <u><strong>13:00 óra</strong></u></a>
</div>
<footer><a href="/documents/d/guest/szmsz_01-1">Szervezeti felépítés</a></footer>
</body></html>
"""

PDF = b"%PDF-1.7\nfake\n"


class FakeHttp:
    """Stands in for HttpClient: serves canned bodies and counts requests."""

    def __init__(self, page_html=PAGE_HTML, pdf=PDF, status=200):
        self.page_html = page_html
        self.pdf = pdf
        self.status = status
        self.calls: list[str] = []

    def get_text(self, url, **kw):
        self.calls.append(url)
        return self.page_html

    def get_capped(self, url, *, max_bytes=0, headers=None):
        self.calls.append(url)
        if self.pdf is None:
            return CappedFetch(data=None, content_type="", status=self.status,
                               over_cap=False)
        return CappedFetch(data=self.pdf, content_type="application/pdf",
                           status=200, over_cap=False)

    def polite_sleep(self):
        pass


def _items(doc):
    return [i for d in doc["days"] for i in d["items"]]


# --- the NR parser ---------------------------------------------------------

def test_header_carries_the_sitting_and_the_moment_it_was_issued():
    doc = nr.parse(NR_TEXT, reference=date(2026, 9, 14))
    assert doc["term"] == {"year": 2026, "season": "autumn"}
    assert doc["extraordinary"] is False
    # An order paper is a plan; the stamp is what lets the site say as of when.
    assert doc["statusLabel"] == "Elfogadott"
    assert doc["statusAt"] == "2026-09-14T15:45"


def test_days_get_a_full_date_and_their_timetable():
    doc = nr.parse(NR_TEXT, reference=date(2026, 9, 14))
    assert [d["date"] for d in doc["days"]] == ["2026-09-14", "2026-09-15"]
    monday = doc["days"][0]
    assert monday["weekday"] == "HÉTFŐ"
    assert monday["startsAt"] == "13:00"
    # The cover lists several "legkorábban" times for one day; all of them count.
    assert doc["days"][1]["decisionsFrom"] == ["09:40", "11:30", "12:00"]
    # A wrapped value on the cover is one value, not a truncated one.
    assert monday["endsNote"].endswith("pontok megtárgyalása")


def test_the_year_comes_from_the_reference_date():
    """The listing prints a month and a day and never a year."""
    doc = nr.parse(NR_TEXT, reference=None)
    # Without a reference the stamp still supplies one.
    assert doc["days"][0]["date"] == "2026-09-14"


def test_an_item_separates_its_gutter_from_its_text():
    doc = nr.parse(NR_TEXT, reference=date(2026, 9, 14))
    item = next(i for i in _items(doc) if i["billCode"] == "T/438")
    assert item["ordinal"] == 5
    assert item["ref"] == "5"
    # The B./5. that wraps into the title column is gutter, not title text.
    assert item["title"] == ("A szakképzésről szóló 2019. évi LXXX. törvény "
                             "módosításáról")
    assert item["submitter"] == "Kormány - oktatási és gyermekügyi miniszter"
    assert item["stage"] == ("Bizottsági jelentések és az összegző módosító "
                            "javaslat vitája")


def test_ordinal_and_ref_are_different_numbers():
    """`ordinal` is the item's place in the day; `ref` identifies it across the
    whole sitting, and the two diverge as soon as a day is not the first."""
    doc = nr.parse(NR_TEXT, reference=date(2026, 9, 14))
    item = next(i for i in _items(doc) if i["billCode"] == "T/669")
    assert (item["ordinal"], item["ref"]) == (7, "11")


def test_the_house_placeholder_number_is_not_a_bill_code():
    doc = nr.parse(NR_TEXT, reference=date(2026, 9, 14))
    item = next(i for i in _items(doc) if i["ordinal"] == 1)
    assert item["billCode"] is None          # the source prints "S/..."
    assert item["title"] == "Bizottsági tisztségviselő(k) és tag(ok) választása"


def test_notes_time_window_and_flags_are_read_off_the_item():
    doc = nr.parse(NR_TEXT, reference=date(2026, 9, 14))
    interp = next(i for i in _items(doc) if i["title"] == "Interpellációk")
    assert interp["timeWindow"] == "Kb. 15:30- kb. 16:30 óráig"
    assert interp["notes"] == ["Határozatképesség szükséges!"]
    assert interp["flags"] == ["quorum"]
    combined = next(i for i in _items(doc) if i["billCode"] == "T/669")
    assert combined["flags"] == ["exceptional"]


def test_items_keep_the_heading_they_sit_under():
    doc = nr.parse(NR_TEXT, reference=date(2026, 9, 14))
    item = next(i for i in _items(doc) if i["billCode"] == "T/438")
    assert item["section"] == ("A bizottsági jelentések és az összegző módosító "
                              "javaslatok vitái")


def test_part_b_detail_is_joined_on_the_b_reference():
    doc = nr.parse(NR_TEXT, reference=date(2026, 9, 14))
    item = next(i for i in _items(doc) if i["billCode"] == "T/438")
    assert item["detail"]["submittedOn"] == "2026.07.28."
    assert item["detail"]["committee"].endswith("Oktatási Bizottság")
    assert item["detail"]["amendmentCount"] == "2"


def test_a_day_heading_closes_the_item_above_it():
    """A block must not swallow the next sitting day's heading."""
    doc = nr.parse(NR_TEXT, reference=date(2026, 9, 14))
    item = next(i for i in _items(doc) if i["billCode"] == "T/404")
    assert "SZEPTEMBER" not in (item["title"] or "")
    assert doc["days"][1]["items"], "the second day still parsed its own items"


def test_a_document_with_no_numbered_items_parses_to_its_days():
    """A purely ceremonial sitting — a government being sworn in — has an order
    paper with no numbered item on it. That is the truth about that sitting,
    not a parse failure (SCR-5)."""
    ceremonial = """
             AZ ORSZÁGGYŰLÉS
      2026. ÉVI TAVASZI ÜLÉSSZAKA
                   MÁJUS 12-i
          ÜLÉSÉNEK NAPIRENDJE

     2026. május 12. 16:05 órai állapot szerint
                  Elfogadott változat

              MÁJUS 12. KEDD
        üléskezdés:     16:00 óra
határozathozatalok:     16:00 órától
            szünet:     szükség szerint
                                 MÁJUS 12. KEDD
Himnusz

Napirend előtt
"""
    doc = nr.parse(ceremonial, reference=date(2026, 5, 12))
    # One day, seen twice (cover + listing) and merged, not two.
    assert len(doc["days"]) == 1
    assert doc["days"][0]["date"] == "2026-05-12"
    assert doc["itemCount"] == 0
    assert doc["statusLabel"] == "Elfogadott változat"


def test_strip_gutter_leaves_a_title_that_opens_with_a_number_alone():
    """The gutter is a column, so a marker only counts when the layout's own
    column gap follows it."""
    assert nr.strip_gutter("      2026. évi költségvetés") == (
        "2026. évi költségvetés", None, None)
    assert nr.strip_gutter("5.      T/438.       A szakképzésről") == (
        "A szakképzésről", None, "T/438")


# --- the portal page -------------------------------------------------------

def test_documents_are_found_by_href_and_the_chrome_is_left_out():
    parsed = page.parse(PAGE_HTML)
    slugs = [d["slug"] for d in parsed["documents"]]
    assert slugs == ["nr_20260914_elfogadott", "ut_20260914_elfogadott",
                     "tajek_benyhatido_20260914", "torvenyalkotasi-program_2026-osz"]
    # The template links the same document above the content and again in the
    # footer; neither belongs to this page.
    assert "szmsz_01-1" not in slugs


def test_a_documents_kind_and_sitting_come_from_its_slug():
    docs = {d["slug"]: d for d in page.parse(PAGE_HTML)["documents"]}
    napirend = docs["nr_20260914_elfogadott"]
    assert napirend["kind"] == "agenda"
    assert napirend["date"] == "2026-09-14"
    assert napirend["label"] == "Napirend"
    assert napirend["group"] == "Plenáris üléshez kapcsolódó információk"
    assert docs["ut_20260914_elfogadott"]["kind"] == "sitting_plan"
    assert docs["torvenyalkotasi-program_2026-osz"]["kind"] == "legislative_programme"
    assert docs["torvenyalkotasi-program_2026-osz"]["date"] is None


def test_the_house_committee_meeting_is_read_off_its_labels():
    hc = page.parse(PAGE_HTML)["houseCommittee"]
    assert hc["place"] == "Országház, Pázmándy Dénes terem (főemelet 11.)"
    assert hc["date"] == "2026-09-17"
    assert hc["time"] == "13:00"
    assert hc["weekday"] == "csütörtök"


def test_a_page_with_nothing_on_it_parses_to_empty_rather_than_raising():
    parsed = page.parse("<html><body><p>Karbantartás</p></body></html>")
    assert parsed == {"documents": [], "houseCommittee": None}


# --- the stage -------------------------------------------------------------

def test_a_run_writes_the_page_and_the_parsed_agenda(tmp_path, monkeypatch):
    monkeypatch.setattr(scrape, "extract_layout_text", lambda pdf, **kw: NR_TEXT)
    monkeypatch.setattr(scrape, "pdftotext_available", lambda: True)
    paths = Paths(tmp_path)
    paths.ensure()
    http = FakeHttp()

    registry = scrape.fetch_aktualis(http)
    scrape.save_aktualis(paths, registry)

    assert paths.aktualis_file().exists()
    assert registry["meta"]["agendaSlug"] == "nr_20260914_elfogadott"
    assert registry["meta"]["agendaError"] is None
    assert registry["meta"]["itemCount"] == registry["data"]["agenda"]["itemCount"]
    assert registry["data"]["houseCommittee"]["date"] == "2026-09-17"


def test_an_unchanged_napirend_is_not_downloaded_again(tmp_path, monkeypatch):
    """The slug names the sitting, and the House issues a new slug rather than
    editing one in place — so an idle poll costs one HTML request (SCR-2)."""
    monkeypatch.setattr(scrape, "extract_layout_text", lambda pdf, **kw: NR_TEXT)
    monkeypatch.setattr(scrape, "pdftotext_available", lambda: True)
    paths = Paths(tmp_path)
    paths.ensure()

    http = FakeHttp()
    scrape.save_aktualis(paths, scrape.fetch_aktualis(http))
    first = list(http.calls)

    http2 = FakeHttp()
    second = scrape.fetch_aktualis(http2, previous=scrape.load_previous(paths))
    assert second["meta"]["agendaReused"] is True
    assert second["data"]["agenda"]["itemCount"] == 7
    assert len(first) == 2 and len(http2.calls) == 1   # page only, no PDF

    # --force pulls it again even so.
    http3 = FakeHttp()
    scrape.fetch_aktualis(http3, previous=scrape.load_previous(paths), force=True)
    assert len(http3.calls) == 2


def test_a_missing_pdftotext_still_writes_what_the_page_said(tmp_path, monkeypatch):
    """Extraction may depend on an external tool, whose absence degrades rather
    than fails the run (SCR-6)."""
    monkeypatch.setattr(scrape, "pdftotext_available", lambda: False)
    paths = Paths(tmp_path)
    paths.ensure()
    registry = scrape.fetch_aktualis(FakeHttp())
    scrape.save_aktualis(paths, registry)

    assert "pdftotext" in registry["meta"]["agendaError"]
    assert "days" not in registry["data"]["agenda"]
    # The page's own findings survive.
    assert len(registry["data"]["documents"]) == 4
    assert registry["data"]["houseCommittee"]["time"] == "13:00"


def test_a_document_that_404s_is_recorded_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(scrape, "pdftotext_available", lambda: True)
    registry = scrape.fetch_aktualis(FakeHttp(pdf=None, status=404))
    assert registry["meta"]["agendaError"] == "HTTP 404"
    assert registry["data"]["documents"]
