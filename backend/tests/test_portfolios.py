"""Portfolios module (§6C) — the tárca as a dimension of the corpus.

The module invents no data: it collates the four free-text office labels the
other modules already store into one entity per tárca. So the tests are mostly
about the collation being right and staying honest — that the four surface forms
of one ministry land on one portfolio, that a label the table doesn't know is
still visible rather than silently dropped, and that the derived tables survive a
re-ingest of the module they read from.
"""

from __future__ import annotations

import sqlite3

from app import loader, portfolios
from tests.test_og import og_client  # noqa: F401  (fixture)
from tests.test_seo import seo_client  # noqa: F401  (fixture)


# --------------------------------------------------------------------------
# The resolution table itself (MIN-3)
# --------------------------------------------------------------------------

def test_the_surface_forms_of_one_ministry_collate_to_one_portfolio():
    """The whole point of the module: a reader should not have to recognise
    "Belügyminisztérium államtitkára", "belügyminiszter" and "kormány
    (belügyminiszter)" as the same tárca by eye."""
    slugs = {portfolios.resolve(label).slug for label in (
        "Belügyminisztérium államtitkára",     # answer event / office registry
        "belügyminiszter",                     # answer event / speech office
        "kormány (belügyminiszter)",           # government submitter
        "belügyminisztériumi politikai államtitkár",   # the older adjectival form
    )}
    assert len(slugs) == 1
    assert portfolios.resolve("belügyminiszter").name == "Belügyminisztérium"


def test_matching_is_accent_and_case_insensitive():
    """Same rule as every other text match on the site (§4B FOLD-3) — the corpus
    writes a tárca capitalised as an institution and lowercased as a title."""
    assert (portfolios.resolve("BELUGYMINISZTERIUM ALLAMTITKARA")
            == portfolios.resolve("Belügyminisztérium államtitkára"))


def test_a_remit_that_no_rule_derives_still_resolves():
    """Hungarian morphology gets from `belügyminiszter` to `Belügyminisztérium`
    and then fails: the ITM's minister is the "innovációért és technológiáért
    felelős miniszter". This is exactly why the mapping is a table."""
    assert (portfolios.resolve("innovációért és technológiáért felelős miniszter").slug
            == portfolios.resolve("Innovációs és Technológiai Minisztérium államtitkára").slug)


def test_renames_are_not_merged():
    """A ministry renamed is a different entry, because the succession is a claim
    the source does not make (MIN-9)."""
    assert (portfolios.resolve("nemzeti erőforrás miniszter").slug
            != portfolios.resolve("emberi erőforrások minisztere").slug)
    assert (portfolios.resolve("külügyminiszter").slug
            != portfolios.resolve("külgazdasági és külügyminiszter").slug)


def test_a_personal_commission_is_excluded_not_unmapped():
    """A miniszterelnöki biztos is one person's brief, attached to no tárca the
    source names — a decision, and distinguishable from a gap in the table."""
    assert portfolios.resolve("miniszterelnöki biztos") is None
    assert portfolios.is_excluded("miniszterelnöki biztos") is True
    assert portfolios.standalone("miniszterelnöki biztos") is None


def test_an_unknown_label_stands_alone_rather_than_vanishing():
    """A tárca the table hasn't got yet must stay visible under its own name — a
    gap to fix, never folded into a neighbour and never dropped (MIN-3)."""
    assert portfolios.resolve("holnaputáni ügyekért felelős miniszter") is None
    assert portfolios.is_excluded("holnaputáni ügyekért felelős miniszter") is False
    solo = portfolios.standalone("holnaputáni ügyekért felelős miniszter")
    assert solo is not None
    assert solo.name == "Holnaputáni ügyekért felelős miniszter"


def test_bodies_that_are_not_ministries_keep_their_own_kind():
    """The House also asks the prosecutor general and the MNB; they belong in the
    listing, but not among the ministries."""
    assert portfolios.resolve("legfőbb ügyész").kind == "body"
    assert portfolios.resolve("belügyminiszter").kind == "ministry"


