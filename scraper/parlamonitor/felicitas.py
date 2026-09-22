"""parlament.hu **Felicitas** REST API client (token-free JSON).

The modern parlament.hu site is backed by a public JSON query API under
``/felicitas/api/query/...`` (POST bodies, no authentication). This one client
covers both data domains the scraper needs:

**Plenary proceedings** (``plenaris-ules-adatok-query-provider``) — restructured
upstream 2026-09 (see :data:`DAY_SPEECH_ROSTER_QUERY`), verified 2026-09-22:

  1. ``ulesnapok-query``            (cycle + date range) → session days + UUIDs
  2. ``ulesnap-adatlap-aktusolatlan-query`` (day UUID, paged) → the day's
     **complete** flat speech listing (the portal's own "ülésnap felszólalásai"
     table): join number (``sorszam``), UUID, speaker, ``felszolaloId`` (the
     representative id — a cross-module link, EXT-2), type, the irományok the
     speech touches, start time and duration — but no agenda act.
  3. ``ulesnap-adatlap-aktusolt-query`` (day UUID) → the day's agenda-act UUIDs,
     and ``aktus-query`` (day + act UUID) → the speeches under one act, with the
     act's name and the government/committee capacity the speech was made in.
     One request per act; neither is complete or per-speech on its own, so (2) is
     the source of every number and (3) only adds the act.
  4. ``felszolalas-adatlap-query`` (speech UUID) → that speech's full text
     (HTML), speaker, type and duration.
  5. ``ulesnap-video-query``        (day UUID) → a ``playseq.php`` URL that
     resolves to the whole-day HLS playlist on ``sgis.parlament.hu``;
     ``felszolalas-video-query`` (speech UUID) → one speech's window in it.

This token-free JSON path replaces the reference pipeline's fragile PAIR-proxy
HTML scraping; the CGI/PAIR backends remain documented fallbacks (SRC-2) but are
not needed for v1.

**Representatives** (``kepviselo-query-provider``):

  * ``kepviselo-lista-idopontban`` (cycle + date range, paged) → the MP roster.
  * ``szoszolo-lista-query`` (cycle) → the **nationality-advocate** roster
    (*nemzetiségi szószólók*, the portal's "Szószólók" page). Advocates are not
    MPs and never appear in the MP roster, but they sit in the same person id
    space, so the per-MP detail queries below work for them unchanged.
  * a family of per-MP detail queries keyed on ``{"pId": <kepviseloId>}``
    (bio, faction history, committees, constituency, education, per-cycle speech
    and bill counts) and a photo resource endpoint.

**Committees** (``bizottsag-query-provider``) — verified 2026-09:

  * ``bizottsag-lista-wwwquery``  (cycle) → every committee body of the cycle,
    main committees and subcommittees in one listing.
  * ``bizottsag-mini-adatlap-query`` (body id) → its type and contact details.
  * ``bizottsag-tagjai-query``    (cycle + date) → the roster on that date, each
    seat carrying the member's ``kepviseloId`` (EXT-2).
  * ``bizottsagi-tagsag-tisztseg-valtozasai-reszletes-biz-query`` (cycle) → every
    dated membership and office term.
  * ``bizottsag-ulesei``          (cycle) → every meeting with its minutes PDF.
  * ``www-bizottsagi-ulesek-szama-query`` (cycle) → per-committee meeting totals.
  * ``bizottsagok-altal-targyalt-iromanyok-lista-query`` (body id) → the irományok
    it dealt with; ``onallo-``/``nem-onallo-inditvanyok-query`` those it tabled.
  * ``tervezett-bizottsagi-ules-idorend-query`` → the meetings still to come.

Cycle date ranges (needed as query bounds) come from the list query's parameter
``rebind`` endpoint.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

try:                                        # stdlib since 3.9; absent on some
    from zoneinfo import ZoneInfo           # minimal builds without tzdata
    _HU_TZ = ZoneInfo("Europe/Budapest")
except Exception:                           # pragma: no cover - platform detail
    # Only ever used to pick "today" for a date bound, where an hour or two
    # either side of midnight cannot change the answer that matters.
    _HU_TZ = timezone(timedelta(hours=1))

from . import magyarkozlony
from .http_client import HttpClient, HttpError

logger = logging.getLogger(__name__)

BASE = "https://www.parlament.hu"
# Plenary uses the bare /felicitas path (verified working); the representative
# endpoints were captured under /web/guest/felicitas. Both resolve.
PLENARY_PROVIDER = (f"{BASE}/felicitas/api/query/select/"
                    "plenarisulesadatok-plenarisules-registry/"
                    "plenaris-ules-adatok-query-provider")
# parlament.hu rebuilt the sitting-day API in 2026-09: ``ulesnapok-aktusok-query``,
# ``ulesnap-felszolalasai``, ``ulesnap-felszolalas-adata-query`` and
# ``ulesnapok-video-query`` all 404 now, and the one nested response that used to
# carry a whole day became a flat roster plus a per-act fan-out. The names below are
# the ones the portal's own sitting-day page requests (page-info page-items
# ``ulesnap-felszolalasai-with-contract`` / ``ulesnap-aktusok-with-contract``).
#
# The day's flat speech listing (paged, ``{"pUlesnapId": <day uuid>}``), behind the
# portal's own "ülésnap felszólalásai" table. It is the authoritative one, twice
# over. It is COMPLETE, where the act listing is not: that one reports a speech only
# through the agenda act it is linked to, and speeches linked to no act are silently
# absent from it — chiefly the ones with no "Felszólalás oka" (type): a speaker's
# continuation after being interrupted, an unclassified remark. Sitting 43015 listed
# 287 of its 303 speeches that way, the 16 gaps being exactly the type-less rows
# (e.g. #300, Dr. Árvay Nikolett's continued rapporteur reply); across the 713
# archived days ≥8 200 speeches (5.8%) were missing, and on legacy days the act
# links are sparse enough that whole sittings nearly vanished (day 40039: 11 of 139
# listed). And it is PER-SPEECH, where the act listing now folds a speech together
# with its continuations into one row carrying a join-number RANGE ("28 - 30") and
# their summed duration — 439 s against the speech's own 129 s on sitting 43029.
DAY_SPEECH_ROSTER_QUERY = "ulesnap-adatlap-aktusolatlan-query"
# The day's agenda acts (``{"pId": <day uuid>}``) — ids and nothing else — then one
# request per act (``{"pUlesnap": <day uuid>, "pAktusEsemeny": <act uuid>}``) for
# the speeches under it. Only this path knows which act a speech belongs to, so a
# day now costs 2 + one-per-act requests where it used to cost 2 (31 acts on a
# typical sitting day).
DAY_ACT_LIST_QUERY = "ulesnap-adatlap-aktusolt-query"
ACT_SPEECHES_QUERY = "aktus-query"
# One speech's full text and metadata (``{"pId": <speech uuid>}``).
SPEECH_DETAIL_QUERY = "felszolalas-adatlap-query"
# The whole-day recording (``{"pId": <day uuid>}``) and one speech's window within
# it (``{"pId": <speech uuid>}``) — a query each since the restructuring split the
# old single query's ``pTeljes`` switch in two.
DAY_VIDEO_QUERY = "ulesnap-video-query"
SPEECH_VIDEO_QUERY = "felszolalas-video-query"
# One request per day instead of ~13: the longest sitting on record has 522
# speeches, and select_all still pages if a day ever exceeds this.
_DAY_ROSTER_PAGE_SIZE = 1000
KEPVISELO_PROVIDER = (f"{BASE}/web/guest/felicitas/api/query/select/"
                     "registry/kepviselo-query-provider")
IROMANY_PROVIDER = (f"{BASE}/web/guest/felicitas/api/query/select/"
                   "iromanyadatok-iromany-registry/iromanyok-query-provider")
# Per-bill detail page. The "adatlap" sub-tables (events, votes, committees,
# deadlines, documents, …) are served by this provider; every sub-query takes
# ``{"pOnalId": <iromanyId>}``.
IROMANY_ADATLAP_PROVIDER = (f"{BASE}/web/guest/felicitas/api/query/select/"
                   "iromanyexportok-registry/iromany-adatlap-query-provider")
# The bill's own header sheet (extra fields not in the list query — subtype,
# character, promulgation, …); takes ``{"pId": <iromanyId>}``.
IROMANY_INTRA_PROVIDER = (f"{BASE}/web/guest/felicitas/api/query/select/"
                   "iromanyadatokonlyintra-registry/"
                   "onallo-iromany-adatlap-for-intra-query-provider")
# Votes (szavazások). The list query and every per-vote detail sub-query
# (per-MP roll call, per-faction breakdown, basic data) are served by this one
# provider. The list takes a cycle + date range; the detail queries take the
# vote's own ``szavazasId`` (``pId`` / ``pSzavazasId``).
SZAVAZAS_PROVIDER = (f"{BASE}/web/guest/felicitas/api/query/select/"
                   "szavazasok-registry/szavazasok-query-provider")
KEPVISELO_REBIND = (f"{BASE}/web/guest/felicitas/api/query/parameter/rebind/"
                   "kepviseloadatok-kepviselo-kepviselolista-idopont/"
                   "kepviselo-lista-idopontban-query")
# The composition-changes registry (*Változások az Országgyűlés összetételében*,
# `/web/guest/valtozasok-az-orszaggyules-osszeteteleben`) — the two listings behind
# that page, served by the same provider as the roster (verified 2026-08):
#   mandate — one row per mandate that ENDED mid-cycle, with the term's dates, the
#     reason it ended (`mandatumAllapotNeve`) and the successor who took the seat;
#   faction — one row per mid-cycle faction switch, with its exact date.
# The page's third query (`ossz-valtozasok-query`) returns only the distinct people
# these two name, which is nothing they don't already carry, so it isn't fetched.
MANDATE_CHANGES_QUERY = "mandatum-valtozasok-query"
FACTION_CHANGES_QUERY = "frakcio-valtozasok-query"
# ~40 rows in the busiest cycle on record, so one request each instead of two.
_CHANGES_PAGE_SIZE = 400
# Office holders (*tisztségviselők*): every recorded term of government / House
# office with its real start and end date, for MPs and non-MPs alike. Its one
# query is named ``tisztsegviselo`` (no ``-query`` suffix — that spelling 404s).
TISZTSEGVISELO_PROVIDER = (f"{BASE}/felicitas/api/query/select/"
                   "tisztsegviselok-registry/tisztsegviselok-query-provider")
# The registry's earliest date bound; the portal's own page passes the system
# epoch (``systemEpochDate`` in its page config), i.e. the first day of the
# freely-elected Assembly.
OFFICE_EPOCH = "1990-05-02"
# ~1 800 terms all-time, so the whole registry is 5 requests instead of 73.
_OFFICE_PAGE_SIZE = 400
# The registry's office categories — the tick-boxes on the portal's own
# Tisztségviselők page — as ``slug -> query parameter``. Insertion order is the
# priority used to file a term returned under several categories (a prime minister
# also comes back as a minister), most specific first, and the display order of the
# category filter downstream. Slugs are stable ids, not labels: the UI translates
# them, so renaming one is a data migration.
OFFICE_CATEGORIES = {
    "pm": "pMiniszterelnok",              # miniszterelnök
    "minister": "pMiniszter",             # miniszter
    "state-secretary": "pAllamtitkar",    # államtitkár
    "parliamentary": "pParlamenti",       # az Országgyűlés tisztségviselői
    "senior": "pEgyebVezetoTisztseg",     # egyéb vezető tisztség (pl. köztársasági elnök)
    "other": "pEgyebTisztseg",            # egyéb tisztség (pl. MNB, Közbeszerzési Hatóság)
}
# Committees (*bizottságok*). One provider serves the whole domain — the
# registry, the membership, the meetings and the iromány listings — and every
# query is a plain cycle+date-range select like the roster's (verified 2026-09).
# The queries behind each portal page were read off its own page definition
# (`/felicitas/api/page-info/page-item/<data-page>`), not guessed.
BIZOTTSAG_PROVIDER = (f"{BASE}/felicitas/api/query/select/"
                     "bizottsagadatok-bizottsag-registry/bizottsag-query-provider")
# A committee's own homepage on the portal: `/web/guest/<ciklus>-<bizottsagKod>`
# (e.g. `/web/guest/43-002J`). The pattern is the Link component's own recipe on
# the *Bizottsági honlapok* page (`bizottsag-lista-top`), which joins those two
# fields with a dash — not a guess. Only main committees have one.
COMMITTEE_SITE_BASE = f"{BASE}/web/guest"
# Committee minutes (jegyzőkönyv) and iromány PDFs come back as site-absolute
# paths ("/biz42/bizjkv42/GAB/2603091.pdf"); they are linked, never mirrored.
COMMITTEE_FILE_BASE = BASE


def _committee_file_url(path: str | None) -> str | None:
    """A committee file path from the API as a URL.

    ``jegyzokonyvPath`` is *documented* as site-absolute and for most of the
    corpus it is — but every one of cycle 40's 1 199 published minutes and 223
    of cycle 41's come back without the leading slash, and concatenating those
    onto the host gave ``https://www.parlament.hubiz40/…``: not a 404 but a
    hostname that does not exist, which made the oldest two cycles' minutes
    unfetchable. The separator is put in here, where the base and the path are
    still two strings — once they are joined, where the host ends is no longer
    recoverable from the URL.
    """
    if not path:
        return None
    return f"{COMMITTEE_FILE_BASE}/{path.lstrip('/')}"
# A cycle has at most a few hundred committee rows and ~3 600 meetings, so every
# listing is one or two requests at this width instead of dozens.
_COMMITTEE_PAGE_SIZE = 1000
# The roles a committee seat can carry, most senior first. Used to order a
# roster and to normalise the upstream free-text `tisztsegNeve` into a stable
# slug the UI translates (renaming one is a data migration, as with the office
# categories above).
COMMITTEE_ROLES = {
    "elnök": "chair",
    "alelnök": "deputy-chair",
    "tag": "member",
}


def committee_role(label: str | None) -> str:
    """Normalise an upstream `tisztsegNeve` to a stable role slug.

    Anything unrecognised keeps `other` rather than being dropped: the label
    itself is stored alongside, so an unseen role still renders correctly and
    only loses its sort position."""
    return COMMITTEE_ROLES.get((label or "").strip().lower(), "other")


PHOTO_RESOURCE = (f"{BASE}/web/guest/felicitas/api/query/resource/"
                 "kepviseloexportok/kepviselo-exported-queries-provider/"
                 "kepviselo-kepek")
# The MP's own CV (*„a képviselő által leadott és kérésére közzétett életrajz”*):
# a static PDF keyed by person id, NOT a Felicitas query — the adatlap links it
# straight from this path. Publication is opt-in and only offered while the MP
# sits (parlament.hu hides the link for anyone else), so its existence is always
# checked rather than assumed (see `cv_url`).
CV_RESOURCE = f"{BASE}/kepv/eletrajz/hu"

_REFERER = {"Referer": f"{BASE}/web/guest/orszaggyulesi-naplo-elozo-ciklusbeli-adatai"}

# Upstream re-versioned every per-MP detail query behind the MP adatlap page to a
# ``_v2`` name (seen 2026-09-10: the old spellings started answering the portal's
# "A megadott URL nem létezik" 404 page, for every MP and every one of these
# queries). Nothing else in the registry moved — the roster, the advocate list and
# the composition changes still answer under their original names.
#
# Callers keep using the unsuffixed name as the key their rows are filed under
# (``parlamonitor.representatives.scrape.DETAIL_QUERIES`` and the builders that
# read them); only the wire spelling lives here. When upstream versions one again,
# add it to this set rather than renaming anything downstream. The current names
# come from the adatlap page definition, which is authoritative:
#   GET /felicitas/api/page-info/page-item/kepviseloexportok/
#       kepviselo-adatlap-with-contract/kepviselo-adatlap-with-contract
# → every datasource's ``recordset`` is the select URL to use.
DETAIL_QUERY_V2 = frozenset({
    "kepviselo-adatok-query",
    "kepviselo-aktivitas-query",
    "kepviselo-benyujtott-iromanyok-szama-query",
    "kepviselo-bizottsagi-tagsagai-query",
    "kepviselo-felszolalasok-szama-query",
    "kepviselo-frakcioja-query",
    "kepviselo-javadalmazasa-query",
    "kepviselo-tisztseg-query",
    "kepviselo-vagyon-nyilatkozata-query",
    "kepviselo-vagyon-nyilatkozata2022query",
    "kepviselo-vagyon-nyilatkozata2023query",
    "kepviselo-vagyon-nyilatkozata2026query",
    "kepviselo-valasztasi-adatok-query",
    "kepviselo-vegzettsege-query",
})


def detail_query_endpoint(query: str) -> str:
    """The name to request a per-MP detail ``query`` under, from the stable name
    callers use. Unknown names pass through unchanged."""
    return f"{query}_v2" if query in DETAIL_QUERY_V2 else query


_PLAYSMIL_RE = re.compile(r"playSmil\('([^']+\.m3u8)'\)", re.I)
# playseq.php?...&offset1=002543.19&...&offset2=010019.19&...  (HHMMSS.fff)
_PLAYSEQ_OFF_RE = re.compile(r"offset1=([\d.]+).*?offset2=([\d.]+)")


# --- response helpers ------------------------------------------------------

def rows_as_dicts(payload: dict) -> list[dict]:
    """Map a Felicitas response's positional ``rows`` to field-name dicts.

    Every response carries ``metadata.fieldnames`` (name → column index); this
    turns ``[v0, v1, ...]`` rows into ``{"name": value, ...}`` so callers never
    hard-code column positions."""
    fn = (payload.get("metadata") or {}).get("fieldnames") or {}
    inv = {idx: name for name, idx in fn.items()}
    out = []
    for row in payload.get("rows") or []:
        out.append({inv[i]: row[i] for i in range(len(row)) if i in inv})
    return out


def _today() -> str:
    """Today in the House's own timezone, as the portal's date params want it."""
    return datetime.now(_HU_TZ).date().isoformat()


def _parse_off(value: str) -> float:
    """Parse a playseq ``HHMMSS(.fff)`` offset into seconds."""
    intpart, _, frac = value.partition(".")
    intpart = intpart.zfill(6)
    h, m, s = int(intpart[:2]), int(intpart[2:4]), int(intpart[4:6])
    return h * 3600 + m * 60 + s + (float("0." + frac) if frac else 0.0)


def playseq_offsets(playseq: str | None) -> tuple[float, float] | None:
    m = _PLAYSEQ_OFF_RE.search(playseq or "")
    return (_parse_off(m.group(1)), _parse_off(m.group(2))) if m else None


_SORSZAM_RE = re.compile(r"\d+")


def _sorszam(value) -> int | None:
    """A speech's join number as an int.

    The act listing reports a speech merged with its continuations as a RANGE
    ("28 - 30"); its first number is the speech's own join number — the one the
    flat roster gives, and the one every downstream stage orders, links and builds
    origin ids from."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    m = _SORSZAM_RE.search(str(value))
    return int(m.group()) if m else None


