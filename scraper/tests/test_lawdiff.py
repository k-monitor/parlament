"""Offline tests for the law-changes stage (BILL-14..16).

The fixtures are small but shaped on the real documents they stand for: njt
markup as njt.hu serves it (a footnote naming two acts, an untitled §, a
paragraph's closing text, a deferred block), bill lines as PyMuPDF reads the
T/438 PDF (the szakképzési törvény amendment, Act LV of 2026), and an amendment
PDF drawn with the Parlex template's markup — bold [deletions] and underlines.
"""

from __future__ import annotations

import pytest

from parlamonitor.lawdiff import motions, njt, novella, pdftext
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
    # replace: the point is the quoted text now, in its place and under its
    # label; the closing text is edited too
    assert by["SZ26@BE4@POA"]["label"] == "a)" and by["SZ26@BE4@POA"]["cls"] == "betusPontNyito"
    assert by["SZ26@BE4@POA"]["text"] == "részeként működő szakképző intézmények ellátásáért, valamint"
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


def test_changes_carry_what_the_full_law_view_needs():
    """Each change keeps its units as they would read — unless the applier
    rebuilt several of them from the bill's wording, which is one flat run of
    text with the labels inline — and a unit new to the law names the unit of
    the current text it follows."""
    units = njt.parse(PAGE)["units"]
    docs = _Docs({"https://x/00438.pdf": {"scan": False, "lines": BILL}})
    rec = stage.build_record(_registry_record(), docs, _Store(units, units), None)
    by = {c["jh"]: c for c in rec["laws"][0]["changes"]}
    # a word replaced in place: the paragraph as it would read, structured
    (para,) = by["SZ26@BE3"]["afterUnits"]
    assert para[:3] == ["SZ26@BE3", "bekezdesNyito", "(3)"] and "szakmai főigazgató-" in para[3]
    assert "anchor" not in by["SZ26@BE3"]
    # its point replaced with the bill's text: one unit, still in its place
    after = {u[0]: u for u in by["SZ26@BE4"]["afterUnits"]}
    assert after["SZ26@BE4@POA"][2:] == ["a)", "részeként működő szakképző intézmények "
                                         "ellátásáért, valamint"]
    # new paragraphs are rebuilt from the bill's wording: nothing structured
    assert "afterUnits" not in by["SZ26@BE4A"]
    # new paragraphs follow the last unit of the (4) they come after
    assert by["SZ26@BE4A"]["anchor"] == by["SZ26@BE4B"]["anchor"] == "SZ26@BE4@ZARO"
    # a repealed paragraph keeps its bare number
    assert by["SZ27@BE1"]["afterUnits"] == [["SZ27@BE1", "bekezdesNyito", "(1)", ""]]


def _cache_state(root, doc, version, page=PAGE, blocks=BLOCKS):
    import lzma
    d = root / doc
    d.mkdir(parents=True, exist_ok=True)
    (d / f"v{version}.html.xz").write_bytes(lzma.compress(page.encode("utf-8")))
    (d / f"v{version}.blocks.html.xz").write_bytes(lzma.compress(blocks.encode("utf-8")))


def test_law_texts_come_from_the_cache_and_are_rewritten_only_on_change(tmp_path):
    import gzip
    import json

    from parlamonitor.config import Paths

    paths = Paths(tmp_path)
    doc = "2019-80-00-00"
    _cache_state(paths.njt_dir(), doc, 33)
    reg = {"data": [{"laws": [{"docId": doc, "base": {"version": 33}}]},
                    {"laws": [{"docId": doc, "base": {"version": 33}},
                              # a state never fetched: skipped, never requested
                              {"docId": doc, "base": {"version": 34}}]}]}
    assert stage.save_law_texts(paths, 43, reg)
    with gzip.open(paths.law_texts_file(43), "rt", encoding="utf-8") as fh:
        meta, *states = [json.loads(line) for line in fh]
    assert meta["meta"]["cycle"] == 43
    (state,) = states
    assert (state["docId"], state["version"]) == (doc, 33)
    assert state["title"] == "2019. évi LXXX. törvény a szakképzésről"
    # the deferred block is in, labels and text apart
    assert ["SZ28@BE0", "bekezdesNyito", "", "Rövid szöveg."] in state["units"]
    assert ["SZ26@BE4@ZARO", "bekezdesZaro", "", "felel 2026. december 31-éig."] in state["units"]
    # nothing moved: not rewritten
    assert not stage.save_law_texts(paths, 43, reg)
    # the missing state turns up in the cache: rewritten with it
    _cache_state(paths.njt_dir(), doc, 34)
    assert stage.save_law_texts(paths, 43, reg)
    with gzip.open(paths.law_texts_file(43), "rt", encoding="utf-8") as fh:
        assert len(fh.readlines()) == 3


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
    # the subtitle is a heading of its own (njt's unnumbered "tagoló"), not
    # glued onto the first §
    assert by["SZ27A"]["text"] == "27/A. § Az átmenet szabályai."
    assert by["SZ27B"]["text"] == "27/B. § A záró szabályok."
    order = [u["jh"] for u in pred]
    assert order.index("TA~27A") == order.index("SZ27A") - 1
    assert by["TA~27A"]["cls"] == "tagoloCim" and by["TA~27A"]["text"] == "Átmeneti szabályok"
    ref = {c["jh"]: c["ref"] for c in novella.changes(njt.parse(PAGE)["units"], pred, log)}
    assert ref["TA~27A"] == "„Átmeneti szabályok” alcím"


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


