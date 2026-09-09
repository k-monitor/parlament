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
      documents/
        <cycle>/
          text/<docid>.txt.xz                     # extracted document text (DOC-1)
          pdf/<docid>.pdf                         # the source PDF, when retained
          index.json                              # per-document manifest
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


# --- local faster-whisper tuning -------------------------------------------
# Mirrors the knobs the Modal image already reads (whisper_modal_app.py) so the
# two backends are configured the same way. They matter for a local GPU backfill
# of the archive: the CPU defaults are ~20x slower, and CTranslate2 picks a
# device silently, so a missing CUDA runtime otherwise looks like a slow run
# rather than a misconfiguration.

def whisper_device() -> str:
    """``auto`` | ``cuda`` | ``cpu`` for the local ``faster-whisper`` backend.

    ``auto`` is resolved to a concrete device before the model is built (see
    :func:`whisper_align._resolve_device`) so the choice can be logged."""
    return (os.environ.get("PARLAMONITOR_WHISPER_DEVICE") or "auto").strip().lower()


def whisper_compute_type(device: str) -> str:
    """CTranslate2 compute type for ``device``. ``int8`` keeps a CPU run light;
    on a GPU ``float16`` is both faster and more accurate, which is why the Modal
    image runs it."""
    default = "float16" if device == "cuda" else "int8"
    return (os.environ.get("PARLAMONITOR_WHISPER_COMPUTE_TYPE") or default).strip()


def whisper_batch_size(device: str) -> int:
    """Intra-day batch size for ``BatchedInferencePipeline``. A GPU has the
    memory to keep more windows in flight (12 GB fits the 16 the Modal image
    uses); on CPU the batch buys little and costs RAM."""
    raw = os.environ.get("PARLAMONITOR_WHISPER_BATCH_SIZE")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 16 if device == "cuda" else 8


def whisper_modal_app() -> str:
    return (os.environ.get("PARLAMONITOR_WHISPER_MODAL_APP")
            or "parlamonitor-whisper").strip()


# --- iromány document files (DOC-1) ----------------------------------------
# The irományok registry only ever held *links* to the document PDFs. Mirroring
# the files themselves is what makes NLP over the actual document text possible
# — but it is also the single biggest thing the pipeline can put on disk: cycle
# 43 alone is 861 documents / ~870 MB of PDF, and a full four-year cycle is many
# times that. The live server has no use for either the PDFs or the text, so the
# whole stage is **off unless asked for**, exactly like the Modal budget guard
# above: a dev box opts in, production stays byte-for-byte as it was.
#
# Keeping only the extracted text is ~200x smaller than keeping the PDFs
# (4 MB vs 868 MB for cycle 43) and is all the NLP actually consumes, so
# `text` — not `all` — is what an opt-in should normally choose.

DOCUMENT_RETENTIONS = ("off", "text", "pdf", "all")


def documents_retention() -> str:
    """``off`` (default) | ``text`` | ``pdf`` | ``all`` — what the document
    stage keeps on disk.

    ``off`` downloads nothing at all; ``text`` keeps only the extracted (and
    compressed) text; ``pdf`` keeps only the source file; ``all`` keeps both.
    An unrecognised value reads as ``off`` — like the Modal guard, this setting
    exists to *bound* what we store, so a typo must never turn storage on."""
    raw = (os.environ.get("PARLAMONITOR_DOCUMENTS") or "off").strip().lower()
    if raw in ("", "none", "no", "0", "false"):
        return "off"
    if raw in ("both", "pdf+text", "text+pdf", "yes", "1", "true"):
        return "all"
    return raw if raw in DOCUMENT_RETENTIONS else "off"


def documents_compression() -> str:
    """``xz`` (default) | ``gzip`` | ``none`` for the stored document text.

    ``xz`` is stdlib ``lzma`` and the best of the options measured over all
    861 cycle-43 documents (16.3 MB of extracted text → 3.96 MB, against
    4.65 MB gzipped and 3.97 MB for ``zstd -19``); ``gzip`` is there for a store
    other tools read without ceremony, and ``none`` for reading it by hand."""
    raw = (os.environ.get("PARLAMONITOR_DOCUMENTS_COMPRESSION") or "xz").strip().lower()
    if raw in ("lzma", "xz"):
        return "xz"
    if raw in ("gz", "gzip"):
        return "gzip"
    return "none" if raw in ("none", "off", "raw", "") else "xz"