def _local_naive(ts):
    """A Felicitas timestamp as the naive House-local wall time the archive stores.

    The restructured plenary queries answer in UTC (``2026-09-15T07:12:00Z``) where
    the old ones answered in local time (``2026-09-15T09:12:00``) — the same
    instant, spelled differently. Every one of the 700+ archived sitting days (and
    the session boundaries, speech clocks and day pages derived from them) carries
    local wall time, so the conversion happens here rather than leaving two
    conventions in the data. Anything already naive, or unparseable, passes
    through."""
    if not isinstance(ts, str) or not ts:
        return ts
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    if dt.tzinfo is None:
        return ts
    return dt.astimezone(_HU_TZ).replace(tzinfo=None).isoformat()


class FelicitasClient:
    def __init__(self, http: HttpClient):
        self.http = http
        # The cycle list is static for the lifetime of a run and several stages ask
        # for it (date ranges, "which cycle is the latest"), so it is fetched once.
        self._cycle_ranges: dict[int, dict] | None = None

    def close(self) -> None:
        """Release transport resources (HTTP session, SSH tunnel if any)."""
        self.http.close()

    def __enter__(self) -> "FelicitasClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- generic POST select --------------------------------------------

    def _select(self, provider: str, query: str, body: dict, page: int = 0,
                size: int | None = None) -> dict:
        url = f"{provider}/{query}?page={page}"
        if size:
            # Pages are 25 rows by default; `size` widens them (the backend caps
            # it at 1000), so a long listing costs one request instead of a dozen.
            url += f"&size={size}"
        self.http.polite_sleep()
        return self.http.post_json(url, body, headers=_REFERER)

    def select_all(self, provider: str, query: str, body: dict,
                   size: int | None = None) -> list[dict]:
        """Page through a select query and return all rows as field-name dicts."""
        first = self._select(provider, query, body, page=0, size=size)
        rows = rows_as_dicts(first)
        resp = first.get("response") or {}
        total = resp.get("totalSize") or len(rows)
        page_size = resp.get("pageSize") or len(rows) or 1
        page = 1
        while len(rows) < total:
            payload = self._select(provider, query, body, page=page, size=size)
            chunk = rows_as_dicts(payload)
            if not chunk:
                break
            rows.extend(chunk)
            page += 1
            if page > 1000:  # safety backstop, far above any real result set
                logger.warning("select_all(%s) exceeded 1000 pages; stopping", query)
                break
        return rows

    # ---- plenary: session days ------------------------------------------

    def session_days(self, cycle: int, date_from: str, date_to: str) -> list[dict]:
        """Session days of ``cycle`` within ``[date_from, date_to]`` (ISO dates).

        Each item exposes the day UUID, ISO date, sitting (``ules``) and the
        sitting-day sequence numbers."""
        body = {"pCiklus": cycle, "pIdoszakEleje": date_from,
                "pIdoszakVege": date_to, "pIdopont": date_to}
        out = []
        for r in self.select_all(PLENARY_PROVIDER, "ulesnapok-query", body):
            datum = r.get("datum") or ""
            out.append({
                "uuid": r.get("id"),
                "date": datum[:10],
                "datum_felirat": r.get("datumFelirat"),
                "ules": r.get("ules"),
                "ulesszak": r.get("ulesszak"),
                "day_in_session": r.get("sorszamUlesszakonBelul"),
                "day_in_ules": r.get("sorszamUlesenBelul"),
                "duration_s": r.get("ulesIdoMasodpercben"),
                "debate_s": r.get("targyalasiIdoMasodpercben"),
            })
        return out

    def day_speech_roster(self, day_uuid: str) -> list[dict]:
        """The day's COMPLETE flat speech listing (:data:`DAY_SPEECH_ROSTER_QUERY`).

        One row per speech in join-number order — UUID, ``sorszam``, speaker,
        representative id, type, the irományok it touches, start time, duration —
        with no agenda act or committee. This is where :meth:`day_speeches` takes
        every per-speech number from; the act listing only adds the act."""
        rows = self.select_all(PLENARY_PROVIDER, DAY_SPEECH_ROSTER_QUERY,
                               {"pUlesnapId": day_uuid},
                               size=_DAY_ROSTER_PAGE_SIZE)
        out = []
        for r in rows:
            # "Felszólalás oka" is a nested table since the restructuring — the
            # same type/iromány shape the act listing carries.
            stype, bills = _parse_type_table(r.get("felszolasOka"))
            out.append({
                "sorszam": _sorszam(r.get("sorszam")),
                "speech_uuid": r.get("felszolalas"),
                "speaker": r.get("felszolalo"),
                "person_id": r.get("felszolaloId"),
                "type": stype,
                "kezdete": _local_naive(r.get("felszolalasKezdete")),
                "duration": r.get("videoIdoMasodperc"),
                "bills": bills,
            })
        return out

    def day_acts(self, day_uuid: str) -> list[str]:
        """The day's agenda-act UUIDs in the portal's own order
        (:data:`DAY_ACT_LIST_QUERY`). The act's name comes with its speeches."""
        rows = self.select_all(PLENARY_PROVIDER, DAY_ACT_LIST_QUERY,
                               {"pId": day_uuid}, size=_DAY_ROSTER_PAGE_SIZE)
        seen: set[str] = set()
        out: list[str] = []
        for r in rows:
            aktus_id = r.get("aktusId")
            if aktus_id and aktus_id not in seen:
                seen.add(aktus_id)
                out.append(aktus_id)
        return out

    def act_speeches(self, day_uuid: str, aktus_id: str) -> list[dict]:
        """The speeches filed under one agenda act (:data:`ACT_SPEECHES_QUERY`).

        Carries what only this path knows — the act's UUID and name, the committee
        id and the capacity the speech was made in ("miniszterelnök",
        "Törvényalkotási Bizottság") — plus the speech fields the roster also has.
        Its ``sorszam`` and ``duration`` cover a merged run of speeches, not one
        speech, so :meth:`day_speeches` overrides both from the roster."""
        rows = self.select_all(PLENARY_PROVIDER, ACT_SPEECHES_QUERY,
                               {"pUlesnap": day_uuid, "pAktusEsemeny": aktus_id},
                               size=_DAY_ROSTER_PAGE_SIZE)
        out = []
        for r in rows:
            # The "felszolalasTipusa" column is itself a nested sub-table (one row
            # per bill the speech touches), each carrying the real type string, the
            # bill UUID and the bill reference text.
            stype, bills = _parse_type_table(r.get("felszolalasTipusa"))
            out.append({
                "sorszam": _sorszam(r.get("sorszam")),
                "speech_uuid": r.get("felszolalasId"),
                "speaker": r.get("felszolalo"),
                "person_id": r.get("felszolaloId"),
                "type": stype,
                "committee_id": r.get("bizottsagId"),
                "is_committee": bool(r.get("bizottsagId")),
                "capacity": r.get("kormanyBizottsag"),
                "kezdete": _local_naive(r.get("felszolalasKezdete")),
                "duration": r.get("videoIdoMasodperc"),
                "aktus_id": aktus_id,
                "aktus": r.get("aktusMegnevezes") or "",
                "bills": bills,
            })
        return out

    def day_speeches(self, day_uuid: str) -> list[dict]:
        """A day's speeches, act by act, completed from the flat day roster.

        Returns one dict per speech *per agenda act it is filed under* (the source
        lists a speech once for each; ``proceedings.transform._dedup_speeches``
        collapses them), each carrying the agenda-act id and name, join number,
        speaker, representative id, type, committee, start time, duration and any
        bill references — then every speech linked to no act at all, folded in from
        :meth:`day_speech_roster` (see :func:`_merge_roster`).

        Costs one request per agenda act since the 2026-09 restructuring, on top of
        the roster and the act list. One act that fails is logged and skipped rather
        than failing the day (SCR-5): its speeches still arrive from the roster,
        act-less, and inherit their neighbour's act."""
        roster: list[dict] = []
        try:
            roster = self.day_speech_roster(day_uuid)
        except HttpError as e:
            # The roster is what makes the day complete and per-speech; without it
            # the act listing alone is a degraded day, not a failed scrape.
            logger.warning("speech roster for day %s unavailable: %s", day_uuid, e)
        by_uuid = {r["speech_uuid"]: r for r in roster if r.get("speech_uuid")}

        try:
            acts = self.day_acts(day_uuid)
        except HttpError as e:
            # Same bargain as a single failing act, one level up: the day survives
            # as its (complete) flat listing, with no agenda sections.
            logger.warning("agenda acts for day %s unavailable: %s", day_uuid, e)
            acts = []

        out: list[dict] = []
        for aktus_id in acts:
            try:
                rows = self.act_speeches(day_uuid, aktus_id)
            except HttpError as e:
                logger.warning("day %s: agenda act %s unavailable: %s",
                               day_uuid, aktus_id, e)
                continue
            for sp in rows:
                own = by_uuid.get(sp.get("speech_uuid"))
                if own:
                    # The act row may cover a merged run of speeches (join-number
                    # range, summed duration); the roster keeps them apart, so its
                    # numbers win wherever it has the speech. So does its type: the
                    # roster states the speech's own reason ("ülésvezetés") while
                    # the act row names what happened in THAT act ("módosító
                    # javaslat fenntartása elutasítva"), which is not the same
                    # thing for a chair turn spanning several acts. The bill
                    # references stay act-scoped, so a speech filed under two acts
                    # does not carry the other's bills into this one.
                    sp.update(sorszam=own.get("sorszam"),
                              kezdete=own.get("kezdete"),
                              duration=own.get("duration"))
                    sp["type"] = own.get("type") or sp.get("type")
                    sp["speaker"] = sp.get("speaker") or own.get("speaker")
                    sp["person_id"] = sp.get("person_id") or own.get("person_id")
                out.append(sp)
        if not roster:
            return out
        return _merge_roster(out, roster, day_uuid)

    def speech_text(self, speech_uuid: str) -> dict | None:
        """Full text + metadata for one speech (:data:`SPEECH_DETAIL_QUERY`).

        Returns the speech's HTML body, speaker, representative id, type,
        duration, agenda caption and next/previous speech UUIDs, or ``None``."""
        payload = self._select(PLENARY_PROVIDER, SPEECH_DETAIL_QUERY,
                               {"pId": speech_uuid})
        rows = rows_as_dicts(payload)
        if not rows:
            return None
        r = rows[0]
        stype, _bills = _parse_type_table(r.get("felszolalasTipusa"))
        return {
            "speech_uuid": r.get("felszolalasId"),
            "next_uuid": r.get("kovetkezoFelszolalasId"),
            "prev_uuid": r.get("elozoFelszolalasId"),
            "caption": r.get("tableCaption"),
            "speaker": r.get("felszolalo"),
            "person_id": r.get("felszolaloId"),
            "role": r.get("tisztseg"),
            "committee_id": r.get("bizottsagId"),
            "committee": r.get("bizottsagNev"),
            "type": stype,
            "duration": r.get("videoIdoMasodperc"),
            "html": r.get("felszolalasSzovege") or "",
        }

    # ---- plenary: day video ---------------------------------------------

    def day_video(self, day_uuid: str) -> dict | None:
        """Resolve a day's whole-recording HLS playlist: ``{m3u8, day_off1,
        day_off2, playseq}``. ``day_off1``/``day_off2`` (seconds) are the
        recording start/end offsets; ``day_off1`` is what per-speech offsets are
        measured against, and the pair delimits the whole-day window a not-yet-
        segmented speech's offsets get spuriously echoed as (see
        :func:`parlamonitor.proceedings.scrape.scrape_day`). ``None`` if no
        recording."""
        payload = self._select(PLENARY_PROVIDER, DAY_VIDEO_QUERY, {"pId": day_uuid})
        rows = rows_as_dicts(payload)
        if not rows:
            return None
        szerver = (rows[0].get("szerverPath") or "").rstrip("/")
        vsrc = rows[0].get("videoUrl") or ""
        if not szerver or not vsrc:
            return None
        playseq = f"{szerver}/{vsrc}"
        offs = playseq_offsets(playseq)
        return {
            "m3u8": self._playseq_to_m3u8(playseq),
            "day_off1": offs[0] if offs else None,
            "day_off2": offs[1] if offs else None,
            "playseq": playseq,
        }

    def speech_offsets(self, speech_uuid: str) -> tuple[float, float] | None:
        """``(off1, off2)`` seconds of one speech in the recording's coordinate
        system (subtract the day ``off1`` for day-stream-relative timing).

        These are the *real* per-speech offsets. v1 timing does not use them
        (TIM-1), but the scraper still records them onto each speech for
        provenance and so the future per-speech timing stage (§10) can swap in
        without re-fetching."""
        payload = self._select(PLENARY_PROVIDER, SPEECH_VIDEO_QUERY,
                               {"pId": speech_uuid})
        rows = rows_as_dicts(payload)
        if not rows:
            return None
        return playseq_offsets(rows[0].get("videoUrl") or "")

    def _playseq_to_m3u8(self, playseq_url: str) -> str | None:
        try:
            html = self.http.get_text(playseq_url, headers=_REFERER)
        except Exception as e:
            logger.warning("playseq fetch failed (%s)", e)
            return None
        m = _PLAYSMIL_RE.search(html)
        return m.group(1) if m else None

    # ---- representatives -------------------------------------------------

    def cycle_ranges(self) -> dict[int, dict]:
        """Map every electoral cycle number → ``{"start": iso, "end": iso|None}``
        from the list query's parameter-rebind endpoint. Memoized per client: the
        answer cannot change mid-run, and every stage that needs a date range (or
        the newest cycle number) would otherwise re-request it."""
        if self._cycle_ranges is not None:
            return self._cycle_ranges
        data = self.http.get_json(f"{KEPVISELO_REBIND}/p-ciklus", headers=_REFERER)
        out: dict[int, dict] = {}
        for item in data.get("idAndRebinding", []):
            cid = item.get("id")
            if not isinstance(cid, int):
                continue
            props = item.get("rebindingByProperties", {})
            out[cid] = {
                "start": (props.get("hCiklusElejeLimit") or {}).get("value"),
                "end": (props.get("hCiklusVegeLimit") or {}).get("value"),
            }
        # Only a non-empty answer is memoized, so a transient empty response
        # doesn't pin "no cycles known" for the rest of the run.
        if out:
            self._cycle_ranges = out
        return out

    def representative_list(self, cycle: int, start: str, end: str) -> list[dict]:
        """The MP roster for ``cycle`` over ``[start, end]`` (ISO dates), paged."""
        body = {
            "pCiklus": cycle,
            "hCiklusElejeLimit": start,
            "pIdoszakEleje": start,
            "pIdoszakVege": end,
            "pIdopont": end,
            "pAktivKepviselo": False,
            "pDatumField": False,
        }
        return self.select_all(KEPVISELO_PROVIDER, "kepviselo-lista-idopontban", body)

    def representative_detail(self, query: str, person_id: str) -> list[dict]:
        """Run one per-MP detail query (e.g. ``kepviselo-adatok-query``).

        ``query`` is the *stable* name callers key their rows on; the request goes
        to whatever upstream currently spells it (see ``DETAIL_QUERY_V2``)."""
        return self.select_all(KEPVISELO_PROVIDER, detail_query_endpoint(query),
                               {"pId": person_id})

    def composition_changes(self, cycle: int, start: str, end: str) -> dict:
        """How the House's composition changed during ``cycle`` (REP-14) —
        ``{"mandate": [...], "faction": [...]}``, the two halves of parlament.hu's
        *Változások az Országgyűlés összetételében* listing.

        The **mandate** rows are the ones the roster cannot give: ``kepviselo-lista-
        idopontban`` answers only "who sat on this date", so an MP whose mandate ended
        mid-cycle is missing from every roster taken after they left. Each row names
        one such mandate — ``kepviseloId`` (the same person-id space as everywhere
        else, so the per-MP detail queries answer for them), ``mandatumKezdete`` /
        ``mandatumVege``, the reason (``mandatumAllapotNeve``: *elhunyt*, *képviselői
        megbízatásról lemondott*, *összeférhetetlenség*) and the successor
        (``kovetkezoKepviselo*``, absent when the seat was never filled).

        Two requests for the whole cycle: a dozen or two mandates change in a term."""
        body = {"pCiklus": int(cycle), "hCiklusElejeLimit": start,
                "hCiklusVegeLimit": end, "pIdoszakEleje": start,
                "pIdoszakVege": end, "pIdopont": end}
        return {
            "mandate": self.select_all(KEPVISELO_PROVIDER, MANDATE_CHANGES_QUERY,
                                       body, _CHANGES_PAGE_SIZE),
            "faction": self.select_all(KEPVISELO_PROVIDER, FACTION_CHANGES_QUERY,
                                       body, _CHANGES_PAGE_SIZE),
        }

    def advocate_list(self, cycle: int) -> list[dict]:
        """The nationality-advocate (*nemzetiségi szószóló*) roster of ``cycle``.

        Backs parlament.hu's "Szószólók" page. One row per advocate, carrying the
        person id (``szoszoloId`` — the same id space as ``kepviseloId``, so it
        joins to a speech's speaker and to the per-MP detail queries), the name,
        the **nationality** they speak for, and — thanks to
        ``pStatisztikaiAdatok`` — that cycle's speech and own-motion counts.

        ``pAktivSzoszolo`` **false** is what returns the cycle's *whole* roster;
        true narrows it to the currently-sitting advocates (and so yields nothing
        for a past cycle). A cycle before the office existed (pre-2014) returns an
        empty list rather than an error, which is how the caller discovers which
        cycles have advocates at all."""
        body = {
            "pCiklus": int(cycle),
            "pNemzetiseg": [],
            "pNem": None,
            "pAktivSzoszolo": False,
            "pStatisztikaiAdatok": True,
        }
        return self.select_all(KEPVISELO_PROVIDER, "szoszolo-lista-query", body)

    def office_holders(self, *, as_of: str,
                       earliest: str = OFFICE_EPOCH) -> list[dict]:
        """Every recorded term of **government / House office** (*tisztség*), for
        MPs and non-MPs alike — the registry behind parlament.hu's "Tisztségviselők"
        page (``/web/guest/tisztsegviselok``, verified 2026-07).

        One row per (person, office, term): ``kepvId`` (the person id, same space as
        ``kepviseloId`` — so it joins straight to a speech's speaker), ``nev``,
        ``tisztseg`` (the office name), and ``tol``/``ig`` — the **real appointment
        and dismissal timestamps**, with ``ig`` null while the office is still held.

        This is the only source that dates the office of a **non-MP** minister or
        state secretary: they are in no roster (not an MP, not an advocate), so
        their profile would otherwise have to guess the term from their speeches.

        Every office category is requested (``pMiniszterelnok`` … ``pEgyebTisztseg``)
        — with the four the portal ticks by default the listing is ~⅔ of the rows,
        silently dropping e.g. an MNB or Közbeszerzési Hatóság seat. ``pTisztsegIg``
        bounds the listing at ``as_of`` (a date, ``YYYY-MM-DD``); ``pTisztsegTol`` is
        deliberately NOT sent — passing it drops the terms that began before it,
        including some that are still running.

        The categories are asked for **one at a time** rather than all at once, and
        each returned row is tagged with the ``category`` it came back under. The
        rows themselves carry no category field — only the free-text ``tisztseg`` —
        so this is the only way to get the portal's own grouping (a "miniszterelnök"
        vs "miniszter" vs "államtitkár" classification guessed from the title text
        would misfile e.g. "miniszterelnök-helyettes"). Costs six paged listings
        instead of one, which for ~1 800 rows is still a handful of requests.

        A term can be returned under more than one category (a prime minister is
        also a minister); the first category in ``OFFICE_CATEGORIES`` order wins, so
        each row appears once, under its most specific category."""
        body = {"pLegkorabbiDatumValue": earliest, "pTisztsegIg": as_of}
        rows: dict[str, dict] = {}
        for category, param in OFFICE_CATEGORIES.items():
            flags = {p: (p == param) for p in OFFICE_CATEGORIES.values()}
            chunk = self.select_all(TISZTSEGVISELO_PROVIDER, "tisztsegviselo",
                                    {**body, **flags}, size=_OFFICE_PAGE_SIZE)
            for row in chunk:
                # Keyed by the registry's own row id (one row = one person-term), so
                # a term seen under an earlier category is not overwritten by a
                # broader one. A row with no id can't be deduped — keep it as its own.
                key = row.get("id") or f"{row.get('kepvId')}|{row.get('tisztseg')}|{row.get('tol')}"
                rows.setdefault(key, {**row, "category": category})
        return list(rows.values())

    def photo(self, person_id: str) -> bytes | None:
        """Fetch an MP's portrait image bytes, or ``None`` if absent."""
        self.http.polite_sleep()
        return self.http.get_bytes(f"{PHOTO_RESOURCE}/{person_id}", headers=_REFERER)

    def cv_url(self, person_id: str) -> str | None:
        """The person's published CV PDF on parlament.hu, or ``None`` if there is
        none. Publication is the MP's own choice, so the URL is only returned when
        a HEAD confirms the file is there — we never publish a link we haven't
        seen resolve. Nothing is downloaded: the PDF is linked, not mirrored."""
        if not person_id:
            return None
        url = f"{CV_RESOURCE}/{person_id}.pdf"
        self.http.polite_sleep()
        return url if self.http.exists(url, headers=_REFERER) else None

    # ---- bills (irományok) ----------------------------------------------

    def bills(self, cycle: int, *, main_type: str = "") -> list[dict]:
        """The irományok (parliamentary documents) of ``cycle`` from
        ``iromany-query``, paged.

        ``main_type`` is the Felicitas ``fotipus`` filter (the iromány-number
        prefix): ``"T"`` selects törvényjavaslatok (bills), ``"H"`` határozati
        javaslatok, etc.; the **default empty string fetches every type**. Each
        returned dict carries the document's number, title, type, status,
        submission date, the PDF text link, and its **submitters** — each with
        the ``personID`` (kepviseloId) that joins to an MP profile (EXT-2). Its
        ``mainType`` is taken from the iromány-number prefix (e.g. ``T/253`` →
        ``"T"``) so it is correct per row even when all types are fetched at once.
        """
        body = {
            "pMultiCiklus": [int(cycle)],
            "pUnios": False, "pNemzetisegi": False, "pIdokeretes": False,
            "pFotipus": [main_type] if main_type else [],
            "pTipus": [], "pKepviselo": [], "pAllapottipus": [],
            "pTargyalasiMod": [], "pIromanyEsemeny": [],
        }
        out: list[dict] = []
        for r in self.select_all(IROMANY_PROVIDER, "iromany-query", body):
            text = _first_subrow(r.get("iromanyszoveg"))
            link = (text or {}).get("iromanyszovegLink")
            num = r.get("iromanyszam") or ""
            prefix = num.split("/", 1)[0] if "/" in num else ""
            out.append({
                "billId": r.get("iromanyId"),
                "billNumber": r.get("iromanyszam"),
                "billNumberSort": r.get("iromanyszamSorrendezeshez"),
                "title": r.get("cim"),
                "type": r.get("iromanytipus"),
                "mainType": prefix or (main_type or None),
                "status": r.get("iromanyAllapot"),
                "stages": _parse_stages(r.get("diagram")) if r.get("kellDiagram") else [],
                "submittedDate": r.get("benyujtasDatuma"),
                "textUrl": f"{BASE}{link}" if link else None,
                "textCaption": (text or {}).get("iromanyszovegCaption"),
                "noText": bool(r.get("nincsSzoveg")),
                "sponsors": [{
                    "personID": s.get("kepviseloId"),
                    "factionId": s.get("kepviseloFrakcioId"),
                    "committeeId": s.get("bizottsagId"),
                    "label": s.get("benyujto"),
                } for s in _subrows(r.get("benyujto"))],
            })
        return out

    def bill_detail(self, bill_id: str) -> dict:
        """The full detail sheet of one bill (the parlament.hu "adatlap").

        Pulls every sub-table the portal shows beyond the bare list row:
        the legislative **event history** (with the speech/vote each event is
        tied to), **committee events** (modifying proposals, reports),
        **votes** (igen/nem/tartózkodás), **deadlines**, the **negotiating
        committees**, **justification & background documents**, the
        **non-self-standing motions** (the individual dependent irományok — each
        with its own number, type, submitters and PDF — plus their per-type
        summary counts), and a handful of extra header
        fields (subtype, character, promulgation). Everything is keyed by the
        bill's own ``iromanyId`` (``pOnalId``/``pId``) — no extra ids needed.

        ~9 small requests per bill, all politely throttled (SCR-4)."""
        oid = {"pOnalId": bill_id}
        P = IROMANY_ADATLAP_PROVIDER

        events = [{
            "date": e.get("esemenyIdeje"),
            "name": e.get("esemenyfajtaNev"),
            "personID": e.get("kapcsolodoSzemelyId"),
            "committeeId": e.get("kapcsolodoBizottsagId"),
            "relatedLabel": e.get("kapcsolodoSzemelyOnalloBizottsag"),
            "speechNumber": e.get("felszolalasSzam"),
            "speechId": e.get("felszolalasId"),
            "voteId": e.get("szavazasId"),
            "remark": e.get("megjegyzes"),
        } for e in self.select_all(P, "iromany-esemenyek-lista-query", oid)]

        committee_events = [{
            "date": e.get("esemenyIdeje"),
            "name": e.get("esemenyfajtaNev"),
            "committee": e.get("bizottsagNev"),
            "committeeId": e.get("bizottsagId"),
            "personID": e.get("kepviseloId"),
            "personLabel": e.get("kepviselo"),
            "amendment": e.get("modositoJavaslat"),
            "overreachingAmendment": e.get("tulterjeszkedoModositoJavaslat"),
            "report": e.get("jelentes"),
        } for e in self.select_all(P, "iromany-bizottsagi-esemenyek-lista-query", oid)]

        votes = [{
            "voteId": v.get("szavazasId"),
            "date": v.get("szavazasIdeje"),
            "subject": v.get("oka"),
            "yes": v.get("igen"),
            "no": v.get("nem"),
            "abstain": v.get("tartozkodas"),
            "result": v.get("eredmeny"),
        } for v in self.select_all(P, "iromany-szavazasai-lista-query", oid)]

        deadlines = [{
            "name": d.get("nev"),
            "deadline": d.get("idopont"),
            "reference": d.get("hivakozas"),
            "remark": d.get("megjegyzes"),
        } for d in self.select_all(P, "iromany-hataridok-lista-query", oid)]

        committees = [{
            "committee": c.get("bizottsagNev"),
            "committeeId": c.get("bizottsagId"),
            "role": c.get("targyalasiSzerepkor"),
            "reference": c.get("jogszabalyiHivatkozas"),
            "parts": ", ".join(c["targyalandoReszek"])
                     if isinstance(c.get("targyalandoReszek"), list) else None,
        } for c in self.select_all(P, "iromanyt-targyalo-bizottsagok-lista-query", oid)]

        documents = [{
            "kind": "justification",
            "title": j.get("megnevezes"),
            "url": f"{BASE}{j['szovegLink']}" if j.get("szovegLink") else None,
            "date": j.get("benyujtasdatuma"),
            "published": j.get("kozzetetel"),
        } for j in self.select_all(P, "indoklasok-lista-query", oid)]
        for h in self.select_all(P, "iromanyhoz-kapcsolodo-hatteranyag-lista-query", oid):
            link = h.get("iromanyhozKapcsolodoHatteranyagLink")
            documents.append({
                "kind": "background",
                "title": h.get("iromanyhozKapcsolodoHatteranyag"),
                # background links are sometimes absolute, sometimes site-relative
                "url": link if (link or "").startswith("http") else
                       (f"{BASE}{link}" if link else None),
                "date": None, "published": None,
            })

        motion_summary = [{
            "type": m.get("tipus"),
            "valid": m.get("ervenyes"),
            "withdrawn": m.get("visszavont"),
            "total": m.get("osszesen"),
        } for m in self.select_all(P, "nemonallo-inditvany-osszesito-lista-query", oid)]

        # The actual non-self-standing (dependent) motions themselves — each is
        # its own iromány with a number, type, submitter(s) and a downloadable
        # PDF/text, attached to this bill (amendments, committee reports, urgency
        # motions, …). ``nemonallo-inditvany-osszesito-lista-query`` above is only
        # the per-type *count*; this query lists the individual documents.
        # (A ``…-biz-query`` committee variant exists but is a strict subset, so
        # the main query alone covers every motion.)
        motions = []
        for m in self.select_all(P, "nem-onallo-iromany-for-onallo-iromany-query", oid):
            text = _first_subrow(m.get("iromanyszoveg"))
            link = (text or {}).get("iromanyszovegLink")
            motions.append({
                "iromanyId": m.get("modositoId"),
                "billNumber": m.get("iromanyszam"),
                "billNumberSort": m.get("iromanyszamSorrendhez"),
                "mainType": m.get("fotipus"),
                "type": m.get("tipus"),
                "submittedDate": m.get("benyujtasDatuma"),
                "textUrl": f"{BASE}{link}" if link else None,
                "textCaption": (text or {}).get("iromanyszovegCaption"),
                "noText": bool(m.get("nincsSzoveg")),
                "hasVote": bool(m.get("vanSzavazas")),
                "note": m.get("megjegyzes"),
                "sponsors": [{
                    "personID": s.get("kepviseloId"),
                    "factionId": s.get("kepviseloFrakcioId"),
                    "committeeId": s.get("bizottsagId"),
                    "label": s.get("benyujto"),
                } for s in _subrows(m.get("benyujto"))],
            })

        header = {}
        intra = self.select_all(IROMANY_INTRA_PROVIDER,
                                "onallo-iromany-adatlap-for-intra-query",
                                {"pId": bill_id})
        if intra:
            h = intra[0]
            header = {
                "subtype": h.get("tipus"),
                "character": h.get("jelleg"),
                "negotiationMode": h.get("targyalasiMod"),
                "statusType": h.get("allapottipus"),
                "currentEvent": h.get("aktualisIromanyEsemeny"),
                "promulgationNumber": h.get("kihirdetesSzama"),
                "mkNumber": h.get("mkSzama"),
                "promulgationDate": h.get("kihirdetesDatuma"),
                "remark": h.get("megjegyzes"),
                "lastModifier": h.get("utolsoModositoIromanySzam"),
            }
            # Once promulgated, the bill has a Magyar Közlöny issue number and
            # date but no link to the gazette itself; resolve the gazette's own
            # PDF link from magyarkozlony.hu (degrades to the listing URL).
            kozlony = magyarkozlony.resolve(self.http, h.get("mkSzama"),
                                            h.get("kihirdetesDatuma"))
            if kozlony:
                header["kozlonyUrl"] = kozlony["url"]
                header["kozlonyDocUrl"] = kozlony["docUrl"]

        return {
            "header": header,
            "events": events,
            "committeeEvents": committee_events,
            "votes": votes,
            "deadlines": deadlines,
            "committees": committees,
            "documents": documents,
            "motionSummary": motion_summary,
            "motions": motions,
        }

    # ---- votes (szavazások) ---------------------------------------------

    def votes(self, cycle: int, date_from: str, date_to: str) -> list[dict]:
        """The roll-call votes of ``cycle`` within ``[date_from, date_to]``, paged.

        Each returned dict carries the vote's UUID (``voteId`` — the same
        ``szavazasId`` a bill's vote tally references, so the two link both
        ways), datetime, voting mode, subject, result, the igen/nem/tartózkodás
        tallies, whether a per-MP roll call exists (``hasPerMp``), and the
        **subjects voted on** — each with the ``billId`` (iromanyId) that joins
        to a bill where it is one we hold (EXT-2)."""
        body = {
            "pCiklus": int(cycle),
            "hCiklusElejeLimit": date_from,
            "pIdoszakEleje": date_from,
            "pIdoszakVege": date_to,
            "pIdopont": date_to,
            "pSzavazasiMod": [], "pSzavazasOka": [],
            "pFrakcio": [], "pKepviselo": [],
        }
        out: list[dict] = []
        for r in self.select_all(SZAVAZAS_PROVIDER, "szavazas-lista-query", body):
            out.append({
                "voteId": r.get("id"),
                "datetime": r.get("idopont"),
                "votingMode": r.get("szavazasiMod"),
                "subject": r.get("szavazasOka"),
                "result": r.get("eredmeny"),
                "yes": r.get("igen"),
                "no": r.get("nem"),
                "abstain": r.get("tartozkodas"),
                "cycle": r.get("ciklus"),
                "hasPerMp": bool(r.get("hasKepviselo")),
                "subjects": _parse_vote_subjects(r.get("szavazasTargya")),
            })
        return out

    def vote_detail(self, vote_id: str) -> dict:
        """The full detail of one vote: its basic header, the **per-MP roll
        call** (every representative's individual vote, keyed by the
        ``kepviseloId`` that joins to an MP profile — EXT-2) and the
        **per-faction breakdown**.

        Three small requests per vote, all politely throttled (SCR-4)."""
        pid = {"pId": vote_id}

        records = [{
            "personID": r.get("kepviseloId"),
            "name": r.get("nev"),
            "factionName": r.get("frakcioNev"),
            "voteValue": r.get("szavazatTipus"),
        } for r in self.select_all(
            SZAVAZAS_PROVIDER, "szavazat-by-szavazas-and-tipus-list-query",
            {"pSzavazasId": vote_id})]

        faction_stats = []
        for r in self.select_all(SZAVAZAS_PROVIDER,
                                 "szavazas-by-frakcio-stat-query", pid):
            sub = _first_subrow(r.get("szavazasByFrakcioSchema")) or {}
            faction_stats.append({
                "factionName": r.get("frakcioNev"),
                "factionId": r.get("frakcioId"),
                "againstFaction": r.get("frakcioElleniSzavazat"),
                "total": sub.get("osszesen"),
                "yes": sub.get("igenSzam"),
                "no": sub.get("nemSzam"),
                "abstain": sub.get("tartozkodasSzam"),
                "absent": sub.get("igazoltanTavolSzam"),
                "notVoting": sub.get("nemSzavazottSzam"),
            })

        header = {}
        basic = self.select_all(SZAVAZAS_PROVIDER, "szavazas-alap-adatok-query", pid)
        if basic:
            b = basic[0]
            header = {
                "votingModeDisplay": b.get("szavazasiMod"),
                "subjectDisplay": b.get("szavazasOkaMegjelenites"),
                "totalVotes": b.get("osszesSzavazat"),
                "remark": b.get("megjegyzes"),
            }

        return {
            "header": header,
            "records": records,
            "factionStats": faction_stats,
        }

    # ---- committees (bizottságok) ---------------------------------------

    def _committee_scope(self, cycle: int, start: str, end: str) -> dict:
        """The cycle+date-range envelope every committee query takes.

        `pIdopont` is the date a "who sits on it now" question is answered for;
        every caller that asks a *point-in-time* question overrides it. The rest
        of the queries are range queries and ignore it."""
        return {"pCiklus": int(cycle), "hCiklusElejeLimit": start,
                "hCiklusVegeLimit": end, "pIdoszakEleje": start,
                "pIdoszakVege": end, "pIdopont": end}

    def committee_bodies(self, cycle: int, start: str, end: str) -> list[dict]:
        """Every committee **body** of ``cycle`` — main committees and their
        subcommittees — one row each.

        The upstream listing is shaped for a two-level table rather than a list:
        with ``pAlbizottsag`` it returns one row per (main committee, body) pair,
        where ``albizottsagId``/``albizottsagNev`` name the body the row is about
        and ``bizottsagId``/``bizottsagNev`` always name the **main** committee —
        equal to each other on a main committee's own row (``fobizottsagSor``),
        the parent's on a subcommittee's. So the body's identity is taken from the
        `albizottsag*` pair and the parent recorded only for subcommittees, which
        is what makes both levels one flat list keyed by a single id.

        The dates are the body's own (a subcommittee is usually created months
        into the term); `code` is the per-cycle committee code that also builds
        the portal homepage URL, and `standingCode` the stable three-letter code
        (AGB, GAB, …) that is the same body across cycles and keys its logo."""
        rows = self.select_all(BIZOTTSAG_PROVIDER, "bizottsag-lista-wwwquery",
                               {**self._committee_scope(cycle, start, end),
                                "pAlbizottsag": True}, _COMMITTEE_PAGE_SIZE)
        out: dict[str, dict] = {}
        for r in rows:
            is_main = bool(r.get("fobizottsagSor"))
            bid = r.get("albizottsagId") or r.get("bizottsagId")
            if not bid:
                continue
            # A body can be listed more than once (it is a row per parent pair);
            # the first spelling wins so a re-listing cannot reorder the registry.
            out.setdefault(bid, {
                "committeeId": bid,
                "name": (r.get("albizottsagNev") if not is_main
                         else r.get("bizottsagNev")) or r.get("bizottsagNev"),
                "parentId": None if is_main else r.get("bizottsagId"),
                "parentName": None if is_main else r.get("bizottsagNev"),
                "isSubcommittee": not is_main,
                "standingCode": r.get("allandoBizottsagKod"),
                # `bizottsagKod` is the *main* committee's code on every row of
                # its block, subcommittees included — it is what builds that
                # committee's homepage URL, so keeping it on a subcommittee would
                # read as the subcommittee's own code and point at the parent.
                "code": r.get("bizottsagKod") if is_main else None,
                "cycle": r.get("ciklus"),
                "dateStart": (r.get("letrahozasDatuma") or "")[:10] or None,
                "dateEnd": (r.get("megszuntetesDatuma") or "")[:10] or None,
                "ord": (r.get("fobizottsagSorszamField") if is_main
                        else r.get("alBizottsagSorszamField")),
            })
        return list(out.values())

    def committee_sheet(self, committee_id: str) -> dict | None:
        """One body's header sheet (*mini adatlap*): its **type** (állandó,
        eseti, vizsgáló, nemzetiségi, törvényalkotási), contact e-mail and
        whether the portal publishes a homepage for it.

        The type is the one field no listing carries, and it is not derivable
        from the name — "A Kegyelmi Botrány Felelőseit Feltáró Vizsgálóbizottság"
        happens to say so, "a Médiatanács elnökét és tagjait jelölő eseti
        bizottság" does not, and a subcommittee has no type at all. One small
        request per body, ~40 per cycle."""
        rows = self.select_all(BIZOTTSAG_PROVIDER, "bizottsag-mini-adatlap-query",
                               {"pId": committee_id})
        if not rows:
            return None
        r = rows[0]
        return {
            "type": r.get("bizottsagTipus"),
            # Addresses are published obfuscated ("agb[kukac]parlament.hu"); we
            # keep them exactly as published rather than un-mangling them, which
            # would republish an address the House chose to hide from crawlers.
            "email": r.get("emailCim") or None,
            "hasSite": bool(r.get("honlapKell")),
            "active": bool(r.get("aktivBizottsag")),
        }

    def committee_members(self, cycle: int, start: str, end: str,
                          as_of: str | None = None) -> list[dict]:
        """Who sits on each committee **on one date** (`as_of`, default the end
        of the range) — the portal's own "Bizottságok tagjai és tisztségviselői".

        Two requests: the query answers for main committees or for subcommittees,
        never both (`pBizottsagAlbizottsagai` switches it over rather than adding
        to it), so each is asked for and the two are concatenated.

        Each row is a committee carrying its roster in a nested sub-table; this
        flattens it to one row per seat, tagged with the committee it is on. The
        seat carries the member's ``kepviseloId`` — the same person-id space as a
        speech's speaker — so the roster joins straight to a profile (EXT-2), and
        the faction they held the seat for.

        This is a **snapshot**, not a history: a member who left before ``as_of``
        is not in it. :meth:`committee_terms` is the dated record."""
        point = as_of or end
        out: list[dict] = []
        for sub in (False, True):
            body = {**self._committee_scope(cycle, start, end),
                    "pIdopont": point, "pTagok": True, "pBizottsag": [],
                    "pBizottsagAlbizottsagai": sub, "pMunkatarsak": False}
            for row in self.select_all(BIZOTTSAG_PROVIDER, "bizottsag-tagjai-query",
                                       body, _COMMITTEE_PAGE_SIZE):
                cid = row.get("bizottsagId")
                for i, m in enumerate(_subrows(row.get("bizottsagiTagok"))):
                    label = m.get("tisztsegNeve")
                    out.append({
                        "committeeId": cid,
                        "personID": m.get("kepviseloId"),
                        # An asterisk marks a name the registry records under an
                        # earlier spelling (`vanKepviseloRegiNeve`); it is a
                        # footnote marker, not part of the name.
                        "name": (m.get("kepviseloNev") or "").rstrip("*").strip(),
                        "role": committee_role(label),
                        "roleLabel": label,
                        "factionId": m.get("frakcioId"),
                        "factionName": m.get("frakcioNeve"),
                        "governing": m.get("kormanyparti"),
                        "ord": i,
                        "asOf": point,
                    })
        return out

    def committee_terms(self, cycle: int, start: str, end: str) -> list[dict]:
        """Every **dated** committee membership and office term of ``cycle``.

        Behind the portal's *Bizottsági tagság, tisztség változásai* page. Despite
        the name this is not only the mid-term churn: over a **closed** cycle every
        seat ends when the term does, so the listing is the cycle's complete
        membership record — for cycle 42 it returned all 212 seats the end-of-cycle
        snapshot shows plus 214 more the snapshot cannot (people who left before
        it). For the **running** cycle it is genuinely only the changes so far,
        which is why :meth:`committee_members` is fetched as well and the two are
        unioned downstream.

        A row can carry a membership span (``tagsag*``), an office span
        (``tisztseg*``) or both; each is emitted separately so a term is one row
        with one pair of dates. ``replacing`` is whom the person took over from,
        when the registry records it."""
        rows = self.select_all(
            BIZOTTSAG_PROVIDER,
            "bizottsagi-tagsag-tisztseg-valtozasai-reszletes-biz-query",
            {**self._committee_scope(cycle, start, end), "pTagvaltozas": True},
            _COMMITTEE_PAGE_SIZE)
        out: list[dict] = []
        for r in rows:
            base = {
                "committeeId": r.get("bizottsagId"),
                "personID": r.get("kepviseloId"),
                # The name comes with the faction in brackets ("Fazekas Sándor
                # (Fidesz)"); the bare name is what joins to a person row, and the
                # faction is already carried by the seat, so the suffix is dropped.
                "name": _strip_faction(r.get("kepviseloNeve")),
                "factionName": _faction_suffix(r.get("kepviseloNeve")),
            }
            if r.get("tagsagKezdete"):
                out.append({**base, "kind": "membership", "role": "member",
                            "roleLabel": None,
                            "dateStart": r.get("tagsagKezdete"),
                            "dateEnd": r.get("tagsagVege"),
                            "reason": r.get("tagsagValtozasOka"),
                            "replacing": _replaced_name(r.get("tagsagKiHelyett"))})
            if r.get("tisztsegKezdete"):
                label = r.get("tisztsegMegnevezese")
                out.append({**base, "kind": "office",
                            "role": committee_role(label), "roleLabel": label,
                            "dateStart": r.get("tisztsegKezdete"),
                            "dateEnd": r.get("tisztsegVege"),
                            "reason": r.get("tisztsegValtozasOka"),
                            "replacing": _replaced_name(r.get("tisztsegKiHelyett"))})
        return out

    def committee_meetings(self, cycle: int, start: str, end: str) -> list[dict]:
        """Every committee meeting held in ``cycle`` — date, kind, quorum,
        duration and the minutes PDF.

        ``pAlbizottsaggal`` includes the subcommittees' own meetings, so this is
        one request per cycle for every body rather than one per committee. The
        minutes path is site-absolute and resolved to a full URL here; it is
        **linked, never mirrored** (LEGAL-1). A meeting with no published minutes
        keeps its row — that a committee met and left no record is itself the
        finding.

        ``duration_s`` is seconds despite the upstream caption saying óra:perc
        (a 27-minute meeting comes back as 1620)."""
        body = {**self._committee_scope(cycle, start, end),
                "pAlbizottsaggal": True, "pCsakAlbizottsag": False,
                "pKihelyezettUles": False}
        out = []
        for r in self.select_all(BIZOTTSAG_PROVIDER, "bizottsag-ulesei", body,
                                 _COMMITTEE_PAGE_SIZE):
            path = r.get("jegyzokonyvPath")
            out.append({
                "meetingId": r.get("ulesId"),
                "committeeId": r.get("bizottsagId"),
                "committeeName": r.get("bizottsagNeve"),
                "number": r.get("ulesSorszam"),
                "numberInYear": r.get("evenBeluliSorszam"),
                "datetime": r.get("ulesDatuma"),
                "kind": r.get("ulesTipusa"),
                "quorum": r.get("hatarozatkepesseg"),
                "durationS": r.get("ulesHosszaMasodPercben"),
                "minutesUrl": _committee_file_url(path),
            })
        return out

    def committee_meeting_stats(self, cycle: int, start: str,
                                end: str) -> list[dict]:
        """Per-committee meeting **aggregates**: how many times it met, for how
        long in total, and how the meetings broke down by quorum.

        Upstream's own totals rather than ours: it counts meetings we do not list
        individually (closed sittings of some bodies) and applies the House's own
        quorum rules, so the two figures are reported side by side downstream
        instead of one being recomputed from the other. ``totalMinutes`` is
        minutes here, unlike the per-meeting seconds above.

        The listing carries a trailing all-committees summary row
        (``nemOsszesitoSor`` false), which is dropped: a sum over the rows is the
        consumer's own business."""
        body = {**self._committee_scope(cycle, start, end),
                "pKihelyezettUles": False, "pAlbizottsaggal": True}
        out = []
        for r in self.select_all(BIZOTTSAG_PROVIDER,
                                 "www-bizottsagi-ulesek-szama-query", body,
                                 _COMMITTEE_PAGE_SIZE):
            if not r.get("nemOsszesitoSor"):
                continue
            out.append({
                "committeeId": r.get("bizId"),
                "meetings": r.get("osszesen"),
                "totalMinutes": r.get("idotartam"),
                "quorate": r.get("osszesenHatarozatkepes"),
                "inquorate": r.get("hatarozatkeptelen"),
                "lostQuorum": r.get("hatarozatkeptelenneValtHatarozatkepes"),
                "agendaRejected": r.get("nemFogadtaElHatarozatkepes"),
            })
        return out

    def committee_documents(self, cycle: int, start: str, end: str,
                            committee_id: str) -> list[dict]:
        """The irományok one committee **dealt with** in ``cycle``.

        One row per iromány: the document's own id — which joins to a held `bill`
        (EXT-2) — its number and title, the committee's stage on it and the date
        it was referred there. Submitter names ride along in a nested sub-table
        carrying each sponsor's ``kepviseloId``, so a document's sponsors link to
        profiles without name matching.

        Asked for **per committee** even though the query runs cycle-wide: run
        that way its rows name the committee only in a display header
        ("Népjóléti Bizottság által tárgyalt irományok") and carry no committee
        id at all, so the listing could only be re-attached to a body by matching
        that string. ``pInputBizottsag`` scopes it instead, and the id comes from
        the caller."""
        body = {**self._committee_scope(cycle, start, end),
                "pInputBizottsag": committee_id, "pFolyamatban": False,
                "pUniosNapirendiPont": False, "pNemzetisegiNapirendiPont": False}
        out = []
        for r in self.select_all(
                BIZOTTSAG_PROVIDER,
                "bizottsagok-altal-targyalt-iromanyok-lista-query", body,
                _COMMITTEE_PAGE_SIZE):
            sponsors = [{"personID": s.get("kepviseloId"),
                         "name": s.get("benyujto")}
                        for s in _subrows(r.get("benyujto"))
                        if s.get("benyujto") or s.get("kepviseloId")]
            out.append({
                "committeeId": committee_id,
                "billId": r.get("iromanyId"),
                "billNumber": r.get("iromanyszam"),
                "title": r.get("iromanycim"),
                "status": r.get("bizottsagiAllapot"),
                "referredAt": r.get("kijelolesDatuma"),
                "sponsors": sponsors,
            })
        return out

    def committee_submissions(self, cycle: int, start: str, end: str,
                              committee_id: str) -> list[dict]:
        """The irományok one committee **submitted** — its own motions
        (*önálló indítvány*) and the reports and unified proposals it tabled on
        other people's bills (*nem önálló indítvány*).

        Per committee rather than per cycle: without ``pEgyBizottsag`` both
        queries answer for the whole House (17 954 rows in cycle 42), which is the
        bills module's job, not this one's. A Törvényalkotási Bizottság alone
        tabled 1 194 non-self-standing motions in that cycle, so the listing is
        paged at the usual width."""
        out = []
        for query, kind in (("onallo-inditvanyok-query", "own"),
                            ("nem-onallo-inditvanyok-query", "motion")):
            body = {**self._committee_scope(cycle, start, end),
                    "pEgyBizottsag": committee_id}
            for r in self.select_all(BIZOTTSAG_PROVIDER, query, body,
                                     _COMMITTEE_PAGE_SIZE):
                text = _first_subrow(r.get("iromanyszoveg") or r.get("szoveg")) or {}
                link = text.get("iromanyszovegLink")
                out.append({
                    "committeeId": committee_id,
                    "kind": kind,
                    "billId": r.get("onalloId") or r.get("modositoId"),
                    "billNumber": r.get("iromanyszam"),
                    "title": r.get("cim"),
                    "docType": r.get("tipus"),
                    "textUrl": f"{COMMITTEE_FILE_BASE}{link}" if link else None,
                })
        return out

    def committee_upcoming(self, cycle: int, start: str, end: str, *,
                           from_date: str | None = None) -> list[dict]:
        """The committee meetings that are **scheduled but have not happened** —
        the portal's *Adott időszak tervezett bizottsági ülései*.

        The only committee-side answer to "what is about to happen", the way the
        napirend is for the plenary (NR-1), and like it there is no history to
        keep: the newest read is the whole truth.

        ``pIdoszakEleje`` is a *from* date here with **no upper bound** — the
        query is "everything scheduled on or after this day". So it defaults to
        today, not to the cycle's start: asked from the start of the term it
        returns every sitting the committees ever put in the diary (369 for
        cycle 43 in September, against the couple of dozen still ahead), and
        each one already held would land in the "upcoming" table as well as in
        the meeting record. ``from_date`` overrides it for a backfill."""
        first = from_date or _today()
        body = {"pCiklus": int(cycle), "hCiklusElejeLimit": start,
                "hCiklusVegeLimit": end, "pIdoszakEleje": first,
                "pHelyszin": True, "pUnios": False, "pNemzetisegi": False}
        out = []
        for r in self.select_all(BIZOTTSAG_PROVIDER,
                                 "tervezett-bizottsagi-ules-idorend-query", body,
                                 _COMMITTEE_PAGE_SIZE):
            for m in _subrows(r.get("adatok")):
                out.append({
                    "meetingId": m.get("id"),
                    "committeeId": m.get("bizottsagId"),
                    "committeeName": m.get("bizottsagNev"),
                    "date": (r.get("nap") or "").replace(".", "-").strip("-"),
                    "time": r.get("idopont"),
                    "venue": " ".join(x for x in (m.get("helyszin"),
                                                  m.get("hely")) if x) or None,
                    # Upstream marks a cancelled sitting by filling this in; an
                    # empty string means it is still on.
                    "cancelled": bool((m.get("elmaradt") or "").strip()),
                })
        return out