# --------------------------------------------------------------------------
# What the loader derives (MIN-2)
# --------------------------------------------------------------------------

def _answered_question(bill_id="q-1", number="I/9", label="belügyminiszter",
                       submitted="2026-06-01T09:00:00Z", answered="2026-06-10T09:00:00Z",
                       title="Interpelláció", qtype="interpelláció", main_type="I",
                       event="interpelláció szóban megválaszolva"):
    return {"billId": bill_id, "billNumber": number, "billNumberSort": 9,
            "title": title, "type": qtype, "mainType": main_type,
            "status": "benyújtva", "submittedDate": submitted,
            "sponsors": [{"personID": "k001", "factionId": 7, "committeeId": None,
                          "label": "Kovács Béla"}],
            "detail": {"events": [
                {"date": answered, "name": event,
                 "personID": None, "committeeId": None, "relatedLabel": label,
                 "speechNumber": None, "speechId": None, "voteId": None,
                 "remark": None}]}}


def _written_question(bill_id="q-2", number="K/12", label="belügyminiszter",
                      submitted="2026-06-01T09:00:00Z", answered="2026-06-21T09:00:00Z"):
    """An `írásbeli kérdés` — the only question type the response-time median is
    computed over (MIN-8), because its span is the tárca's own rather than the
    House's sitting calendar."""
    return _answered_question(bill_id, number, label, submitted, answered,
                              title="Írásbeli kérdés", qtype="írásbeli kérdés",
                              main_type="K", event="kérdés írásban megválaszolva")


def _load_portfolio_corpus(db_path, conn=None, answer_label="belügyminiszter",
                           extra=()):
    """Add an answered question to the shared fixture corpus (which already has a
    government-submitted bill), then derive the §6C tables. ``extra`` appends
    further irományok for tests that need more than the one interpelláció."""
    from tests.conftest import _bills_registry
    c = conn or loader.connect(db_path)
    reg = _bills_registry()
    reg["data"].append(_answered_question(label=answer_label))
    reg["data"].extend(extra)
    loader.load_bills(c, reg)
    loader.rebuild_portfolios(c)
    return c


def test_an_interpellation_answered_in_writing_counts_as_answered(db_path):
    """1994-98 only: the era answered interpellations in writing too, and named
    the responding tárca when it did (parlamonitor.bills.legacy). The modern API's
    vocabulary has no such event, so the name exists for that cycle alone."""
    c = _load_portfolio_corpus(db_path, extra=[_answered_question(
        bill_id="q-legacy", number="I/1632", label="földművelésügyi miniszter",
        submitted="1995-11-08", answered="1995-12-19",
        event="interpelláció írásban megválaszolva")])
    rows = c.execute("SELECT portfolio_slug FROM portfolio_bill "
                     "WHERE role = 'answered' AND bill_id = 'q-legacy'").fetchall()
    assert [r["portfolio_slug"] for r in rows] == [
        portfolios.resolve("földművelésügyi miniszter").slug]
    c.close()


def test_the_three_link_kinds_land_on_their_portfolios(db_path):
    c = _load_portfolio_corpus(db_path)
    # answered: the interpelláció's responding minister
    rows = c.execute("SELECT portfolio_slug, bill_id FROM portfolio_bill "
                     "WHERE role = 'answered'").fetchall()
    assert [(r["portfolio_slug"], r["bill_id"]) for r in rows] == [
        (portfolios.resolve("belügyminiszter").slug, "q-1")]
    # submitted: the fixture's government bill, "kormány (pénzügyminiszter)"
    rows = c.execute("SELECT portfolio_slug, bill_id FROM portfolio_bill "
                     "WHERE role = 'submitted'").fetchall()
    assert [(r["portfolio_slug"], r["bill_id"]) for r in rows] == [
        (portfolios.resolve("pénzügyminiszter").slug, "bill-uuid-2")]
    # who held it: the office registry's term for the interior ministry
    held = c.execute(
        "SELECT person_id, title FROM portfolio_office WHERE portfolio_slug = ?",
        (portfolios.resolve("belügyminiszter").slug,)).fetchall()
    assert ("k001", "Belügyminisztérium államtitkára") in [
        (r["person_id"], r["title"]) for r in held]
    c.close()


