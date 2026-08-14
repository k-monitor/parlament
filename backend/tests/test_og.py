"""Server-rendered OpenGraph / Twitter share cards (app/og.py).

A shared deep link (a sentence, a speech, an MP, a sitting day) must preview
with per-page content — a quote + speaker, not the generic site card — for
crawlers that don't run the SPA's JS. These tests build a fresh app with a
minimal app-shell and assert the injected `<head>` metadata.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db as db_module
from app import og

SHELL = (
    "<!DOCTYPE html><html lang=\"hu\"><head>"
    "<meta charset=\"UTF-8\" />"
    "<meta name=\"description\" content=\"GENERIC SITE DESCRIPTION\" />"
    "<title>Parlamonitor</title>"
    "</head><body><div id=\"app\"></div></body></html>")


@pytest.fixture
def og_client(tmp_path, db_path, monkeypatch):
    """A fresh app that serves share cards over the test DB. The shell lives in a
    temp dist dir; the canonical site URL is pinned so absolute URLs are stable."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(SHELL, encoding="utf-8")

    # Patch the settings object `og` actually holds (it binds `settings` at
    # import). Another test may importlib.reload(app.config), rebinding
    # app.config.settings to a NEW object, so patching that import here could
    # miss the one og reads — patch og.settings directly.
    monkeypatch.setattr(og.settings, "frontend_dist", str(dist))
    monkeypatch.setattr(og.settings, "site_url", "https://parlamonitor.k-monitor.hu")
    monkeypatch.setattr(db_module.settings, "db_path", str(db_path))
    monkeypatch.setattr(og, "_shell_cache", None)  # force a re-read of our shell

    app = FastAPI()
    og.register(app)
    return TestClient(app)


def _meta(html: str) -> dict:
    """Parse the injected og:* / twitter:* / description content into a dict."""
    import re
    out = {}
    for prop, content in re.findall(
            r'<meta\s+(?:property|name)="([^"]+)"\s+content="([^"]*)"', html):
        out[prop] = content
    m = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
    if m:
        out["title"] = m.group(1)
    return out


def _canonical(html: str) -> str | None:
    """The page's declared canonical URL — the tag that decides which of several
    addresses for the same content gets indexed (§SEO-2)."""
    import re
    m = re.search(r'<link rel="canonical" href="([^"]*)"', html)
    return m.group(1) if m else None


def _jsonld(html: str) -> list[dict]:
    """Every JSON-LD block on the page, parsed."""
    import json
    import re
    return [json.loads(b.replace("\\u003c", "<")) for b in re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)]


# --- pure helpers -----------------------------------------------------------

def test_strip_speaker_label_removes_caps_prefix():
    assert og._strip_speaker_label(
        "DR. ÁDER JÁNOS köztársasági elnök: Jó napot!") == "Jó napot!"
    assert og._strip_speaker_label("ELNÖK: Következik a szavazás.") == "Következik a szavazás."


def test_strip_speaker_label_keeps_ordinary_sentence():
    # A normal sentence with a mid colon (not a caps label) is left intact.
    txt = "A törvény szerint ez a helyzet: mindenki egyenlő."
    assert og._strip_speaker_label(txt) == txt


def test_hu_date_formats_iso():
    assert og._hu_date("2026-06-18") == "2026. június 18."
    assert og._hu_date("2026-06-18T09:00:00") == "2026. június 18."
    assert og._hu_date(None) == ""


def test_render_escapes_and_deduplicates(og_client):
    # A quote with a double-quote must be attribute-escaped, and the generic
    # shell title/description must be dropped (not duplicated).
    from starlette.requests import Request
    scope = {"type": "http", "headers": [], "method": "GET", "scheme": "https",
             "server": ("parlamonitor.k-monitor.hu", 443), "path": "/"}
    resp = og.render(Request(scope), title='A "híres" mondat',
                     description='Ő azt mondta: "igen".')
    html = resp.body.decode()
    assert html.count("<title>") == 1
    assert "GENERIC SITE DESCRIPTION" not in html
    assert "&quot;" in html  # the quotes were escaped


# --- integration: the share-card routes -------------------------------------

