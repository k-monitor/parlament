"""Offline tests for the 1994-98 static iromány archive (`parlamonitor.bills.legacy`).

The fixtures are verbatim excerpts of the real pages, kept as **bytes** in the
encoding the archive is served in, so every test runs through the decoding step
that the rest of the module depends on. Between them they cover the two listing
layouts, an adatlap with each labelled line the archive uses, the multi-submitter
form, the motions and speakers listings, and the request budget / caching of a
whole run.
"""

from __future__ import annotations

import json

from parlamonitor.bills import legacy


def page(text: str) -> bytes:
    """A fixture as the archive serves it: ISO-8859-2, CRLF line ends."""
    return text.replace("\n", "\r\n").encode("iso-8859-2")


# --- fixtures --------------------------------------------------------------

# tvossz.htm: four ---separated fields, the title wrapped in <EM>.
BILLS_LIST = page(
    "<html><BODY BGCOLOR=\"#ffffff\">\n<H2> T&ouml;rv&eacute;nyjavaslatok </H2>\n"
    "<p> K&eacute;sz&uuml;lt: 1998.04.03.\n"
    "<p>  <a href='04754ir.htm'>T/4754</a>     ---  1997.08.07.  ---  "
    "t&ouml;rv&eacute;nyjavaslat       ---  <EM>\n"
    "A g&eacute;ntechnol&oacute;gi&aacute;val m&oacute;dos&iacute;tott "
    "szervezetekr&otilde;l.\n                              </EM>\n"
    "<p>  <a href='03365ir.htm'>T/3365</a>     ---  1996.10.31.  ---  "
    "t&ouml;rv&eacute;nyjavaslat       ---  <EM>\n"
    "A szem&eacute;lyi j&ouml;vedelemad&oacute;r&oacute;l\n     </EM>\n</html>")

# intossz.htm: the same fields, one per line, and no <EM>. The § is a raw
# high byte rather than an entity — the one place the archive uses one.
QUESTIONS_LIST = page(
    "<html><BODY>\n<H2> Interpell&aacute;ci&oacute;k </H2>\n"
    "<p>  <a href='05646ir.htm'>K/5646</a>     ---\n"
    "1998.03.30.  ---\nk&eacute;rd&eacute;s                ---\n"
    "Abony város laboratóriumának finanszírozása a 27.\xa7 alapján\n"
    "<p>  <a href='01632ir.htm'>I/1632</a>     ---\n"
    "1995.11.08.  ---\ninterpell&aacute;ci&oacute;       ---\n"
    "A k&aacute;rp&oacute;tl&aacute;si jegy&eacute;rt v&aacute;s&aacute;rolhat&oacute; "
    "f&ouml;ldter&uuml;letek\n</html>")

# 04754ir.htm — every labelled line the archive uses, one committee block.
BILL_ADATLAP = page(
    "<html><BODY>\n<name=\"Eleje\">\n"
    "<TITLE> T/4754. sz&aacute;m&uacute; irom&aacute;ny </TITLE>\n"
    "<H2> A g&eacute;ntechnol&oacute;gi&aacute;val m&oacute;dos&iacute;tott "
    "szervezetekr&otilde;l. </H2><p><UL>\n"
    "<LI> <a href='fulltext/04754txt.htm'>Teljes sz&ouml;veg</a>\n"
    "<LI> <a href='mod/04754imo.htm'>M&oacute;dos&iacute;t&oacute;k (2 db)</a>\n"
    "<LI> Szavaz&aacute;s --- <a href='../szavaz/szavlist/83gb5132.htm'>"
    "98.03.16.11:51:32</a>\n"
    "<LI> <a href='felsz/04754npl.htm'>Felsz&oacute;lal&oacute;k</a>\n"
    "</UL><p>\nT/4754 -- t&ouml;rv&eacute;nyjavaslat<p>\n"
    "Beny&uacute;jt&oacute; (1997.08.07.): f&ouml;ldm&ucirc;vel&eacute;s&uuml;gyi "
    "miniszter<p>\nElj&aacute;r&aacute;s: norm&aacute;l<p>\n"
    "Plen&aacute;ris &aacute;llapot (1998.04.01.): kihirdetve --- XXVII.tv, "
    "Magyar K&ouml;zl&ouml;ny 28.sz&aacute;m<p>\n"
    "Bizotts&aacute;gi &aacute;llapot: bizotts&aacute;gi t&aacute;rgyal&aacute;s "
    "befejezve<p>\n"
    "<EM> ==================== ESEM&Eacute;NYEK ==================== <br>\n"
    "1997.09.09.  ---  eln&ouml;k bejelenti az ind&iacute;tv&aacute;nyt<br>\n"
    "1998.03.16.  ---  Ogy. elfogadja<br>\n<p>\n"
    "<EM>===== MEZ&Otilde;GAZDAS&Aacute;GI BIZOTTS&Aacute;G: bizotts&aacute;gi "
    "t&aacute;rgyal&aacute;s befejezve <br>\n"
    "1997.09.09.  ---  els&otilde; helyen kijel&ouml;lt bizotts&aacute;gk&eacute;nt "
    "t&aacute;rgyalja <br>\n"
    "1997.10.01.  ---  &aacute;ltal&aacute;nos vit&aacute;ra aj&aacute;nlja <br>\n"
    "<p><p><a href='#Eleje'>  Eleje</a>\n</body></html>")

