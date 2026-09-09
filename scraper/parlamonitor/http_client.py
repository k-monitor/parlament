"""A small, polite HTTP client shared by every scraper module.

Centralises the politeness/robustness behaviour the requirements mandate
(SCR-4): a configurable inter-request delay, bounded exponential-backoff
retries, an optional proxy, and a civic-tech User-Agent. Both JSON-API calls
and binary resource fetches (MP photos) go through here so the knobs apply
uniformly.

It also owns the response to being rate-limited. parlament.hu answers a client
it considers too eager with a CAPTCHA challenge page (HTTP 200, HTML) in place of
data; that is a wall, not a glitch, so it gets its own retry track — stand off for
ten minutes, then ask again — and once ``captcha_retries`` such pauses have all
come back walled the whole run is abandoned rather than left knocking.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import requests

from .config import RuntimeConfig
from .ssh_proxy import SSHProxy

logger = logging.getLogger(__name__)


class HttpError(RuntimeError):
    """Raised when a request still fails after all retries."""


class CaptchaBlocked(HttpError):
    """One response was the site's CAPTCHA challenge page, not the resource.

    An :class:`HttpError` subclass so a caller that already degrades gracefully
    around a failed fetch keeps working, but :meth:`HttpClient._with_retries`
    handles it on a track of its own (see :meth:`HttpClient._captcha_wait`).
    """


class CaptchaWall(BaseException):
    """The CAPTCHA wall outlasted every stand-off retry: stop scraping.

    Deliberately a :class:`BaseException` rather than an ``Exception``. Every
    stage of the pipeline isolates its own failures behind ``except Exception``
    so that one bad item never aborts a whole run (SCR-5) — which is exactly the
    wrong instinct here, because this block is *global*: each surviving stage
    would go on to sit through hours of its own before failing the same way.
    Only :func:`parlamonitor.cli.main` catches it, to end the run.
    """


@dataclass(frozen=True)
class CappedFetch:
    """One :meth:`HttpClient.get_capped` result.

    ``data`` is ``None`` when the resource 404'd (``status`` 404) or when it was
    abandoned for exceeding the cap (``over_cap``) — the two are distinguished
    so a caller can record *why* a document is missing rather than guessing."""

    data: bytes | None
    content_type: str
    status: int
    over_cap: bool


# Sniff only the head of an HTML body: enough to catch the challenge page, not
# enough for a legitimately-HTML fetch (the gazette listing, the video playseq
# page) to match on some incidental mention further down.
CAPTCHA_SNIFF_CHARS = 2000

# How long a walled client stands off before asking again. Long enough that the
# retry is not just re-confirming the same block (which is what the second-scale
# backoff was doing), short enough that a brief throttle costs the run minutes
# rather than an afternoon. Times `config.captcha_retries` this is the whole
# patience budget: 9 × 10 min ≈ 1.5 h before the run is abandoned.
CAPTCHA_WAIT = 600.0


def looks_like_captcha(r: requests.Response) -> bool:
    """Is this response the CAPTCHA interstitial rather than what we asked for?

    parlament.hu answers a client it considers too eager with **HTTP 200** and an
    HTML challenge page (``<title>CAPTCHA Ellenőrzés</title>``), so the status
    code says nothing at all; the tell is an HTML head mentioning a CAPTCHA where
    JSON or an image was expected."""
    if "html" not in (r.headers.get("Content-Type") or "").lower():
        return False
    return "captcha" in r.text[:CAPTCHA_SNIFF_CHARS].lower()


class HttpClient:
    def __init__(self, config: RuntimeConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers["User-Agent"] = config.user_agent
        # An SSH tunnel, if configured, wins over a plain proxy: it stands up a
        # local HTTP proxy and we point the session at that. Otherwise fall
        # back to an explicit proxy URL.
        self._ssh_proxy = SSHProxy.from_config(config)
        if self._ssh_proxy is not None:
            self._ssh_proxy.start()
            self.session.proxies = self._ssh_proxy.requests_proxies()
        elif config.proxy:
            self.session.proxies = {"http": config.proxy, "https": config.proxy}
        # Consecutive CAPTCHA walls hit since the last response that carried real
        # data — lives on the client, not on one request, because "in a row" spans
        # requests: the wall is put up against us, not against a URL.
        self._captcha_blocks = 0

    def _with_retries(self, desc: str, fn):
        """Run ``fn`` with bounded exponential-backoff retries.

        Retries on any :class:`requests.RequestException` or :class:`HttpError`
        ``fn`` raises — so callers can signal a retryable condition (a 5xx, or a
        200 whose body failed to parse) by raising ``HttpError`` from inside.

        A CAPTCHA wall (:class:`CaptchaBlocked`) is not one of those transient
        conditions and does not consume a retry attempt: it is handled by
        :meth:`_captcha_wait`, which stands off for minutes at a time."""
        last_exc: Exception | None = None
        attempt = 0
        while True:
            try:
                result = fn()
            except CaptchaBlocked as e:
                self._captcha_wait(desc, e)
                continue
            except (requests.RequestException, HttpError) as e:
                last_exc = e
                if attempt >= self.config.retry_count:
                    break
                wait = min(self.config.retry_delay_max, 2 ** attempt)
                attempt += 1
                logger.warning("%s failed (%s); retry %d/%d in %.0fs",
                               desc, e, attempt, self.config.retry_count, wait)
                time.sleep(wait)
                continue
            self._captcha_blocks = 0
            return result
        raise HttpError(f"{desc} failed after retries: {last_exc}")

    def _captcha_wait(self, desc: str, exc: CaptchaBlocked) -> None:
        """Stand off from a CAPTCHA wall — or abandon the run if it will not lift.

        Retrying seconds later just re-confirms the block (and deepens it), so the
        one useful move is to leave the site alone for a while and ask again.
        Raises :class:`CaptchaWall` once ``config.captcha_retries`` consecutive
        stand-offs have all come back walled: at that point we have been refused
        for ~1.5 h, and the polite thing is to stop and let the next scheduled run
        try with a clean slate."""
        self._captcha_blocks += 1
        if self._captcha_blocks > max(0, self.config.captcha_retries):
            raise CaptchaWall(
                f"{desc}: still behind the CAPTCHA wall after "
                f"{self.config.captcha_retries} retries "
                f"{CAPTCHA_WAIT / 60:.0f} minutes apart; abandoning the run "
                f"({exc})")
        logger.warning("%s hit the CAPTCHA wall (%s); sleeping %.0fs, then "
                       "retrying (%d/%d)",
                       desc, exc, CAPTCHA_WAIT, self._captcha_blocks,
                       self.config.captcha_retries)
        time.sleep(CAPTCHA_WAIT)

    def _send(self, method: str, url: str, **kw) -> requests.Response:
        r = self.session.request(method, url, timeout=self.config.timeout, **kw)
        if r.status_code >= 500:
            raise HttpError(f"HTTP {r.status_code} for {url}")
        if looks_like_captcha(r):
            raise CaptchaBlocked(f"CAPTCHA challenge page returned for {url}")
        return r

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        return self._with_retries(f"{method} {url}",
                                  lambda: self._send(method, url, **kw))

    def _parse_json(self, r: requests.Response, url: str) -> dict:
        """Parse a JSON body, turning a non-JSON/blank response into a
        (retryable) :class:`HttpError` with a diagnostic body snippet.

        The Felicitas backend (and the SSH proxy in front of it) occasionally
        returns a 200 with an empty or whitespace-only body; raising ``HttpError``
        here lets :meth:`_with_retries` retry it instead of crashing the run with
        an opaque ``JSONDecodeError``."""
        r.encoding = "utf-8"
        try:
            return r.json()
        except ValueError as e:
            snippet = " ".join(r.text.split())[:200]
            raise HttpError(
                f"non-JSON response from {url} (HTTP {r.status_code}, "
                f"{len(r.text)} bytes): {snippet!r}") from e

    def get_json(self, url: str, **kw) -> dict:
        return self._with_retries(
            f"GET {url}",
            lambda: self._parse_json(self._send("GET", url, **kw), url))

    def post_json(self, url: str, body: dict, *, headers: dict | None = None) -> dict:
        """POST a JSON body and parse a JSON response (the Felicitas pattern)."""
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if headers:
            h.update(headers)
        data = json.dumps(body).encode("utf-8")
        return self._with_retries(
            f"POST {url}",
            lambda: self._parse_json(
                self._send("POST", url, data=data, headers=h), url))

    def get_text(self, url: str, **kw) -> str:
        r = self._request("GET", url, **kw)
        r.encoding = "utf-8"
        return r.text

    def get_bytes(self, url: str, **kw) -> bytes | None:
        """Fetch a binary resource (e.g. an MP photo). ``None`` on 404."""
        r = self._request("GET", url, **kw)
        if r.status_code == 404:
            return None
        return r.content

    def get_capped(self, url: str, *, max_bytes: int = 0,
                   **kw) -> "CappedFetch":
        """Stream a binary resource, giving up on it once it exceeds ``max_bytes``.

        For the iromány document mirror (DOC-1), where the size tail is heavy —
        a handful of scans run to tens of MB — and an over-cap document must
        cost us the *headers*, not the whole download. ``max_bytes`` of ``0``
        means no cap, in which case this is just :meth:`get_bytes` with the
        content type reported alongside.

        A 404 is an answer, not a failure (``status`` 404, ``data`` ``None``),
        matching :meth:`get_bytes`; the CAPTCHA interstitial is still detected,
        because it arrives as a small HTML body we can afford to read."""
        return self._with_retries(
            f"GET {url}", lambda: self._send_capped(url, max_bytes, **kw))

    def _send_capped(self, url: str, max_bytes: int, **kw) -> "CappedFetch":
        r = self.session.get(url, timeout=self.config.timeout, stream=True, **kw)
        try:
            if r.status_code >= 500:
                raise HttpError(f"HTTP {r.status_code} for {url}")
            ctype = r.headers.get("Content-Type") or ""
            if r.status_code == 404:
                return CappedFetch(None, ctype, 404, False)
            # An HTML body where a PDF was expected is either the CAPTCHA wall or
            # an error page; both are small, so reading them costs nothing.
            if "html" in ctype.lower():
                if looks_like_captcha(r):
                    raise CaptchaBlocked(
                        f"CAPTCHA challenge page returned for {url}")
                return CappedFetch(r.content, ctype, r.status_code, False)
            # Trust a declared over-cap length and never start the body at all.
            declared = r.headers.get("Content-Length")
            if max_bytes and declared and declared.isdigit() \
                    and int(declared) > max_bytes:
                return CappedFetch(None, ctype, r.status_code, True)
            buf = bytearray()
            for chunk in r.iter_content(chunk_size=65536):
                buf.extend(chunk)
                if max_bytes and len(buf) > max_bytes:
                    return CappedFetch(None, ctype, r.status_code, True)
            return CappedFetch(bytes(buf), ctype, r.status_code, False)
        finally:
            r.close()

    def exists(self, url: str, **kw) -> bool:
        """Does this resource exist? A HEAD, so nothing is downloaded to find out.

        For static documents we only want to *link* to (an MP's CV PDF): the link
        must not be published unless it resolves (TRUST-1), and a 404 here is an
        answer, not a failure — unlike a 5xx, which ``_request`` still retries."""
        return self._request("HEAD", url, **kw).ok

    def polite_sleep(self) -> None:
        if self.config.sleep:
            time.sleep(self.config.sleep)

    def close(self) -> None:
        """Release the HTTP session and tear down the SSH tunnel, if any."""
        self.session.close()
        if self._ssh_proxy is not None:
            self._ssh_proxy.close()
            self._ssh_proxy = None

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
