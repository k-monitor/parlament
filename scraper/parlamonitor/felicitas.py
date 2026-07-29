"""parlament.hu **Felicitas** REST API client (token-free JSON).

The modern parlament.hu site is backed by a public JSON query API under
``/felicitas/api/query/...`` (POST bodies, no authentication). This one client
covers both data domains the scraper needs:

**Plenary proceedings** (``plenaris-ules-adatok-query-provider``) — verified
2026-06:

  1. ``ulesnapok-query``            (cycle + date range) → session days + UUIDs
  2. ``ulesnapok-aktusok-query``    (day UUID) → every speech of the day grouped
     by agenda act, each with its join number (``sorszam``), UUID, speaker,
     ``felszolaloId`` (the representative id — a cross-module link, EXT-2),
     type, committee, start time and duration.
  3. ``ulesnap-felszolalasai``      (day UUID, paged) → the day's **complete**
     flat speech listing (the portal's own "ülésnap felszólalásai" table): join
     number, UUID, speaker, representative id, type, start time and duration —
     but no agenda act. Query (2) is *not* complete on its own (see
     :data:`DAY_SPEECH_ROSTER_QUERY`), so the two are merged.
  4. ``ulesnap-felszolalas-adata-query`` (speech UUID) → that speech's full text
     (HTML), speaker, type and duration.
  5. ``ulesnapok-video-query``      (day UUID, ``pTeljes=true``) → a ``playseq.php``
     URL that resolves to the whole-day HLS playlist on ``sgis.parlament.hu``.

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

Cycle date ranges (needed as query bounds) come from the list query's parameter
``rebind`` endpoint.
"""

from __future__ import annotations

import logging
import re

from . import magyarkozlony
from .http_client import HttpClient, HttpError

logger = logging.getLogger(__name__)

BASE = "https://www.parlament.hu"
# Plenary uses the bare /felicitas path (verified working); the representative
# endpoints were captured under /web/guest/felicitas. Both resolve.
PLENARY_PROVIDER = (f"{BASE}/felicitas/api/query/select/"
                    "plenarisulesadatok-plenarisules-registry/"
                    "plenaris-ules-adatok-query-provider")
# The day's flat speech listing (paged, ``{"pUlesnapId": <day uuid>}``), behind the
# portal's own "ülésnap felszólalásai" table. Unlike ``ulesnapok-aktusok-query`` it
# is COMPLETE: that query reports a speech only through the agenda act it is linked
# to, and speeches linked to no act are silently absent from it — chiefly the ones
# with no "Felszólalás oka" (type): a speaker's continuation after being
# interrupted, an unclassified remark. Sitting 43015 listed 287 of its 303 speeches
# that way, the 16 gaps being exactly the type-less rows (e.g. #300, Dr. Árvay
# Nikolett's continued rapporteur reply); across the 713 archived days ≥8 200
# speeches (5.8%) were missing, and on legacy days the act links are sparse enough
# that whole sittings nearly vanished (day 40039: 11 of 139 listed). Note the query
# name carries no ``-query`` suffix — that spelling 404s.
DAY_SPEECH_ROSTER_QUERY = "ulesnap-felszolalasai"
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
PHOTO_RESOURCE = (f"{BASE}/web/guest/felicitas/api/query/resource/"
                 "kepviseloexportok/kepviselo-exported-queries-provider/"
                 "kepviselo-kepek")

_REFERER = {"Referer": f"{BASE}/web/guest/orszaggyulesi-naplo-elozo-ciklusbeli-adatai"}

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


def _parse_off(value: str) -> float:
    """Parse a playseq ``HHMMSS(.fff)`` offset into seconds."""
    intpart, _, frac = value.partition(".")
    intpart = intpart.zfill(6)
    h, m, s = int(intpart[:2]), int(intpart[2:4]), int(intpart[4:6])
    return h * 3600 + m * 60 + s + (float("0." + frac) if frac else 0.0)


def playseq_offsets(playseq: str | None) -> tuple[float, float] | None:
    m = _PLAYSEQ_OFF_RE.search(playseq or "")
    return (_parse_off(m.group(1)), _parse_off(m.group(2))) if m else None