# 05646ir.htm — a kérdés: an MP submitter, an addressee, no resources at all.
QUESTION_ADATLAP = page(
    "<html><BODY>\n<TITLE> K/5646. sz&aacute;m&uacute; irom&aacute;ny </TITLE>\n"
    "<H2> Abony v&aacute;ros laborat&oacute;rium&aacute;nak finansz&iacute;roz&aacute;sa "
    "a 27.\xa7 alapján </H2><p><UL>\n"
    "<LI> Teljes sz&ouml;veg\n<LI> M&oacute;dos&iacute;t&oacute;k (0 db)\n"
    "<LI> Szavaz&aacute;s\n<LI> Felsz&oacute;lal&oacute;k\n</UL><p>\n"
    "K/5646 -- k&eacute;rd&eacute;s<p>\n"
    "Beny&uacute;jt&oacute; (1998.03.30.): <a href='../kepviselo/elet/s322.htm'>"
    "Selmeczi Gabriella</a> (FIDESZ-MPP)<p>\n"
    "Kihez sz&oacute;l: n&eacute;pj&oacute;l&eacute;ti miniszter<p>\n"
    "Plen&aacute;ris &aacute;llapot: beny&uacute;jtva<p>\n"
    "<EM> ==================== ESEM&Eacute;NYEK ==================== <br>\n"
    "1998.04.02.  ---  k&eacute;rd&eacute;st &iacute;r&aacute;sban "
    "megv&aacute;laszolja --- n&eacute;pj&oacute;l&eacute;ti miniszter<br>\n"
    "<p><p><a href='#Eleje'>  Eleje</a>\n</body></html>")

# 03365ir.htm — several submitters, as an <ol> under "Benyújtók".
MULTI_SPONSOR_ADATLAP = page(
    "<html><BODY>\n<H2> A szem&eacute;lyi j&ouml;vedelemad&oacute;r&oacute;l. </H2>"
    "<p><UL>\n<LI> Teljes sz&ouml;veg\n<LI> M&oacute;dos&iacute;t&oacute;k (0 db)\n"
    "<LI> Szavaz&aacute;s\n<LI> Felsz&oacute;lal&oacute;k\n</UL><p>\n"
    "T/3365 -- t&ouml;rv&eacute;nyjavaslat<p>\n"
    "Beny&uacute;jt&oacute;k (1996.10.31.): <ol>\n"
    "<li><a href='../kepviselo/elet/b149.htm'>Bark&oacute;czy Gell&eacute;rt</a> (FKGP)\n"
    "<li><a href='../kepviselo/elet/c179.htm'>Czoma K&aacute;lm&aacute;n</a> "
    "(FKGP)</ol><p>\n"
    "Plen&aacute;ris &aacute;llapot (1996.11.25.): t&aacute;rgyal&aacute;sa "
    "lez&aacute;rva<p>\n</body></html>")

