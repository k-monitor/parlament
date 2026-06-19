"""On-disk layout, file naming, and environment-driven runtime config.

The scraper writes everything under one ``data_dir``; the layout mirrors the
reference pipeline so its outputs stay comparable:

    <data_dir>/
      original/
        plenary/    raw-<session>-day.json        # one raw bundle per sitting
      processed/
        <session>-session.json                    # the published session record
        representatives-<cycle>.json              # the MP registry for a cycle
      logs/
        ingest-<timestamp>.json                   # per-run ingestion log (SCR-3)
      ogywatch.lock                               # concurrency lockfile (SCR-1)

Every operational knob (politeness sleep, retries, proxy, optional API key)
is read from the environment so nothing is hard-coded (requirements OPS-4 /
SCR-4); :meth:`RuntimeConfig.from_env` applies the defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def session_id(cycle: int, sitting: int) -> str:
    """Canonical session key ``<cycle><sitting:03d>`` (e.g. 43, day 7 -> ``43007``).

    Matches the reference convention: a 2-digit electoral cycle followed by a
    zero-padded 3-digit sitting number, sortable and prefix-selectable by cycle.
    """
    return f"{int(cycle)}{int(sitting):03d}"


@dataclass
class RuntimeConfig:
    """Runtime knobs, all environment-overridable (OPS-4)."""

    sleep: float = 1.0              # politeness delay between requests (SCR-4)
    retry_count: int = 5            # max retries per HTTP request
    retry_delay_max: float = 30.0   # cap on exponential backoff
    timeout: float = 40.0           # per-request timeout (seconds)
    proxy: str | None = None        # optional SOCKS5/HTTP proxy URL
    user_agent: str = (
        "OrszaggyulesWatch/1.0 (+https://github.com/k-monitor; civic-tech)"
    )

    @classmethod
    def from_env(cls, **overrides) -> "RuntimeConfig":
        """Build from ``OGYWATCH_*`` env vars; explicit ``overrides`` win."""
        def _f(name: str, default: float) -> float:
            raw = os.environ.get(name)
            return float(raw) if raw not in (None, "") else default

        cfg = cls(
            sleep=_f("OGYWATCH_SLEEP", cls.sleep),
            retry_count=int(_f("OGYWATCH_RETRY_COUNT", cls.retry_count)),
            retry_delay_max=_f("OGYWATCH_RETRY_DELAY_MAX", cls.retry_delay_max),
            timeout=_f("OGYWATCH_TIMEOUT", cls.timeout),
            proxy=os.environ.get("OGYWATCH_PROXY") or None,
            user_agent=os.environ.get("OGYWATCH_USER_AGENT") or cls.user_agent,
        )
        for k, v in overrides.items():
            if v is not None:
                setattr(cfg, k, v)
        return cfg


class Paths:
    """Owns every path the pipeline reads or writes under one data directory."""

    def __init__(self, data_dir: str | Path):
        self.data = Path(data_dir)
        self.raw_plenary = self.data / "original" / "plenary"
        self.processed = self.data / "processed"
        self.logs = self.data / "logs"
        self.lockfile = self.data / "ogywatch.lock"

    def ensure(self) -> None:
        for d in (self.raw_plenary, self.processed, self.logs):
            d.mkdir(parents=True, exist_ok=True)

    # --- per-sitting files -------------------------------------------------

    def raw_day(self, session: str) -> Path:
        """Raw scraped bundle for one sitting, pre-transform."""
        return self.raw_plenary / f"raw-{session}-day.json"

    def session_file(self, session: str) -> Path:
        """The published per-sitting session record."""
        return self.processed / f"{session}-session.json"

    # --- representatives ---------------------------------------------------

    def representatives_file(self, cycle: int) -> Path:
        return self.processed / f"representatives-{int(cycle)}.json"

    # --- bills -------------------------------------------------------------

    def bills_file(self, cycle: int) -> Path:
        return self.processed / f"bills-{int(cycle)}.json"