def test_a_law_named_inside_another_acts_title_is_not_the_one_amended():
    """T/571 12. § repeals provisions of an amending act whose title names the
    law it amended; T/791 4. § amends such an act too. The act is the outer one
    — unless the next law is a second one of an enumeration (it has an article)."""
    lines = _lines(
        ("2026. évi ..... törvény", 244),
        ("1. §", 289),
        ("Hatályát veszti az egyes állami és önkormányzati ingatlanok ingyenes tulajdonba adásáról, valamint", 57),
        ("a hitelszerződésből eredő kötelezettségeiknek eleget tenni nem tudó természetes személyek", 57),
        ("lakhatásának biztosításáról szóló 2011. évi CLXX. törvény módosításáról szóló 2013. évi CXVIII.", 57),
        ("törvény", 57),
        ("a) 10. § (2) bekezdése,", 57),
        ("b) 10/A. §-a.", 57),
        ("2. §", 289),
        ("A sportról szóló 2004. évi I. törvény és egyéb törvények sporttal kapcsolatos módosításáról", 57),
        ("szóló 2025. évi CXXXVI. törvény 39. § (4) bekezdésében az „x” szövegrész helyébe az „y” szöveg lép.", 57),
        ("3. §", 289),
        ("Hatályát veszti a szakképzésről szóló 2019. évi LXXX. törvény és a felnőttképzésről szóló 2013. évi", 57),
        ("LXXVII. törvény.", 57),
    )
    a, b, c, d = novella.parse_bill(lines)["instructions"]
    assert (a["op"], a["doc"], a["refs"]) == ("repeal", "2013-118-00-00", ["SZ10@BE2"])
    assert (b["op"], b["doc"], b["refs"]) == ("repeal", "2013-118-00-00", ["SZ10A"])
    # the quoted item names the act it is about, not the law inside its title
    assert b["src"].endswith("2013. évi CXVIII. törvény b) 10/A. §-a")
    assert (c["op"], c["doc"], c["refs"]) == ("replace_text", "2025-136-00-00", ["SZ39@BE4"])
    assert d["doc"] == "2019-80-00-00"


ANNEXED = _lines(
    ("2026. évi ..... törvény", 244),
    ("1. §", 289),
    ("Nem lép hatályba az Alkotmánybírósággal összefüggő egyes törvények módosításáról szóló 2025.", 57),
    ("évi CXI. törvény 14. § a) pontja.", 57),
    ("1. melléklet a 2026. évi ..... törvényhez", 353),
    ("1. Az egyes ingatlanok fővárosi önkormányzat részére történő átadásáról szóló 2012. évi CXC. törvény 1.", 57),
    ("mellékletében foglalt táblázat a következő 14. sorral egészül ki:", 57),
    ("„", 57),
    ("14. 232045/5 Magyar Állam", 57),
    ("”", 57),
    ("2. Az általános forgalmi adóról szóló 2007. évi CXXVII. törvény 3/B. mellékletében foglalt táblázat", 57),
    ("a következő 2. sorral egészül ki:", 57),
    ("„2. Gyógyszerek”", 57),
)


def test_the_bills_own_annex_is_not_part_of_its_last_section():
    """T/571: the centred annex title was dropped as a heading, so the annex read
    as part of 27. § — one "ismeretlen szerkezet" for both. Each is its own now:
    the call-off of a provision, and an annex point per amended annex."""
    ins = novella.parse_bill(ANNEXED)["instructions"]
    assert [(i["at"], i["op"], i.get("why"), i["doc"]) for i in ins] == [
        ("1. §", "unparsed", "hatálybalépés elmarad", "2025-111-00-00"),
        ("1. melléklet 1. pont", "unparsed", "melléklet", "2012-190-00-00"),
        ("1. melléklet 2. pont", "unparsed", "melléklet", "2007-127-00-00")]
    assert "melléklet" not in ins[0]["src"]

    units = njt.parse(PAGE)["units"]
    docs = _Docs({"https://x/00438.pdf": {"scan": False, "lines": ANNEXED}})
    rec = stage.build_record(_registry_record(), docs, _Store(units, units), None)
    assert [(u["provision"], u["reason"]) for u in rec["unresolved"]] == [
        ("1. §", "hatálybalépés elmarad"), ("1. melléklet 1. pont", "melléklet"),
        ("1. melléklet 2. pont", "melléklet")]


# --- found by checking the applier against njt's own states (2026-10-15) --------------

def test_one_replacement_in_several_sections_reaches_every_one():
    """T/666 14. § g): fifteen provisions in one list. Each § started the
    reference over, so only the last one was kept (and the part of another)."""
    refs = novella.parse_refs(
        "348. § (3) bekezdésében, 350. § (4) bekezdésében, 356. § (1) bekezdés b) "
        "pontjában, 357. § (5) és (6) bekezdésében, 384. § (3) bekezdés nyitó "
        "szövegrészében és 419. § (3) bekezdésében")
    assert refs == [("SZ348@BE3", None), ("SZ350@BE4", None), ("SZ356@BE1@POB", None),
                    ("SZ357@BE5", None), ("SZ357@BE6", None), ("SZ384@BE3", "ny"),
                    ("SZ419@BE3", None)]
    # a later unit no deeper than the one before keeps what they share
    assert novella.parse_refs("26. § (2) bekezdés a) pontja és (3) bekezdése") == [
        ("SZ26@BE2@POA", None), ("SZ26@BE3", None)]
    (ins,) = novella.parse_instruction(
        "A Bv. tv. 26. § (3) bekezdésében és 27. § (1) bekezdésében az ⟦0⟧ szövegrész "
        "helyébe az ⟦1⟧ szöveg lép.", ["a", "b"], {"Bv. tv.": "D"}, "1. §")
    assert ins["refs"] == ["SZ26@BE3", "SZ27@BE1"] and "parts" not in ins