# mod/04754imo.htm — a motion with two submitters and several vote markers, and
# one whose full text is linked.
MOTIONS = page(
    "<html><BODY>\n<TITLE> A 4754. sz&aacute;m&uacute; irom&aacute;ny </TITLE>\n"
    "<H3> A g&eacute;ntechnol&oacute;gi&aacute;val </H3>\nT/4754\n"
    "--t&ouml;rv&eacute;nyjavaslat\n --- 1997.08.07. ---\n"
    " -- BENY&Uacute;JT&Oacute;: f&ouml;ldm&ucirc;vel&eacute;s&uuml;gyi miniszter\n"
    " <hr> <H3> ============= NEM &Ouml;N&Aacute;LL&Oacute; IND&Iacute;TV&Aacute;NYOK "
    "============== </H3> <hr>\n"
    " <p> <LI> <a href='../04754/0030mod.htm'>4754/30</a>\n"
    " (m&oacute;dos&iacute;t&oacute; javaslat) ---\n 1998.02.16. ---\n\n"
    " visszavonva\n Teljes sz&ouml;veg\n"
    " -- BENY&Uacute;JT&Oacute;: <a href='../../kepviselo/elet/j159.htm'>"
    "Juh&aacute;sz P&aacute;l</a> (SZDSZ)\n"
    " -- BENY&Uacute;JT&Oacute;: <a href='../../kepviselo/elet/k311.htm'>"
    "Dr. Kiss R&oacute;bert</a> (SZDSZ)\n"
    " **SZAVAZ&Aacute;S: -\n **SZAVAZ&Aacute;S: +\n"
    " <p> <LI> <a href='../04754/0098mod.htm'>4754/98</a>\n"
    " (bizotts&aacute;gi aj&aacute;nl&aacute;s) ---\n 1998.03.16. ---\n"
    " z&aacute;r&oacute; vit&aacute;hoz\n"
    " <a href='../04754/0098txt.htm'>Teljes sz&ouml;veg</a>\n"
    " -- BENY&Uacute;JT&Oacute;: Alkotm&aacute;ny&uuml;gyi<p><p>"
    "<a href='#Eleje'>  Eleje</a>\n</body></html>")

# felsz/04754npl.htm — the speeches held on the document. The last row has no
# naplo link, and the page's footer sits inside the last <LI>'s markup.
SPEAKERS = page(
    "<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 4.0 Transitional//EN\">\n<html>\n"
    "<head>\n<meta http-equiv=\"Content-Type\" content=\"text/html; "
    "charset=iso-8859-2\">\n</head>\n<BODY>\n<H2> A géntechnológiával "
    "módosított szervezetekről. </H2><p><OL>\n"
    "<LI>1997.09.09. <a href=\"/naplo35/295/2950249.htm\">249. </a> "
    "felsz&oacute;lal&oacute; <a href=\"../../kepviselo/elet/n287.htm\">"
    "Dr. Nagy Frigyes</a> [01:10 sec]\n"
    "<LI>1998.03.10. <a href=\"../../kepviselo/elet/j159.htm\">Juh&aacute;sz "
    "P&aacute;l</a> [00:20 sec]\n"
    "</OL><p>Ez a lap fejleszt&eacute;s alatt &aacute;ll.<p>\n</body></html>")


class FakeHttp:
    """Serves the fixtures by URL and records every request made."""

    def __init__(self, pages: dict[str, bytes] | None = None):
        self.pages = dict(pages if pages is not None else DEFAULT_PAGES)
        self.requests: list[str] = []
        self.sleeps = 0

    def get_bytes(self, url: str, **kw):
        self.requests.append(url)
        return self.pages.get(url)

    def polite_sleep(self) -> None:
        self.sleeps += 1


B = legacy.BASE
DEFAULT_PAGES = {
    f"{B}/tvossz.htm": BILLS_LIST,
    f"{B}/intossz.htm": QUESTIONS_LIST,
    f"{B}/04754ir.htm": BILL_ADATLAP,
    f"{B}/03365ir.htm": MULTI_SPONSOR_ADATLAP,
    f"{B}/05646ir.htm": QUESTION_ADATLAP,
    f"{B}/01632ir.htm": QUESTION_ADATLAP,
    f"{B}/mod/04754imo.htm": MOTIONS,
    f"{B}/felsz/04754npl.htm": SPEAKERS,
}


# --- encoding --------------------------------------------------------------

def test_decode_renders_the_hungarian_long_vowels():
    """A plain html.unescape() gets these four letters wrong, and only these."""
    text = legacy.decode(BILL_ADATLAP)
    assert "szervezetekről" in text          # &otilde; -> ő, not õ
    assert "földművelésügyi" in text         # &ucirc;  -> ű, not û
    assert "MEZŐGAZDASÁGI" in text           # &Otilde; -> Ő, not Õ
    assert "õ" not in text and "û" not in text


