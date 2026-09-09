"""Offline tests for the iromány document mirror (parlamonitor/documents).

Focus: the two things that make this stage safe to ship. It must store
**nothing at all** unless a run explicitly asks it to — the live server's disk
is the whole reason the retention ladder exists — and, once asked, it must keep
exactly the artefacts that retention names and skip on a re-run what it already
has. The network is stubbed throughout; nothing here touches parlament.hu.
"""

from __future__ import annotations

import gzip
import json
import lzma

import pytest

from parlamonitor import config
from parlamonitor.config import Paths
from parlamonitor.documents import scrape
from parlamonitor.http_client import CappedFetch


# A minimal but real-shaped registry: a bill with its own PDF, a motion under
# its detail, and a background file — the three places a document link lives.
REGISTRY = {
    "meta": {"cycle": 43},
    "data": [{
        "billId": "b-1", "billNumber": "T/303", "title": "Törvényjavaslat",
        "type": "törvényjavaslat",
        "textUrl": "https://www.parlament.hu/irom43/00303/00303.pdf",
        "detail": {
            "motions": [{"billNumber": "T/303/2", "type": "módosító",
                         "textUrl": "https://www.parlament.hu/irom43/00303/00303-0002.pdf"}],
            "documents": [{"kind": "background", "title": "Háttéranyag",
                           "url": "https://www.parlament.hu/documents/d/guest/hatter-x"}],
        },
    }, {
        # A second bill pointing at the SAME background file, plus no text of
        # its own (noText documents carry a null textUrl).
        "billId": "b-2", "billNumber": "K/477", "title": "Kérdés",
        "textUrl": None,
        "detail": {"documents": [{"kind": "background", "title": "Háttéranyag",
                                  "url": "https://www.parlament.hu/documents/d/guest/hatter-x"}]},
    }],
}

PDF = b"%PDF-1.7\nfake\n"


class FakeHttp:
    """Stands in for HttpClient: serves canned bodies and counts requests."""

    def __init__(self, bodies=None, over_cap=(), ctypes=None):
        self.bodies = bodies or {}
        self.over_cap = set(over_cap)
        self.ctypes = ctypes or {}       # url -> Content-Type, default PDF
        self.calls = []

    def get_capped(self, url, *, max_bytes=0, headers=None):
        self.calls.append(url)
        ctype = self.ctypes.get(url, "application/pdf")
        if url in self.over_cap:
            return CappedFetch(None, ctype, 200, True)
        body = self.bodies.get(url, PDF)
        if body is None:
            return CappedFetch(None, ctype, 404, False)
        return CappedFetch(body, ctype, 200, False)

    def polite_sleep(self):
        pass


@pytest.fixture
def paths(tmp_path):
    p = Paths(tmp_path)
    p.ensure()
    return p


@pytest.fixture(autouse=True)
def _stub_extract(monkeypatch):
    """Extraction is poppler's job, not ours — stub it so the tests run on a host
    with no pdftotext, and assert on what we do with its output."""
    monkeypatch.setattr(scrape, "pdftotext_available", lambda: True)
    monkeypatch.setattr(scrape, "extract_text", lambda pdf, **kw: "Tisztelt Elnök Úr!\n")
    monkeypatch.setattr(scrape, "page_count", lambda pdf, **kw: 3)


# --- what counts as a document ---------------------------------------------

def test_refs_cover_every_link_site_and_dedupe():
    refs = scrape.document_refs(REGISTRY)
    urls = [r["url"] for r in refs]
    assert urls == [
        "https://www.parlament.hu/irom43/00303/00303.pdf",
        "https://www.parlament.hu/irom43/00303/00303-0002.pdf",
        "https://www.parlament.hu/documents/d/guest/hatter-x",
    ]
    # the shared background file is listed once, under the first bill that saw it
    assert [r["kind"] for r in refs] == ["main", "motion", "background"]
    assert refs[2]["billId"] == "b-1"
    # a null textUrl contributes nothing
    assert all(r["url"] for r in refs)