# A bill reference embedded in a speech's agenda event (nested ``esemenyId``
# blob from aktusok). Best-effort: the structure varies, so we pull recognisable
# bill codes out of whatever string content is present.
_BILL_CODE_RE = re.compile(r"\b[A-ZÁÉÍÓÖŐÚÜŰ]/\d+")


def _subrows(nested) -> list[dict]:
    """Rows of a Felicitas nested sub-table (``{metadata, rows}``) as dicts."""
    if not isinstance(nested, dict):
        return []
    return rows_as_dicts(nested)


def _first_subrow(nested) -> dict | None:
    rows = _subrows(nested)
    return rows[0] if rows else None


# The committee registry writes a person as "Fazekas Sándor (Fidesz)" — name and
# faction in one string — while every other listing keeps them apart. These split
# it back, so the name that joins to a person row is the bare name (SCR-5: the
# faction is kept, not thrown away).
_FACTION_SUFFIX_RE = re.compile(r"\s*\(([^()]*)\)\s*$")


def _strip_faction(label: str | None) -> str | None:
    """"Fazekas Sándor (Fidesz)" -> "Fazekas Sándor"."""
    if not label:
        return None
    # The trailing asterisk marks a name recorded under an earlier spelling.
    return _FACTION_SUFFIX_RE.sub("", label).rstrip("*").strip() or None