def test_a_speech_is_filed_under_the_office_it_was_given_in(db_path):
    from tests.test_api import _load_minister_speech
    c = loader.connect(db_path)
    _load_minister_speech(db_path, office="igazságügyi miniszter", conn=c)
    loader.rebuild_portfolios(c)
    rows = c.execute(
        """SELECT s.uid FROM portfolio_speech ps JOIN speech s ON s.uid = ps.speech_uid
           WHERE ps.portfolio_slug = ?""",
        (portfolios.resolve("igazságügyi miniszter").slug,)).fetchall()
    assert len(rows) == 1
    c.close()


def test_an_unmapped_responder_becomes_its_own_portfolio(db_path):
    """The safety net of MIN-3, exercised end to end: a label parlament.hu might
    introduce tomorrow shows up as a tárca of its own rather than disappearing
    from the record."""
    c = _load_portfolio_corpus(db_path, answer_label="holnaputáni miniszter")
    row = c.execute(
        """SELECT p.name, p.kind FROM portfolio p
           JOIN portfolio_bill pb ON pb.portfolio_slug = p.slug
           WHERE pb.bill_id = 'q-1'""").fetchone()
    assert row["name"] == "Holnaputáni miniszter"
    c.close()


def test_a_personal_commission_creates_no_portfolio(db_path):
    c = _load_portfolio_corpus(db_path, answer_label="miniszterelnöki biztos")
    assert c.execute("SELECT COUNT(*) c FROM portfolio_bill "
                     "WHERE bill_id = 'q-1'").fetchone()["c"] == 0
    c.close()


def test_reingesting_the_bills_module_leaves_the_links_rebuildable(db_path):
    """The derived tables hold foreign keys into `bill`, so a cycle re-ingest has
    to clear them first — otherwise loading new bill data fails outright."""
    c = _load_portfolio_corpus(db_path)
    from tests.conftest import _bills_registry
    reg = _bills_registry()
    reg["data"].append(_answered_question())
    loader.load_bills(c, reg)              # must not raise on the stale links
    loader.rebuild_portfolios(c)
    assert c.execute("SELECT COUNT(*) c FROM portfolio_bill "
                     "WHERE role = 'answered'").fetchone()["c"] == 1
    c.close()


def test_rebuild_is_idempotent(db_path):
    c = _load_portfolio_corpus(db_path)
    before = c.execute("SELECT COUNT(*) c FROM portfolio_bill").fetchone()["c"]
    loader.rebuild_portfolios(c)
    assert c.execute("SELECT COUNT(*) c FROM portfolio_bill").fetchone()["c"] == before
    c.close()


# --------------------------------------------------------------------------
# The API (MIN-5 / MIN-6 / MIN-7)
# --------------------------------------------------------------------------

def test_listing_groups_by_kind_and_counts_the_links(client, db_path):
    _load_portfolio_corpus(db_path).close()
    d = client.get("/api/v1/portfolios", params={"period": 43}).json()
    assert d["built"] is True
    by_name = {p["name"]: p for p in d["portfolios"]}
    assert by_name["Belügyminisztérium"]["answered"] == 1
    assert by_name["Pénzügyminisztérium"]["submitted"] == 1
    assert "ministry" in d["kinds"]
    # A tárca with no activity in scope says nothing about the cycle being viewed.
    assert all(p["answered"] or p["submitted"] or p["speeches"] for p in d["portfolios"])


