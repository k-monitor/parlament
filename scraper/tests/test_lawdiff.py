"""Offline tests for the law-changes stage (BILL-14..16).

The fixtures are small but shaped on the real documents they stand for: njt
markup as njt.hu serves it (a footnote naming two acts, an untitled §, a
paragraph's closing text, a deferred block), bill lines as PyMuPDF reads the
T/438 PDF (the szakképzési törvény amendment, Act LV of 2026), and an amendment
PDF drawn with the Parlex template's markup — bold [deletions] and underlines.
"""

from __future__ import annotations

import pytest

from parlamonitor.lawdiff import motions, njt, novella
from parlamonitor.lawdiff import scrape as stage

# --- njt markup -----------------------------------------------------------------

PAGE = """<html><head>
<meta name="description" content="2019. évi LXXX. törvény a szakképzésről"/>
</head><body><main><div class="mainContent" id="jogszabaly-reader">
<span class="jhId" id="SZ26"></span><div id="sc1" class="szakasz"><p class="szakaszCim mt">
<span class="szakasz-jel">26. §<sup class="fnSup" data-note-id="fnr_1">86</sup> </span>
[<i>A szakképző intézmény</i>]</p></div>
<span class="jhId" id="SZ26@BE3"></span><div id="sc2" class="bekezdesNyito"><p>
<span class="jel"> (3)</span><sup class="fnSup" data-note-id="fnr_2">89</sup>
A szakképzési centrumot a főigazgató és a kancellár önállóan vezeti.</p></div>
<span class="jhId" id="SZ26@BE4"></span><div id="sc3" class="bekezdesNyito"><p>
<span class="jel"> (4)</span> A főigazgató felel a szakképzési centrum</p></div>
<span class="jhId" id="SZ26@BE4@POA"></span><div id="sc4" class="betusPontNyito"><p>
<span class="jel"> a)</span> részeként működő intézmények feladatainak ellátásáért, valamint</p></div>
<span class="jhId" id="SZ26@BE4@POB"></span><div id="sc5" class="betusPontNyito"><p>
<span class="jel"> b)</span> törvényes működéséért</p></div>
<div id="sc6" class="bekezdesZaro"><p>felel 2026. december 31-éig.</p></div>
<span class="jhId" id="SZ27"></span><div id="sc7" class="szakasz"><p class="szakaszCim mt"></p></div>
<span class="jhId" id="SZ27@BE1"></span><div id="sc8" class="bekezdesNyito"><p>
<span class="jel"><span class="szakasz-jel">27. §</span> (1)</span> Az
<a class="link" data-jhid="SZ26@BE4" data-type="belso">(4) bekezdés</a> szerinti feladat.</p></div>
<div id="sc9" class="pH borderStart" data-show-order="40" data-b-n="1"><span id="SZ28"></span></div>
</div></main>
<div class="footnote" id="fnr_1"><sup>86</sup><p>A 26. §-t a
<a class="link" href="jogszabaly/2020-1-00-00" data-jhid="SZ5" data-type="kulso">2020. évi I. törvény 5. §-a</a>
iktatta be.</p></div>
<div class="footnote" id="fnr_2"><sup>89</sup><p>A 26. § (3) bekezdése a
<a class="link" href="jogszabaly/2025-95-00-00" data-jhid="SZ43" data-type="kulso">2025. évi XCV. törvény 43. §-ával</a>
megállapított, a
<a class="link" href="jogszabaly/2026-55-00-00" data-jhid="SZ6@BE0@POC" data-type="kulso">2026. évi LV. törvény 6. § c) pontja</a>
szerint módosított szöveg.</p></div>
</body></html>"""

BLOCKS = """<span class="jhId" id="SZ28"></span><div id="sc10" class="szakasz">
<p class="szakaszCim mt"><span class="szakasz-jel">28. §</span> [<i>Átmeneti</i>]</p></div>
<span class="jhId" id="SZ28@BE0"></span><div id="sc11" class="bekezdesNyito"><p>Rövid szöveg.</p></div>"""