def test_decode_reads_raw_high_bytes_as_latin2():
    """Most letters are entities, but not all — a raw 0xA7 is a §, not a Ł."""
    assert "27.§ alapján" in legacy.decode(QUESTION_ADATLAP)


# --- the listings ----------------------------------------------------------

def test_parse_listing_reads_the_em_wrapped_layout():
    rows = legacy.parse_listing(legacy.decode(BILLS_LIST))
    assert [r["number"] for r in rows] == ["T/4754", "T/3365"]
    first = rows[0]
    assert first["num"] == "04754"
    assert first["href"] == "04754ir.htm"
    assert first["submittedDate"] == "1997-08-07"
    assert first["type"] == "törvényjavaslat"
    assert first["title"] == "A géntechnológiával módosított szervezetekről."


def test_parse_listing_reads_the_line_broken_layout():
    """The interpellation listing breaks the same four fields over four lines
    and drops the <EM>, so the fields come off the text, not the markup."""
    rows = legacy.parse_listing(legacy.decode(QUESTIONS_LIST))
    assert [r["number"] for r in rows] == ["K/5646", "I/1632"]
    assert rows[0]["submittedDate"] == "1998-03-30"
    assert rows[0]["type"] == "kérdés"
    assert rows[0]["title"] == "Abony város laboratóriumának finanszírozása a 27.§ alapján"


# --- the adatlap -----------------------------------------------------------

def test_parse_adatlap_reads_every_labelled_line():
    a = legacy.parse_adatlap(legacy.decode(BILL_ADATLAP))
    assert a["titleFull"] == "A géntechnológiával módosított szervezetekről"
    assert a["subtype"] == "törvényjavaslat"
    assert a["submittedDate"] == "1997-08-07"
    assert a["negotiationMode"] == "normál"
    assert a["status"] == "kihirdetve"
    assert a["statusDate"] == "1998-04-01"
    assert a["promulgationNumber"] == "XXVII.tv"
    assert a["mkNumber"] == "28"
    assert a["committeeStatus"] == "bizottsági tárgyalás befejezve"
    assert a["sponsors"] == [{"personID": None, "factionId": None,
                             "factionLabel": None, "committeeId": None,
                             "label": "földművelésügyi miniszter"}]


def test_parse_adatlap_reads_the_resource_links():
    a = legacy.parse_adatlap(legacy.decode(BILL_ADATLAP))
    assert a["textUrl"] == f"{B}/fulltext/04754txt.htm"
    assert a["motionsUrl"] == f"{B}/mod/04754imo.htm"
    assert a["motionCount"] == 2
    assert a["speakersUrl"] == f"{B}/felsz/04754npl.htm"


def test_parse_adatlap_converts_the_vote_stamp_to_utc():
    """The roll-call caption is Budapest wall clock; every date the API emits is
    UTC, and 1998-03-16 is still CET."""
    vote = legacy.parse_adatlap(legacy.decode(BILL_ADATLAP))["votes"][0]
    assert vote["voteId"] == "83gb5132"
    assert vote["date"] == "1998-03-16T10:51:32Z"
    assert vote["sourceUrl"] == "https://www.parlament.hu/szavaz/szavlist/83gb5132.htm"
    assert vote["yes"] is None      # the archive never published the tallies


def test_parse_adatlap_reads_the_event_history():
    a = legacy.parse_adatlap(legacy.decode(BILL_ADATLAP))
    assert [(e["date"], e["name"]) for e in a["events"]] == [
        ("1997-09-09", "elnök bejelenti az indítványt"),
        ("1998-03-16", "Ogy. elfogadja"),
    ]


def test_parse_adatlap_keeps_the_answering_ministry_on_the_event():
    """The third field of an answer event names the responding tárca — what §6C
    derives a portfolio's answered questions from."""
    a = legacy.parse_adatlap(legacy.decode(QUESTION_ADATLAP))
    assert a["addressee"] == "népjóléti miniszter"
    assert a["events"][0]["relatedLabel"] == "népjóléti miniszter"