def test_listing_matches_the_name_accent_insensitively(client, db_path):
    _load_portfolio_corpus(db_path).close()
    d = client.get("/api/v1/portfolios", params={"q": "belugyminiszterium"}).json()
    assert [p["name"] for p in d["portfolios"]] == ["Belügyminisztérium"]


def test_detail_reports_holders_response_time_and_the_labels_it_collated(client, db_path):
    # Two questions answered by the same tárca — an interpelláció and a written
    # one. Both count as answered; only the written one is in the median (MIN-8).
    _load_portfolio_corpus(db_path, extra=[_written_question()]).close()
    slug = portfolios.resolve("belügyminiszter").slug
    d = client.get(f"/api/v1/portfolios/{slug}").json()
    assert d["name"] == "Belügyminisztérium"
    assert d["answered"] == 2
    # the written question: submitted 2026-06-01, answered 2026-06-21
    assert d["response_time"]["median_days"] == 20.0
    assert d["response_time"]["n"] == 1
    assert "k001" in [h["person_id"] for h in d["holders"]]
    # The grouping is checkable by the reader (TRUST-1).
    assert "belügyminiszter" in d["aliases"]


def test_the_response_time_median_covers_written_questions_only(client, db_path):
    """MIN-8: an interpelláció is answered from the floor, so its span records when
    the House next sat, not how fast the ministry worked. A tárca that answered
    only in plenary therefore reports no median rather than the sitting calendar's.
    """
    _load_portfolio_corpus(db_path).close()      # the corpus holds one interpelláció
    slug = portfolios.resolve("belügyminiszter").slug
    d = client.get(f"/api/v1/portfolios/{slug}").json()
    assert d["answered"] == 1
    assert d["response_time"] is None


def test_detail_404s_for_an_unknown_portfolio(client, db_path):
    _load_portfolio_corpus(db_path).close()
    assert client.get("/api/v1/portfolios/nincs-ilyen").status_code == 404


def test_the_cycle_scope_applies_to_the_counts(client, db_path):
    _load_portfolio_corpus(db_path).close()
    slug = portfolios.resolve("belügyminiszter").slug
    assert client.get(f"/api/v1/portfolios/{slug}",
                      params={"period": 43}).json()["answered"] == 1
    assert client.get(f"/api/v1/portfolios/{slug}",
                      params={"period": 42}).json()["answered"] == 0


def test_bills_list_can_be_filtered_by_portfolio_in_both_senses(client, db_path):
    """MIN-7: the tárca is a filter on the list the rest of the site already has,
    so a profile's document panels are that list rather than a second one."""
    _load_portfolio_corpus(db_path).close()
    interior = portfolios.resolve("belügyminiszter").slug
    finance = portfolios.resolve("pénzügyminiszter").slug

    answered = client.get("/api/v1/bills", params={
        "portfolio": interior, "portfolio_role": "answered"}).json()
    assert [b["bill_number"] for b in answered["bills"]] == ["I/9"]

    submitted = client.get("/api/v1/bills", params={
        "portfolio": finance, "portfolio_role": "submitted"}).json()
    assert [b["bill_number"] for b in submitted["bills"]] == ["T/101"]

    # The roles do not leak into one another.
    assert client.get("/api/v1/bills", params={
        "portfolio": interior, "portfolio_role": "submitted"}).json()["total"] == 0


def test_trend_buckets_every_year_in_range(client, db_path):
    _load_portfolio_corpus(db_path).close()
    slug = portfolios.resolve("belügyminiszter").slug
    d = client.get(f"/api/v1/portfolios/{slug}/trend").json()
    assert d["granularity"] == "year"
    assert d["buckets"] == [{"period": "2026", "hits": 1}]