def test_njt_parse_units_labels_and_closing_text():
    law = njt.parse(PAGE, BLOCKS)
    assert law["title"] == "2019. évi LXXX. törvény a szakképzésről"
    by = {u["jh"]: u for u in law["units"]}
    assert by["SZ26"]["label"] == "26. §" and by["SZ26"]["text"] == "[A szakképző intézmény]"
    assert by["SZ26@BE4@POA"]["label"] == "a)"
    # a paragraph's closing text belongs to the paragraph, not to its last point
    assert by["SZ26@BE4@ZARO"]["text"] == "felel 2026. december 31-éig."
    # an untitled § carries its number in its first paragraph's label
    assert by["SZ27"]["label"] == "27. §" and by["SZ27@BE1"]["label"] == "(1)"
    assert by["SZ27@BE1"]["text"] == "Az (4) bekezdés szerinti feladat."
    # the deferred block follows the page's own units
    assert [u["jh"] for u in law["units"]][-2:] == ["SZ28", "SZ28@BE0"]


def test_njt_footnotes_name_every_act_with_what_it_did():
    law = njt.parse(PAGE)
    fn = {u["jh"]: u["fn"] for u in law["units"]}["SZ26@BE3"][0]
    docs = [l["doc"] for l in fn["links"]]
    assert docs == ["2025-95-00-00", "2026-55-00-00"]
    lv = fn["links"][1]
    assert lv["jh"] == "SZ6@BE0@POC" and "módosított szöveg" in lv["after"]
    assert "módosított" not in fn["links"][0]["after"]


def test_njt_flatten_unit_label_and_deferred_blocks():
    units = njt.parse(PAGE)["units"]
    assert njt.flatten(units, "SZ26@BE4") == (
        "(4) A főigazgató felel a szakképzési centrum a) részeként működő intézmények "
        "feladatainak ellátásáért, valamint b) törvényes működéséért felel 2026. december 31-éig.")
    assert njt.unit_label("SZ14D@BE4A@POB") == "14/D. § (4a) bekezdés b) pont"
    assert njt.unit_label("SZ102@BE0@PO9") == "102. § 9. pont"
    assert njt.deferred_blocks(PAGE) == [{"start": 40, "last": None}]


def test_njt_choosing_time_states():
    versions = [
        {"comingIntoForce": "2027-01-01T00:00:00+01:00", "expiresOn": None, "version": 33},
        {"comingIntoForce": "2026-10-01T00:00:00+02:00", "expiresOn": "2027-01-01T00:00:00+01:00", "version": 31},
        {"comingIntoForce": "2026-01-01T00:00:00+01:00", "expiresOn": "2026-10-01T00:00:00+02:00", "version": 28},
        {"comingIntoForce": "2020-01-01T00:00:00+01:00", "expiresOn": "2020-01-01T00:00:00+01:00",
         "gazetteState": True, "version": 0},
    ]
    assert njt.latest_version(versions)["version"] == 33
    assert njt.version_on(versions, "2026-09-22")["version"] == 28
    assert njt.version_on(versions, "2026-10-01")["version"] == 31


def test_budget_caps_requests():
    b = njt.Budget(2)
    b.spend(); b.spend()
    assert b.exhausted
    with pytest.raises(njt.BudgetExhausted):
        b.spend()
    assert not njt.Budget(None).exhausted


# --- the novella ------------------------------------------------------------------

def _lines(*rows):
    """Bill lines as pdftext.lines yields them: (text, x[, italic])."""
    out = []
    for r in rows:
        text, x = r[0], r[1]
        out.append({"text": text, "x": x, "y": 0, "page": 0,
                    "italic": len(r) > 2 and r[2]})
    return out