def test_share_specific_sentence_shows_quote_and_speaker(og_client):
    # Sentence ord 1 of speech 43001-1 (conftest): "Az ÁGAZATI fejlesztés ügye sürgős!"
    r = og_client.get("/proceedings/43001-1?s=1")
    assert r.status_code == 200
    m = _meta(r.text)
    assert "Kovács Béla" in m["og:title"]
    assert "Fidesz" in m["og:title"]
    assert "ágazati fejlesztés".lower() in m["og:description"].lower()
    assert m["og:type"] == "article"
    assert m["og:site_name"] == "Parlamonitor"
    # The card text follows `?s=`, but the address does not: a sentence is part
    # of this page, not a page of its own, so canonical/og:url stay the speech
    # and every sentence link consolidates onto it (§SEO-2).
    assert m["og:url"] == "https://parlamonitor.k-monitor.hu/proceedings/43001-1"
    assert _canonical(r.text) == "https://parlamonitor.k-monitor.hu/proceedings/43001-1"


def test_share_whole_speech_uses_opening_and_strips_label(og_client):
    r = og_client.get("/proceedings/43001-1")
    m = _meta(r.text)
    # First sentence has no caps label here, so both opening sentences appear.
    assert "költségvetés" in m["og:description"].lower()
    assert m["og:url"] == "https://parlamonitor.k-monitor.hu/proceedings/43001-1"


def test_share_video_only_speech_has_no_quote_but_still_a_card(og_client):
    # Speech 43001-2 (Nagy Anna) has no transcript (VIE-8): a card without a quote.
    r = og_client.get("/proceedings/43001-2")
    m = _meta(r.text)
    assert "Nagy Anna" in m["og:title"]
    assert m["og:description"]  # a sensible fallback description, not empty
    assert "„" not in m["og:description"]  # no empty quote marks


def test_share_unknown_speech_is_a_noindex_404_carrying_the_app(og_client):
    # An unknown id is a real 404 — a 200 "not found" page is the soft 404 that
    # Search Console files under "Crawled – currently not indexed" (§SEO-4) —
    # but the body is still the app shell, so the SPA boots and renders its own
    # 404 view rather than the user seeing a JSON error.
    r = og_client.get("/proceedings/nope-nope")
    assert r.status_code == 404
    assert '<div id="app">' in r.text
    m = _meta(r.text)
    assert "GENERIC SITE DESCRIPTION" not in r.text  # generic shell desc replaced
    assert m["og:title"] == "Parlamonitor"
    assert m["robots"] == "noindex, follow"
    assert m["og:image"].endswith("/og-image.png")


def test_share_profile_card(og_client):
    r = og_client.get("/representatives/k001")
    m = _meta(r.text)
    assert "Kovács Béla" in m["og:title"]
    assert m["og:type"] == "profile"
    assert m["og:url"] == "https://parlamonitor.k-monitor.hu/representatives/k001"
    # k001 has no photo in the fixture -> default OG image + large card.
    assert m["og:image"].endswith("/og-image.png")


def test_share_vote_card(og_client):
    # v-1 (conftest): decides T/100, 2026-05-26, with a roll call.
    r = og_client.get("/votes/v-1")
    assert r.status_code == 200
    m = _meta(r.text)
    assert "T/100" in m["og:title"]
    assert "2026. május 26." in m["og:title"]
    assert m["og:url"] == "https://parlamonitor.k-monitor.hu/votes/v-1"
    # The vote's own numbers, so 19 000 vote pages don't share one description.
    assert "igen" in m["og:description"]


def test_votes_cohesion_is_not_taken_for_a_vote_id(og_client):
    # /votes/cohesion is a real sub-page, not a vote — it keeps its own card
    # and a 200 rather than becoming a 404 for a vote that doesn't exist.
    r = og_client.get("/votes/cohesion")
    assert r.status_code == 200
    assert _meta(r.text)["og:title"] == "Frakcióelemzés · Parlamonitor"


def test_detail_pages_carry_structured_data(og_client):
    """Each entity type declares what it *is* (SEO-5). The `Person` block is the
    one that ties an MP page to the person as an entity rather than a name."""
    person = _jsonld(og_client.get("/representatives/k001").text)
    types = {b["@type"] for b in person}
    assert types == {"Person", "BreadcrumbList"}
    p = next(b for b in person if b["@type"] == "Person")
    assert p["name"] == "Kovács Béla"
    assert p["memberOf"]["name"] == "Fidesz"

    speech = _jsonld(og_client.get("/proceedings/43001-1").text)
    article = next(b for b in speech if b["@type"] == "Article")
    assert article["author"]["name"] == "Kovács Béla"
    assert article["datePublished"] == "2026-05-09"

    iromany = _jsonld(og_client.get("/bills/bill-uuid-1").text)
    law = next(b for b in iromany if b["@type"] == "Legislation")
    assert law["legislationIdentifier"] == "T/100"

    # Breadcrumbs everywhere, rooted at the home page and ending on the page.
    crumbs = next(b for b in speech if b["@type"] == "BreadcrumbList")
    assert crumbs["itemListElement"][0]["item"].endswith("/")
    assert crumbs["itemListElement"][-1]["item"].endswith("/proceedings/43001-1")