def test_more_reference_shapes():
    groups = novella.ref_groups
    assert novella.parse_ref("41. és 42. alcíme")[0] == ["AC41", "AC42"]
    assert novella.parse_ref("164. § (1) bekezdés zs) pont zsi) alpontjában")[0] == [
        "SZ164@BE1@POZS@APZSI"]
    assert novella.parse_ref("341. § (1) bekezdés 2. pont a) alpontjában")[0] == [
        "SZ341@BE1@PO2@APA"]
    assert novella.parse_ref("4. § 23/f. pontja")[0] == ["SZ4@PO23F"]
    assert novella.parse_ref("6:105. § (1) bekezdés c) pontjában")[0] == ["SZ6:105@BE1@POC"]
    assert novella.parse_ref("a következő 24–28. ponttal")[0] == [
        "PO24", "PO25", "PO26", "PO27", "PO28"]
    assert novella.parse_ref("(4a)–(4c) bekezdése")[0] == ["BE4A", "BE4B", "BE4C"]
    assert novella.parse_ref("XIII. Fejezet 47. alcíme")[0] == ["AC47"]
    assert novella.parse_ref("ÖTÖDIK Rész címében") == (["REÖTÖDIK"], "cim")
    assert novella.parse_ref("40/H. alcím címében") == (["AC40H"], "cim")
    assert novella.parse_ref("preambulumában")[0] == ["PR"]
    # a range is marked as one, so the applier can read the law between its ends
    (g,) = groups("7. § (26)–(29) bekezdése")
    assert g["range"] and g["word"] == "bekezdése"
    # a reference that only places another is not a target
    loc, title = groups("a 394. §-át követő ⟦3⟧ alcíme")
    assert loc["locator"] and not title["locator"] and title["refs"] == [("ACQ3", None)]
    # an annex's insides stay in the annex — not a point of the § before it,
    # nor the law's own chapter or preamble
    assert novella.parse_refs("33. § (2) bekezdés 61. pontja, és b) melléklete XXII. "
                              "fejezet 1. pontjának a) alpontja") == [
        ("SZ33@BE2@PO61", None), ("MEL@PO1@APA", None)]
    assert novella.parse_refs("3. melléklet I. Fejezet 4. pontjában") == [("MEL3@FEI@PO4", None)]
    assert novella.parse_refs("2. melléklet és 5. §-a") == [("MEL2", None), ("SZ5", None)]
    # words that name something else cut a list: no point of the wrong provision
    assert novella.parse_refs("33. § (2) bekezdés 61. pontja, és a 2014. évi törvény "
                              "1. pontja") == [("SZ33@BE2@PO61", None), ("PO1", None)]


def test_a_repeal_list_tells_whole_units_from_fragments_by_case():
    """T/359 2. §, T/123 4. §: a numbered list mixing whole units ("…
    bekezdése") and fragments in units ("… bekezdésében az „X” szövegrész"),
    items that only name a point of the provision the list hangs off, and two
    fragments of one unit."""
    tmpl = ("Hatályát veszti a Bt. 1. 5. § (1) bekezdés g) pontjában a ⟦0⟧ szövegrész, "
            "2. 8. § (2) bekezdése, 3. 14. §-ában az ⟦1⟧ és a ⟦2⟧ szövegrész, "
            "4. 22. § (6a)–(6c) bekezdése.")
    ins = novella.parse_instruction(tmpl, ["x", "y", "z"], {"Bt.": "D"}, "2. §")
    assert [(i["at"], i["op"], i["refs"], i.get("old")) for i in ins] == [
        ("2. § 1.", "delete_text", ["SZ5@BE1@POG"], "x"),
        ("2. § 2.", "repeal", ["SZ8@BE2"], None),
        ("2. § 3.", "delete_text", ["SZ14"], "y"),
        ("2. § 3.", "delete_text", ["SZ14"], "z"),
        ("2. § 4.", "repeal", ["SZ22@BE6A", "SZ22@BE6B", "SZ22@BE6C"], None)]
    tmpl = ("Hatályát veszti a Ksztv. 1. § (4) bekezdés a) f) pontjában az ⟦0⟧ szövegrész, "
            "és b) g) pontja.")
    a, b = novella.parse_instruction(tmpl, [", és"], {"Ksztv.": "D"}, "4. §")
    assert (a["at"], a["op"], a["refs"]) == ("4. § a)", "delete_text", ["SZ1@BE4@POF"])
    assert (b["at"], b["op"], b["refs"]) == ("4. § b)", "repeal", ["SZ1@BE4@POG"])
    # a subtitle placed by the § it follows: the § itself is not repealed
    (c,) = novella.parse_instruction(
        "Hatályát veszti a Btk. 394. §-át követő ⟦0⟧ alcíme.", ["Kriptoeszközzel visszaélés"],
        {"Btk.": "D"}, "1. §")
    assert c["op"] == "repeal" and c["refs"] == ["AC=Kriptoeszközzel visszaélés"]


def test_mistyped_quote_marks_do_not_swallow_the_rest_of_the_bill():
    """T/359, T/173: a closing mark typed as an opening one („X„ szövegrész) or
    as a straight one ("X" szöveg), and an opening one typed as two commas."""
    lines = _lines(
        ("2026. évi ..... törvény", 244),
        ("1. §", 289),
        ("Hatályát veszti a bányászatról szóló 1993. évi XLVIII. törvény (a továbbiakban: Bt.)", 57),
        ("1. 14. §-ában a „– a munkaprogram alapján – „ szövegrész,", 58),
        ("2. 15. § (2) bekezdésében a ,,, vagy a védőidom” szövegrész.", 57),
        ("2. §", 289),
        ("A Bt. 16. §-ában az „érvénytelenítésének lehetőségéről\" szöveg és az „x” szövegrész", 57),
        ("helyébe az „y” szöveg lép.", 57),
        ("3. §", 289),
        ("Hatályát veszti a Bt. 17. §-a.", 57),
    )
    ins = novella.parse_bill(lines)["instructions"]
    assert [(i["at"], i["op"], i.get("refs"), i.get("old")) for i in ins] == [
        ("1. § 1.", "delete_text", ["SZ14"], "– a munkaprogram alapján –"),
        ("1. § 2.", "delete_text", ["SZ15@BE2"], ", vagy a védőidom"),
        ("2. §", "replace_text", ["SZ16"], "x"),
        ("3. §", "repeal", ["SZ17"], None)]


