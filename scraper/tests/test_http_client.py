"""Tests for the polite HTTP client (parlamonitor/http_client.py).

Focus: how a rate-limited scrape behaves. parlament.hu serves a CAPTCHA
challenge page (HTTP 200 + HTML) instead of data when it decides we are too
eager, and that must not be treated like a transient glitch — no second-scale
retry storm, one attempt every ten minutes, and the whole run abandoned once the
wall has outlasted the last of those stand-offs (~1.5 h).
"""

from __future__ import annotations

import json
import os

import pytest
import requests

from parlamonitor import http_client
from parlamonitor.config import RuntimeConfig
from parlamonitor.http_client import (CAPTCHA_WAIT, CaptchaWall, HttpClient,
                                      HttpError, looks_like_captcha)


# The head of the real challenge page (see the WARNING line in a walled run).
CAPTCHA_HTML = (
    '<!DOCTYPE html> <html lang="hu"> <head> <meta charset="UTF-8"> '
    '<title>CAPTCHA Ellenőrzés</title> <style> * { margin: 0'
).encode()


def _response(*, status=200, body=b"{}", ctype="application/json"):
    """A real ``requests.Response`` with a canned body, so the client's own
    ``.text``/``.json()`` handling is exercised rather than a stub's."""
    r = requests.Response()
    r.status_code = status
    r._content = body
    r.url = "https://www.parlament.hu/x"
    r.headers["Content-Type"] = ctype
    return r