def test_detail_pages_serve_their_text_without_javascript(og_client):
    """The shell's `#app` carries the page's own content, so the first crawl
    pass (and a reader with no JS) sees the transcript rather than an empty
    div. Vue clears the container on mount, so nothing is rendered twice."""
    body = og_client.get("/proceedings/43001-1").text
    assert "A költségvetés fontos kérdés." in body
    assert "Az ÁGAZATI fejlesztés ügye sürgős!" in body
    # …and a link back to the sitting day, which is how a crawler walks the
    # corpus at all: nothing else links to a speech.
    assert 'href="/sessions/43001"' in body

    # The sitting day lists its speeches — the other half of that crawl path.
    session = og_client.get("/sessions/43001").text
    assert 'href="/proceedings/43001-1"' in session
    assert 'href="/proceedings/43001-2"' in session

    # A video-only speech (VIE-8) says so instead of rendering an empty page.
    assert "csak a videófelvétel" in og_client.get("/proceedings/43001-2").text


def test_share_representatives_index_is_not_hijacked(og_client):
    # /representatives/factions must NOT be treated as an MP id: it is a real
    # browse page, so it keeps a 200 and gets its OWN card (not a profile card,
    # and not the generic site one — every route needs a distinct title).
    r = og_client.get("/representatives/factions")
    assert r.status_code == 200
    m = _meta(r.text)
    assert m["og:type"] == "website"  # a browse page, not "profile"
    assert m["og:title"] == "Frakciók · Parlamonitor"
    assert "robots" not in m  # indexable
    assert m["og:image"].endswith("/og-image.png")
    assert m["og:url"] == "https://parlamonitor.k-monitor.hu/representatives/factions"


def test_share_merged_speaker_list_card(og_client):
    """Same for `/representatives/all`, the "Összes" chip of the Felszólalók page:
    a browse page under the person-profile prefix, so without a card of its own it
    would be looked up as a person id and answer a real page with a 404 shell."""
    r = og_client.get("/representatives/all")
    assert r.status_code == 200
    m = _meta(r.text)
    assert m["og:type"] == "website"
    assert m["og:title"] == "Felszólalók · Parlamonitor"
    assert "robots" not in m  # indexable, just not advertised in the sitemap
    assert m["og:url"] == "https://parlamonitor.k-monitor.hu/representatives/all"


def test_share_session_card(og_client):
    r = og_client.get("/sessions/43001")
    m = _meta(r.text)
    assert "ülésnap" in m["og:title"].lower()
    assert m["og:url"] == "https://parlamonitor.k-monitor.hu/sessions/43001"


def test_share_bill_card(og_client):
    # bill-uuid-1 (conftest): T/100, "A költségvetésről szóló törvényjavaslat",
    # sponsor Kovács Béla, status tárgysorozatban, benyújtva 2026-05-10.
    r = og_client.get("/bills/bill-uuid-1")
    assert r.status_code == 200
    m = _meta(r.text)
    assert "T/100" in m["og:title"]
    assert "költségvetésről" in m["og:title"]
    assert "Kovács Béla" in m["og:description"]
    assert "tárgysorozatban" in m["og:description"]
    assert "2026. május 10." in m["og:description"]
    assert m["og:type"] == "article"
    assert m["og:url"] == "https://parlamonitor.k-monitor.hu/bills/bill-uuid-1"


def test_share_document_card_uses_the_preferred_path(og_client):
    # doc-uuid-3 (conftest): I/5, an interpelláció, shared on the /documents path.
    r = og_client.get("/documents/doc-uuid-3")
    m = _meta(r.text)
    assert "I/5" in m["og:title"]
    assert "közlekedésről" in m["og:title"]
    assert m["og:url"] == "https://parlamonitor.k-monitor.hu/documents/doc-uuid-3"