def test_speeches_panel_lists_the_office_spoken_in(client, db_path):
    from tests.test_api import _load_minister_speech
    c = loader.connect(db_path)
    _load_minister_speech(db_path, office="igazságügyi miniszter", conn=c)
    loader.rebuild_portfolios(c)
    c.close()
    slug = portfolios.resolve("igazságügyi miniszter").slug
    d = client.get(f"/api/v1/portfolios/{slug}/speeches").json()
    assert d["total"] == 1
    assert d["speeches"][0]["office"] == "igazságügyi miniszter"
    assert d["speeches"][0]["name"] == "Törőcsikné Görög Márta"


def test_speech_coverage_is_reported_so_a_zero_is_not_read_as_silence(client, db_path):
    """Only the cycles re-scraped since `speaker_office` was added carry it, so a
    tárca's empty speech panel must be distinguishable from a silent ministry
    (MIN-10)."""
    _load_portfolio_corpus(db_path).close()
    slug = portfolios.resolve("belügyminiszter").slug
    d = client.get(f"/api/v1/portfolios/{slug}", params={"period": 42}).json()
    assert 42 not in d["speech_coverage"]
    assert d["speech_coverage_partial"] is True


def test_endpoints_degrade_on_a_database_built_before_the_module(client, db_path):
    """A DB the loader hasn't derived the tables into yet has no portfolios; the
    listing answers empty (so the frontend hides the tab) rather than erroring."""
    c = loader.connect(db_path)
    for tbl in ("portfolio_office", "portfolio_speech", "portfolio_bill",
                "portfolio_alias", "portfolio"):
        c.execute(f"DROP TABLE IF EXISTS {tbl}")
    c.commit()
    c.close()
    d = client.get("/api/v1/portfolios").json()
    assert d == {"portfolios": [], "kinds": [], "speech_coverage": [], "built": False}
    # And the bills filter matches nothing rather than failing (EXT-6).
    assert client.get("/api/v1/bills", params={"portfolio": "belugy"}).json()["total"] == 0


# --------------------------------------------------------------------------
# Crawler-facing plumbing (SEO-1)
# --------------------------------------------------------------------------

def test_a_portfolio_page_carries_its_own_metadata(og_client, db_path):
    """Without this the ministry pages would all share the generic site card and
    the same <title> — nothing for a search engine to tell them apart by."""
    _load_portfolio_corpus(db_path).close()
    slug = portfolios.resolve("belügyminiszter").slug
    r = og_client.get(f"/representatives/portfolios/{slug}")
    assert r.status_code == 200
    assert "Belügyminisztérium" in r.text
    assert f"/representatives/portfolios/{slug}" in r.text


def test_an_unknown_portfolio_page_is_a_404_not_a_soft_one(og_client, db_path):
    _load_portfolio_corpus(db_path).close()
    assert og_client.get("/representatives/portfolios/nincs-ilyen").status_code == 404


def test_the_sitemap_lists_every_portfolio(seo_client, db_path):
    _load_portfolio_corpus(db_path).close()
    body = seo_client.get("/sitemap-portfolios-1.xml").text
    slug = portfolios.resolve("belügyminiszter").slug
    assert f"/representatives/portfolios/{slug}" in body


def test_an_office_term_reported_by_both_sources_is_listed_once(client, db_path):
    """`person_office` holds the same term twice — from the all-time registry and
    from the per-MP roster (REP-2a) — and only the registry copy is categorised.
    Undeduped, a minister shows up under their rank *and* again under "egyéb
    tisztség" on the same page. The registry copy wins, as it does on the
    profile."""
    c = _load_portfolio_corpus(db_path)
    # The roster's copy of the same term the registry already reports.
    c.execute("""INSERT INTO person_office (person_id, title, category, date_start,
                     date_end, source)
                 SELECT person_id, title, NULL, date_start, date_end, 'roster'
                   FROM person_office WHERE source = 'registry'""")
    c.commit()
    loader.rebuild_portfolios(c)
    c.close()

    slug = portfolios.resolve("belügyminiszter").slug
    holders = client.get(f"/api/v1/portfolios/{slug}").json()["holders"]
    assert holders
    terms = [(h["person_id"], h["title"], h["date_start"]) for h in holders]
    assert len(terms) == len(set(terms))
    assert all(h["category"] for h in holders)