def test_document_id_is_readable_stable_and_unique():
    a = scrape.document_id("https://www.parlament.hu/irom43/00477/00477.pdf")
    assert a.startswith("irom43-00477-00477-")
    assert a == scrape.document_id("https://www.parlament.hu/irom43/00477/00477.pdf")
    # Long guest slugs are truncated, so the hash is what keeps them distinct.
    long1 = "https://www.parlament.hu/documents/d/guest/" + "a" * 200 + "-one"
    long2 = "https://www.parlament.hu/documents/d/guest/" + "a" * 200 + "-two"
    assert scrape.document_id(long1) != scrape.document_id(long2)
    assert len(scrape.document_id(long1)) < 100


# --- retention: the storage guarantee ---------------------------------------

def test_retention_text_keeps_only_text(paths):
    http = FakeHttp()
    meta = scrape.fetch_documents(http, paths, 43, REGISTRY, retention="text")
    assert meta["fetched"] == 3 and meta["errors"] == 0
    assert not paths.document_pdf_dir(43).exists()
    stored = sorted(p.name for p in paths.document_text_dir(43).iterdir())
    assert len(stored) == 3 and all(n.endswith(".txt.xz") for n in stored)
    assert meta["bytesStoredPdf"] == 0 and meta["bytesStoredText"] > 0


def test_retention_pdf_keeps_only_the_source(paths):
    http = FakeHttp()
    meta = scrape.fetch_documents(http, paths, 43, REGISTRY, retention="pdf")
    assert not paths.document_text_dir(43).exists()
    assert len(list(paths.document_pdf_dir(43).iterdir())) == 3
    assert meta["bytesStoredText"] == 0
    assert meta["bytesStoredPdf"] == 3 * len(PDF)


def test_retention_all_keeps_both(paths):
    scrape.fetch_documents(FakeHttp(), paths, 43, REGISTRY, retention="all")
    assert len(list(paths.document_pdf_dir(43).iterdir())) == 3
    assert len(list(paths.document_text_dir(43).iterdir())) == 3


def test_off_is_never_a_valid_call(paths):
    # `off` is handled by the caller; reaching the stage with it is a bug, not a
    # silent no-op that might still have written something.
    with pytest.raises(ValueError):
        scrape.fetch_documents(FakeHttp(), paths, 43, REGISTRY, retention="off")


def test_retention_defaults_to_off_and_a_typo_does_not_enable_it(monkeypatch):
    monkeypatch.delenv("PARLAMONITOR_DOCUMENTS", raising=False)
    assert config.documents_retention() == "off"
    monkeypatch.setenv("PARLAMONITOR_DOCUMENTS", "txt")   # typo for `text`
    assert config.documents_retention() == "off"
    monkeypatch.setenv("PARLAMONITOR_DOCUMENTS", "TEXT")
    assert config.documents_retention() == "text"


# --- compression ------------------------------------------------------------

@pytest.mark.parametrize("codec,suffix,decode", [
    ("xz", ".txt.xz", lzma.decompress),
    ("gzip", ".txt.gz", gzip.decompress),
    ("none", ".txt", lambda b: b),
])
def test_each_codec_round_trips(paths, codec, suffix, decode):
    scrape.fetch_documents(FakeHttp(), paths, 43, REGISTRY, retention="text",
                           compression=codec)
    f = next(p for p in paths.document_text_dir(43).iterdir()
             if p.name.endswith(suffix))
    assert decode(f.read_bytes()).decode() == "Tisztelt Elnök Úr!\n"
    # …and read_text finds it whatever it was compressed with
    index = scrape.load_index(paths, 43)
    entry = next(e for e in index.values() if e.get("textFile", "").endswith(suffix))
    assert scrape.read_text(paths, 43, entry) == "Tisztelt Elnök Úr!\n"


# --- incrementality ---------------------------------------------------------

def test_rerun_refetches_nothing(paths):
    first = FakeHttp()
    scrape.fetch_documents(first, paths, 43, REGISTRY, retention="text")
    second = FakeHttp()
    meta = scrape.fetch_documents(second, paths, 43, REGISTRY, retention="text")
    assert second.calls == []
    assert meta["reused"] == 3 and meta["fetched"] == 0