def _faction_suffix(label: str | None) -> str | None:
    """"Fazekas Sándor (Fidesz)" -> "Fidesz"; None when no faction is given."""
    m = _FACTION_SUFFIX_RE.search(label or "")
    return (m.group(1).strip() or None) if m else None


def _replaced_name(nested) -> str | None:
    """Whom this term's holder took over from, from the ``kiHelyett`` sub-table
    (usually empty — the seat was new, not inherited)."""
    row = _first_subrow(nested)
    return _strip_faction(row.get("tisztsegviseloNeve")) if row else None


def _parse_stages(diagram) -> list[dict]:
    """The bill's legislative-stage diagram → ordered ``[{key, label, done}]``.

    The Felicitas ``diagram`` sub-table lists the fixed legislative stages
    (Tárgysorozatban → … → Kihirdetve) in order; each row's ``allapot`` ends in
    ``_MEGTORTENT`` (the stage has happened — a past event) or ``_NEM_TORTENT_MEG``
    (not yet — a future event). This drives the bill timeline (status/history)."""
    stages = []
    for d in _subrows(diagram):
        allapot = d.get("allapot") or ""
        stages.append({
            "key": d.get("diagramElem"),
            "label": d.get("tooltip"),
            "done": allapot.endswith("_MEGTORTENT"),
        })
    return stages