def test_iromany_canonical_is_the_same_whichever_path_is_requested(og_client):
    """`/bills/:id` and `/documents/:id` render the same iromány — duplicate
    content at two addresses. Both must name ONE canonical: a törvényjavaslat
    belongs under /bills, everything else under /documents (§SEO-2)."""
    bill = "https://parlamonitor.k-monitor.hu/bills/bill-uuid-1"
    assert _canonical(og_client.get("/bills/bill-uuid-1").text) == bill
    assert _canonical(og_client.get("/documents/bill-uuid-1").text) == bill

    doc = "https://parlamonitor.k-monitor.hu/documents/doc-uuid-3"
    assert _canonical(og_client.get("/documents/doc-uuid-3").text) == doc
    assert _canonical(og_client.get("/bills/doc-uuid-3").text) == doc


def test_share_unknown_bill_is_a_noindex_404(og_client):
    r = og_client.get("/bills/no-such-bill")
    assert r.status_code == 404
    m = _meta(r.text)
    assert "GENERIC SITE DESCRIPTION" not in r.text
    assert m["og:title"] == "Parlamonitor"
    assert m["robots"] == "noindex, follow"


def test_home_and_list_routes_get_default_card(tmp_path, db_path, monkeypatch):
    """The SPA root and any client route without its own card are served through
    SPAStaticFiles with the site-wide default OG card injected — so the home page
    previews with the default image, not a bare shell (the earlier bug)."""
    from app import main as main_module

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(SHELL, encoding="utf-8")

    monkeypatch.setattr(og.settings, "frontend_dist", str(dist))
    monkeypatch.setattr(og.settings, "site_url", "https://parlamonitor.k-monitor.hu")
    monkeypatch.setattr(db_module.settings, "db_path", str(db_path))
    monkeypatch.setattr(og, "_shell_cache", None)  # force a re-read of our shell

    app = FastAPI()
    og.register(app)
    app.mount("/", main_module.SPAStaticFiles(directory=str(dist), html=True),
              name="frontend")
    client = TestClient(app)

    # The home page (root) — served as index.html by StaticFiles, now with the
    # default card injected, and og:image/og:url made absolute.
    m = _meta(client.get("/").text)
    assert m["og:title"] == "Parlamonitor"
    assert m["og:type"] == "website"
    assert m["og:image"] == "https://parlamonitor.k-monitor.hu/og-image.png"
    assert m["og:url"] == "https://parlamonitor.k-monitor.hu/"
    assert m["twitter:card"] == "summary_large_image"

    # …and the home page alone declares the site itself and how to search it.
    site = _jsonld(client.get("/").text)
    assert [b["@type"] for b in site] == ["WebSite"]
    assert site[0]["potentialAction"]["target"]["urlTemplate"].endswith(
        "/search?q={search_term_string}")

    # A browse route resolves via the history fallback and gets its OWN card —
    # a site whose every page is titled "Parlamonitor" gives a search engine
    # nothing to tell its pages apart by (§SEO-2).
    m2 = _meta(client.get("/representatives").text)
    assert m2["og:title"] == "Képviselők · Parlamonitor"
    assert m2["og:image"].endswith("/og-image.png")
    assert m2["og:url"] == "https://parlamonitor.k-monitor.hu/representatives"

    # A filtered/paginated variant of a browse page is the same page: it
    # consolidates onto the clean URL rather than competing with it.
    assert (_canonical(client.get("/representatives?faction=7&offset=40").text)
            == "https://parlamonitor.k-monitor.hu/representatives")

    # The home page carries the site's navigation without JS — otherwise the
    # only way into the corpus for a first-pass crawler is the sitemap.
    home = client.get("/").text
    assert 'href="/representatives"' in home
    assert 'href="/votes"' in home

    # A URL matching no route at all renders the SPA's 404 view — keep it out
    # of the index instead of letting it in as a soft 404, and give it no
    # content of its own.
    notfound = client.get("/nincs-ilyen-oldal")
    assert _meta(notfound.text)["robots"] == "noindex, follow"
    assert '<div id="app"></div>' in notfound.text

    # An embed is a chart inside someone else's page (§4C), not a destination:
    # crawlable (so this very tag can be read) but never indexed on its own.
    assert _meta(client.get("/embed/faction-cohesion").text)["robots"] \
        == "noindex, follow"

    # A real asset miss still 404s (never the shell) so the edge can't cache
    # HTML under a hashed-asset URL.
    assert client.get("/assets/missing-abcd.js").status_code == 404
