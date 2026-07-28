"""On-disk layout, file naming, and environment-driven runtime config.

The scraper writes everything under one ``data_dir``; the layout mirrors the
reference pipeline so its outputs stay comparable:

    <data_dir>/
      original/
        plenary/    raw-<session>-day.json        # one raw bundle per sitting
      processed/
        <session>-session.json                    # the published session record
        representatives-<cycle>.json              # the MP registry for a cycle
        advocates-<cycle>.json                    # the nationality-advocate registry
      logs/
        ingest-<timestamp>.json                   # per-run ingestion log (SCR-3)
      parlamonitor.lock                               # concurrency lockfile (SCR-1)

Every operational knob (politeness sleep, retries, proxy, optional API key)
is read from the environment so nothing is hard-coded (requirements OPS-4 /
SCR-4); :meth:`RuntimeConfig.from_env` applies the defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# --- sentence↔video timing backend (TIM-1) --------------------------------
# The default sentence timing is Whisper forced alignment (parlamonitor.whisper_align);
# it degrades to the positional character estimate when no transcription backend is
# available, so a clean checkout still scrapes with no GPU/audio toolchain (SCR-6).

def timing_backend() -> str:
    """``auto`` | ``whisper-modal`` | ``whisper-local`` | ``character``.

    ``auto`` prefers Whisper on Modal, then local ``faster-whisper``, then the
    character estimate — whichever is actually available."""
    return (os.environ.get("PARLAMONITOR_TIMING_BACKEND") or "auto").strip().lower()


def whisper_model() -> str:
    """The Whisper model id. ``large-v3-turbo`` is fast and cheap yet accurate."""
    return (os.environ.get("PARLAMONITOR_WHISPER_MODEL") or "large-v3-turbo").strip()


def whisper_language() -> str:
    return (os.environ.get("PARLAMONITOR_WHISPER_LANGUAGE") or "hu").strip()


def whisper_modal_app() -> str:
    return (os.environ.get("PARLAMONITOR_WHISPER_MODAL_APP")
            or "parlamonitor-whisper").strip()


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
        "Parlamonitor/1.0 (+https://github.com/k-monitor; info@k-monitor.hu)"
    )

    # Optional SSH tunnel: route parlament.hu traffic through an SSH host so it
    # egresses from a known IP (see parlamonitor/ssh_proxy.py). Active once
    # ``ssh_host`` and ``ssh_key`` are both set; takes precedence over ``proxy``.
    ssh_host: str | None = None
    ssh_port: int = 22
    ssh_user: str | None = None
    ssh_key: str | None = None              # path to the private key file
    ssh_key_passphrase: str | None = None
    ssh_known_hosts: str | None = None      # path; None -> trust-on-first-use

    @classmethod
    def from_env(cls, **overrides) -> "RuntimeConfig":
        """Build from ``PARLAMONITOR_*`` env vars; explicit ``overrides`` win."""
        def _f(name: str, default: float) -> float:
            raw = os.environ.get(name)
            return float(raw) if raw not in (None, "") else default

        def _s(name: str) -> str | None:
            return os.environ.get(name) or None

        cfg = cls(
            sleep=_f("PARLAMONITOR_SLEEP", cls.sleep),
            retry_count=int(_f("PARLAMONITOR_RETRY_COUNT", cls.retry_count)),
            retry_delay_max=_f("PARLAMONITOR_RETRY_DELAY_MAX", cls.retry_delay_max),
            timeout=_f("PARLAMONITOR_TIMEOUT", cls.timeout),
            proxy=_s("PARLAMONITOR_PROXY"),
            user_agent=os.environ.get("PARLAMONITOR_USER_AGENT") or cls.user_agent,
            ssh_host=_s("PARLAMONITOR_SSH_HOST"),
            ssh_port=int(_f("PARLAMONITOR_SSH_PORT", cls.ssh_port)),
            ssh_user=_s("PARLAMONITOR_SSH_USER"),
            ssh_key=_s("PARLAMONITOR_SSH_KEY"),
            ssh_key_passphrase=_s("PARLAMONITOR_SSH_KEY_PASSPHRASE"),
            ssh_known_hosts=_s("PARLAMONITOR_SSH_KNOWN_HOSTS"),
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
        self.photos = self.data / "media" / "photos"
        self.logs = self.data / "logs"
        self.lockfile = self.data / "parlamonitor.lock"
        # Small state file for the continuous `sync` watcher: the per-day / bills /
        # votes / reps signatures of the last check, so an idle poll can decide
        # nothing changed without re-fetching anything heavy (SCR-2 / SCR-4).
        self.sync_state = self.data / "sync-state.json"

    def ensure(self) -> None:
        for d in (self.raw_plenary, self.processed, self.logs):
            d.mkdir(parents=True, exist_ok=True)

    # --- per-sitting files -------------------------------------------------

    def raw_day(self, session: str) -> Path:
        """Raw scraped bundle for one sitting, pre-transform."""
        return self.raw_plenary / f"raw-{session}-day.json"

    def whisper_cache(self, session: str) -> Path:
        """Cached Whisper word-timestamps for one sitting (TIM-1).

        Keyed by the recording URL + model inside the file, so an unchanged sitting
        is never re-transcribed on a re-run — the GPU cost tracks only new work."""
        return self.raw_plenary / f"whisper-{session}.json"

    def session_file(self, session: str) -> Path:
        """The published per-sitting session record."""
        return self.processed / f"{session}-session.json"

    # --- representatives ---------------------------------------------------

    def representatives_file(self, cycle: int) -> Path:
        return self.processed / f"representatives-{int(cycle)}.json"

    # --- nationality advocates (nemzetiségi szószólók) ----------------------
    # A file of their own rather than extra rows in the MP registry: the two come
    # from different upstream queries, and keeping them separate means adding
    # advocates to an existing scrape doesn't invalidate (or require re-running)
    # the MP roster stage.

    def advocates_file(self, cycle: int) -> Path:
        return self.processed / f"advocates-{int(cycle)}.json"

    # --- bills -------------------------------------------------------------

    def bills_file(self, cycle: int) -> Path:
        return self.processed / f"bills-{int(cycle)}.json"

    # --- votes -------------------------------------------------------------

    def votes_file(self, cycle: int) -> Path:
        return self.processed / f"votes-{int(cycle)}.json"
