"""robots.txt, the XML sitemaps and llms.txt (app/seo.py).

The site is a client-rendered SPA over a corpus nothing links to from a
crawlable index, so a search engine finds its pages through the sitemap or not
at all (SEO-1/SEO-3). These tests assert the index and its children cover every
enabled section, stay in step with the DB, and skip a module that isn't mounted.

The llms.txt tests (SEO-7) guard the one thing that file can get wrong that the
sitemap cannot: saying something about the corpus that isn't so — a scope, a
figure or a caveat that the site itself does not also say.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app import seo

SM = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


@pytest.fixture
def seo_client(db_path, monkeypatch):
    """A full app over the test DB, with the canonical site URL pinned so the
    absolute URLs a sitemap must carry are stable."""
    from app import db as db_module
    from app.config import settings
    monkeypatch.setattr(settings, "db_path", str(db_path))
    monkeypatch.setattr(db_module.settings, "db_path", str(db_path))
    monkeypatch.setattr(seo.settings, "site_url", "https://parlamonitor.k-monitor.hu")
    monkeypatch.setattr(seo, "_cache", {})  # no bleed between tests
    from app.main import app
    return TestClient(app)


def _locs(xml: str) -> list[str]:
    return [el.text for el in ET.fromstring(xml).iter(SM + "loc")]


# --- robots.txt -------------------------------------------------------------

def test_robots_points_at_the_sitemap_and_closes_the_facets(seo_client):
    r = seo_client.get("/robots.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    assert "Sitemap: https://parlamonitor.k-monitor.hu/sitemap.xml" in body
    assert "Allow: /" in body
    # The filter permutations Search Console showed Google spending its crawl
    # on (`/votes?person=…&value=missed`) duplicate the clean URL they
    # canonicalise to and are effectively unbounded in number.
    assert "Disallow: /*?*person=" in body
    assert "Disallow: /*?*value=" in body
    assert "Disallow: /*?*sponsor=" in body
    # …but the cycle scope is NOT closed off: those URLs must stay crawlable so
    # the canonical tag on them is actually read and consolidated.
    assert "cycle=" not in body
    assert "Disallow: /api/" in body
    # The embeds stay crawlable: they carry their own noindex, and a crawler
    # can only read that if it is allowed to fetch them.
    assert "Disallow: /embed/" not in body


# --- the sitemap index ------------------------------------------------------

def test_sitemap_index_lists_a_child_per_section(seo_client):
    r = seo_client.get("/sitemap.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    locs = _locs(r.text)
    names = {re.search(r"/sitemap-(.+)-\d+\.xml$", loc).group(1) for loc in locs}
    assert names == {"core", "sessions", "speeches", "representatives",
                     "bills", "documents", "portfolios", "committees",
                     "committee-minutes", "votes"}
    assert all(loc.startswith("https://parlamonitor.k-monitor.hu/") for loc in locs)


def test_every_advertised_child_actually_resolves(seo_client):
    """A sitemap index that lists a 404 is a broken sitemap: Search Console
    reports the whole submission as failed."""
    for loc in _locs(seo_client.get("/sitemap.xml").text):
        assert seo_client.get(loc.replace("https://parlamonitor.k-monitor.hu", "")
                              ).status_code == 200


def test_sitemap_index_paginates_beyond_the_per_file_cap(seo_client, monkeypatch):
    """Past URLS_PER_SITEMAP a section is split across numbered children — the
    real corpus needs several files for its speeches alone."""
    monkeypatch.setattr(seo, "URLS_PER_SITEMAP", 1)
    monkeypatch.setattr(seo, "_cache", {})
    # The fixture holds two törvényjavaslatok, so /bills splits in two.
    locs = [loc for loc in _locs(seo_client.get("/sitemap.xml").text)
            if "/sitemap-bills-" in loc]
    assert len(locs) == 2
    seen = []
    for loc in locs:
        path = loc.replace("https://parlamonitor.k-monitor.hu", "")
        page = _locs(seo_client.get(path).text)
        assert len(page) == 1  # one URL per file at this cap…
        seen += page
    assert len(set(seen)) == 2  # …and no URL repeated or dropped across pages


# --- the child sitemaps -----------------------------------------------------

def test_core_sitemap_holds_the_browse_pages(seo_client):
    locs = _locs(seo_client.get("/sitemap-core-1.xml").text)
    assert "https://parlamonitor.k-monitor.hu/" in locs
    assert "https://parlamonitor.k-monitor.hu/representatives" in locs
    assert "https://parlamonitor.k-monitor.hu/votes" in locs
    # The Kérdések browse page (BILL-13) — its own entry point into the
    # question-type irományok the all-irományok list buries.
    assert "https://parlamonitor.k-monitor.hu/bills/questions" in locs
    # Témák (TOPIC-9) — an analysis that stands as an entry point of its own, so
    # it is advertised like the others in its section.
    assert "https://parlamonitor.k-monitor.hu/analyses/topics" in locs


def test_speech_sitemap_carries_content_pages_with_their_date(
        seo_client, monkeypatch):
    # The fixture's speech has two sentences; production asks for MIN_SENTENCES
    # (3) before a speech is worth crawling, so lower the bar to reach it.
    assert seo.MIN_SENTENCES == 3
    monkeypatch.setattr(seo, "MIN_SENTENCES", 2)
    monkeypatch.setattr(seo, "_cache", {})
    root = ET.fromstring(seo_client.get("/sitemap-speeches-1.xml").text)
    urls = [(u.find(SM + "loc").text, getattr(u.find(SM + "lastmod"), "text", None))
            for u in root.iter(SM + "url")]
    assert ("https://parlamonitor.k-monitor.hu/proceedings/43001-1",
            "2026-05-09") in urls
    # 43001-2 is the video-only speech with no transcript (VIE-8): a page with
    # nothing to read is left out rather than offered up to be crawled.
    assert not any(loc.endswith("/proceedings/43001-2") for loc, _ in urls)


def test_thin_speeches_are_left_out_of_the_sitemap(seo_client):
    """At the production threshold the fixture's two-sentence speech doesn't
    make the cut — and the section that holds nothing still resolves rather
    than 404ing a URL the index advertises."""
    r = seo_client.get("/sitemap-speeches-1.xml")
    assert r.status_code == 200
    assert _locs(r.text) == []


def test_irományok_are_split_the_way_their_canonical_is(seo_client):
    """Each iromány appears once, under the path its canonical names — a
    törvényjavaslat under /bills, everything else under /documents."""
    bills = _locs(seo_client.get("/sitemap-bills-1.xml").text)
    docs = _locs(seo_client.get("/sitemap-documents-1.xml").text)
    assert "https://parlamonitor.k-monitor.hu/bills/bill-uuid-1" in bills
    assert "https://parlamonitor.k-monitor.hu/documents/doc-uuid-3" in docs
    assert not any("bill-uuid-1" in loc for loc in docs)
    assert not any("doc-uuid-3" in loc for loc in bills)


def test_unknown_section_or_page_is_a_404(seo_client):
    assert seo_client.get("/sitemap-nosuch-1.xml").status_code == 404
    assert seo_client.get("/sitemap-speeches-99.xml").status_code == 404
    assert seo_client.get("/sitemap-core-2.xml").status_code == 404


def test_a_disabled_module_is_not_advertised(seo_client, monkeypatch):
    """A deployment running without the votes module (EXT-6) must not sitemap
    URLs it does not serve."""
    monkeypatch.setattr(seo.settings, "enabled_modules",
                        {"proceedings", "representatives", "bills"})
    monkeypatch.setattr(seo, "_cache", {})
    locs = _locs(seo_client.get("/sitemap.xml").text)
    assert not any("votes" in loc for loc in locs)
    assert seo_client.get("/sitemap-votes-1.xml").status_code == 404
    assert "https://parlamonitor.k-monitor.hu/votes" not in _locs(
        seo_client.get("/sitemap-core-1.xml").text)


def test_the_sitemap_cache_stays_bounded(seo_client, monkeypatch):
    """A child sitemap is megabytes of string and there are a dozen of them —
    a crawler walking the whole index must not pin all of them in every worker
    (the DB is sized to sit in the page cache next to them)."""
    monkeypatch.setattr(seo, "_cache", {})
    for loc in _locs(seo_client.get("/sitemap.xml").text):
        seo_client.get(loc.replace("https://parlamonitor.k-monitor.hu", ""))
    assert len(seo._cache) <= seo.MAX_CACHED_SITEMAPS


def test_sitemaps_are_regenerated_after_a_load(seo_client, db_path):
    """The cache is keyed on the corpus build stamped in `build_meta`, so the
    next loader run is picked up. Keying on the DB file's mtime would not be
    enough: SQLite runs in WAL mode, so a commit need not touch that file."""
    import sqlite3
    first = seo_client.get("/sitemap-sessions-1.xml").text
    assert "https://parlamonitor.k-monitor.hu/sessions/43999" not in _locs(first)

    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO session (id, period_number, sitting, date, status) "
                 "VALUES ('43999', 43, 99, '2026-06-01', 'published')")
    # …as a loader run would: new data, new build stamp.
    conn.execute("UPDATE build_meta SET value = '2026-06-02T00:00:00+00:00' "
                 "WHERE key = 'data_updated_at'")
    conn.commit()
    conn.close()

    second = seo_client.get("/sitemap-sessions-1.xml").text
    assert "https://parlamonitor.k-monitor.hu/sessions/43999" in _locs(second)


# --- llms.txt ---------------------------------------------------------------

def test_llms_txt_is_markdown_served_as_text(seo_client):
    """llmstxt.org layout — H1, a `>` summary, then H2 sections — under a .txt
    address, so a fetcher renders it instead of offering it as a download."""
    r = seo_client.get("/llms.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    assert body.startswith("# Parlamonitor\n")
    assert "\n> " in body
    assert "\n## " in body


def test_llms_txt_links_are_absolute_and_on_the_indexed_host(seo_client):
    """The file is read on its own, far from the page it describes: a relative
    link in it resolves against nothing."""
    for link in re.findall(r"\]\((.+?)\)", seo_client.get("/llms.txt").text):
        assert link.startswith("https://"), link


def test_llms_txt_offers_the_same_entry_points_as_the_sitemap(seo_client):
    """The browse list is STATIC_PATHS, like the core sitemap and the crawlable
    nav — three surfaces, one list, so none of them can go stale alone."""
    body = seo_client.get("/llms.txt").text
    for path in seo._enabled_static():
        assert f"](https://parlamonitor.k-monitor.hu{path})" in body


def test_llms_txt_gives_the_url_shape_of_every_mounted_section(seo_client):
    """What a model needs to link a specific speech or vote rather than guess
    one. Taken from the sitemap's own sections, so it cannot advertise a page
    the sitemap doesn't carry."""
    body = seo_client.get("/llms.txt").text
    for section in seo._enabled_sections():
        assert f"`{section.pattern}`" in body
    assert "`/proceedings/{felszolalas_uid}`" in body