class FelicitasClient:
    def __init__(self, http: HttpClient):
        self.http = http

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

        One row per speech in join-number order — UUID, ``sorszam``, speaker (bare
        name, no faction suffix), representative id, type, start time, duration —
        with no agenda act, committee or bill references. Used to complete the
        agenda-grouped listing in :meth:`day_speeches`."""
        rows = self.select_all(PLENARY_PROVIDER, DAY_SPEECH_ROSTER_QUERY,
                               {"pUlesnapId": day_uuid},
                               size=_DAY_ROSTER_PAGE_SIZE)
        return [{
            "sorszam": r.get("sorszam"),
            "speech_uuid": r.get("felszolalas"),
            "speaker": r.get("felszolalo"),
            "person_id": r.get("felszolaloId"),
            "type": r.get("felszolasOka"),
            "kezdete": r.get("felszolalasKezdete"),
            "duration": r.get("videoIdoMasodperc"),
        } for r in rows]

    def day_speeches(self, day_uuid: str) -> list[dict]:
        """Per-speech listing of a day from ``ulesnapok-aktusok-query``, completed
        from the flat day roster.

        Returns one dict per speech across all agenda acts, in join-number order,
        each carrying the agenda-act name, join number, speaker, representative
        id, type, committee, start time, duration and any bill references. Speeches
        the agenda-grouped query omits are folded in from
        :meth:`day_speech_roster` (see :func:`_merge_roster`)."""
        payload = self._select(PLENARY_PROVIDER, "ulesnapok-aktusok-query",
                               {"pId": day_uuid})
        out: list[dict] = []
        for arow in payload.get("rows", []):
            # arow == [aktusId, esemenyfajtaNev, {rows, metadata}]
            if len(arow) < 3 or not isinstance(arow[2], dict):
                continue
            aktus_id, aktus_name, fels = arow[0], arow[1] or "", arow[2]
            for r in rows_as_dicts(fels):
                # The "felszolalasTipusa" column is itself a nested sub-table
                # (one row per bill the speech touches), each carrying the real
                # type string, the bill UUID and the bill reference text.
                stype, bills = _parse_type_table(r.get("felszolalasTipusa"))
                out.append({
                    "sorszam": r.get("sorszam"),
                    "speech_uuid": r.get("felszolalasId"),
                    "speaker": r.get("felszolalo"),
                    "person_id": r.get("felszolaloId"),
                    "type": stype,
                    "committee_id": r.get("bizottsagId"),
                    "is_committee": r.get("bizottsagBool"),
                    "kezdete": r.get("felszolalasKezdete"),
                    "duration": r.get("videoIdoMasodperc"),
                    "aktus_id": aktus_id,
                    "aktus": aktus_name,
                    "bills": bills,
                })
        try:
            roster = self.day_speech_roster(day_uuid)
        except HttpError as e:
            # The roster only ADDS speeches; losing it degrades the day to the
            # (incomplete) agenda-grouped listing rather than failing the scrape.
            logger.warning("speech roster for day %s unavailable: %s", day_uuid, e)
            return out
        return _merge_roster(out, roster, day_uuid)

    def speech_text(self, speech_uuid: str) -> dict | None:
        """Full text + metadata for one speech (``ulesnap-felszolalas-adata-query``).

        Returns the speech's HTML body, speaker, representative id, type,
        duration, agenda caption and next/previous speech UUIDs, or ``None``."""
        payload = self._select(PLENARY_PROVIDER, "ulesnap-felszolalas-adata-query",
                               {"pId": speech_uuid})
        rows = rows_as_dicts(payload)
        if not rows:
            return None
        r = rows[0]
        return {
            "speech_uuid": r.get("felszolalasId"),
            "next_uuid": r.get("kovetkezoId"),
            "prev_uuid": r.get("elozoId"),
            "caption": r.get("tableCaption"),
            "speaker": r.get("felszolalo"),
            "person_id": r.get("felszolaloId"),
            "role": r.get("tisztseg"),
            "committee_id": r.get("bizottsagId"),
            "committee": r.get("bizottsagNev"),
            "type": r.get("felszolalasTipusa"),
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
        payload = self._select(PLENARY_PROVIDER, "ulesnapok-video-query",
                               {"pId": day_uuid, "pTeljes": True})
        rows = rows_as_dicts(payload)
        if not rows:
            return None
        szerver = (rows[0].get("szerverpath") or "").rstrip("/")
        vsrc = rows[0].get("videoSource") or ""
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
        payload = self._select(PLENARY_PROVIDER, "ulesnapok-video-query",
                               {"pId": speech_uuid, "pTeljes": False})
        rows = rows_as_dicts(payload)
        if not rows:
            return None
        return playseq_offsets(rows[0].get("videoSource") or "")

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
        from the list query's parameter-rebind endpoint."""
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
        """Run one per-MP detail query (e.g. ``kepviselo-adatok-query``)."""
        return self.select_all(KEPVISELO_PROVIDER, query, {"pId": person_id})

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
        including some that are still running."""
        body = {
            "pLegkorabbiDatumValue": earliest,
            "pTisztsegIg": as_of,
            "pMiniszterelnok": True,
            "pMiniszter": True,
            "pAllamtitkar": True,
            "pParlamenti": True,
            "pEgyebVezetoTisztseg": True,
            "pEgyebTisztseg": True,
        }
        return self.select_all(TISZTSEGVISELO_PROVIDER, "tisztsegviselo", body,
                               size=_OFFICE_PAGE_SIZE)

    def photo(self, person_id: str) -> bytes | None:
        """Fetch an MP's portrait image bytes, or ``None`` if absent."""
        self.http.polite_sleep()
        return self.http.get_bytes(f"{PHOTO_RESOURCE}/{person_id}", headers=_REFERER)

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
    """Parse the aktusok nested type/iromany sub-table → ``(type, bills)``.

    Each nested row is ``{iromanyId, felszolalasTipusa, felszolalasIromany}``.
    The first non-empty ``felszolalasTipusa`` is the speech type; bill
    references are collected from the iromany columns (kept for the future
    Bills module, EXT-2)."""
    if not isinstance(nested, dict):
        return (nested if isinstance(nested, str) else None), []
    stype = None
    bills: list[str] = []
    for r in rows_as_dicts(nested):
        if stype is None and r.get("felszolalasTipusa"):
            stype = r["felszolalasTipusa"]
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
                  aktus=None, bills=[], from_roster=True)
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