def test_force_refetches_everything(paths):
    scrape.fetch_documents(FakeHttp(), paths, 43, REGISTRY, retention="text")
    http = FakeHttp()
    meta = scrape.fetch_documents(http, paths, 43, REGISTRY, retention="text",
                                  force=True)
    assert len(http.calls) == 3 and meta["fetched"] == 3


def test_a_deleted_text_file_is_refetched(paths):
    scrape.fetch_documents(FakeHttp(), paths, 43, REGISTRY, retention="text")
    next(paths.document_text_dir(43).iterdir()).unlink()
    http = FakeHttp()
    scrape.fetch_documents(http, paths, 43, REGISTRY, retention="text")
    assert len(http.calls) == 1


def test_widening_retention_backfills_the_missing_half(paths):
    scrape.fetch_documents(FakeHttp(), paths, 43, REGISTRY, retention="text")
    http = FakeHttp()
    scrape.fetch_documents(http, paths, 43, REGISTRY, retention="all")
    assert len(http.calls) == 3            # the PDFs were never kept, so re-fetch
    assert len(list(paths.document_pdf_dir(43).iterdir())) == 3


# --- degradation (SCR-5): one bad document never sinks the cycle ------------

def test_a_404_is_recorded_not_retried(paths):
    url = "https://www.parlament.hu/irom43/00303/00303.pdf"
    meta = scrape.fetch_documents(FakeHttp({url: None}), paths, 43, REGISTRY,
                                  retention="text")
    assert meta["missing"] == 1 and meta["errors"] == 0
    entry = scrape.load_index(paths, 43)[scrape.document_id(url)]
    assert entry["status"] == "missing" and entry["httpStatus"] == 404
    # settled: a second pass does not ask again
    http = FakeHttp({url: None})
    scrape.fetch_documents(http, paths, 43, REGISTRY, retention="text")
    assert url not in http.calls


def test_an_over_cap_document_is_skipped_and_settled(paths):
    url = "https://www.parlament.hu/irom43/00303/00303.pdf"
    meta = scrape.fetch_documents(FakeHttp(over_cap=[url]), paths, 43, REGISTRY,
                                  retention="text", max_mb=0.001)
    assert meta["tooLarge"] == 1
    assert scrape.load_index(paths, 43)[scrape.document_id(url)]["status"] == "too-large"
    assert len(list(paths.document_text_dir(43).iterdir())) == 2


def test_a_raising_fetch_is_isolated_and_retried_next_run(paths):
    class Boom(FakeHttp):
        def get_capped(self, url, **kw):
            self.calls.append(url)
            if url.endswith("00303.pdf"):
                raise OSError("connection reset")
            return super().get_capped(url, **kw)

    meta = scrape.fetch_documents(Boom(), paths, 43, REGISTRY, retention="text")
    assert meta["errors"] == 1
    assert len(list(paths.document_text_dir(43).iterdir())) == 2   # the rest landed
    http = FakeHttp()
    scrape.fetch_documents(http, paths, 43, REGISTRY, retention="text")
    assert len(http.calls) == 1            # an error, unlike a 404, is re-tried


def test_an_image_only_scan_yields_no_text_but_is_not_a_failure(paths, monkeypatch):
    monkeypatch.setattr(scrape, "extract_text", lambda pdf, **kw: "  \n \n")
    meta = scrape.fetch_documents(FakeHttp(), paths, 43, REGISTRY, retention="text")
    assert meta["noText"] == 3 and meta["errors"] == 0
    assert not any(paths.document_text_dir(43).iterdir()) \
        if paths.document_text_dir(43).exists() else True
    entry = next(iter(scrape.load_index(paths, 43).values()))
    assert entry["status"] == "ok" and entry["textChars"] == 0
    http = FakeHttp()
    scrape.fetch_documents(http, paths, 43, REGISTRY, retention="text")
    assert http.calls == []                # settled, not re-fetched forever


def test_missing_pdftotext_does_not_abort_the_run(paths, monkeypatch):
    monkeypatch.setattr(scrape, "pdftotext_available", lambda: False)
    monkeypatch.setattr(scrape, "extract_text", lambda pdf, **kw: None)
    meta = scrape.fetch_documents(FakeHttp(), paths, 43, REGISTRY, retention="all")
    assert meta["fetched"] == 3 and meta["errors"] == 0
    assert len(list(paths.document_pdf_dir(43).iterdir())) == 3   # PDFs still kept
    assert all("textError" in e for e in scrape.load_index(paths, 43).values())