def _parse_vote_subjects(nested) -> list[dict]:
    """The vote's ``szavazasTargya`` nested sub-table → the bills/motions it
    decided. Each row carries the iromány UUID (``iromanyId`` — joins to a bill
    when it is a type we hold, EXT-2), number and title; a vote can decide more
    than one subject (final vote on a bill plus its motions)."""
    out: list[dict] = []
    for r in _subrows(nested):
        num = r.get("iromanySzam")
        if not (r.get("iromanyId") or num):
            continue
        out.append({
            "billId": r.get("iromanyId"),
            "billNumber": num,
            "title": r.get("iromanyCim"),
        })
    return out


def _parse_type_table(nested) -> tuple[str | None, list[str]]:
    """Parse a speech's nested type/iromány sub-table → ``(type, bills)``.

    Each nested row names the speech's type and the irományok it touches. The
    restructured API (2026-09) spells the type ``esemenyNeve`` and nests the
    references one level deeper, as a ``kapcsolodoIromanyok`` table of
    ``{onalloIromanyId, modositoIromanyId, iromanySzama}``; the older flat spelling
    (``felszolalasTipusa`` / ``felszolalasIromany`` strings) is still read so an
    archived raw bundle parses the same way. The first non-empty type wins; bill
    references are collected as their base code ("T/438/4" → ``T/438``, the form
    the archive and the agenda labels use) and kept for the Bills module (EXT-2)."""
    if not isinstance(nested, dict):
        return (nested if isinstance(nested, str) else None), []
    stype = None
    bills: list[str] = []
    for r in rows_as_dicts(nested):
        if stype is None:
            stype = r.get("esemenyNeve") or r.get("felszolalasTipusa") or None
        for ref in _subrows(r.get("kapcsolodoIromanyok")):
            bills.extend(_BILL_CODE_RE.findall(ref.get("iromanySzama") or ""))
        iromany = r.get("felszolalasIromany")
        if iromany and iromany.strip() not in ("", "-"):
            bills.extend(_BILL_CODE_RE.findall(iromany))
    return stype, sorted(set(bills))