def test_parse_adatlap_normalises_the_committee_block():
    a = legacy.parse_adatlap(legacy.decode(BILL_ADATLAP))
    assert a["committees"] == [{
        "committee": "Mezőgazdasági bizottság",   # not the report's ALL CAPS
        "committeeId": None,
        "role": "első helyen kijelölt bizottság", # read off the designation event
        "reference": None, "parts": None,
        "status": "bizottsági tárgyalás befejezve",
    }]
    assert [(e["committee"], e["name"]) for e in a["committeeEvents"]] == [
        ("Mezőgazdasági bizottság", "első helyen kijelölt bizottságként tárgyalja"),
        ("Mezőgazdasági bizottság", "általános vitára ajánlja"),
    ]


def test_parse_adatlap_links_an_mp_submitter_by_the_id_the_registry_uses():
    """The stem of the static CV link (`s322`) *is* the cycle-35 personID, so a
    submitter joins to the MP with no name matching (EXT-2)."""
    sponsors = legacy.parse_adatlap(legacy.decode(QUESTION_ADATLAP))["sponsors"]
    assert sponsors == [{"personID": "s322", "factionId": None,
                        "factionLabel": "FIDESZ-MPP", "committeeId": None,
                        "label": "Selmeczi Gabriella (FIDESZ-MPP)"}]


def test_parse_adatlap_reads_several_submitters():
    a = legacy.parse_adatlap(legacy.decode(MULTI_SPONSOR_ADATLAP))
    assert [(s["personID"], s["factionLabel"]) for s in a["sponsors"]] == [
        ("b149", "FKGP"), ("c179", "FKGP")]
    assert a["submittedDate"] == "1996-10-31"
    assert a["status"] == "tárgyalása lezárva"
    assert a["promulgationNumber"] is None


# --- the motions listing ---------------------------------------------------

def test_parse_motions_reads_submitters_votes_and_text_links():
    motions, summary = legacy.parse_motions(legacy.decode(MOTIONS),
                                            parent_number="4754")
    assert [m["billNumber"] for m in motions] == ["4754/30", "4754/98"]
    first, last = motions
    assert first["type"] == "módosító javaslat"
    assert first["submittedDate"] == "1998-02-16"
    assert first["note"] == "visszavonva"
    assert [s["personID"] for s in first["sponsors"]] == ["j159", "k311"]
    assert first["hasVote"] is True          # **SZAVAZÁS markers
    assert first["noText"] is True           # "Teljes szöveg" unlinked
    # The motions listing lives in /iromany/mod/, so its ../ resolves back up.
    assert last["textUrl"] == f"{B}/04754/0098txt.htm"
    assert last["noText"] is False
    assert last["hasVote"] is False
    assert last["sponsors"] == [{"personID": None, "factionId": None,
                                "factionLabel": None, "committeeId": None,
                                "label": "Alkotmányügyi"}]
    assert sorted((s["type"], s["total"]) for s in summary) == [
        ("bizottsági ajánlás", "1"), ("módosító javaslat", "1")]


def test_parse_motions_sort_key_matches_the_api_packing():
    assert legacy._motion_sort("4754/30") == 4754030
    assert legacy._motion_sort("15790/16017") == 15806017
    assert legacy._motion_sort("nem szám") is None


def test_parse_motions_ignores_a_page_with_no_motions_section():
    assert legacy.parse_motions(legacy.decode(BILL_ADATLAP),
                                parent_number="4754") == ([], [])


# --- the speakers listing --------------------------------------------------

def test_parse_speakers_addresses_the_speech_already_in_the_database():
    speakers = legacy.parse_speakers(legacy.decode(SPEAKERS))
    assert len(speakers) == 2
    first = speakers[0]
    assert first["date"] == "1997-09-09"
    assert first["role"] == "felszólaló"
    assert first["personID"] == "n287"
    assert first["label"] == "Dr. Nagy Frigyes"
    # /naplo35/295/2950249.htm -> cycle 35, sitting 295, speech 249
    assert (first["sessionId"], first["speechNumber"]) == ("35295", "249")
    assert first["speechUid"] == "35295-249"
    assert first["durationSeconds"] == 70


def test_parse_speakers_does_not_swallow_the_page_footer():
    """The last <LI> is never closed, so the footer text sits inside it."""
    last = legacy.parse_speakers(legacy.decode(SPEAKERS))[-1]
    assert last["label"] == "Juhász Pál"
    assert last["speechUid"] is None          # this row has no naplo link
    assert last["role"] is None               # and no role either


# --- a whole run -----------------------------------------------------------