def test_a_long_subtitle_wrapped_over_several_lines_is_a_heading():
    """T/341: "24. A bűnügyi nyilvántartási rendszerről, … törvény módosítása"
    over three lines (one of them at the margin) was glued to the provision
    before — which turned a whole-law repeal into an unreadable one."""
    lines = _lines(
        ("2026. évi ..... törvény", 244),
        ("1. §", 289),
        ("Hatályát veszti a Jövő Nemzedék Földje Alapítványról szóló 2021. évi XV. törvény.", 57),
        ("35. A Magyar Kultúráért Alapítvány létrehozásáról, valamint a Magyar Kultúráért", 73),
        ("Alapítvány részére történő vagyonjuttatásról szóló 2021. évi XVI. törvény", 58),
        ("hatályon kívül helyezése", 236),
        ("2. §", 283),
        ("Hatályát veszti a Magyar Kultúráért Alapítvány létrehozásáról szóló 2021. évi XVI. törvény.", 57),
    )
    a, b = novella.parse_bill(lines)["instructions"]
    assert (a["op"], a["doc"]) == ("repeal_law", "2021-15-00-00")
    assert "Kultúráért" not in a["src"]
    assert (b["op"], b["doc"]) == ("repeal_law", "2021-16-00-00")


def test_replace_and_insert_in_one_sentence():
    """T/173 4.–5. §: "… f) pontja helyébe a következő rendelkezés lép és e
    bekezdés a következő g) ponttal egészül ki" — the g) was glued into f)."""
    tmpl = ("A Tpt. 400. § (2) bekezdés f) pontja helyébe a következő rendelkezés lép és e "
            "bekezdés a következő g) ponttal egészül ki: ⟦0⟧")
    rep, ins = novella.parse_instruction(tmpl, ["f) új f.\ng) új g."], {"Tpt.": "D"}, "4. §")
    assert (rep["op"], rep["refs"], rep["new"]) == ("replace", ["SZ400@BE2@POF"], "f) új f.\n")
    assert (ins["op"], ins["parent"], ins["newrefs"], ins["new"]) == (
        "insert", "SZ400@BE2", ["POG"], "g) új g.")


def test_a_new_subtitle_brings_its_numbered_heading():
    tmpl = "A Púétv. VII. Fejezete a következő alcímmel egészül ki: ⟦0⟧"
    quote = "22/A. A még elégséges szolgáltatás\n154/A. § Első.\n154/B. § Második."
    (ins,) = novella.parse_instruction(tmpl, [quote], {"Púétv.": "D"}, "16. §")
    assert ins["newrefs"] == ["SZ154A", "SZ154B"]
    assert ins["heading"] == [["AC22A", "alcim", "22/A.", "A még elégséges szolgáltatás"]]
    units = _sec("154", "Régi.") + _sec("155", "Más.")
    after, log = novella.apply(units, [ins], "D")
    assert [u["jh"] for u in after if "@" not in u["jh"]] == [
        "SZ154", "AC22A", "SZ154A", "SZ154B", "SZ155"]
    by = {c["jh"]: c for c in novella.changes(units, after, log)}
    assert by["SZ154A"]["after"] == "154/A. § Első."          # no heading glued on
    assert by["AC22A"]["after"] == "22/A. A még elégséges szolgáltatás"


def test_whole_subtitles_and_ranges_reach_what_the_law_has_between():
    """T/341 80. § k): "43-48. alcíme" is every subtitle from 43 to 48 — 47/A
    included — with every § under them; T/387: "(26)–(29) bekezdése" takes in
    the (26a) inserted since."""
    head = lambda jh, lab, t: {"jh": jh, "cls": "alcim", "label": lab, "text": t, "fn": []}  # noqa: E731
    units = ([head("AC43", "43.", "Egy")] + _sec("100", "a") + [head("AC47A", "47/A.", "Köz")]
             + _sec("101", "b") + [head("AC48", "48.", "Utolsó")] + _sec("102", "c")
             + [head("AC49", "49.", "Marad")] + _sec("103", "d"))
    (ins,) = novella.parse_instruction("Hatályát veszti az Ogytv. 43–48. alcíme.", [],
                                       {"Ogytv.": "D"}, "80. §")
    assert ins["ranges"] == [[0, 5]]
    after, log = novella.apply(units, [ins], "D")
    assert log[0]["ok"]
    gone = {c["jh"] for c in novella.changes(units, after, log) if not c["after"]}
    assert gone == {"AC43", "SZ100", "AC47A", "SZ101", "AC48", "SZ102"}
    units = _sec("7") + [{"jh": f"SZ7@BE{k}", "cls": "bekezdesNyito", "label": f"({k.lower()})",
                          "text": "x", "fn": []} for k in ("26", "26A", "27", "28", "29", "30")]
    (ins,) = novella.parse_instruction("Hatályát veszti a Tao. 7. § (26)–(29) bekezdése.", [],
                                       {"Tao.": "D"}, "13. §")
    after, log = novella.apply(units, [ins], "D")
    assert {c["jh"] for c in novella.changes(units, after, log)} == {
        "SZ7@BE26", "SZ7@BE26A", "SZ7@BE27", "SZ7@BE28", "SZ7@BE29"}