BILL = _lines(
    ("Iromány száma: T/438.", 312),
    ("2026. évi ..... törvény", 244),
    ("a szakképzésről szóló 2019. évi LXXX. törvény módosításáról", 141),
    ("1. §", 289),
    ("(1) A szakképzésről szóló 2019. évi LXXX. törvény (a továbbiakban: Szkt.) 26. § (4)", 57),
    ("bekezdés a) pontja helyébe a következő rendelkezés lép:", 57),
    ("(A főigazgató felel a szakképzési centrum)", 57, True),
    ("„a) részeként működő szakképző intézmények ellátásáért, valamint”", 58),
    ("(2) Az Szkt. 26. §-a a következő (4a) és (4b) bekezdéssel egészül ki:", 57),
    ("„(4a) A szakmai főigazgató-helyettest a miniszter bízza meg.", 57),
    ("(4b) A megbízás a (4a), (4b) és (5) bekezdés szerint szűnik meg.”", 57),
    ("2. Záró rendelkezések", 230),
    ("2. §", 289),
    ("Az Szkt.", 57),
    ("a) 26. § (3) bekezdésében az „és a kancellár” szövegrész helyébe az „és a", 86),
    ("szakmai főigazgató-” szöveg,", 86),
    ("b) 26. § (4) bekezdés záró szövegrészében a „2026” szövegrész helyébe a „2027” szöveg", 86),
    ("lép.", 57),
    ("3. §", 289),
    ("Hatályát veszti az Szkt. 27. § (1) bekezdése.", 57),
    ("4. §", 289),
    ("Ez a törvény a kihirdetését követő napon lép hatályba.", 57),
    ("Általános indokolás", 250),
    ("Az Szkt. 99. §-a helyébe a következő rendelkezés lép: „nem része a törvénynek”", 57),
)


def test_parse_bill_reads_every_instruction_kind():
    parsed = novella.parse_bill(BILL)
    assert parsed["abbr"] == {"Szkt.": "2019-80-00-00"}
    ops = [(i["at"], i["op"]) for i in parsed["instructions"]]
    assert ops == [("1. § (1)", "replace"), ("1. § (2)", "insert"),
                   ("2. § a)", "replace_text"), ("2. § b)", "replace_text"),
                   ("3. §", "repeal"), ("4. §", "none")]
    rep = parsed["instructions"][0]
    assert rep["refs"] == ["SZ26@BE4@POA"] and rep["doc"] == "2019-80-00-00"
    # the italic context line says which sentence the point continues; it is
    # not part of the new text
    assert rep["new"].startswith("a) részeként")
    ins = parsed["instructions"][1]
    assert ins["parent"] == "SZ26" and ins["newrefs"] == ["BE4A", "BE4B"]
    # a real hyphen at a line end joins without a space
    assert parsed["instructions"][2]["new"] == "és a szakmai főigazgató-szöveg" or \
        parsed["instructions"][2]["new"] == "és a szakmai főigazgató-"
    assert parsed["instructions"][3]["part"] == "za"
    # the bill's own centred heading is not part of any instruction, and the
    # justification is not part of the bill
    assert all("Záró rendelkezések" not in i["src"] for i in parsed["instructions"])
    assert all("99. §" not in i["src"] for i in parsed["instructions"])


def test_apply_and_changes_against_njt_units():
    units = njt.parse(PAGE)["units"]
    parsed = novella.parse_bill(BILL)
    pred, log = novella.apply(units, parsed["instructions"], "2019-80-00-00")
    assert all(r["ok"] for r in log if r["ins"]["op"] != "none"), [r["why"] for r in log]
    by = {u["jh"]: u for u in pred}
    # replace: the point is the quoted text now; the closing text is edited too
    assert by["SZ26@BE4@POA"]["text"] == "a) részeként működő szakképző intézmények ellátásáért, valamint"
    assert by["SZ26@BE4@ZARO"]["text"] == "felel 2027. december 31-éig."
    # insert of two paragraphs splits at each paragraph's own label — not at the
    # "(4b)" quoted inside the second one's text — and lands after (4)
    assert by["SZ26@BE4A"]["text"] == "(4a) A szakmai főigazgató-helyettest a miniszter bízza meg."
    assert by["SZ26@BE4B"]["text"].startswith("(4b) A megbízás a (4a), (4b) és (5)")
    order = [u["jh"] for u in pred]
    assert order.index("SZ26@BE4A") < order.index("SZ26@BE4B") < order.index("SZ27")
    # one occurrence replaced in the paragraph's own text
    assert "és a szakmai főigazgató-" in by["SZ26@BE3"]["text"]
    # repeal empties the paragraph
    assert by["SZ27@BE1"]["text"] == ""

    changes = novella.changes(units, pred, log)
    refs = [c["ref"] for c in changes]
    assert refs == ["26. § (3) bekezdés", "26. § (4) bekezdés", "26. § (4a) bekezdés",
                    "26. § (4b) bekezdés", "27. § (1) bekezdés"]
    # the point's change is shown inside its paragraph's, and still says which
    # provision of the bill made it
    para = next(c for c in changes if c["jh"] == "SZ26@BE4")
    assert set(para["provisions"]) == {"1. § (1)", "2. § b)"}
    new = next(c for c in changes if c["jh"] == "SZ26@BE4A")
    assert new["before"] == "" and new["provisions"] == ["1. § (2)"]
    gone = next(c for c in changes if c["jh"] == "SZ27@BE1")
    assert gone["after"] == "" and gone["before"].startswith("(1) Az (4) bekezdés")