def test_fetch_reads_both_listings_and_one_adatlap_per_document():
    http = FakeHttp()
    registry = legacy.fetch_legacy_bills(http)

    assert registry["meta"]["source"] == legacy.SOURCE
    assert registry["meta"]["cycle"] == 35
    assert registry["meta"]["count"] == 4
    # Two listings + four adatlapok + the one motions listing that exists. No
    # request for a document whose adatlap says it has no motions, and none for
    # the speakers listing unless it is asked for.
    assert http.requests.count(f"{B}/tvossz.htm") == 1
    assert http.requests.count(f"{B}/intossz.htm") == 1
    assert http.requests.count(f"{B}/mod/04754imo.htm") == 1
    assert f"{B}/felsz/04754npl.htm" not in http.requests
    assert len(http.requests) == 7
    assert http.sleeps == len(http.requests)     # politeness on every fetch


def test_fetch_orders_newest_iromany_number_first():
    numbers = [r["billNumber"] for r in
               legacy.fetch_legacy_bills(FakeHttp())["data"]]
    assert numbers == ["K/5646", "T/4754", "T/3365", "I/1632"]


def test_record_maps_onto_the_api_record_shape():
    """The registry has to be a drop-in bills-35.json, so a record carries the
    same keys `fetch_bills` emits — the loader reads no others."""
    rec = next(r for r in legacy.fetch_legacy_bills(FakeHttp())["data"]
               if r["billNumber"] == "T/4754")
    assert rec["billId"] == "35-04754"
    assert rec["billNumberSort"] == 4754
    assert rec["mainType"] == "T"
    assert rec["type"] == "törvényjavaslat"
    assert rec["status"] == "kihirdetve"
    assert rec["submittedDate"] == "1997-08-07"
    assert rec["stages"] == []
    assert rec["textUrl"] == f"{B}/fulltext/04754txt.htm"
    assert rec["noText"] is False
    assert rec["sourceUrl"] == f"{B}/04754ir.htm"
    # The adatlap's title, not the listing's: the listing hard-wraps at column
    # 200, mid-word, and drops nothing to mark it.
    assert rec["title"] == "A géntechnológiával módosított szervezetekről"
    header = rec["detail"]["header"]
    assert header["negotiationMode"] == "normál"
    assert header["promulgationNumber"] == "XXVII.tv"
    assert header["mkNumber"] == "28"
    assert header["promulgationDate"] == "1998-04-01"
    assert header["addressee"] is None
    assert rec["detail"]["deadlines"] == []      # never recorded in that era
    assert rec["detail"]["documents"] == []
    assert len(rec["detail"]["motions"]) == 2


def test_a_question_gets_no_promulgation_date():
    """`promulgationDate` is the plenary-status date, which only *means* the
    promulgation when the status carries a gazette reference."""
    rec = next(r for r in legacy.fetch_legacy_bills(FakeHttp())["data"]
               if r["billNumber"] == "K/5646")
    assert rec["status"] == "benyújtva"
    assert rec["detail"]["header"]["promulgationDate"] is None
    assert rec["detail"]["header"]["addressee"] == "népjóléti miniszter"


def test_no_detail_reads_only_the_two_listings():
    http = FakeHttp()
    registry = legacy.fetch_legacy_bills(http, with_detail=False)
    assert len(http.requests) == 2
    assert registry["meta"]["count"] == 4
    rec = registry["data"][1]
    assert rec["billNumber"] == "T/4754"
    assert rec["detail"] is None
    # Falls back to the listing title, minus the trailing stop the listing adds.
    assert rec["title"] == "A géntechnológiával módosított szervezetekről"


def test_limit_caps_the_documents_fetched():
    http = FakeHttp()
    registry = legacy.fetch_legacy_bills(http, limit=1)
    assert registry["meta"]["count"] == 1
    assert registry["data"][0]["billNumber"] == "K/5646"


def test_speakers_are_opt_in():
    http = FakeHttp()
    registry = legacy.fetch_legacy_bills(http, with_speakers=True)
    assert f"{B}/felsz/04754npl.htm" in http.requests
    rec = next(r for r in registry["data"] if r["billNumber"] == "T/4754")
    assert [s["speechUid"] for s in rec["detail"]["speakers"]] == ["35295-249", None]
    # A document whose adatlap links no speakers listing costs no request.
    other = next(r for r in registry["data"] if r["billNumber"] == "T/3365")
    assert other["detail"]["speakers"] == []