def test_holders_are_the_cycles_own_not_the_outgoing_governments(client, db_path):
    """An outgoing government serves until the new one is sworn in, so all of its
    terms overlap the cycle that replaced it. Scoped by overlap, a ministry's 2026
    panel opened with the 2022 bench above the people actually running it — so a
    term belongs to the cycle it **began** in."""
    c = _load_portfolio_corpus(db_path)
    # The fixture's registry term for the interior ministry ran 2018–2022, i.e.
    # it ended before cycle 43 began; and a term from the cycle before that ends
    # a fortnight *into* it, exactly like a handover.
    c.execute("""INSERT INTO person_office (person_id, title, category, date_start,
                     date_end, source)
                 VALUES ('k001', 'belügyminiszter', 'minister',
                         '2022-05-24T12:00:01Z', '2026-05-22T21:59:59Z', 'registry')""")
    c.execute("""INSERT INTO person_office (person_id, title, category, date_start,
                     date_end, source)
                 VALUES ('n002', 'belügyminiszter', 'minister',
                         '2026-05-23T22:00:00Z', NULL, 'registry')""")
    c.commit()
    loader.rebuild_portfolios(c)
    c.close()

    slug = portfolios.resolve("belügyminiszter").slug
    holders = client.get(f"/api/v1/portfolios/{slug}", params={"period": 43}).json()["holders"]
    assert [h["person_id"] for h in holders] == ["n002"]


def test_an_office_still_held_from_an_earlier_cycle_is_kept(client, db_path):
    """The offices that answer to the House but are deliberately not synchronised
    with it — the MNB, the ombudsman, the prosecutor general — run across cycle
    boundaries. A start-date-only rule would empty their panels of the very people
    holding them right now."""
    c = _load_portfolio_corpus(db_path)
    c.execute("""INSERT INTO person_office (person_id, title, category, date_start,
                     date_end, source)
                 VALUES ('k001', 'legfőbb ügyész', 'senior',
                         '2019-12-06T23:00:00Z', NULL, 'registry')""")
    c.commit()
    loader.rebuild_portfolios(c)
    c.close()

    slug = portfolios.resolve("legfőbb ügyész").slug
    holders = client.get(f"/api/v1/portfolios/{slug}", params={"period": 43}).json()["holders"]
    assert [h["person_id"] for h in holders] == ["k001"]


def _earlier_cycles(c) -> None:
    """The three cycles before the fixture's own (which opens 2026-05-09), so a
    scope can span several of them — and leave one out."""
    for number, start, end in ((40, "2014-05-06", "2018-05-07"),
                               (41, "2018-05-08", "2022-05-01"),
                               (42, "2022-05-02", "2026-05-08")):
        c.execute("INSERT OR IGNORE INTO electoral_period(number, date_start, "
                  "date_end) VALUES (?, ?, ?)", (number, start, end))


def _one_minister_per_cycle(c) -> None:
    """An interior minister for each of those cycles, each sworn in a fortnight
    into it and serving a fortnight past its end — the handover the office
    registry actually records."""
    for person, start, end in (
            ("n002", "2014-06-05T22:00:00Z", "2018-05-17T21:59:59Z"),
            ("k001", "2018-05-17T22:00:00Z", "2022-05-24T12:00:00Z"),
            ("n002", "2022-05-24T12:00:01Z", "2026-05-12T21:59:59Z"),
            ("zzz9", "2026-05-12T22:00:00Z", None)):
        c.execute("""INSERT INTO person_office (person_id, title, category,
                         date_start, date_end, source)
                     VALUES (?, 'belügyminiszter', 'minister', ?, ?, 'registry')""",
                  (person, start, end))
    c.commit()
    loader.rebuild_portfolios(c)