def test_titles_of_chapters_and_subtitles():
    """"II. Fejezet címében …" is in the chapter's title (njt's next unit), not
    a repeal of the chapter; a subtitle is found by its quoted title."""
    units = [{"jh": "FEII", "cls": "fejezet", "label": "", "text": "II. Fejezet", "fn": []},
             {"jh": "FEII@CIM", "cls": "fejezetCim", "label": "", "text": "A RENDŐRSÉG ÉS AZ ŐRSÉG",
              "fn": []},
             {"jh": "AC13", "cls": "alcim", "label": "13.", "text": "A terrorelhárítás", "fn": []}
             ] + _sec("7/E", "Szöveg.")
    a, = novella.parse_instruction("Hatályát veszti az Rtv. II. Fejezet címében az ⟦0⟧ szövegrész.",
                                   [" ÉS AZ ŐRSÉG"], {"Rtv.": "D"}, "1. §")
    b, = novella.parse_instruction("Hatályát veszti az Rtv. ⟦0⟧ alcíme.", ["A terrorelhárítás"],
                                   {"Rtv.": "D"}, "2. §")
    assert (a["op"], a["refs"], a["part"]) == ("delete_text", ["FEII"], "cim")
    after, log = novella.apply(units, [a, b], "D")
    assert [r["ok"] for r in log] == [True, True]
    by = {u["jh"]: u for u in after}
    assert by["FEII@CIM"]["text"] == "A RENDŐRSÉG" and by["FEII"]["text"] == "II. Fejezet"
    assert by["AC13"]["text"] == "" and by["SZ7E@BE1"]["text"] == ""


def test_a_unit_njt_files_under_another_id_is_found_by_its_label():
    """njt's ids do not always follow the labels: "cb)" filed as APBB."""
    units = _sec("97", "x") + [
        {"jh": "SZ97@BE1@POC", "cls": "betusPontNyito", "label": "c)", "text": "pont", "fn": []},
        {"jh": "SZ97@BE1@POC@APAA", "cls": "ketbetusAlPont", "label": "ca)", "text": "egy", "fn": []},
        {"jh": "SZ97@BE1@POC@APBB", "cls": "ketbetusAlPont", "label": "cb)", "text": "kettő", "fn": []}]
    assert novella.resolve(units, "SZ97@BE1@POC@APCB") == "SZ97@BE1@POC@APBB"
    assert novella.resolve(units, "SZ97@BE1@POC@APCC") is None


NJT_QUIRKS = """<html><body><main>
<span class="jhId" id="FEI"></span><div id="sc1" class="fejezet">I. Fejezet</div>
<div id="sc2" class="fejezetCim"><p>ÁLTALÁNOS RENDELKEZÉSEK</p></div>
<span class="jhId" id="SZ1"></span><div id="sc3" class="szakasz"><p><span class="szakasz-jel">1. §</span></p></div>
<span class="jhId" id="SZ1@PO1"></span><div id="sc4" class="szelet"><span class="jel">1.</span> Fogalom: egy.</div>
<span class="jhId" id="SZ4"></span><div id="sc5" class="szakasz"><p><span class="szakasz-jel">4. §</span></p></div>
<span class="jhId" id="47"></span><div id="sc6" class="bekezdesNyito"><p>E törvény alkalmazásában</p></div>
<span class="jhId" id="SZ4@BE0@POF"></span><div id="sc7" class="betusPontNyito"><p><span class="jel">f)</span> közszolgáltatás:</p></div>
<span class="jhId" id="SZ4@BE0@POF@APFA"></span><div id="sc8" class="ketbetusAlPont"><p><span class="jel">fa)</span> első,</p></div>
<span class="jhId" id="SZ4@POF@APFB"></span><div id="sc9" class="ketbetusAlPont"><p><span class="jel">fb)</span> második.</p></div>
<span class="jhId" id="SZ5@BE1@PO2"></span><div id="sc10" class="szamosPontNyito"><p><span class="jel">2.</span> megállapítsa</p></div>
<span class="jhId" id="SZ5@BE1@PO2@APA"></span><div id="sc11" class="betusAlPont"><p><span class="jel">a)</span> a beosztásokat,</p></div>
<span class="jhId" id="SZ5@BE1@PO2@APZSI)"></span><div id="sc12" class="betusAlPont"><p><span class="jel">zsi)</span> a többit.</p></div>
<span class="jhId" id="TA"></span><div id="sc2000-1-00-00-14105" class="tagoloCim"><p><i>Etikai szabályok</i></p></div>
<span class="jhId" id="RR12"></span><div id="sc13" class="szelet"><div>Melléklet sora</div></div>
</main></body></html>"""