def test_a_missing_page_leaves_the_listing_row_intact(caplog):
    """SCR-5: one unreachable adatlap must not cost us the document."""
    pages = dict(DEFAULT_PAGES)
    del pages[f"{B}/04754ir.htm"]
    registry = legacy.fetch_legacy_bills(FakeHttp(pages))
    rec = next(r for r in registry["data"] if r["billNumber"] == "T/4754")
    assert rec["title"] == "A géntechnológiával módosított szervezetekről"
    assert rec["detail"] is None


def test_faction_labels_resolve_to_the_id_the_loader_joins_on(tmp_path):
    """Ids are pooled across cycles on purpose: a frakcioId is global, and a
    group of this parliament (KDNP) can be absent from its own registry."""
    (tmp_path / "representatives-35.json").write_text(json.dumps({"data": [
        {"personID": "s322", "faction": {"id": 7, "label": "Fidesz"}},
        {"personID": "b149", "faction": {"id": 6, "label": "FKGP"}},
    ]}))
    (tmp_path / "representatives-39.json").write_text(json.dumps({"data": [
        {"personID": "x001", "faction": {"id": 8, "label": "KDNP"}},
    ]}))
    ids = legacy.faction_ids_from_registries(
        sorted(tmp_path.glob("representatives-*.json")))
    assert ids["fkgp"] == 6
    assert ids["kdnp"] == 8
    # The archive spells the group as it was in 1998; the alias table maps it.
    assert ids["fidesz-mpp"] == 7

    registry = legacy.fetch_legacy_bills(FakeHttp(), faction_ids=ids)
    question = next(r for r in registry["data"] if r["billNumber"] == "K/5646")
    assert question["sponsors"][0]["factionId"] == 7
    multi = next(r for r in registry["data"] if r["billNumber"] == "T/3365")
    assert [s["factionId"] for s in multi["sponsors"]] == [6, 6]
    # Motion submitters get the same treatment.
    bill = next(r for r in registry["data"] if r["billNumber"] == "T/4754")
    assert bill["detail"]["motions"][0]["sponsors"][0]["factionId"] is None  # SZDSZ


def test_faction_map_missing_is_not_fatal(tmp_path):
    assert legacy.faction_ids_from_registries([tmp_path / "nope.json"]) == {}
    assert legacy.faction_ids_from_registries([]) == {}


# --- resuming --------------------------------------------------------------

def test_a_second_run_reuses_what_is_already_on_file(tmp_path):
    """The archive was generated once, in 1998, so a record on file is reused
    whole — which is what makes an interrupted 5 650-document run resumable."""
    out = tmp_path / "bills-35.json"
    first = legacy.fetch_legacy_bills(FakeHttp(), cache_path=out)
    out.write_text(json.dumps(first))

    http = FakeHttp()
    second = legacy.fetch_legacy_bills(http, cache_path=out)
    assert second["meta"]["detailFetched"] == 0
    assert second["meta"]["detailReused"] == 4
    assert http.requests == [f"{B}/tvossz.htm", f"{B}/intossz.htm"]
    assert second["data"] == first["data"]


def test_force_re_fetches_everything(tmp_path):
    out = tmp_path / "bills-35.json"
    out.write_text(json.dumps(legacy.fetch_legacy_bills(FakeHttp(),
                                                        cache_path=out)))
    http = FakeHttp()
    again = legacy.fetch_legacy_bills(http, cache_path=out, force=True)
    assert again["meta"]["detailFetched"] == 4
    assert f"{B}/04754ir.htm" in http.requests


def test_adding_speakers_later_re_fetches_only_for_them(tmp_path):
    """A skipped layer leaves its key out, so a cached record is only reused
    when it already has every layer this run asks for."""
    out = tmp_path / "bills-35.json"
    out.write_text(json.dumps(legacy.fetch_legacy_bills(FakeHttp(),
                                                        cache_path=out)))
    http = FakeHttp()
    again = legacy.fetch_legacy_bills(http, cache_path=out, with_speakers=True)
    assert again["meta"]["detailReused"] == 0
    assert f"{B}/felsz/04754npl.htm" in http.requests


def test_an_api_written_registry_is_never_reused(tmp_path):
    """bills-35.json may already exist from an (empty) API run; mixing the two
    sources silently would be worse than re-fetching."""
    out = tmp_path / "bills-35.json"
    out.write_text(json.dumps({"meta": {"cycle": 35,
                                        "source": "felicitas-iromany-api"},
                               "data": [{"billId": "35-04754",
                                         "detail": {"events": []}}]}))
    registry = legacy.fetch_legacy_bills(FakeHttp(), cache_path=out)
    assert registry["meta"]["detailReused"] == 0


