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

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(self.config.retry_count + 1):
            try:
                r = self.session.request(method, url, timeout=self.config.timeout, **kw)
                if r.status_code >= 500:
                    raise HttpError(f"HTTP {r.status_code} for {url}")
                return r
            except (requests.RequestException, HttpError) as e:
                last_exc = e
                wait = min(self.config.retry_delay_max, 2 ** attempt)
                logger.warning("%s %s failed (%s); retry %d/%d in %.0fs",
                               method, url, e, attempt + 1,
                               self.config.retry_count, wait)
                time.sleep(wait)
        raise HttpError(f"{method} {url} failed after retries: {last_exc}")

    def get_json(self, url: str, **kw) -> dict:
        r = self._request("GET", url, **kw)
        r.encoding = "utf-8"
        return r.json()

    def post_json(self, url: str, body: dict, *, headers: dict | None = None) -> dict:
        """POST a JSON body and parse a JSON response (the Felicitas pattern)."""
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if headers:
            h.update(headers)
        r = self._request("POST", url, data=json.dumps(body).encode("utf-8"), headers=h)
        r.encoding = "utf-8"
        return r.json()

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