class _FakeSession:
    """Serves a scripted list of responses/exceptions, one per request."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def request(self, method, url, **kw):
        self.calls += 1
        item = self.script.pop(0) if self.script else _response()
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        pass


@pytest.fixture
def client(monkeypatch):
    """A client whose session is scriptable and whose sleeps are recorded rather
    than slept."""
    c = HttpClient(RuntimeConfig(sleep=0))
    c.session.close()
    slept: list[float] = []
    monkeypatch.setattr(http_client.time, "sleep", slept.append)
    c.slept = slept
    return c


# --- detecting the wall ----------------------------------------------------

def test_looks_like_captcha_only_for_the_challenge_page():
    assert looks_like_captcha(_response(body=CAPTCHA_HTML, ctype="text/html"))
    # A JSON payload is never the wall, whatever it happens to contain.
    assert not looks_like_captcha(
        _response(body=b'{"rows": [{"text": "a captcha"}]}'))
    # Nor is a legitimately-HTML fetch (the gazette listing, the playseq page).
    assert not looks_like_captcha(
        _response(body=b"<html><head><title>Magyar Kozlony</title></head>",
                  ctype="text/html; charset=utf-8"))
    # An image (an MP portrait) is safe even if its bytes decode oddly.
    assert not looks_like_captcha(_response(body=b"\xff\xd8\xff", ctype="image/jpeg"))


def test_captcha_further_down_a_long_html_page_is_not_the_wall():
    """Only the head is sniffed, so an incidental mention deep in a real page
    (a news article about CAPTCHAs, say) does not stall the scrape for an hour."""
    body = (b"<html><head><title>Hirek</title></head><body>"
            + b"x" * http_client.CAPTCHA_SNIFF_CHARS + b"captcha</body>")
    assert not looks_like_captcha(_response(body=body, ctype="text/html"))


# --- the stand-off retry track ---------------------------------------------

def test_captcha_stands_off_ten_minutes_then_retries(client):
    client.session = _FakeSession([
        _response(body=CAPTCHA_HTML, ctype="text/html"),
        _response(body=b'{"rows": []}'),
    ])
    assert client.get_json("https://www.parlament.hu/x") == {"rows": []}
    # Exactly one wait, and it is the flat stand-off — not an exponential backoff.
    assert client.slept == [600.0]
    assert CAPTCHA_WAIT == 600.0


def test_captcha_does_not_consume_the_transient_retry_budget(client):
    """The wall gets its own budget: three stand-offs followed by three connection
    errors is still a success, even though either alone is under the limit and
    both together exceed ``retry_count``."""
    client.config.retry_count = 3
    client.config.captcha_retries = 3
    client.session = _FakeSession(
        [_response(body=CAPTCHA_HTML, ctype="text/html")] * 3
        + [requests.ConnectionError("reset")] * 3
        + [_response(body=b'{"ok": true}')])
    assert client.get_json("https://www.parlament.hu/x") == {"ok": True}
    assert client.slept == [600.0] * 3 + [1.0, 2.0, 4.0]


def test_captcha_wall_abandons_the_run_after_the_stand_offs(client):
    client.session = _FakeSession([_response(body=CAPTCHA_HTML,
                                             ctype="text/html")] * 20)
    with pytest.raises(CaptchaWall):
        client.get_json("https://www.parlament.hu/x")
    # 9 stand-offs spent (~1.5h), then the 10th consecutive wall ends the run;
    # no request is made after the last wait.
    assert client.config.captcha_retries == 9
    assert client.slept == [600.0] * 9
    assert client.session.calls == 10
    assert sum(client.slept) == pytest.approx(1.5 * 3600, rel=0.02)


def test_captcha_wall_is_not_swallowed_by_a_stage_error_handler(client):
    """It is a BaseException on purpose: the pipeline's per-stage ``except
    Exception`` isolation (SCR-5) must not turn a global block into a stage that
    "failed" while the run marches on into five more hours of waiting."""
    client.config.captcha_retries = 0
    client.session = _FakeSession([_response(body=CAPTCHA_HTML,
                                             ctype="text/html")])
    with pytest.raises(CaptchaWall):
        try:
            client.get_json("https://www.parlament.hu/x")
        except Exception as e:      # noqa: BLE001 - mirrors the pipeline stages
            pytest.fail(f"CaptchaWall was caught as an ordinary error: {e}")
    assert client.slept == []       # nothing to wait for once the budget is 0


def test_captcha_streak_is_consecutive_only(client):
    """A success clears the streak, so an occasional wall over a long scrape
    never adds up to an abandoned run."""
    client.config.captcha_retries = 2
    wall = lambda: _response(body=CAPTCHA_HTML, ctype="text/html")  # noqa: E731
    client.session = _FakeSession([
        wall(), wall(), _response(),        # two walls, then through
        wall(), wall(), _response(),        # …and again: 4 walls in total
    ])
    for _ in range(2):
        client.get_json("https://www.parlament.hu/x")
    assert client.slept == [600.0] * 4


# --- unchanged behaviour for ordinary failures ------------------------------

def test_transient_failures_keep_exponential_backoff(client):
    client.config.retry_count = 3
    client.session = _FakeSession([_response(status=503)] * 4)
    with pytest.raises(HttpError):
        client.get_json("https://www.parlament.hu/x")
    assert client.session.calls == 4            # 1 attempt + 3 retries
    assert client.slept == [1.0, 2.0, 4.0]      # and no wait after the last one


def test_backoff_is_capped(client):
    client.config.retry_count = 6
    client.config.retry_delay_max = 8.0
    client.session = _FakeSession([_response(status=500)] * 7)
    with pytest.raises(HttpError):
        client.get_json("https://www.parlament.hu/x")
    assert client.slept == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]


# --- what ending the run looks like from outside ----------------------------

def test_cli_ends_the_run_with_a_distinct_exit_status(monkeypatch, tmp_path):
    """Exit 3, so a cron wrapper can tell "we're rate-limited" from the 1 that
    means "some items failed"."""
    from parlamonitor import cli

    def walled(args):
        raise CaptchaWall("still behind the CAPTCHA wall")

    monkeypatch.setattr(cli, "cmd_sync", walled)
    with pytest.raises(SystemExit) as exc:
        cli.main(["sync", str(tmp_path)])
    assert exc.value.code == 3


def test_lockfile_is_released_when_the_wall_ends_the_run(tmp_path):
    """A BaseException must not wedge the scheduler either (SCR-1)."""
    from parlamonitor.lockfile import acquire

    lock = tmp_path / "parlamonitor.lock"
    with pytest.raises(CaptchaWall):
        with acquire(lock):
            raise CaptchaWall("walled")
    # The file stays (an flock belongs to the inode, so it is never unlinked);
    # what must be true is that the next run can take it.
    with acquire(lock):
        pass
    assert lock.read_text() == ""       # no PID outlives the run that wrote it


def test_lockfile_ignores_a_pid_it_does_not_hold(tmp_path):
    """A lockfile left by a killed run must be reclaimable even when its PID looks
    alive — it was written in another PID namespace (a container the deploy
    recreated mid-scrape), so the number says nothing about the writer (SCR-1)."""
    from parlamonitor.lockfile import acquire

    lock = tmp_path / "parlamonitor.lock"
    lock.write_text(json.dumps({"pid": os.getpid()}))   # our own PID: certainly alive
    with acquire(lock):
        pass


def test_lockfile_blocks_a_second_run_while_actually_held(tmp_path):
    """...while a run that really holds the flock still excludes the next one."""
    from parlamonitor.lockfile import LockBusy, acquire

    lock = tmp_path / "parlamonitor.lock"
    with acquire(lock):
        with pytest.raises(LockBusy):
            with acquire(lock):
                pass
        with acquire(lock, force=True):  # the operator's escape hatch still works
            pass


# --- capped streaming fetch (get_capped, DOC-1) -----------------------------
# The document mirror pulls files whose size distribution has a long tail (the
# largest cycle-43 iromány is 58 MB), so it streams with a cap. What matters is
# that the cap is enforced *before* the whole body is pulled down, that the
# CAPTCHA wall is still detected on this path, and that "404" and "too big" stay
# distinguishable — the caller records them as different outcomes.

class _StreamResponse(requests.Response):
    """A Response whose body arrives in chunks, counting what was consumed."""

    def __init__(self, chunks, *, status=200, ctype="application/pdf",
                 declared=None):
        super().__init__()
        self.status_code = status
        self.url = "https://www.parlament.hu/irom43/00001/00001.pdf"
        self.headers["Content-Type"] = ctype
        if declared is not None:
            self.headers["Content-Length"] = str(declared)
        self._chunks = list(chunks)
        self.consumed = 0

    def iter_content(self, chunk_size=1, **kw):
        for c in self._chunks:
            self.consumed += len(c)
            yield c

    def close(self):
        pass


def _capped_client(response, monkeypatch):
    client = HttpClient(RuntimeConfig(sleep=0, retry_count=0))
    monkeypatch.setattr(client.session, "get", lambda *a, **kw: response)
    return client


def test_get_capped_returns_the_body_under_the_cap(monkeypatch):
    r = _StreamResponse([b"%PDF-", b"body"])
    got = _capped_client(r, monkeypatch).get_capped(r.url, max_bytes=1000)
    assert got.data == b"%PDF-body"
    assert got.over_cap is False and got.status == 200


def test_get_capped_abandons_a_body_that_grows_past_the_cap(monkeypatch):
    """No Content-Length to go on, so the cap has to bite mid-stream — and stop
    there rather than reading the rest of a 58 MB scan."""
    r = _StreamResponse([b"x" * 100] * 10)
    got = _capped_client(r, monkeypatch).get_capped(r.url, max_bytes=250)
    assert got.data is None and got.over_cap is True
    assert r.consumed == 300            # stopped at the third chunk, not the tenth


def test_get_capped_trusts_a_declared_over_cap_length_and_reads_nothing(monkeypatch):
    r = _StreamResponse([b"x" * 100], declared=50_000_000)
    got = _capped_client(r, monkeypatch).get_capped(r.url, max_bytes=1000)
    assert got.data is None and got.over_cap is True
    assert r.consumed == 0              # the body was never started


def test_get_capped_without_a_cap_reads_everything(monkeypatch):
    r = _StreamResponse([b"x" * 100] * 10, declared=1000)
    got = _capped_client(r, monkeypatch).get_capped(r.url, max_bytes=0)
    assert got.data == b"x" * 1000 and got.over_cap is False


def test_get_capped_reports_a_404_as_an_answer(monkeypatch):
    r = _StreamResponse([], status=404)
    got = _capped_client(r, monkeypatch).get_capped(r.url, max_bytes=1000)
    assert got.data is None and got.status == 404
    assert got.over_cap is False        # missing, not oversized


def test_get_capped_still_sees_the_captcha_wall(monkeypatch):
    """The wall arrives as HTML where a PDF was expected; it must be recognised
    here too, or the mirror would happily store challenge pages as documents."""
    r = _StreamResponse([], ctype="text/html")
    r._content = CAPTCHA_HTML
    client = HttpClient(RuntimeConfig(sleep=0, retry_count=0, captcha_retries=0))
    monkeypatch.setattr(client.session, "get", lambda *a, **kw: r)
    monkeypatch.setattr(http_client.time, "sleep", lambda s: None)
    with pytest.raises(CaptchaWall):
        client.get_capped(r.url, max_bytes=1000)


def test_get_capped_retries_a_5xx(monkeypatch):
    calls = []

    def _get(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            return _StreamResponse([], status=503)
        return _StreamResponse([b"%PDF-ok"])

    client = HttpClient(RuntimeConfig(sleep=0, retry_count=2))
    monkeypatch.setattr(client.session, "get", _get)
    monkeypatch.setattr(http_client.time, "sleep", lambda s: None)
    assert client.get_capped("https://www.parlament.hu/x", max_bytes=0).data == b"%PDF-ok"
    assert len(calls) == 2
