"""A small, polite HTTP client shared by every scraper module.

Centralises the politeness/robustness behaviour the requirements mandate
(SCR-4): a configurable inter-request delay, bounded exponential-backoff
retries, an optional proxy, and a civic-tech User-Agent. Both JSON-API calls
and binary resource fetches (MP photos) go through here so the knobs apply
uniformly.
"""

from __future__ import annotations

import json
import logging
import time

import requests

from .config import RuntimeConfig
from .ssh_proxy import SSHProxy

logger = logging.getLogger(__name__)


class HttpError(RuntimeError):
    """Raised when a request still fails after all retries."""


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

    def _with_retries(self, desc: str, fn):
        """Run ``fn`` with bounded exponential-backoff retries.

        Retries on any :class:`requests.RequestException` or :class:`HttpError`
        ``fn`` raises — so callers can signal a retryable condition (a 5xx, or a
        200 whose body failed to parse) by raising ``HttpError`` from inside."""
        last_exc: Exception | None = None
        for attempt in range(self.config.retry_count + 1):
            try:
                return fn()
            except (requests.RequestException, HttpError) as e:
                last_exc = e
                wait = min(self.config.retry_delay_max, 2 ** attempt)
                logger.warning("%s failed (%s); retry %d/%d in %.0fs",
                               desc, e, attempt + 1,
                               self.config.retry_count, wait)
                time.sleep(wait)
        raise HttpError(f"{desc} failed after retries: {last_exc}")

    def _send(self, method: str, url: str, **kw) -> requests.Response:
        r = self.session.request(method, url, timeout=self.config.timeout, **kw)
        if r.status_code >= 500:
            raise HttpError(f"HTTP {r.status_code} for {url}")
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