def test_a_row_names_every_minister_of_the_cycles_in_scope(client, db_path):
    """A scope of several cycles usually means several ministers, and the row
    names them all. Named by the newest alone it dated the whole tárca to whoever
    holds the post now: the interior ministry read as the 2026 minister's even for
    a reader who had asked for 2018–2022 as well."""
    c = _load_portfolio_corpus(db_path)
    _earlier_cycles(c)
    _one_minister_per_cycle(c)
    c.close()

    slug = portfolios.resolve("belügyminiszter").slug
    d = client.get("/api/v1/portfolios", params={"period": [40, 41, 42, 43]}).json()
    row = next(p for p in d["portfolios"] if p["slug"] == slug)
    assert [h["person_id"] for h in row["holders"]] == ["zzz9", "n002", "k001", "n002"]
    # Only the top rank: the ranks below it are the profile's business (MIN-6),
    # so the fixture's state secretary of the same tárca is not on the row.
    assert {h["category"] for h in row["holders"]} == {"minister"}
    assert row["holder_count"] > len(row["holders"])


def test_a_cycle_left_out_of_the_scope_brings_no_holders_with_it(client, db_path):
    """The cycle bounds are one span from the earliest start to the latest end, so
    a scope that **skips** a cycle still contains it: with 2018–2022 and 2026–
    chosen, the minister of the cycle between them sat inside the span and was
    listed as though his cycle had been chosen — next to counts that are a
    per-cycle union, on the same row."""
    c = _load_portfolio_corpus(db_path)
    _earlier_cycles(c)
    _one_minister_per_cycle(c)
    c.close()

    slug = portfolios.resolve("belügyminiszter").slug
    d = client.get("/api/v1/portfolios", params={"period": [41, 43]}).json()
    row = next(p for p in d["portfolios"] if p["slug"] == slug)
    assert [h["person_id"] for h in row["holders"]] == ["zzz9", "k001"]


def test_the_head_of_a_body_is_named_before_its_deputies(client, db_path):
    """Rank, then date: the row names who *runs* the tárca. The heads of the
    bodies that are not ministries are filed as `senior`, and ranked with the
    ordinary `other` offices they sorted by date alone — so the Magyar Nemzeti
    Bank's row named whichever **deputy** governor was appointed most recently."""
    c = _load_portfolio_corpus(db_path)
    c.execute("""INSERT INTO person_office (person_id, title, category, date_start,
                     date_end, source)
                 VALUES ('k001', 'a Magyar Nemzeti Bank elnöke', 'senior',
                         '2026-05-19T22:00:00Z', NULL, 'registry')""")
    c.execute("""INSERT INTO person_office (person_id, title, category, date_start,
                     date_end, source)
                 VALUES ('n002', 'a Magyar Nemzeti Bank alelnöke', 'other',
                         '2026-06-01T22:00:00Z', NULL, 'registry')""")
    c.commit()
    loader.rebuild_portfolios(c)
    c.close()

    slug = portfolios.resolve("a Magyar Nemzeti Bank elnöke").slug
    holders = client.get(f"/api/v1/portfolios/{slug}",
                         params={"period": 43}).json()["holders"]
    assert [h["person_id"] for h in holders] == ["k001", "n002"]