def documents_max_mb() -> float:
    """Skip documents larger than this many MB (``0`` = no limit, the default).

    The tail is heavy — the largest single iromány in cycle 43 is 58 MB — and an
    oversized scan is also the least useful document to hold, so a dev box with
    a small disk can cap it. The cap is applied while streaming, so an
    over-cap document is never fully downloaded."""
    raw = os.environ.get("PARLAMONITOR_DOCUMENTS_MAX_MB")
    try:
        return max(0.0, float(raw)) if raw not in (None, "") else 0.0
    except ValueError:
        return 0.0


# --- Modal budget scope ----------------------------------------------------
# Modal GPU time is metered, and transcribing the archive is by far the biggest
# bill the pipeline can run up: a backfill of an old cycle is hundreds of
# whole-day recordings, all of them days nobody is waiting for, and it can drain
# the credit the LIVE cycle needs to stay timed. So Modal is scoped to the newest
# cycle by default; out-of-scope days simply keep the positional character
# estimate for their sentence timing (TIM-3), which is what the pipeline already
# degrades to when no transcription backend exists at all (SCR-6).
#
# The backend (backend/app/config.py) reads the SAME variable for the word-cloud /
# NER offload, so one setting scopes every Modal cost the project can incur.

def modal_cycles() -> str | frozenset:
    """``PARLAMONITOR_MODAL_CYCLES`` parsed: ``"all"``, ``"latest"`` (default) or
    the set of cycle numbers allowed to use Modal. An unparsable value reads as
    ``"latest"`` — the setting exists to *limit* spend, so a typo must never open
    the offload up to the whole archive."""
    raw = (os.environ.get("PARLAMONITOR_MODAL_CYCLES") or "latest").strip().lower()
    if raw in ("all", "*"):
        return "all"
    if raw in ("", "latest", "current"):
        return "latest"
    cycles = {int(p) for p in raw.replace(";", ",").split(",")
              if p.strip().lstrip("-").isdigit()}
    return frozenset(cycles) if cycles else "latest"


def session_cycle(session: str) -> int | None:
    """The electoral cycle a session id belongs to (``"43007"`` → ``43``), or
    ``None`` if it doesn't look like one — the inverse of :func:`session_id`."""
    s = str(session or "").strip()
    return int(s[:-3]) if len(s) > 3 and s.isdigit() else None


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
    # A CAPTCHA challenge page (rate limiting) is retried on its own schedule —
    # ten minutes apart, no exponential backoff — and this is how many such
    # stand-offs a run tolerates before giving up entirely (~1.5 h of being
    # walled). See http_client.HttpClient._captcha_wait.
    captcha_retries: int = 9
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
            captcha_retries=int(_f("PARLAMONITOR_CAPTCHA_RETRIES",
                                   cls.captcha_retries)),
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

    # --- office holders (tisztségviselők) -----------------------------------
    # One cycle-less file: the upstream registry is a single all-time listing of
    # government/House offices (*tisztségek*) with their real start/end dates, for
    # MPs and non-MPs alike, so there is nothing per-cycle to key it by.

    def officeholders_file(self) -> Path:
        return self.processed / "officeholders.json"

    # --- bills -------------------------------------------------------------

    def bills_file(self, cycle: int) -> Path:
        return self.processed / f"bills-{int(cycle)}.json"

    # --- votes -------------------------------------------------------------

    def votes_file(self, cycle: int) -> Path:
        return self.processed / f"votes-{int(cycle)}.json"

    # --- iromány document files (DOC-1) -------------------------------------
    # Deliberately NOT under ``processed/``: these are a mirror of upstream
    # binaries plus text derived from them, they are optional (see
    # :func:`documents_retention`), and they dwarf everything else on disk — so
    # they live in a tree of their own that can simply be deleted.

    def documents_dir(self, cycle: int) -> Path:
        return self.data / "documents" / str(int(cycle))

    def documents_index(self, cycle: int) -> Path:
        """Per-document manifest: what was fetched, its hash, where it is
        stored, and why anything was skipped. Also the incremental cache — a
        document already recorded here is not re-fetched (SCR-2)."""
        return self.documents_dir(cycle) / "index.json"

    def document_text_dir(self, cycle: int) -> Path:
        return self.documents_dir(cycle) / "text"

    def document_pdf_dir(self, cycle: int) -> Path:
        return self.documents_dir(cycle) / "pdf"