# --- reconciling the era's vocabulary with the modern one -------------------

SUPERSEDED_ADATLAP = page(
    "<html><BODY>\n<H2> A rend&otilde;rs&eacute;gr&otilde;l. </H2><p><UL>\n"
    "<LI> Teljes sz&ouml;veg\n<LI> M&oacute;dos&iacute;t&oacute;k (0 db)\n"
    "<LI> Szavaz&aacute;s\n<LI> Felsz&oacute;lal&oacute;k\n</UL><p>\n"
    "H/1174 -- hat&aacute;rozati javaslat (hat. n. szerz.-r&otilde;l)  --- "
    "&Uacute;j v&aacute;ltozat 1180. sz&aacute;mon.<p>\n"
    "Beny&uacute;jt&oacute; (1995.06.13.): H&aacute;zbizotts&aacute;g<p>\n"
    "Plen&aacute;ris &aacute;llapot (1995.06.13.): t&aacute;rgyal&aacute;sa "
    "lez&aacute;rva<p>\n</body></html>")


def test_a_superseded_document_keeps_its_remark_out_of_the_subtype():
    """The header line carries both, separated by a spaced dash run — and a
    subtype can contain a hyphen of its own."""
    a = legacy.parse_adatlap(legacy.decode(SUPERSEDED_ADATLAP))
    assert a["subtype"] == "határozati javaslat (hat. n. szerz.-ről)"
    assert a["remark"] == "Új változat 1180. számon."


def test_answer_events_are_renamed_to_the_names_6c_matches_on():
    """§6C matches event names exactly, so the era's abbreviated active-voice
    names are mapped onto the API's; the original is kept on the event."""
    a = legacy.parse_adatlap(legacy.decode(QUESTION_ADATLAP))
    event = a["events"][0]
    assert event["name"] == "kérdés írásban megválaszolva"
    assert event["remark"] == "kérdést írásban megválaszolja"
    assert event["relatedLabel"] == "népjóléti miniszter"


def test_other_event_names_are_left_alone():
    events = legacy.parse_adatlap(legacy.decode(BILL_ADATLAP))["events"]
    assert [e["name"] for e in events] == ["elnök bejelenti az indítványt",
                                          "Ogy. elfogadja"]
    assert all(e["remark"] == "" for e in events)


def test_reusing_a_record_still_picks_up_a_faction_map_it_lacked(tmp_path):
    """Scraping the MP registry after the archive shouldn't cost a re-scrape:
    resolving a faction label needs no request, so it is redone on reuse."""
    out = tmp_path / "bills-35.json"
    first = legacy.fetch_legacy_bills(FakeHttp(), cache_path=out)
    out.write_text(json.dumps(first))
    assert all(s["factionId"] is None for r in first["data"]
               for s in r["sponsors"])

    http = FakeHttp()
    again = legacy.fetch_legacy_bills(http, cache_path=out,
                                      faction_ids={"fidesz-mpp": 7, "fkgp": 6})
    assert again["meta"]["detailReused"] == 4
    assert http.requests == [f"{B}/tvossz.htm", f"{B}/intossz.htm"]
    question = next(r for r in again["data"] if r["billNumber"] == "K/5646")
    assert question["sponsors"][0]["factionId"] == 7


NON_MP_SPEAKERS = page(
    "<html><BODY>\n<H2> A k&ouml;lts&eacute;gvet&eacute;sr&otilde;l. </H2><p><OL>\n"
    "<LI>1996.02.20. <a href=\"/naplo35/213/2130012.htm\">12. </a> "
    "felsz&oacute;lal&oacute; Bokros Lajos [05:00 sec]\n</OL><p>\n</body></html>")


def test_a_speaker_with_no_cv_page_still_gets_a_name():
    """A minister or the chief prosecutor spoke without being an MP, so the
    archive has no page to link them to and prints the name as plain text."""
    row = legacy.parse_speakers(legacy.decode(NON_MP_SPEAKERS))[0]
    assert row["role"] == "felszólaló"
    assert row["label"] == "Bokros Lajos"
    assert row["personID"] is None
    assert row["speechUid"] == "35213-12"
    assert row["durationSeconds"] == 300