def test_a_carried_over_iromany_counts_in_the_cycle_it_was_submitted_in(client, db_path):
    """parlament.hu re-lists an iromány that is still in progress under the **new**
    cycle, so a bill the government submitted in 2024 comes back as a cycle-43
    row. Counted by the parent's cycle, a ministry abolished in 2026 turned up in
    the 2026 listing on the strength of that one 2024 document."""
    from tests.conftest import _bills_registry
    c = loader.connect(db_path)
    # The fixture corpus is one cycle deep; the cycle the carried-over bill was
    # actually submitted in has to exist for a date to resolve to it.
    c.execute("INSERT OR IGNORE INTO electoral_period(number, date_start, date_end) "
              "VALUES (42, '2022-05-02', '2026-05-08')")
    reg = _bills_registry()
    # A cycle-43 iromány whose submission predates cycle 43 (which starts 2026-05-09).
    reg["data"].append(
        {"billId": "carried-1", "billNumber": "T/19", "billNumberSort": 19,
         "title": "Áthúzódó törvényjavaslat", "type": "törvényjavaslat",
         "mainType": "T", "status": "Törvényalkotási Bizottság eljárására vár",
         "submittedDate": "2024-10-29T09:00:00Z",
         "sponsors": [{"personID": None, "factionId": None, "committeeId": None,
                       "label": "kormány (belügyminiszter)"}]})
    loader.load_bills(c, reg)
    loader.rebuild_portfolios(c)
    c.close()

    slug = portfolios.resolve("belügyminiszter").slug
    # It belongs to the cycle it was actually submitted in…
    assert client.get(f"/api/v1/portfolios/{slug}",
                      params={"period": 42}).json()["submitted"] == 1
    assert client.get(f"/api/v1/portfolios/{slug}",
                      params={"period": 43}).json()["submitted"] == 0
    # …and the tárca is not listed for a cycle in which it did nothing.
    listed = client.get("/api/v1/portfolios", params={"period": 43}).json()
    assert slug not in [p["slug"] for p in listed["portfolios"]]


def test_the_document_list_is_the_same_set_as_the_count(client, db_path):
    """The profile's panels are `/bills?portfolio=…`, so they must scope by the
    same cycle the counts do — otherwise a tárca reports 0 and then lists one."""
    from tests.conftest import _bills_registry
    c = loader.connect(db_path)
    c.execute("INSERT OR IGNORE INTO electoral_period(number, date_start, date_end) "
              "VALUES (42, '2022-05-02', '2026-05-08')")
    reg = _bills_registry()
    reg["data"].append(
        {"billId": "carried-1", "billNumber": "T/19", "billNumberSort": 19,
         "title": "Áthúzódó törvényjavaslat", "type": "törvényjavaslat",
         "mainType": "T", "status": "folyamatban",
         "submittedDate": "2024-10-29T09:00:00Z",
         "sponsors": [{"personID": None, "factionId": None, "committeeId": None,
                       "label": "kormány (belügyminiszter)"}]})
    loader.load_bills(c, reg)
    loader.rebuild_portfolios(c)
    c.close()

    slug = portfolios.resolve("belügyminiszter").slug
    for cycle, expected in ((42, 1), (43, 0)):
        count = client.get(f"/api/v1/portfolios/{slug}",
                           params={"period": cycle}).json()["submitted"]
        listed = client.get("/api/v1/bills", params={
            "portfolio": slug, "portfolio_role": "submitted",
            "portfolio_period": cycle}).json()["total"]
        assert count == listed == expected


def test_a_link_with_no_datable_cycle_falls_back_to_the_iromanys_own(client, db_path):
    """The date rule must not be able to drop a link out of *every* scope: where
    the date resolves to no known cycle (a corpus that doesn't reach back that
    far), the link keeps the cycle its iromány is filed under."""
    from tests.conftest import _bills_registry
    c = loader.connect(db_path)
    reg = _bills_registry()
    reg["data"].append(
        {"billId": "carried-2", "billNumber": "T/20", "billNumberSort": 20,
         "title": "Régi javaslat", "type": "törvényjavaslat", "mainType": "T",
         "status": "folyamatban", "submittedDate": "1994-03-01T09:00:00Z",
         "sponsors": [{"personID": None, "factionId": None, "committeeId": None,
                       "label": "kormány (belügyminiszter)"}]})
    loader.load_bills(c, reg)
    loader.rebuild_portfolios(c)
    per = c.execute("SELECT period_number FROM portfolio_bill "
                    "WHERE bill_id = 'carried-2'").fetchone()["period_number"]
    c.close()
    assert per == 43