def test_llms_txt_states_the_corpus_it_actually_serves(seo_client, conn):
    """The figures come from the DB, not from prose someone has to remember to
    update: a model told the corpus stops in 2022 when it stops today answers
    confidently and wrongly."""
    body = seo_client.get("/llms.txt").text
    sessions, speeches = conn.execute(
        "SELECT (SELECT COUNT(*) FROM session), (SELECT COUNT(*) FROM speech)"
    ).fetchone()
    assert f"{seo._hu_int(sessions)} ülésnap" in body
    assert f"{seo._hu_int(speeches)} felszólalás" in body
    # …and how fresh that is, from the loader's own build stamp.
    stamp = conn.execute(
        "SELECT value FROM build_meta WHERE key='data_updated_at'").fetchone()[0]
    assert stamp[:10] in body


def test_llms_txt_repeats_the_sites_own_caveats(seo_client):
    """A caveat that holds in the API but not in the file a model reads is worse
    than none — so the timing disclaimer is one constant, said in both."""
    from app.main import TIMING_DISCLAIMER
    body = seo_client.get("/llms.txt").text
    assert TIMING_DISCLAIMER in body
    # …and the attribution the whole project rests on (LEGAL-1 / TRUST-1).
    assert "parlament.hu" in body
    assert "Nem hivatalos oldal" in body