# --- the manifest -----------------------------------------------------------

def test_index_records_provenance(paths):
    scrape.fetch_documents(FakeHttp(), paths, 43, REGISTRY, retention="all")
    data = json.loads(paths.documents_index(43).read_text())
    assert data["meta"]["retention"] == "all"
    assert data["meta"]["total"] == 3
    entry = next(e for e in data["documents"] if e["kind"] == "main")
    assert entry["billNumber"] == "T/303"
    assert entry["docType"] == "törvényjavaslat"
    assert entry["pdfBytes"] == len(PDF)
    assert entry["pages"] == 3
    assert len(entry["sha256"]) == 64
    assert entry["url"].endswith("00303.pdf")


def test_limit_stops_fetching_but_keeps_the_manifest(paths):
    http = FakeHttp()
    meta = scrape.fetch_documents(http, paths, 43, REGISTRY, retention="text",
                                  limit=1)
    assert len(http.calls) == 1 and meta["fetched"] == 1
    assert len(list(paths.document_text_dir(43).iterdir())) == 1


# --- re-extraction never re-downloads --------------------------------------
# Two ways a run can need the text of a document whose bytes we already hold:
# poppler was installed since the last pass, or retention widened from `pdf` to
# `all`. Either way the PDF is on disk, so asking parlament.hu again would be
# both slow and rude (SCR-4).

def test_installing_pdftotext_backfills_text_from_the_stored_pdf(paths, monkeypatch):
    monkeypatch.setattr(scrape, "pdftotext_available", lambda: False)
    monkeypatch.setattr(scrape, "extract_text", lambda pdf, **kw: None)
    scrape.fetch_documents(FakeHttp(), paths, 43, REGISTRY, retention="all")
    assert not any(e.get("textFile") for e in scrape.load_index(paths, 43).values())

    monkeypatch.setattr(scrape, "pdftotext_available", lambda: True)
    monkeypatch.setattr(scrape, "extract_text", lambda pdf, **kw: "megvan")
    http = FakeHttp()
    meta = scrape.fetch_documents(http, paths, 43, REGISTRY, retention="all")
    assert http.calls == []                       # nothing re-downloaded
    assert meta["reused"] == 3
    assert len(list(paths.document_text_dir(43).iterdir())) == 3
    assert all("textError" not in e for e in scrape.load_index(paths, 43).values())


def test_widening_pdf_to_all_extracts_from_disk(paths):
    scrape.fetch_documents(FakeHttp(), paths, 43, REGISTRY, retention="pdf")
    http = FakeHttp()
    scrape.fetch_documents(http, paths, 43, REGISTRY, retention="all")
    assert http.calls == []
    assert len(list(paths.document_text_dir(43).iterdir())) == 3


def test_force_still_refetches_even_with_a_stored_pdf(paths):
    scrape.fetch_documents(FakeHttp(), paths, 43, REGISTRY, retention="all")
    http = FakeHttp()
    scrape.fetch_documents(http, paths, 43, REGISTRY, retention="all", force=True)
    assert len(http.calls) == 3


def test_a_non_pdf_resource_is_settled_not_chased_forever(paths):
    """A Word attachment or an HTML page yields no text and never will; under
    `text` retention it must not look 'incomplete' and be re-fetched each run."""
    url = "https://www.parlament.hu/documents/d/guest/hatter-x"
    http = FakeHttp({url: b"<html>not a pdf</html>"},
                    ctypes={url: "text/html"})
    scrape.fetch_documents(http, paths, 43, REGISTRY, retention="text")
    entry = scrape.load_index(paths, 43)[scrape.document_id(url)]
    assert entry["notPdf"] is True and "textFile" not in entry
    again = FakeHttp({url: b"<html>not a pdf</html>"},
                     ctypes={url: "text/html"})
    scrape.fetch_documents(again, paths, 43, REGISTRY, retention="text")
    assert again.calls == []