def _merge_roster(listing: list[dict], roster: list[dict],
                  day_uuid: str | None = None) -> list[dict]:
    """Fold the speeches the agenda-grouped listing omits into it.

    The roster (:data:`DAY_SPEECH_ROSTER_QUERY`) is the complete set of the day's
    speeches but knows no agenda act, while the agenda-grouped listing knows the
    acts but drops every speech that is linked to none. So each roster speech the
    listing does not have is inserted at its join-number position, inheriting the
    agenda act of the speech it follows — it is a continuation of that act's
    debate — or, when it comes before any known act, of the speech that follows it.
    Without an act the transform would file it under a stray agenda item and the
    site's sitting-day view (which renders speeches per agenda item) would drop it.

    The listing's own rows are never overwritten: they carry the act, committee and
    bill references the roster lacks. Order is by ``sorszam``, stable, so a speech
    the source lists once per act (see ``_dedup_speeches``) keeps its run.
    """
    known = {s.get("speech_uuid") for s in listing if s.get("speech_uuid")}
    extra = [dict(r, committee_id=None, is_committee=False, aktus_id=None,
                  aktus=None, bills=r.get("bills") or [], from_roster=True)
             for r in roster
             if r.get("speech_uuid") and r["speech_uuid"] not in known]
    if not extra:
        return listing
    logger.info("day %s: %d speech(es) missing from the agenda listing, "
                "filled from the roster (%d listed, %d total)",
                day_uuid, len(extra), len(listing), len(listing) + len(extra))
    merged = sorted(listing + extra,
                    key=lambda s: (s.get("sorszam") is None, s.get("sorszam") or 0))
    act = None
    for sp in merged:                       # inherit from the preceding act
        if sp.get("aktus_id") or sp.get("aktus"):
            act = (sp.get("aktus_id"), sp.get("aktus"))
        elif act:
            sp["aktus_id"], sp["aktus"] = act
    act = None
    for sp in reversed(merged):             # leading gap: from the following act
        if sp.get("aktus_id") or sp.get("aktus"):
            act = (sp.get("aktus_id"), sp.get("aktus"))
        elif act:
            sp["aktus_id"], sp["aktus"] = act
    return merged