def test_njt_markup_quirks():
    """What njt does now and then: a § body with an internal number for an id,
    the points of one § with and without ``@BE0``, sub-points of a numbered
    point (``betusAlPont``), provisions set as ``szelet``, a chapter title with
    no id, a stray bracket in an id."""
    units = njt.parse(NJT_QUIRKS)["units"]
    by = {u["jh"]: u for u in units}
    assert by["FEI@CIM"]["text"] == "ÁLTALÁNOS RENDELKEZÉSEK"
    assert by["SZ1@PO1"]["cls"] == "szamosPontNyito" and by["SZ1@PO1"]["text"] == "Fogalom: egy."
    assert by["SZ4@BE0"]["text"] == "E törvény alkalmazásában" and by["SZ4@BE0"]["anchor"] == "47"
    assert {"SZ4@POF", "SZ4@POF@APFA", "SZ4@POF@APFB"} <= set(by)
    assert by["SZ4@POF"]["anchor"] == "SZ4@BE0@POF"
    assert njt.flatten(units, "SZ4") == ("4. § E törvény alkalmazásában f) közszolgáltatás: "
                                         "fa) első, fb) második.")
    assert by["SZ5@BE1@PO2@APA"]["text"] == "a beosztásokat,"
    assert "SZ5@BE1@PO2@APZSI" in by
    # an unnumbered subtitle, and one njt left with a bare "TA" id
    assert by["TA14105"]["cls"] == "tagoloCim" and by["TA14105"]["text"] == "Etikai szabályok"
    assert not any("Melléklet" in u["text"] for u in units)
    # the opening text of a § without paragraphs is its body, not its title
    ins = {"at": "1. §", "op": "replace_text", "doc": "D", "refs": ["SZ4"], "part": "ny",
           "old": "alkalmazásában", "new": "alkalmazásában:", "src": ""}
    after, log = novella.apply(units, [ins], "D")
    assert log[0]["ok"] and {u["jh"]: u for u in after}["SZ4@BE0"]["text"].endswith(":")


def test_one_sentences_text_edits_are_made_at_once():
    """T/405 153. § 20.: "főispán" → "kormánymegbízott", "főispánt" →
    "kormánymegbízottat" — made one after the other, the first turned
    "főispánt" into "kormánymegbízottt" and the second found nothing; T/571
    16. § c) swaps two words, which made them one."""
    units = _sec("89/A", "a főispán értesíti a főispánt és a főispánok döntenek")
    tmpl = ("A Kit. 89/A. § (1) bekezdésében a ⟦0⟧ szövegrészek helyébe a ⟦1⟧ szöveg, a ⟦2⟧ "
            "szövegrészek helyébe a ⟦3⟧ szöveg és a ⟦4⟧ szövegrész helyébe a ⟦5⟧ szöveg lép.")
    quotes = ["főispán", "kormánymegbízott", "főispánt", "kormánymegbízottat",
              "főispánok", "kormánymegbízottak"]
    ins = novella.parse_instruction(tmpl, quotes, {"Kit.": "D"}, "153. §")
    after, log = novella.apply(units, ins, "D")
    assert all(r["ok"] for r in log)
    assert {u["jh"]: u for u in after}["SZ89A@BE1"]["text"] == (
        "a kormánymegbízott értesíti a kormánymegbízottat és a kormánymegbízottak döntenek")
    units = _sec("2/A", "a vagyonkezelőnek vagy az építtetőnek")
    tmpl = ("A Vtv. 2/A. § (1) bekezdésében a ⟦0⟧ szövegrész helyébe az ⟦1⟧ szöveg és az ⟦1⟧ "
            "szövegrész helyébe a ⟦0⟧ szöveg lép.")
    ins = novella.parse_instruction(tmpl, ["vagyonkezelőnek", "építtetőnek"], {"Vtv.": "D"}, "16. §")
    after, _ = novella.apply(units, ins, "D")
    assert {u["jh"]: u for u in after}["SZ2A@BE1"]["text"] == "a építtetőnek vagy az vagyonkezelőnek"


def _evo_lines(*sections):
    rows = [("2026. évi ..... törvény", 244)]
    for n, text in sections:
        rows += [(f"{n}. §", 289), (text, 57)]
    return {"lines": _lines(*rows)}


def test_the_bills_evolution_pairs_sections_by_what_they_say():
    """A § the egységes javaslat inserts renumbers every one after it; by number,
    each would be set against its neighbour (T/341: 172 "changed" of 174)."""
    main = _evo_lines((1, "Az Szkt. 5. §-a hatályát veszti."),
                      (2, "Az Szkt. 7. §-a hatályát veszti."),
                      (3, "Ez a törvény a kihirdetését követő napon lép hatályba."))
    egys = _evo_lines((1, "Az Szkt. 5. §-a hatályát veszti."),
                      (2, "Az Szkt. 6. §-a helyébe a következő rendelkezés lép."),
                      (3, "Az Szkt. 7. §-a és 8. §-a hatályát veszti."),
                      (4, "Ez a törvény a kihirdetését követő napon lép hatályba."))
    ev = stage._evolution(main, egys, {"billNumber": "T/1", "textUrl": "u"},
                          {"billNumber": "T/1/9", "textUrl": "v", "type": "Egységes javaslat"})
    assert ev["unchanged"] == 3     # the title, 1. § and the old 3. § (now 4. §)
    assert [(p["key"], p.get("from"), bool(p["before"]), bool(p["after"]))
            for p in ev["provisions"]] == [("2. §", None, False, True),
                                           ("3. §", "2. §", True, True)]