def test_unresolvable_targets_are_reported_not_dropped():
    units = njt.parse(PAGE)["units"]
    instrs = [{"at": "5. §", "op": "replace_text", "doc": "D", "refs": ["SZ26@BE3"],
               "part": None, "old": "nincs ilyen", "new": "x", "src": ""},
              {"at": "6. §", "op": "repeal", "doc": "D", "refs": ["SZ99"], "part": None, "src": ""}]
    _, log = novella.apply(units, instrs, "D")
    assert [r["ok"] for r in log] == [False, False]
    assert "szövegrész nem található" in log[0]["why"] and "99. §" in log[1]["why"]


def test_attributed_changes_follow_njt_footnotes():
    after = njt.parse(PAGE)["units"]
    before = [dict(u) for u in after]
    for u in before:
        if u["jh"] == "SZ26@BE3":
            u["text"] = "A szakképzési centrumot a főigazgató és a kancellár vezeti."
    found = novella.attributed_changes(before, after, "2026-55-00-00")
    assert [c["jh"] for c in found] == ["SZ26@BE3"]
    assert found[0]["provisions"] == ["6. § c) pont"]
    assert found[0]["ops"] == ["replace_text"]
    # a unit the act is cited on but whose text did not move is no change
    assert novella.attributed_changes(after, after, "2026-55-00-00") == []


def test_split_units_ignores_labels_quoted_in_running_text():
    text = "(4c) Szöveg a (4a), (4b) és (4d) bekezdés szerint.\n(4d) Másik."
    assert novella.split_units(text, ["SZ1@BE4C", "SZ1@BE4D"]) == [
        "(4c) Szöveg a (4a), (4b) és (4d) bekezdés szerint.", "(4d) Másik."]


def test_parse_ref_ranges_and_parts():
    assert novella.parse_ref("14/D. § (2) és (3) bekezdése") == (["SZ14D@BE2", "SZ14D@BE3"], None)
    assert novella.parse_ref("6. § b)–d) pontja")[0] == ["SZ6@POB", "SZ6@POC", "SZ6@POD"]
    assert novella.parse_ref("125. § (9) bekezdés nyitó szövegrészében") == (["SZ125@BE9"], "ny")
    assert novella.parse_ref("XXIII. Fejezete")[0] == ["FEXXIII"]
    # several sections, listed or as a range
    assert novella.parse_ref("18/C–18/E. §-sal")[0] == ["SZ18C", "SZ18D", "SZ18E"]
    assert novella.parse_ref("32. és 33. §-a")[0] == ["SZ32", "SZ33"]
    assert novella.parse_ref("5–7. §")[0] == ["SZ5", "SZ6", "SZ7"]
    assert novella.parse_ref("18–18/B. §")[0] == ["SZ18", "SZ18A", "SZ18B"]


def _sec(n: str, *paras: str) -> list[dict]:
    jh = "SZ" + n.replace("/", "")
    out = [{"jh": jh, "cls": "szakasz", "label": f"{n}. §", "text": "", "fn": []}]
    out += [{"jh": f"{jh}@BE{k}", "cls": "bekezdesNyito", "label": f"({k})", "text": t, "fn": []}
            for k, t in enumerate(paras, 1)]
    return out


