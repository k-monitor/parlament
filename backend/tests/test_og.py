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
    monkeypatch.setattr(og.settings, "site_url", "https://parlamonitor.hu")
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
             "server": ("parlamonitor.hu", 443), "path": "/"}
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
    assert m["og:url"] == "https://parlamonitor.hu/proceedings/43001-1?s=1"
    assert m["og:site_name"] == "Parlamonitor"


def test_share_whole_speech_uses_opening_and_strips_label(og_client):
    r = og_client.get("/proceedings/43001-1")
    m = _meta(r.text)
    # First sentence has no caps label here, so both opening sentences appear.
    assert "költségvetés" in m["og:description"].lower()
    assert m["og:url"] == "https://parlamonitor.hu/proceedings/43001-1"


def test_share_video_only_speech_has_no_quote_but_still_a_card(og_client):
    # Speech 43001-2 (Nagy Anna) has no transcript (VIE-8): a card without a quote.
    r = og_client.get("/proceedings/43001-2")
    m = _meta(r.text)
    assert "Nagy Anna" in m["og:title"]
    assert m["og:description"]  # a sensible fallback description, not empty
    assert "„" not in m["og:description"]  # no empty quote marks


def test_share_unknown_speech_falls_back_to_default_card(og_client):
    # An unknown id has no per-page card, so it previews with the site-wide
    # DEFAULT card (not a bare shell): generic title + the default OG image.
    r = og_client.get("/proceedings/nope-nope")
    assert r.status_code == 200
    m = _meta(r.text)
    assert "GENERIC SITE DESCRIPTION" not in r.text  # generic shell desc replaced
    assert m["og:title"] == "Parlamonitor"
    assert m["og:type"] == "website"
    assert m["og:image"].endswith("/og-image.png")
    assert m["twitter:card"] == "summary_large_image"


def test_share_profile_card(og_client):
    r = og_client.get("/representatives/k001")
    m = _meta(r.text)
    assert "Kovács Béla" in m["og:title"]
    assert m["og:type"] == "profile"
    assert m["og:url"] == "https://parlamonitor.hu/representatives/k001"
    # k001 has no photo in the fixture -> default OG image + large card.
    assert m["og:image"].endswith("/og-image.png")


def test_share_representatives_index_is_not_hijacked(og_client):
    # /representatives (the list) and /representatives/factions must NOT be
    # treated as an MP id: they get the site-wide default card, NOT a profile
    # card (og:type website, not profile; no MP-specific image).
    r = og_client.get("/representatives/factions")
    m = _meta(r.text)
    assert m["og:type"] == "website"  # the default card, not "profile"
    assert m["og:title"] == "Parlamonitor"
    assert m["og:image"].endswith("/og-image.png")
    assert m["og:url"] == "https://parlamonitor.hu/representatives/factions"


def test_share_session_card(og_client):
    r = og_client.get("/sessions/43001")
    m = _meta(r.text)
    assert "ülésnap" in m["og:title"].lower()
    assert m["og:url"] == "https://parlamonitor.hu/sessions/43001"


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
    assert m["og:url"] == "https://parlamonitor.hu/bills/bill-uuid-1"


def test_share_document_card_uses_its_own_path(og_client):
    # doc-uuid-3 (conftest): I/5, an interpelláció, shared on the /documents path.
    r = og_client.get("/documents/doc-uuid-3")
    m = _meta(r.text)
    assert "I/5" in m["og:title"]
    assert "közlekedésről" in m["og:title"]
    # The card's canonical URL matches the path the link was shared on.
    assert m["og:url"] == "https://parlamonitor.hu/documents/doc-uuid-3"


def test_share_unknown_bill_falls_back_to_default_card(og_client):
    # An unknown bill id previews with the site-wide default card.
    r = og_client.get("/bills/no-such-bill")
    assert r.status_code == 200
    m = _meta(r.text)
    assert "GENERIC SITE DESCRIPTION" not in r.text
    assert m["og:title"] == "Parlamonitor"
    assert m["og:type"] == "website"
    assert m["og:image"].endswith("/og-image.png")


def test_home_and_list_routes_get_default_card(tmp_path, db_path, monkeypatch):
    """The SPA root and any client route without its own card are served through
    SPAStaticFiles with the site-wide default OG card injected — so the home page
    previews with the default image, not a bare shell (the earlier bug)."""
    from app import main as main_module

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(SHELL, encoding="utf-8")

    monkeypatch.setattr(og.settings, "frontend_dist", str(dist))
    monkeypatch.setattr(og.settings, "site_url", "https://parlamonitor.hu")
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
    assert m["og:image"] == "https://parlamonitor.hu/og-image.png"
    assert m["og:url"] == "https://parlamonitor.hu/"
    assert m["twitter:card"] == "summary_large_image"

    # A list route with no card of its own (the representatives index) resolves
    # via the history fallback and also gets the default card + canonical URL.
    m2 = _meta(client.get("/representatives").text)
    assert m2["og:title"] == "Parlamonitor"
    assert m2["og:image"].endswith("/og-image.png")
    assert m2["og:url"] == "https://parlamonitor.hu/representatives"

    # A real asset miss still 404s (never the shell) so the edge can't cache
    # HTML under a hashed-asset URL.
    assert client.get("/assets/missing-abcd.js").status_code == 404