def test_llms_txt_drops_a_disabled_module(seo_client, monkeypatch):
    """EXT-6, same as the sitemap: a deployment without the votes module must
    not describe vote pages it does not serve."""
    monkeypatch.setattr(seo.settings, "enabled_modules",
                        {"proceedings", "representatives", "bills"})
    monkeypatch.setattr(seo, "_cache", {})
    body = seo_client.get("/llms.txt").text
    assert "/votes" not in body
    assert "/proceedings/" in body  # the mounted ones stay


def test_llms_txt_follows_the_served_cycle_window(seo_client, conn, monkeypatch):
    """A windowed deployment (CYC-7) describes its window, not the whole corpus:
    the coverage line carries the same period predicate the API's queries do, so
    it can never claim a cycle the site answers 404 to."""
    conn.execute("INSERT INTO electoral_period (number, label, date_start, "
                 "date_end) VALUES (42, '42. ciklus', '2022-05-02', '2026-05-08')")
    conn.commit()
    monkeypatch.setattr(seo, "_cache", {})
    assert "42–43. ciklus" in seo_client.get("/llms.txt").text

    monkeypatch.setattr(seo.settings, "site_cycles", "43")
    monkeypatch.setattr(seo, "_cache", {})
    body = seo_client.get("/llms.txt").text
    assert "43. ciklus" in body
    assert "42–43" not in body  # the cycle it does not serve is gone


def test_committee_minutes_are_in_the_sitemap_but_only_the_readable_ones(seo_client):
    """A sitting whose PDF could not be read keeps its row in the API — the page
    says so — but has nothing on it worth crawling."""
    locs = _locs(seo_client.get("/sitemap-committee-minutes-1.xml").text)
    assert [l for l in locs if l.endswith(
        "/representatives/committees/meetings/ules-1")]
    # `ules-2` published no minutes at all, so it is not advertised.
    assert not [l for l in locs if l.endswith("/meetings/ules-2")]