def test_a_section_range_fills_new_numbers_and_an_empty_slot():
    # 18/E was repealed long ago; njt still lists its number with no text
    units = _sec("18/B", "Régi.") + _sec("18/E") + _sec("19", "Utolsó.")
    tmpl = ("Az árak megállapításáról szóló 1990. évi LXXXVII. törvény „Hatósági "
            "üzemanyagárral kapcsolatos rendelkezések” alcíme a következő "
            "18/C–18/E. §-sal egészül ki: ⟦0⟧")
    quote = "18/C. §\n(1) Első.\n18/D. §\nMásodik.\n18/E. §\n(1) Harmadik."
    [ins] = novella.parse_instruction(tmpl, [quote], {}, "3. §")
    assert ins["op"] == "insert" and ins["newrefs"] == ["SZ18C", "SZ18D", "SZ18E"]
    after, log = novella.apply(units, [ins], "1990-87-00-00")
    found = novella.changes(units, after, log)
    assert [(c["ref"], c["before"], c["after"]) for c in found] == [
        ("18/C. §", "", "18/C. § (1) Első."),
        ("18/D. §", "", "18/D. § Második."),
        ("18/E. §", "", "18/E. § (1) Harmadik.")]
    assert [u["jh"] for u in after if "@" not in u["jh"]] == [
        "SZ18B", "SZ18C", "SZ18D", "SZ18E", "SZ19"]


def test_a_repealed_section_reads_as_gone_not_as_its_bare_number():
    units = _sec("18/B", "Régi.") + _sec("19", "Utolsó.")
    ins = {"at": "5. §", "op": "repeal", "doc": "D", "refs": ["SZ19"], "part": None, "src": ""}
    after, log = novella.apply(units, [ins], "D")
    [c] = novella.changes(units, after, log)
    assert (c["before"], c["after"]) == ("19. § (1) Utolsó.", "")


# --- amendments --------------------------------------------------------------------

pymupdf = pytest.importorskip("pymupdf")


