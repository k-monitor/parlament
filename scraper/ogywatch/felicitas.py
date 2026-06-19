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
  3. ``ulesnap-felszolalas-adata-query`` (speech UUID) → that speech's full text
     (HTML), speaker, type and duration.
  4. ``ulesnapok-video-query``      (day UUID, ``pTeljes=true``) → a ``playseq.php``
     URL that resolves to the whole-day HLS playlist on ``sgis.parlament.hu``.

This token-free JSON path replaces the reference pipeline's fragile PAIR-proxy
HTML scraping; the CGI/PAIR backends remain documented fallbacks (SRC-2) but are
not needed for v1.

**Representatives** (``kepviselo-query-provider``):

  * ``kepviselo-lista-idopontban`` (cycle + date range, paged) → the MP roster.
  * a family of per-MP detail queries keyed on ``{"pId": <kepviseloId>}``
    (bio, faction history, committees, constituency, education, per-cycle speech
    and bill counts) and a photo resource endpoint.

Cycle date ranges (needed as query bounds) come from the list query's parameter
``rebind`` endpoint.
"""

from __future__ import annotations

import logging
import re

from .http_client import HttpClient

logger = logging.getLogger(__name__)

BASE = "https://www.parlament.hu"
# Plenary uses the bare /felicitas path (verified working); the representative
# endpoints were captured under /web/guest/felicitas. Both resolve.
PLENARY_PROVIDER = (f"{BASE}/felicitas/api/query/select/"
                    "plenarisulesadatok-plenarisules-registry/"
                    "plenaris-ules-adatok-query-provider")
KEPVISELO_PROVIDER = (f"{BASE}/web/guest/felicitas/api/query/select/"
                     "registry/kepviselo-query-provider")
IROMANY_PROVIDER = (f"{BASE}/web/guest/felicitas/api/query/select/"
                   "iromanyadatok-iromany-registry/iromanyok-query-provider")
KEPVISELO_REBIND = (f"{BASE}/web/guest/felicitas/api/query/parameter/rebind/"
                   "kepviseloadatok-kepviselo-kepviselolista-idopont/"
                   "kepviselo-lista-idopontban-query")
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

    # ---- generic POST select --------------------------------------------

    def _select(self, provider: str, query: str, body: dict, page: int = 0) -> dict:
        url = f"{provider}/{query}?page={page}"
        self.http.polite_sleep()
        return self.http.post_json(url, body, headers=_REFERER)

    def select_all(self, provider: str, query: str, body: dict) -> list[dict]:
        """Page through a select query and return all rows as field-name dicts."""
        first = self._select(provider, query, body, page=0)
        rows = rows_as_dicts(first)
        resp = first.get("response") or {}
        total = resp.get("totalSize") or len(rows)
        page_size = resp.get("pageSize") or len(rows) or 1
        page = 1
        while len(rows) < total:
            payload = self._select(provider, query, body, page=page)
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

    def day_speeches(self, day_uuid: str) -> list[dict]:
        """Per-speech listing of a day from ``ulesnapok-aktusok-query``.

        Returns one dict per speech across all agenda acts, in source order,
        each carrying the agenda-act name, join number, speaker, representative
        id, type, committee, start time, duration and any bill references."""
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
        return out

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
        clip}``. ``day_off1`` (seconds) is the recording start offset that
        per-speech offsets are measured against. ``None`` if no recording."""
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

    def photo(self, person_id: str) -> bytes | None:
        """Fetch an MP's portrait image bytes, or ``None`` if absent."""
        self.http.polite_sleep()
        return self.http.get_bytes(f"{PHOTO_RESOURCE}/{person_id}", headers=_REFERER)

    # ---- bills (irományok) ----------------------------------------------

    def bills(self, cycle: int, *, main_type: str = "T") -> list[dict]:
        """The bills (irományok) of ``cycle`` from ``iromany-query``, paged.

        ``main_type`` is the Felicitas ``fotipus`` filter; ``"T"`` selects
        törvényjavaslatok (law proposals), the basic-support default. Each
        returned dict carries the bill's number, title, type, status, submission
        date, the PDF text link, and its **submitters** — each with the
        ``personID`` (kepviseloId) that joins to an MP profile (EXT-2).
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
            out.append({
                "billId": r.get("iromanyId"),
                "billNumber": r.get("iromanyszam"),
                "billNumberSort": r.get("iromanyszamSorrendezeshez"),
                "title": r.get("cim"),
                "type": r.get("iromanytipus"),
                "mainType": main_type,
                "status": r.get("iromanyAllapot"),
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