def test_a_tables_rules_are_not_underlines():
    """A nyelvhelyességi módosítás is a table of corrections; its rules ran under
    every cell, so every word read as inserted. A table's rules meet its
    vertical ones; an insertion's underline stands alone."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    font = pymupdf.Font("tiro")

    def write(x, y, text):
        tw = pymupdf.TextWriter(page.rect)
        tw.append((x, y), text, font=font, fontsize=12)
        tw.write_text(page)
        return x + font.text_length(text, fontsize=12)

    write(57, 100, "Módosítópont sorszáma: 1.")
    write(57, 116, "Módosítás jellege: nyelvhelyességi és szerkesztési módosítás")
    for y0, cells in ((140, ("Sorszám", "Korrektúra")), (158, ("1.", "legalább "))):
        for x0, x1, text in ((57, 114, cells[0]), (114, 538, cells[1])):
            for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y0 + 18)),
                         ((x1, y0 + 18), (x0, y0 + 18)), ((x0, y0 + 18), (x0, y0))):
                page.draw_line(a, b, width=0.75)
            end = write(x0 + 3, y0 + 14, text)
        if cells[0] == "1.":
            x = write(end, y0 + 14, "10%-a")
            page.draw_line((end, y0 + 16), (x, y0 + 16), width=0.7)   # a real insertion
    data = doc.tobytes()
    doc.close()
    (point,) = motions.parse(pdftext.marked_lines(data))["points"]
    assert [t for op, t in point["segments"] if op == "ins"] == ["10%-a"]


def test_subtitles_between_the_sections_a_new_chapter_brings():
    """T/503 7. §: a new chapter's second subtitle ("155/B. Különös szabályok")
    stood at the end of the § before it; a wrapped line that starts with a
    reference ("79. § rendelkezéseit …") is not a new §."""
    tmpl = "A Pp. a következő XLV/A. Fejezettel egészül ki: ⟦0⟧"
    quote = ("XLV/A. Fejezet\nKÜLFÖLDI ELEM\n603/A. § (1) Első szabály, amelyre a\n"
             "79. § rendelkezéseit kell alkalmazni.\n155/B. Különös szabályok\n"
             "603/B. § Második szabály.\nAz eljárás vége\n603/C. § Harmadik.")
    (ins,) = novella.parse_instruction(tmpl, [quote], {"Pp.": "D"}, "7. §")
    assert ins["newrefs"] == ["SZ603A", "SZ603B", "SZ603C"]
    units = _sec("603", "Régi.") + _sec("604", "Más.")
    after, log = novella.apply(units, [ins], "D")
    assert [(u["jh"], u["cls"]) for u in after if "@" not in u["jh"] or "CIM" in u["jh"]][1:-1] == [
        ("FEXLVA", "fejezet"), ("FEXLVA@CIM", "fejezetCim"), ("SZ603A", "new"),
        ("AC155B", "alcim"), ("SZ603B", "new"), ("TA~603C", "tagoloCim"), ("SZ603C", "new")]
    by = {u["jh"]: u for u in after}
    assert by["SZ603A"]["text"].endswith("79. § rendelkezéseit kell alkalmazni.")
    assert by["AC155B"]["text"] == "Különös szabályok" and by["TA~603C"]["text"] == "Az eljárás vége"


def test_the_evaluation_compares_the_applier_with_what_njt_attributes():
    """``evaluate.compare`` — the check behind the accuracy the docs quote: a
    provision njt attributes to the act and the applier got right is exact; one
    it attributes and the applier did not touch is missed."""
    from parlamonitor.lawdiff import evaluate

    after = njt.parse(PAGE)["units"]            # 26. § (3): footnote names 2026. évi LV.
    before = [dict(u) for u in after]
    for u in before:
        if u["jh"] == "SZ26@BE3":
            u["text"] = "A szakképzési centrumot a főigazgató vezeti."
    truth = novella.attributed_changes(before, after, "2026-55-00-00")
    assert [c["jh"] for c in truth] == ["SZ26@BE3"]
    ins = {"at": "1. §", "op": "replace_text", "doc": "D", "refs": ["SZ26@BE3"], "part": None,
           "old": "a főigazgató vezeti", "new": "a főigazgató és a kancellár önállóan vezeti",
           "src": ""}
    pred, log = novella.apply(before, [ins], "D")
    rows = evaluate.compare(before, after, pred, novella.changes(before, pred, log), truth,
                            "2026-55-00-00")
    assert [(r["jh"], r["kind"]) for r in rows] == [("SZ26@BE3", "exact")]
    rows = evaluate.compare(before, after, before, [], truth, "2026-55-00-00")
    assert [(r["jh"], r["kind"]) for r in rows] == [("SZ26@BE3", "missed")]


def test_each_fragment_after_a_provision_is_repealed():
    """T/341 19. § 26.: "… h) pontjában az „X” szövegrész és az „Y” szövegrész" —
    each fragment has its own "szövegrész"; the second was dropped silently."""
    ins = novella.parse_instruction(
        "Hatályát veszti az Rtv. 100. § (1) bekezdés h) pontjában az ⟦0⟧ szövegrész és az ⟦1⟧ "
        "szövegrész, valamint 101. § (4) bekezdése.", ["X,", "és Y"], {"Rtv.": "D"}, "19. §")
    assert [(i["op"], i["refs"], i.get("old")) for i in ins] == [
        ("delete_text", ["SZ100@BE1@POH"], "X,"), ("delete_text", ["SZ100@BE1@POH"], "és Y"),
        ("repeal", ["SZ101@BE4"], None)]


def test_subtitles_named_without_quotes_or_by_place_and_quotes_without_article():
    """T/387 13. § (1) 8.: "Növekedési adóhitel alcíme" (its title, unquoted);
    28. § 4.: "42/B. §-t megelőző alcím címében" (no title at all); T/669 6. §
    a): "h) alpontjában „X” szövegrész helyébe" (no article) — each was dropped."""
    a, b, c = novella.parse_instruction(
        "Hatályát veszti a Tao. 1. 6. § (13) bekezdésében az ⟦0⟧ szövegrész, 2. Növekedési "
        "adóhitel alcíme, 3. 42/B. §-t megelőző alcím címében az ⟦1⟧ szövegrész.",
        ["x", "és települési adók"], {"Tao.": "D"}, "13. §")
    assert (b["op"], b["refs"]) == ("repeal", ["AC=Növekedési adóhitel"])
    assert (c["op"], c["refs"], c["part"]) == ("delete_text", ["AC<SZ42B"], "cim")
    head = lambda jh, t: {"jh": jh, "cls": "tagoloCim", "label": "", "text": t, "fn": []}  # noqa: E731
    units = ([head("TA1", "Növekedési adóhitel")] + _sec("42/A", "a") + [head("TA2", "Helyi adók és települési adók")]
             + _sec("42/B", "b"))
    after, log = novella.apply(units, [b, c], "D")
    assert [r["ok"] for r in log] == [True, True]
    by = {u["jh"]: u for u in after}
    assert by["TA1"]["text"] == "" and by["SZ42A@BE1"]["text"] == ""      # the subtitle and its §
    assert by["TA2"]["text"] == "Helyi adók" and by["SZ42B@BE1"]["text"] == "b"
    (d,) = novella.parse_instruction(
        "Az Áht. 1. § 14. pont h) alpontjában ⟦0⟧ szövegrész helyébe az ⟦1⟧ szöveg lép.",
        ["régi", "új"], {"Áht.": "D"}, "6. §")
    assert (d["op"], d["refs"], d["old"]) == ("replace_text", ["SZ1@PO14@APH"], "régi")


# --- njt regenerates future states in place -----------------------------------------

def _state_page(text: str) -> str:
    return ('<html><head><meta name="description" content="2000. évi I. törvény"/></head><body>'
            '<span class="jhId" id="SZ1"></span><div id="sc1" class="szakasz"><p>'
            '<span class="szakasz-jel">1. §</span></p></div>'
            '<span class="jhId" id="SZ1@BE1"></span><div id="sc2" class="bekezdesNyito"><p>'
            f'<span class="jel">(1)</span> {text}</p></div></body></html>')


class _NjtHttp:
    """njt as the store sees it: ``pages[(doc, version)]`` is what it serves now."""

    def __init__(self):
        self.pages: dict = {}
        self.gets = 0

    def get_text(self, url):
        self.gets += 1
        doc, ver = url.rsplit("/", 1)[1].rsplit(".", 1)
        return self.pages[(doc, int(ver))]

    def polite_sleep(self):
        pass


def _list_versions(root, doc, versions):
    import json
    import time
    d = root / doc
    d.mkdir(parents=True, exist_ok=True)
    (d / "versions.json").write_text(json.dumps({"fetchedAt": time.time(), "data": versions}))


def test_a_future_state_is_fetched_again_once_the_law_gains_a_state(tmp_path):
    doc = "2000-1-00-00"
    now = {"version": 1, "comingIntoForce": "2020-01-01T00:00:00+01:00", "expiresOn": "2099-01-01"}
    later = {"version": 2, "comingIntoForce": "2099-01-01T00:00:00+01:00", "expiresOn": None}
    http = _NjtHttp()
    http.pages = {(doc, 1): _state_page("Régi."), (doc, 2): _state_page("Jövőbeli.")}
    _list_versions(tmp_path, doc, [now, later])
    store = lambda: njt.NjtStore(http, tmp_path, budget=njt.Budget(None))  # noqa: E731
    text = lambda law: {u["jh"]: u["text"] for u in law["units"]}["SZ1@BE1"]  # noqa: E731
    assert text(store().law(doc, 1)) == "Régi." and text(store().law(doc, 2)) == "Jövőbeli."
    assert http.gets == 2
    # nothing new on njt: both come from the cache
    store().law(doc, 1), store().law(doc, 2)
    assert http.gets == 2
    # an amendment is promulgated: a new state, and njt rewrites the future one
    # under its old number — the state in force when fetched stays as it was
    http.pages[(doc, 2)] = _state_page("Jövőbeli, módosítva.")
    _list_versions(tmp_path, doc, [now, later, {"version": 3, "comingIntoForce": "2098-01-01",
                                                "expiresOn": "2099-01-01"}])
    assert store().stale(doc, 2) and not store().stale(doc, 1)
    assert text(store().law(doc, 2)) == "Jövőbeli, módosítva."
    assert text(store().law(doc, 1)) == "Régi." and http.gets == 3
    assert not store().stale(doc, 2)
    # offline (the law-texts export, the evaluation) never fetches
    _list_versions(tmp_path, doc, [now, later, {"version": 4, "comingIntoForce": "2098-06-01"}])
    assert text(store().law(doc, 2, offline=True)) == "Jövőbeli, módosítva." and http.gets == 3


def test_a_future_state_cached_before_it_was_tracked_is_fetched_once(tmp_path):
    import lzma
    doc = "2000-1-00-00"
    _list_versions(tmp_path, doc, [{"version": 2, "comingIntoForce": "2099-01-01T00:00:00+01:00"}])
    (tmp_path / doc / "v2.html.xz").write_bytes(lzma.compress(_state_page("Régi.").encode()))
    http = _NjtHttp()
    http.pages = {(doc, 2): _state_page("Friss.")}
    law = njt.NjtStore(http, tmp_path, budget=njt.Budget(None)).law(doc, 2)
    assert {u["jh"]: u["text"] for u in law["units"]}["SZ1@BE1"] == "Friss." and http.gets == 1
    njt.NjtStore(http, tmp_path, budget=njt.Budget(None)).law(doc, 2)
    assert http.gets == 1


def test_a_bills_fingerprint_moves_when_an_amended_law_gains_a_state(tmp_path):
    doc = "2019-80-00-00"
    _list_versions(tmp_path, doc, [{"version": 33, "comingIntoForce": "2026-01-01"}])
    store = njt.NjtStore(None, tmp_path, budget=njt.Budget(None))
    parsed = {"instructions": [{"doc": doc, "op": "replace_text"}]}
    (p,) = stage._plan(store, parsed, None)
    assert p["ids"] == [33]
    _list_versions(tmp_path, doc, [{"version": 33, "comingIntoForce": "2026-01-01"},
                                   {"version": 34, "comingIntoForce": "2027-01-01"}])
    (p,) = stage._plan(store, parsed, None)
    assert p["ids"] == [33, 34]