def _amendment_pdf() -> bytes:
    """One módosítópont drawn the Parlex way: the deletion in bold inside
    brackets, the insertion underlined with a vector line."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 100
    fonts = {False: pymupdf.Font("tiro"), True: pymupdf.Font("tibo")}

    def write(x, text, bold=False):
        tw = pymupdf.TextWriter(page.rect)
        tw.append((x, y), text, font=fonts[bold], fontsize=12)
        tw.write_text(page)
        return x + fonts[bold].text_length(text, fontsize=12)

    write(57, "Módosítópont sorszáma: 1.")
    y += 16
    write(57, "Törvényjavaslat érintett rendelkezése: 7. §")
    y += 16
    write(57, "Módosítás jellege: módosítás")
    y += 24
    x = write(57, "(1) Ez a törvény 2026. ")
    x = write(x, "[szeptember]", bold=True)
    x0 = x
    x = write(x, "október")
    page.draw_line((x0, y + 2), (x, y + 2), width=0.6)
    write(x, " 1-jén lép hatályba.")
    y += 30
    write(57, "Indokolás")
    y += 16
    write(57, "1. A tanév közepén ne kerüljön sor a változásra.")
    data = doc.tobytes()
    doc.close()
    return data


def test_amendment_markup_becomes_diff_segments():
    from parlamonitor.lawdiff import pdftext
    pdf = _amendment_pdf()
    assert not pdftext.is_scan(pdf)
    parsed = motions.parse(pdftext.marked_lines(pdf))
    assert parsed["marked"]
    (p,) = parsed["points"]
    assert p["n"] == "1" and p["target"] == "7. §" and p["kind"] == "módosítás"
    segs = [s for s in p["segments"] if s[0] != "eq"]
    assert segs == [["del", "szeptember"], ["ins", "október"]]
    text = "".join(t for _, t in p["segments"])
    assert "Indokolás" not in text and "[" not in text


def test_motion_types():
    assert motions.is_amendment("Módosító javaslat")
    assert motions.is_amendment("Összegző módosító javaslat")
    assert motions.is_amendment("Részletes vitát lezáró bizottsági módosító javaslat")
    assert not motions.is_amendment("Módosító javaslat fenntartásának kérése")
    assert not motions.is_amendment("Túlterjeszkedő módosító javaslatot tartalmazó egységes javaslat")
    assert motions.is_consolidated("Túlterjeszkedő módosító javaslatot tartalmazó egységes javaslat")


# --- the stage -----------------------------------------------------------------------

class _Docs:
    """A DocCache stand-in serving prepared extractions by URL."""

    def __init__(self, docs):
        self.docs = docs
        self.fetched = self.reused = 0

    def get(self, url, kind):
        return self.docs[url]


class _Store:
    """An NjtStore stand-in: one law, two states."""

    def __init__(self, units_before, units_after):
        self.calls = 0
        self.states = {28: units_before, 33: units_after}

    def versions(self, doc):
        self.calls += 1
        return [{"comingIntoForce": "2027-01-01T00:00:00+01:00", "expiresOn": None, "version": 33},
                {"comingIntoForce": "2026-01-01T00:00:00+01:00",
                 "expiresOn": "2027-01-01T00:00:00+01:00", "version": 28}]

    def law(self, doc, version):
        return {"docId": doc, "version": version, "title": "2019. évi LXXX. törvény a szakképzésről",
                "units": self.states[version]}


def _registry_record(status="tárgysorozatban", header=None, motions_=()):
    return {"billId": "b-1", "billNumber": "T/438", "mainType": "T", "status": status,
            "submittedDate": "2026-07-28T09:10:02Z",
            "textUrl": "https://x/00438.pdf",
            "detail": {"header": header or {}, "motions": list(motions_)}}


def test_pending_bill_is_computed_and_then_reused():
    units = njt.parse(PAGE)["units"]
    store = _Store(units, units)
    docs = _Docs({"https://x/00438.pdf": {"scan": False, "lines": BILL}})
    rec = stage.build_record(_registry_record(), docs, store, None)
    assert rec["source"]["kind"] == "bill" and not rec["final"]
    (law,) = rec["laws"]
    assert law["method"] == "computed" and law["base"]["version"] == 33
    assert law["abbr"] == "Szkt." and law["changes"][0]["url"].endswith("#SZ26@BE3")
    assert rec["stats"]["changes"] == 5
    # nothing it was computed from moved: the previous record is returned as is
    again = stage.build_record(_registry_record(), docs, store, rec)
    assert again is rec


def test_adopted_bill_is_read_off_njt_and_final():
    after = njt.parse(PAGE)["units"]
    before = [dict(u) for u in after]
    for u in before:
        if u["jh"] == "SZ26@BE3":
            u["text"] = "Régi szöveg."
    store = _Store(before, after)
    docs = _Docs({"https://x/00438.pdf": {"scan": False, "lines": BILL}})
    header = {"promulgationNumber": "LV", "promulgationDate": "2026-09-22T18:00:00Z"}
    rec = stage.build_record(_registry_record("kihirdetve", header), docs, store, None)
    assert rec["act"]["docId"] == "2026-55-00-00" and rec["act"]["label"] == "2026. évi LV. törvény"
    (law,) = rec["laws"]
    assert law["method"] == "njt" and law["base"]["version"] == 28 and law["target"]["version"] == 33
    assert [c["jh"] for c in law["changes"]] == ["SZ26@BE3"]
    assert rec["final"]
    # final: reused without even asking njt for its versions again
    calls = store.calls
    assert stage.build_record(_registry_record("kihirdetve", header), docs, store, rec) is rec
    assert store.calls == calls


def test_scanned_bill_is_recorded_as_such():
    docs = _Docs({"https://x/00438.pdf": {"scan": True, "lines": []}})
    rec = stage.build_record(_registry_record(), docs, _Store([], []), None)
    assert rec["scan"] and rec["laws"] == [] and rec["source"] is None


def test_candidates_put_pending_bills_first():
    reg = {"data": [
        {"billId": "a", "mainType": "T", "textUrl": "u", "status": "kihirdetve",
         "detail": {"header": {"promulgationDate": "2026-09-01"}}},
        {"billId": "b", "mainType": "T", "textUrl": "u", "status": "tárgysorozatban",
         "submittedDate": "2026-08-01"},
        {"billId": "c", "mainType": "T", "textUrl": "u", "status": "tárgysorozatban",
         "submittedDate": "2026-09-01"},
        {"billId": "d", "mainType": "H", "textUrl": "u", "status": "tárgysorozatban"},
        {"billId": "e", "mainType": "T", "textUrl": None, "status": "tárgysorozatban"},
    ]}
    assert [r["billId"] for r in stage.candidates(reg)] == ["c", "b", "a"]


def test_whole_law_repeal_is_one_law_level_change():
    lines = _lines(
        ("2026. évi ..... törvény", 244),
        ("1. §", 289),
        ("Hatályát veszti a fővárosi önkormányzat csődjének elkerülését szolgáló hitel", 57),
        ("nyújtásáról szóló 2025. évi CXXXIV. törvény.", 57),
    )
    (ins,) = novella.parse_bill(lines)["instructions"]
    assert ins["op"] == "repeal_law" and ins["doc"] == "2025-134-00-00"

    units = njt.parse(PAGE)["units"]
    docs = _Docs({"https://x/00438.pdf": {"scan": False, "lines": lines}})
    rec = stage.build_record(_registry_record(), docs, _Store(units, units), None)
    (law,) = rec["laws"]
    (change,) = law["changes"]
    assert change["ref"] == "a törvény egésze" and change["after"] == ""
    assert change["before"] == "2019. évi LXXX. törvény a szakképzésről"
    assert rec["unresolved"] == []


def test_a_new_subtitle_is_read_as_the_sections_it_brings():
    lines = _lines(
        ("2026. évi ..... törvény", 244),
        ("1. §", 289),
        ("A szakképzésről szóló 2019. évi LXXX. törvény (a továbbiakban: Szkt.) a 27. §-t", 57),
        ("követően a következő alcímmel egészül ki:", 57),
        ("„Átmeneti szabályok", 120),
        ("27/A. § Az átmenet szabályai.", 289),
        ("27/B. § A záró szabályok.”", 289),
    )
    (ins,) = novella.parse_bill(lines)["instructions"]
    assert ins["op"] == "insert" and ins["newrefs"] == ["SZ27A", "SZ27B"]
    pred, log = novella.apply(njt.parse(PAGE)["units"], [ins], "2019-80-00-00")
    assert log[0]["ok"]
    by = {u["jh"]: u for u in pred}
    assert by["SZ27A"]["text"].startswith("Átmeneti szabályok 27/A. §")
    assert by["SZ27B"]["text"] == "27/B. § A záró szabályok."


def test_an_annex_target_is_reported_as_an_annex():
    ins = {"at": "1. §", "op": "replace_text", "doc": "D", "refs": ["MEL3@PO25"],
           "part": None, "old": "x", "new": "y", "src": ""}
    _, log = novella.apply(njt.parse(PAGE)["units"], [ins], "D")
    assert log[0]["why"] == "melléklet"


def test_an_enumerated_item_quotes_only_itself():
    """An unresolved item of a long enumeration is shown as itself, with the law
    named — not as the whole enumeration again."""
    parsed = novella.parse_bill(BILL)
    a, b = [i for i in parsed["instructions"] if i["op"] == "replace_text"]
    assert a["src"].startswith("Az Szkt. a) 26. § (3)") and "b)" not in a["src"]
    assert b["src"].startswith("Az Szkt. b) 26. § (4)")


def test_bill_subtitles_and_self_references_are_not_instructions():
    lines = _lines(
        ("2026. évi ..... törvény", 244),
        ("1. §", 289),
        ("A szakképzésről szóló 2019. évi LXXX. törvény (a továbbiakban: Szkt.) 26. § (3)", 57),
        ("bekezdésében az „és a kancellár” szövegrész helyébe az „és az igazgató” szöveg", 57),
        ("lép.", 57),
        # a long subtitle is centred so wide it starts near the margin
        ("2. A nemzetközi bűnügyi jogsegélyről szóló 1996. évi XXXVIII. törvény módosítása", 86),
        ("2. §", 289),
        ("E törvény a 2. melléklet szerinti irányelvnek való megfelelést szolgálja.", 57),
    )
    parsed = novella.parse_bill(lines)
    first, second = parsed["instructions"]
    assert first["op"] == "replace_text" and first["new"] == "és az igazgató"
    assert "módosítása" not in first["src"]
    assert second["op"] == "none"
