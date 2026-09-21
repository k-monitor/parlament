"""Environment-driven backend configuration (OPS-4).

Nothing operational is hard-coded: the DB path, CORS origins, photo directory
and the set of *enabled modules* (EXT-6) all come from the environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# Every module the backend knows how to mount. A module absent from
# PARLAMONITOR_MODULES is never registered: its API routes 404 and the frontend,
# which reads /api/v1/meta, hides its nav entry (EXT-6).
ALL_MODULES = ("proceedings", "representatives", "bills", "votes", "portfolios",
               "settlements", "interjections", "committees")

# Per-speech types (felszólalás típusa) whose speeches are procedural/chairing
# and therefore excluded from representative/faction statistics (STAT-1). Kept
# configurable (OPS-4) so further types can be added without a code change.
#
# These are the presiding officer's own procedural utterances: running the
# sitting, opening/closing each debate, and reading out what the House has just
# decided. They are not contributions to the debate, and counting them wrecks the
# statistics twice over — the chair racks up thousands of them, and the media
# segment behind an announcement typically spans the whole voting block it
# concludes (corpus-wide, "Országgyűlés határozatképes" averages 114 minutes),
# so each one also contributes hours of phantom speaking time.
#
# Enumerated explicitly rather than pattern-matched: the list is auditable at a
# glance and can never silently swallow a substantive type (TRUST-1). The trade
# is that a type parlament.hu introduces later is counted until it is added here
# — the sanity check is the speaker base, since every type below is spoken by at
# most 27 people (the presiding officers) while substantive types have 100–478.
#
# Deliberately NOT listed, as judgment calls rather than oversights — add them
# via PARLAMONITOR_PROCEDURAL_SPEECH_TYPES if you disagree:
#   "jegyzői ismertetés"   — a procedural reading, but by the notaries (43 MPs,
#                            ~1 min each), so it distorts nothing
#   "Eskü", "Expozé"       — ceremonial / the presenter's own exposition
#   "ügyrendi kérdés", "ügyrendi javaslat" — MP-initiated points of order, i.e.
#                            a real intervention by that MP, not chairing
DEFAULT_PROCEDURAL_SPEECH_TYPES = (
    # Running the sitting day.
    "ülésvezetés",
    "Az ülésnap megnyitása",
    "Az ülésnap bezárása",
    "ülés napirendjének elfogadása",
    "ülés napirendjének módosítása/kiegészítése",
    "bejelentés",
    "mandátum igazolás",

    # Opening / closing / adjourning a debate.
    "általános vita megkezdve",
    "általános vita lezárva",
    "általános vita folytatása",
    "általános vita elnapolása",
    "újra megnyitott általános vita megkezdve",
    "újra megnyitott általános vita lezárva, kiegészítő részletesvita-szakasz megnyitva",
    "vita megkezdve",
    "vita lezárva",
    "összevont vita megkezdve",
    "összevont vita lezárva",
    "összevont vita folytatása",
    "tárgyalás megkezdve",
    "tárgyalás lezárva",
    "tárgysorozatba-vételi kérelem tárgyalása megkezdve",
    "tárgysorozatba-vételi kérelem tárgyalása lezárva",
    "bizottsági jelentés(ek) vitája megkezdve",
    "bizottsági jelentés(ek) vitája lezárva",
    "bizottsági jelentés és módosító javaslat vitája megkezdve",
    "bizottsági jelentés és módosító javaslat vitája lezárva",
    "bizottsági jelentések és az összegző módosító javaslat vitája megkezdve",
    "bizottsági jelentések és az összegző módosító javaslat vitája lezárva",
    "zárószavazás előtti jelentés és a zárószavazás előtti módosító javaslat vitája megkezdve",
    "zárószavazás előtti jelentés és a zárószavazás előtti módosító javaslat vitája lezárva",
    "Törvényalkotási Bizottság jelentésének vitája megkezdve",
    "Törvényalkotási Bizottság jelentésének vitája lezárva",
    "Törvényalkotási Bizottság jelentése és az elfogadott, de ki nem hirdetett "
    "törvényhez benyújtott módosító javaslat vitája megkezdve",
    "Törvényalkotási Bizottság jelentése és az elfogadott, de ki nem hirdetett "
    "törvényhez benyújtott módosító javaslat vitája lezárva",
    "Törvényalkotási Bizottság jelentése és az alaptörvény-ellenesség kiküszöbölése "
    "érdekében benyújtott módosító javaslat vitája megkezdve",
    "Törvényalkotási Bizottság jelentése és az alaptörvény-ellenesség kiküszöbölése "
    "érdekében benyújtott módosító javaslat vitája lezárva",
    "normakontroll-javaslat, normakontroll kezdeményezését előkészítő jelentés és a "
    "normakontroll kezdeményezését előkészítő módosító javaslat vitája megkezdve",
    "normakontroll-javaslat, normakontroll kezdeményezését előkészítő jelentés és a "
    "normakontroll kezdeményezését előkészítő módosító javaslat vitája lezárva",

    # Announcing a vote and its outcome.
    "egyéb szavazás",
    "határozatképtelen szavazás",
    "Országgyűlés határozatképes",
    "Országgyűlés határozatképtelen",
    "önálló indítvány elfogadva",
    "önálló indítvány elutasítva",
    "módosító javaslat(ok) elfogadva",
    "módosító javaslat fenntartása elutasítva",
    "összegző módosító javaslat elfogadva",
    "összegző módosító javaslat minősített többséget igénylő része elfogadva",
    "zárószavazás előtti módosító javaslat elfogadva",
    "zárószavazás elhalasztása elfogadva",
    "javaslat zárószavazás elhalasztására benyújtva",
    "elfogadott, de ki nem hirdetett törvényhez benyújtott módosító javaslat elfogadva",
    "elfogadott, de ki nem hirdetett törvényhez benyújtott módosító javaslat elutasítva",
    "sürgősségi javaslat elfogadva",
    "sürgősségi javaslat elutasítva",
    "kivételességi javaslat elfogadva",
    "kivételességi javaslat elutasítva",
    "napirendi pont tárgyalásának elnapolása elfogadva",
    "napirendi pont tárgyalásának elnapolása elutasítva",
    "határozati házszabályi rendelkezésektől való eltéréshez hozzájárulás elfogadva",
    "határozati házszabályi rendelkezésektől való eltérés elutasítva",
    "beszámolóról történő határozathozatalra felkérés elfogadva",
    "túlterjeszkedő módosító javaslat szabályszerű",
    "visszavontnak tekintendő",
    "Országgyűlés az interpellációs választ elfogadta",
    "Országgyűlés időkeretben történő tárgyaláshoz hozzájárult",
    "Országgyűlés időkeretben történő tárgyalást elutasította",
    "Országgyűlés a tárgysorozatba vételt elutasította",
    "Országgyűlés az indítvány visszavonásához hozzájárult",
    "Országgyűlés a népszavazást elrendelte",

    # Announcing a decision about a member.
    "mentelmi jog felfüggesztve",
    "mentelmi jog fenntartva",
    "Országgyűlés az összeférhetetlenséget kimondta",
    "Országgyűlés kizárta a képviselőt az ülésnapról",
    # NB: the double space is verbatim in the source data — matching is
    # strip()+casefold() only, it does not collapse inner whitespace.
    "Országgyűlés a képviselő tiszteletdíjának csökkentését  fenntartotta",
    "Országgyűlés a képviselő tiszteletdíjának csökkentését és kitiltását fenntartotta",
)

# How a chair turn opens in the transcript, for the speeches upstream left
# untyped (see `Settings.chair_transcript_prefixes`). Matched against the first
# sentence, strip()+casefold()ed, as a PREFIX — the transcript renders these as a
# speaker tag ("ELNÖK: Köszönöm szépen."), so anchoring at the start is what keeps
# a mere mention of the Speaker mid-sentence from counting. One entry is enough:
# the name-tagged variant ("SZABAD GYÖRGY elnök:") accounts for 29 further rows
# corpus-wide, and matching "elnök" unanchored would swallow every speech that
# merely addresses the chair.
DEFAULT_CHAIR_TRANSCRIPT_PREFIXES = (
    "ELNÖK",
)


@dataclass
class Settings:
    db_path: str = field(default_factory=lambda: os.environ.get(
        "PARLAMONITOR_DB", str(Path(__file__).resolve().parents[1] / "parlamonitor.db")))
    photos_dir: str | None = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_PHOTOS_DIR") or _default_photos_dir())
    cors_origins: list[str] = field(default_factory=lambda: [
        o.strip() for o in os.environ.get(
            "PARLAMONITOR_CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173").split(",")
        if o.strip()])
    enabled_modules: list[str] = field(default_factory=lambda: _enabled_modules())
    frontend_dist: str | None = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_FRONTEND_DIST") or None)
    # Canonical public base URL (e.g. "https://parlamonitor.k-monitor.hu"), used to build
    # the absolute og:url / og:image links in the server-rendered share cards
    # (og.py). Behind a reverse proxy the request's own scheme/host is often wrong
    # (http, internal hostname), so this env pins the outward-facing origin; when
    # unset the cards fall back to the request's base URL.
    site_url: str | None = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_SITE_URL") or "").strip().rstrip("/") or None)
    # Which electoral cycles the site SERVES (§4A CYC-7). Unset (or "all") means
    # the whole corpus — every cycle the DB was built with, the default. Set to a
    # comma-separated list of cycle numbers ("43", "42,43") to run the site as a
    # window onto those cycles only: the header's cycle chooser offers nothing
    # else, every period-aware query is clamped to them (a request naming an
    # out-of-window cycle — or none at all, "all cycles" — is answered over the
    # window, never wider), a sitting/speech/bill/vote page outside them 404s,
    # and the sitemaps stop advertising those pages.
    #
    # A serving-time window, not a build-time one: the DB keeps every cycle it
    # was loaded with, so widening or removing the window shows them again with
    # no re-load. Use it to launch with the current cycle while older ones are
    # still being checked, or to run a cycle-specific edition of the site.
    site_cycles: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_SITE_CYCLES", "").strip())
    # Cap on reported search totals so a pathological query can't scan forever.
    max_search_total: int = int(os.environ.get("PARLAMONITOR_MAX_SEARCH_TOTAL", "5000"))
    # Shortest search term still expanded to a PREFIX term (SEA-1). Bare words
    # become `word*` so Hungarian morphology matches without stemming, which is
    # right for real words and pathological for very short ones: `a*` expands to
    # every word in the corpus beginning with "a" — 5.9M of 9.0M sentences, ~2.8 s
    # just to merge the doclists, where the exact term `a` costs 0.7 ms. A term
    # below this length is matched exactly instead. The default of 2 disarms only
    # the single-character case, which is a corpus scan rather than a search; two-
    # letter terms ("EU", "uj") keep their suffixes and stay prefix terms.
    min_prefix_len: int = int(os.environ.get("PARLAMONITOR_MIN_PREFIX_LEN", "2"))
    # Wall-clock ceiling, in seconds, on the SQL behind one search request
    # (app/db.py `query_budget`). The read path is shared and stateless, so a
    # single request scanning a large share of the corpus holds a worker thread
    # for as long as it takes; enough of them together are an availability
    # problem, not a slow page. Over budget, the request is abandoned and answered
    # 503 (Retry-After) instead of the origin being held.
    #
    # A backstop, not a policy: it must never fire on a query someone might mean.
    # The expensive half of a search is bm25, which has to score every match to
    # find the best twenty and cannot be indexed around — so the cost is set by
    # how much of the corpus a term matches. On the 10-cycle corpus the worst
    # *reachable* query is a very common two-letter prefix over all cycles
    # ("el" — the Hungarian verbal prefix — at ~7 s; ~4 s of it bm25 alone), and
    # a cycle-scoped one is 2-3x cheaper again. 20 s leaves that real headroom
    # under load while still bounding anything unforeseen. Tighten it only after
    # measuring your own corpus; 0 disables it.
    search_timeout: float = float(os.environ.get("PARLAMONITOR_SEARCH_TIMEOUT", "20"))
    # Read-path SQLite mmap ceiling in bytes (OPS-4). SQLite memory-maps up to this
    # much of the DB file into the shared OS page cache — it is a ceiling, not a
    # reservation. Default 1 GiB; raise it above the DB file's size so the whole
    # file is mapped (a DB larger than the ceiling reads its tail via ordinary
    # read() syscalls instead of the page cache).
    sqlite_mmap_size: int = int(os.environ.get(
        "PARLAMONITOR_SQLITE_MMAP_SIZE", str(1024 * 1024 * 1024)))
    # In-process TTL/LRU cache for expensive read-only aggregates — the search
    # trend/breakdown and the module /facets endpoints (app/query_cache.py).
    # `ttl` is seconds (0 disables the cache entirely); `size` is the max number
    # of distinct (query+filters) entries kept per cached endpoint. Defense in
    # depth behind the CDN (caching.py): absorbs cache-miss bursts and un-CDN'd
    # deployments. The DB file's identity is folded into every key, so the
    # loader's atomic swap (DB-4) invalidates it automatically.
    query_cache_ttl: int = int(os.environ.get("PARLAMONITOR_QUERY_CACHE_TTL", "300"))
    query_cache_size: int = int(os.environ.get("PARLAMONITOR_QUERY_CACHE_SIZE", "256"))
    # Search analytics (PRIV-1). Privacy-respecting, GDPR-friendly logging of what
    # people search for — the search KEYWORDS and the FILTERS combined with them —
    # WITHOUT any personal data: no IP addresses, no user agents, no cookies, and
    # NO exact timestamps (events are counted into whole-hour buckets). Only the
    # aggregate per-hour COUNT is persisted, and to a SEPARATE SQLite file (never
    # the read-only content DB, app/analytics.py). Enabled by default; set
    # PARLAMONITOR_SEARCH_ANALYTICS=0 to turn it off.
    search_analytics: bool = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_SEARCH_ANALYTICS", "1").strip().lower()
         not in ("0", "false", "no", "")))
    # Where the aggregated search-analytics file lives. Point it at a host-mounted
    # volume so the aggregates are readable from OUTSIDE the container. Unset →
    # `search-analytics.db` next to the main DB (resolved lazily in analytics.py so
    # it honours a db_path overridden after construction, e.g. in tests).
    analytics_db: str | None = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_ANALYTICS_DB") or None)
    # Daily CSV export of the aggregated search analytics. Once per UTC day each
    # completed day's aggregates are written as a plain CSV file
    # (search-analytics-YYYY-MM-DD.csv) so the stats can be read from OUTSIDE the
    # container without opening SQLite. Enabled by default (set
    # PARLAMONITOR_ANALYTICS_CSV=0 to keep only the SQLite store).
    analytics_csv_export: bool = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_ANALYTICS_CSV", "1").strip().lower()
         not in ("0", "false", "no", "")))
    # Where the CSV files land. Unset → a `csv/` sub-directory next to the
    # analytics DB (so on the default deploy they land in the host-mounted
    # ./analytics/csv, readable from outside the container).
    analytics_csv_dir: str | None = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_ANALYTICS_CSV_DIR") or None)
    # Folded set of speech types excluded from statistics (STAT-1).
    procedural_speech_types: frozenset = field(default_factory=lambda: _procedural_speech_types())
    # Transcript fallback for the same rule, used ONLY where upstream supplies no
    # type at all. Cycle 34 (1990–94) arrives with `felszolalasTipusa` unset on
    # 99.8% of its speeches, so the type list above cannot see its chair turns:
    # ~38.8k of them (half the cycle, 511 hours) would count as substantive and
    # put the period's Speaker and deputies at the top of every speech ranking.
    # Their transcripts do carry the marker the type field lost — a chair turn is
    # transcribed opening with "ELNÖK:" — so a folded prefix match on the first
    # sentence recovers what the label should have said. Deliberately narrow: an
    # explicit type always wins, so this can only ever add a flag where upstream
    # said nothing. Measured over the whole 10-cycle corpus it matches 38,756 of
    # cycle 34's 38,785 chair turns and exactly ZERO speeches in any other cycle
    # (41,363 untyped rows there, all left alone).
    chair_transcript_prefixes: tuple = field(
        default_factory=lambda: _chair_transcript_prefixes())
    # Word-cloud term-extraction backend (WCLOUD-2). "huspacy" lemmatizes and
    # extracts named entities with the HuSpaCy model locally; "modal" runs that
    # same HuSpaCy pipeline on Modal (modal.com) GPU/CPU workers so a
    # resource-constrained host offloads the heavy NER/lemmatization; "regex" uses
    # the dependency-free tokenizer; "auto" (default) prefers local HuSpaCy when
    # its model is installed and falls back to regex otherwise ("auto" never
    # auto-selects Modal — it is opt-in). The processing runs at load time and is
    # cached, never at request time (OPS-4).
    wordcloud_backend: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_WORDCLOUD_BACKEND", "auto").strip().lower())
    # Which HuSpaCy model to lemmatize/NER with — the transformer
    # (`hu_core_news_trf`, best accuracy; run it on Modal GPU workers via
    # wordcloud_backend="modal") by default; a lighter CPU model
    # (`hu_core_news_md`/`_lg`) can be swapped in via env for local runs.
    # The model name is part of method_tag(), so changing it busts the
    # word-cloud + entity caches and re-runs NER over the affected sittings.
    # This model applies to the CURRENT (newest) electoral period only — see
    # `huspacy_model_archive` for the frozen earlier cycles.
    huspacy_model: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_HUSPACY_MODEL", "hu_core_news_trf").strip())
    # Cheaper model for ARCHIVE cycles (every electoral period except the
    # newest). Their transcripts are frozen, so whatever this model produced
    # stays cached forever and the expensive transformer only ever processes
    # the live cycle. Set it equal to `huspacy_model` to use one model
    # everywhere. When a new cycle starts, the previously-current cycle re-NERs
    # once with this model (cheap) as it ages into the archive.
    huspacy_model_archive: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_HUSPACY_MODEL_ARCHIVE", "hu_core_news_md").strip())
    # Modal offload (wordcloud_backend="modal"). The deployed Modal app name to
    # look the NLP service up under, and how many sentences to pack into each
    # remote batch — larger batches = fewer, fatter calls (less overhead), bounded
    # so a batch's payload/memory stays reasonable. Modal auth comes from the
    # standard MODAL_TOKEN_ID/MODAL_TOKEN_SECRET env (or ~/.modal.toml).
    modal_app_name: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_MODAL_APP", "parlamonitor-nlp").strip())
    # The Modal app serving `huspacy_model_archive` (a cheap CPU deployment of
    # the same modal_app.py, e.g. `PARLAMONITOR_MODAL_APP=parlamonitor-nlp-md
    # PARLAMONITOR_HUSPACY_MODEL=hu_core_news_md modal deploy modal_app.py`).
    # Only contacted when an archive sitting actually misses the cache.
    modal_app_name_archive: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_MODAL_APP_ARCHIVE", "parlamonitor-nlp-md").strip())
    modal_batch_sentences: int = int(
        os.environ.get("PARLAMONITOR_MODAL_BATCH_SENTENCES", "5000"))
    # Which electoral cycles may be processed ON MODAL at all (budget guard).
    # Modal time is metered, so a backfill of the archive — thousands of frozen
    # sitting days nobody is watching — can burn a month's credit in one run and
    # then leave the CURRENT cycle, the one the site actually shows, unprocessed.
    # So the offload is scoped:
    #   "latest" (default) — only the newest electoral period is ever dispatched
    #   "all"              — every cycle (the pre-guard behaviour)
    #   "43" / "42,43"     — exactly these cycle numbers
    # An out-of-scope sitting is NOT sent to Modal; it falls back to whatever runs
    # locally — the HuSpaCy model if one is installed, else the regex tokenizer for
    # the word cloud (entity extraction, which needs a model, is skipped). Cached
    # results are always reused first, so an archive sitting processed back when it
    # was in scope keeps exactly what it had.
    # The scraper honours the same variable for the Whisper offload
    # (scraper/parlamonitor/config.py), so one setting scopes all Modal spend.
    modal_cycles: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_MODAL_CYCLES", "latest").strip().lower())
    # Per-speech readability + lexical diversity (READ-1..7, app/readability.py).
    # When enabled the loader measures every substantive speech with `saphes`:
    # LIX/RIX from the transcript's surface text (no model needed) and TTR/MATTR
    # from the HuSpaCy lemma stream (shares the word cloud's backend/model/cycle
    # scope; omitted, never faked from surface forms, when no lemmatizer is
    # reachable). Set PARLAMONITOR_SPEECH_METRICS=0 to skip the pass and hide the
    # annotations. The metric parameters themselves — the LIX long-word threshold,
    # the word-length policy, the MATTR window and the minimum speech length — are
    # read in app/readability.py (PARLAMONITOR_LIX_THRESHOLD,
    # PARLAMONITOR_LIX_LENGTH_POLICY, PARLAMONITOR_MATTR_WINDOW,
    # PARLAMONITOR_READABILITY_MIN_WORDS).
    speech_metrics: bool = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_SPEECH_METRICS", "1").strip().lower()
         not in ("0", "false", "no", "")))
    # CAP policy-topic classification of speech paragraphs (TOPIC-1..7,
    # app/parlacap.py). Off switches the whole pass, so the DB carries no topics
    # and the SPA hides the annotation rather than rendering blanks.
    parlacap: bool = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_PARLACAP", "1").strip().lower()
         not in ("0", "false", "no", "")))
    parlacap_model: str = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_PARLACAP_MODEL", "").strip()
         or "classla/ParlaCAP-Topic-Classifier"))
    # Where classification runs: "auto", "modal", "local", or "off". As with
    # PARLAMONITOR_WORDCLOUD_BACKEND, **"auto" never auto-selects Modal** — that
    # path is metered, so spending credit is always an explicit choice, and a GPU
    # box (which usually holds Modal credentials for the other offloads) must not
    # start billing an archive backfill just because it was left unconfigured.
    # The production host sets "modal" — it
    # has neither the disk for the torch stack nor the cores to run a 560M-param
    # encoder (~1.5 blocks/s on 4 threads, i.e. 11 minutes per sitting day) — while
    # the GPU box that classified the archive uses "local". Both produce identical
    # output, so the backend is NOT part of the method tag and the two share one
    # cache file. Modal is additionally gated by PARLAMONITOR_MODAL_CYCLES (below,
    # default `latest`), which is what keeps a 600k-block archive backfill off a
    # metered GPU.
    parlacap_backend: str = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_PARLACAP_BACKEND", "").strip().lower()
         or "auto"))
    parlacap_modal_app: str = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_PARLACAP_MODAL_APP", "").strip()
         or "parlamonitor-parlacap"))
    # Blocks per Modal request. Sittings are packed to roughly this many blocks so
    # a bounded pool of warm containers works through them in parallel; one sitting
    # day is ~1000 blocks, so the default sends a typical update as one call.
    parlacap_modal_batch: int = int(
        os.environ.get("PARLAMONITOR_PARLACAP_MODAL_BATCH", "1000"))
    # The confidence a paragraph's label must reach to count toward its speech's
    # topic. Applied at READ time (app/parlacap.aggregate), never baked into the
    # stored predictions and deliberately absent from the method tag — so retuning
    # it costs a restart, not a reclassification or even a rebuild. Higher trades
    # coverage for accuracy: measured on this corpus, 0.60 keeps 90% of paragraphs
    # at 0.741 accuracy, 0.90 keeps 69% at 0.809, 0.95 keeps 62% at 0.832.
    parlacap_threshold: float = float(
        os.environ.get("PARLAMONITOR_PARLACAP_THRESHOLD", "0.90"))
    # Blocks shorter than this are never sent to the model: below roughly a
    # sentence there is not enough context to place a topic, and a confident label
    # on "Köszönöm a szót." is worse than no label at all.
    parlacap_min_words: int = int(
        os.environ.get("PARLAMONITOR_PARLACAP_MIN_WORDS", "12"))
    # Block assembly (app/parlacap.build_blocks). The corpus does not segment
    # paragraphs consistently across its five cycles — cycle 41 has no paragraph
    # marks at all and cycles 39-40 mark source *line* breaks, so their
    # "paragraphs" are line fragments — so classification units are built to a word
    # budget that prefers paragraph boundaries rather than trusting them. TARGET is
    # the size a block closes at when a paragraph boundary is available (the median
    # real paragraph here is ~65 words, so real paragraphs mostly survive 1:1);
    # MAX is the hard cap, in words, that keeps a block inside the model's 512
    # tokens (measured at 1.89 tokens/word on this corpus, 2.23 at p95 → 220 words
    # effectively never truncates). Both are part of the method tag.
    parlacap_target_words: int = int(
        os.environ.get("PARLAMONITOR_PARLACAP_TARGET_WORDS", "60"))
    parlacap_max_words: int = int(
        os.environ.get("PARLAMONITOR_PARLACAP_MAX_WORDS", "220"))
    # The model's own limit is 512; lowering it speeds the pass up at the cost of
    # truncating long paragraphs. Part of the method tag — changing it invalidates
    # the cache, because it changes what the model saw.
    parlacap_max_length: int = int(
        os.environ.get("PARLAMONITOR_PARLACAP_MAX_LENGTH", "512"))
    parlacap_batch_size: int = int(
        os.environ.get("PARLAMONITOR_PARLACAP_BATCH_SIZE", "32"))
    # Blank = pick automatically (CUDA when present). Set to "cpu" to force the
    # slow path, or "cuda:1" to pin a second card.
    parlacap_device: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_PARLACAP_DEVICE", "").strip())
    parlacap_fp16: bool = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_PARLACAP_FP16", "1").strip().lower()
         not in ("0", "false", "no", "")))
    # Classify irományok as well as speeches (TOPIC-8). Separate from `parlacap`
    # above because the two have different inputs and different prerequisites: the
    # speech pass reads the DB, while this one reads the *document text* the
    # scraper's optional `documents` stage mirrors (DOC-1) — or, on a server that
    # has no such store, replays whatever the shipped cache already covers. Left on
    # by default precisely because that degradation is total: with neither
    # documents nor cache the pass writes nothing and the badge simply never shows.
    bill_topics: bool = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_BILL_TOPICS", "1").strip().lower()
         not in ("0", "false", "no", "")))
    # Where the scraper's mirrored document text lives (`documents/<cycle>/` with
    # its index.json). Unset → `documents/` under the loader's data directory,
    # which is where the scraper writes it. A server that was never given the
    # mirror leaves this unset and simply finds nothing, which is the normal case.
    documents_dir: str | None = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_DOCUMENTS_DIR") or None)
    # Shared lemma cache (app/lemma_cache.py). The word cloud and the lexical
    # diversity metric both need HuSpaCy lemmas for the *same* sentences, so the
    # cloud's pass emits them once and they are kept on disk, keyed by sentence id,
    # for every later pass and future feature. Set PARLAMONITOR_LEMMA_CACHE=0 to
    # turn the store off: nothing breaks, each pass simply lemmatizes for itself
    # again (the pre-existing behaviour) — worth doing only when the disk it would
    # occupy is scarcer than the model time it saves. See the module docstring for
    # the size it reaches on a full corpus.
    lemma_cache: bool = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_LEMMA_CACHE", "1").strip().lower()
         not in ("0", "false", "no", "")))
    # Person-entity linking (NEL, §10). When enabled the loader extracts PERSON
    # mentions from transcript sentences (HuSpaCy NER — shares the wordcloud
    # backend/model) into the `entity` table and resolves each distinct name to a
    # Wikidata item / Wikipedia article (app/wikidata.py) into `entity_link`, so
    # the transcript can link names inline. Extraction needs a model (skipped on
    # the regex backend); resolution needs network to `wikidata_endpoint` and
    # degrades gracefully when it is unreachable.
    entity_links: bool = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_ENTITY_LINKS", "1").strip().lower()
         not in ("0", "false", "no", "")))
    wikidata_endpoint: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_WIKIDATA_ENDPOINT",
                       "https://query.wikidata.org/sparql").strip())
    # Wikidata asks API clients to send a descriptive User-Agent.
    wikidata_user_agent: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_WIKIDATA_USER_AGENT",
                       "Parlamonitor/1.0 (+https://github.com/k-monitor; civic-tech)").strip())
    # K-Monitor linking (NEL primary target, app/kmonitor.py). When enabled the
    # loader fetches K-Monitor's person + institution tag lists and links recognized
    # names to their `adatbazis.k-monitor.hu` tag page (and sets `person.kmonitor_url`
    # for MP profiles); Wikipedia is used only as a fallback where no tag matches.
    # Independent of `entity_links` for the MP-profile side; the transcript side also
    # needs `entity_links` (the extracted mentions). Degrades gracefully when the
    # site is unreachable (names stay K-Monitor-unlinked, retried next run).
    kmonitor_links: bool = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_KMONITOR_LINKS", "1").strip().lower()
         not in ("0", "false", "no", "")))
    kmonitor_base_url: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_KMONITOR_BASE_URL",
                       "https://adatbazis.k-monitor.hu/").strip())
    kmonitor_persons_url: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_KMONITOR_PERSONS_URL",
                       "https://adatbazis.k-monitor.hu/adatbazis/szemelyek").strip())
    kmonitor_institutions_url: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_KMONITOR_INSTITUTIONS_URL",
                       "https://adatbazis.k-monitor.hu/adatbazis/intezmenyek").strip())
    # The site 403s a bare requests UA; reuse the descriptive Wikidata-style UA.
    kmonitor_user_agent: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_KMONITOR_USER_AGENT",
                       "Parlamonitor/1.0 (+https://github.com/k-monitor; info@k-monitor.hu)").strip())
    # "Which constituency am I in?" lookup (REP-10, app/valasztas.py). parlament.hu
    # says which OEVK an MP was elected in but never where it is; the National
    # Election Office (NVI) publishes the settlement → constituency mapping and the
    # constituency boundaries as static JSON on its result site. Enabled by default;
    # set PARLAMONITOR_EVK_LOOKUP=0 to hide the page and 404 its endpoints.
    evk_lookup: bool = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_EVK_LOOKUP", "1").strip().lower()
         not in ("0", "false", "no", "")))
    # Base of the election's data tree. Its `config.json` names the current data
    # VERSION directory, which every other file hangs off — so the version is
    # resolved at runtime, never hard-coded. Point this at the next election's tree
    # (…/ogy2030/data) when the time comes; nothing else changes.
    vtr_base_url: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_VTR_BASE_URL",
                       "https://vtr.valasztas.hu/ogy2026/data").strip())
    # How long a downloaded file is trusted before it is re-fetched. The electoral
    # map is fixed between elections, so this is deliberately long: a poll costs a
    # request and would learn nothing. A *stale* cache entry is still used when a
    # re-fetch fails, so the page keeps working through an upstream outage.
    vtr_cache_ttl: int = int(os.environ.get("PARLAMONITOR_VTR_CACHE_TTL",
                                           str(7 * 24 * 3600)))
    # Where the downloaded files are cached. Unset → `vtr-cache/` next to the DB,
    # which on the standard deploy is a mounted volume, so the cache survives a
    # container restart and a cold start costs no fetch.
    vtr_cache_dir: str | None = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_VTR_CACHE_DIR") or None)
    vtr_timeout: int = int(os.environ.get("PARLAMONITOR_VTR_TIMEOUT", "20"))
    vtr_user_agent: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_VTR_USER_AGENT",
                       "Parlamonitor/1.0 (+https://github.com/k-monitor; info@k-monitor.hu)").strip())
    # Basemap for the constituency-picker map (REP-10). Street context is what makes
    # the map answerable — a reader recognises their own neighbourhood, not an
    # abstract polygon. The tiles come from a third party, so both the URL template
    # and its **required attribution** are deployment config (OPS-4): point them at
    # your own tile server (or a paid provider) if OpenStreetMap's usage policy
    # doesn't fit the site's traffic. The lookup endpoint hands these to the
    # frontend, so the map needs no build-time configuration of its own.
    map_tile_url: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_MAP_TILE_URL",
                       "https://tile.openstreetmap.org/{z}/{x}/{y}.png").strip())
    map_tile_attribution: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_MAP_TILE_ATTRIBUTION",
                       '© <a href="https://www.openstreetmap.org/copyright" '
                       'target="_blank" rel="noopener">OpenStreetMap</a>').strip())
    map_max_zoom: int = int(os.environ.get("PARLAMONITOR_MAP_MAX_ZOOM", "18"))
    # How coarsely constituency boundaries are generalised for the settlement map's
    # constituency binning (§6D TEL-16), in degrees. The office draws them for a
    # street-level map — 99 000 vertices over the 106 of them — where that view shows
    # the whole country at once. 0.002° ≈ 220 m, a third of a pixel at a national
    # zoom. Raise it to trade fidelity for payload, or set 0 to send them verbatim.
    oevk_tolerance: float = float(
        os.environ.get("PARLAMONITOR_OEVK_TOLERANCE", "0.002"))

    # --- Bluesky announcements (§8.7, app/social.py + app/bluesky.py) --------
    # The bot posts when a sitting day becomes fully processed (SOC-2) and when a
    # representative accidentally spoke a haiku (SOC-4). Off unless credentials are
    # configured: `bluesky_auth` is both the secret and the switch.
    #
    # ONE variable carries the authentication: handle + app password, colon-
    # separated — `PARLAMONITOR_BLUESKY_AUTH=parlamonitor.bsky.social:abcd-efgh-ijkl-mnop`.
    # Create the app password on bsky.app (Settings → Privacy and security → App
    # passwords); never put the account password here. Nothing is written to disk
    # but the state file, which holds no credentials.
    bluesky_auth: str = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_BLUESKY_AUTH") or "").strip())
    # Alternative to the combined form, for secret stores that inject one value per
    # key. Ignored when `bluesky_auth` is set.
    bluesky_handle: str = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_BLUESKY_HANDLE") or "").strip())
    bluesky_password: str = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_BLUESKY_APP_PASSWORD") or "").strip())
    # The PDS to post to. bsky.social is the hosted default; point it at a
    # self-hosted PDS if the account lives there.
    bluesky_service: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_BLUESKY_SERVICE", "https://bsky.social").strip())
    bluesky_timeout: int = int(os.environ.get("PARLAMONITOR_BLUESKY_TIMEOUT", "20"))
    bluesky_user_agent: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_BLUESKY_USER_AGENT",
                       "Parlamonitor/1.0 (+https://github.com/k-monitor; info@k-monitor.hu)").strip())
    # Declared post language (app.bsky.feed.post `langs`), so clients don't offer to
    # translate Hungarian into Hungarian.
    bluesky_lang: str = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_BLUESKY_LANG", "hu").strip())
    # Master switch, independent of the credentials: set to 0 to keep the account
    # configured but stop the bot (an embargo, a debugging pass) without removing
    # the secret from the environment.
    bluesky_announce: bool = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_BLUESKY_ANNOUNCE", "1").strip().lower()
         not in ("0", "false", "no", "")))
    # Decide and log the posts, send nothing, record nothing — what to run a new
    # deployment on for a few days before letting it speak.
    bluesky_dry_run: bool = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_BLUESKY_DRY_RUN", "0").strip().lower()
         in ("1", "true", "yes", "on")))
    # Where the "what has been announced already" file lives. Unset → beside the DB
    # (the mounted volume on the standard deploy), so it survives restarts and DB
    # rebuilds. It is the bot's ONLY memory: the content DB is regenerable, so
    # anything remembered there would re-announce the corpus after a rebuild.
    bluesky_state: str | None = field(default_factory=lambda:
        os.environ.get("PARLAMONITOR_BLUESKY_STATE") or None)
    # The public origin the posts link to. Defaults to `site_url` — the same
    # canonical base the share cards use — and exists separately only for a deploy
    # whose bot should point at a different front (a staging origin, a vanity host).
    bluesky_base_url: str | None = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_BLUESKY_BASE_URL") or "").strip().rstrip("/") or None)
    # How far back a sitting day may be and still be announced. A day that only
    # completes months late is not news, and this also bounds the work: only days
    # inside the window are ever queried or scanned for haikus. Mirrors the SIT-2
    # publication-lag grace (app/publication.py), past which a gap is permanent
    # rather than pending, so nothing outside it is expected to complete anyway.
    bluesky_max_age_days: int = int(
        os.environ.get("PARLAMONITOR_BLUESKY_MAX_AGE_DAYS", "30"))
    # Hard cap on posts per pass. The backstop against a feed flood from an
    # unexpected backlog (a wiped state file plus --backfill, a re-scrape that
    # completes twenty days at once): the rest simply waits for the next pass.
    bluesky_max_posts: int = int(os.environ.get("PARLAMONITOR_BLUESKY_MAX_POSTS", "4"))
    # Seconds to wait between two posts in one pass. Belt and braces on top of the
    # millisecond `createdAt` (app/bluesky.py): a burst of posts sharing one
    # timestamp collapses in the AppView's author feed, and spacing them also keeps
    # a day's worth of announcements clear of the PDS write rate limits. Only ever
    # between posts, never before the first, so a single-post pass is unaffected.
    bluesky_post_interval: float = float(
        os.environ.get("PARLAMONITOR_BLUESKY_POST_INTERVAL", "1"))
    # Haiku announcements (SOC-4) and their per-sitting-day quota. One a day keeps
    # them a curiosity rather than the account's whole output; the quota counts
    # POSTED poems per day, so a transcript arriving in instalments cannot multiply
    # it. `mps_only` keeps it to mandate-holding members — "a representative said a
    # haiku" — rather than every minister and advocate who spoke.
    bluesky_haikus: bool = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_BLUESKY_HAIKUS", "1").strip().lower()
         not in ("0", "false", "no", "")))
    bluesky_haiku_per_day: int = int(
        os.environ.get("PARLAMONITOR_BLUESKY_HAIKU_PER_DAY", "1"))
    bluesky_haiku_mps_only: bool = field(default_factory=lambda:
        (os.environ.get("PARLAMONITOR_BLUESKY_HAIKU_MPS_ONLY", "1").strip().lower()
         not in ("0", "false", "no", "")))

    def module_enabled(self, name: str) -> bool:
        return name in self.enabled_modules

    def is_procedural_type(self, speech_type: str | None) -> bool:
        """Whether a per-speech type is a statistics-excluded chairing type
        (STAT-1). Case/whitespace-insensitive."""
        return bool(speech_type) and speech_type.strip().casefold() in self.procedural_speech_types

    def looks_like_chairing(self, first_sentence: str | None) -> bool:
        """Whether a speech's FIRST sentence opens like a chair turn.

        The transcript half of the STAT-1 rule, for speeches upstream left
        untyped (see ``chair_transcript_prefixes``). Folded rather than
        `LIKE`-matched because the marker is non-ASCII ("ELNÖK") and SQLite's
        `LIKE` only folds ASCII — so a naive query would miss "Elnök".
        """
        if not first_sentence or not self.chair_transcript_prefixes:
            return False
        head = first_sentence.lstrip().casefold()
        return any(head.startswith(p) for p in self.chair_transcript_prefixes)

    def is_procedural(self, speech_type: str | None,
                      first_sentence: str | None = None) -> bool:
        """The whole STAT-1 exclusion decision for one speech.

        The type decides whenever upstream gives one — a label is authoritative,
        and a non-chairing label is as much of an answer as a chairing one. Only
        a *missing* type falls through to the transcript, which is why passing no
        ``first_sentence`` simply reproduces ``is_procedural_type``.
        """
        if speech_type:
            return self.is_procedural_type(speech_type)
        return self.looks_like_chairing(first_sentence)

    @property
    def site_periods(self) -> tuple[int, ...]:
        """The electoral cycles the site serves, ascending — ``()`` for "every
        cycle in the DB" (see ``site_cycles``). A property rather than a stored
        field so tests (and a reload) can move the window by setting
        ``site_cycles``, exactly as the environment does."""
        return parse_site_cycles(self.site_cycles)

    def modal_cycle_allowed(self, period: int | None,
                            latest: int | None = None) -> bool:
        """Whether sittings of electoral ``period`` may be dispatched to Modal,
        given the corpus' ``latest`` period (see ``modal_cycles``).

        A sitting with no period recorded counts as current — those are always the
        newest days — and an unknown ``latest`` allows everything, since there is
        then nothing to be newer than."""
        spec = parse_modal_cycles(self.modal_cycles)
        if spec == "all":
            return True
        if isinstance(spec, frozenset):
            return period in spec
        return period is None or latest is None or period == latest


@lru_cache(maxsize=8)
def parse_site_cycles(raw: str) -> tuple[int, ...]:
    """Parse a ``PARLAMONITOR_SITE_CYCLES`` value into the ascending tuple of
    cycle numbers the site serves; ``()`` means "no window, the whole corpus".

    Where ``parse_modal_cycles`` resolves a typo *downwards* (that setting caps
    spend, so an unreadable value must not open the archive up), this one
    resolves it upwards to ``()``: this setting decides what the public site
    shows, and a mistyped value must fall back to the corpus as built rather
    than to a silently emptied site."""
    raw = (raw or "").strip().lower()
    if raw in ("", "all", "*"):
        return ()
    cycles = {int(p) for p in raw.replace(";", ",").split(",")
              if p.strip().lstrip("-").isdigit()}
    return tuple(sorted(cycles))


@lru_cache(maxsize=8)
def parse_modal_cycles(raw: str) -> str | frozenset:
    """Parse a ``PARLAMONITOR_MODAL_CYCLES`` value into ``"all"``, ``"latest"`` or
    the frozenset of cycle numbers it names. An unparsable value falls back to
    ``"latest"`` — the conservative reading, since the setting exists to *limit*
    spend, so a typo must never open the offload up to the whole archive."""
    raw = (raw or "").strip().lower()
    if raw in ("all", "*"):
        return "all"
    if raw in ("", "latest", "current"):
        return "latest"
    cycles = {int(p) for p in raw.replace(";", ",").split(",")
              if p.strip().lstrip("-").isdigit()}
    return frozenset(cycles) if cycles else "latest"


def _procedural_speech_types() -> frozenset:
    raw = os.environ.get("PARLAMONITOR_PROCEDURAL_SPEECH_TYPES")
    if raw is None:
        types = DEFAULT_PROCEDURAL_SPEECH_TYPES
    else:
        types = [t.strip() for t in raw.split(",") if t.strip()]
    return frozenset(t.casefold() for t in types)


def _chair_transcript_prefixes() -> tuple:
    raw = os.environ.get("PARLAMONITOR_CHAIR_TRANSCRIPT_PREFIXES")
    if raw is None:
        prefixes = DEFAULT_CHAIR_TRANSCRIPT_PREFIXES
    else:
        prefixes = [p.strip() for p in raw.split(",") if p.strip()]
    return tuple(p.casefold() for p in prefixes)


def _enabled_modules() -> list[str]:
    raw = os.environ.get("PARLAMONITOR_MODULES")
    if not raw:
        return list(ALL_MODULES)
    requested = [m.strip() for m in raw.split(",") if m.strip()]
    return [m for m in requested if m in ALL_MODULES]


def _default_photos_dir() -> str | None:
    # The scraper writes MP portraits under data/media/photos.
    guess = Path(__file__).resolve().parents[2] / "data" / "media" / "photos"
    return str(guess) if guess.exists() else None


settings = Settings()
