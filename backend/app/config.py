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
               "settlements")

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
