"""What a bill changes: in the law, and in itself along the way (BILL-14..16).

For every törvényjavaslat of a cycle this stage produces three things, written
to ``processed/law-changes-<cycle>.json``:

* **The law diff** (BILL-14) — each provision of each law the bill amends, as
  it reads now and as it would read. For a bill that became law it is read off
  njt itself: the time state in force on the promulgation day against the
  newest one, keeping exactly the units whose njt footnote names the new act
  (``method: njt`` — official, exact by construction). For a bill still in
  Parliament there is no such state yet, so the bill's amending instructions
  are applied to njt's newest state (``method: computed``), from the egységes
  javaslat once there is one and from the bill as submitted before that.
  Whatever could not be applied is listed in the bill's own words
  (``unresolved``) rather than dropped.
* **The bill's evolution** (BILL-15) — the bill as submitted against the
  egységes javaslat, which is Parliament's own consolidation of the bill with
  the adopted összegző módosító javaslat, provision by provision.
* **Each amendment as a diff** (BILL-16) — every módosító javaslat's points
  with their insertions and deletions, read off the PDF's formatting.

**Cost.** Documents are read once: the PDF is downloaded, its extraction
(:mod:`.pdftext`) cached per URL and the PDF itself discarded, so a parser
change re-runs from disk. njt states are cached by :class:`.njt.NjtStore`.
Each bill's result carries a fingerprint of everything it was computed from,
so an unchanged bill costs no request and almost no CPU on the next pass; an
adopted bill read off njt is final and is never recomputed. A pass spends at
most ``limit`` requests (SCR-4) and leaves the rest for the next one, keeping
the previous result of any bill it did not reach.

**Degradation (SCR-5/SCR-6).** Without PyMuPDF the stage does nothing. A
scanned document (a paper filing) is recorded as one and not parsed; a failed
fetch or parse costs that bill's record, never the pass.
"""

from __future__ import annotations

import hashlib
import json
import logging
import lzma
from datetime import datetime, timezone
from pathlib import Path

from ..config import Paths
from ..documents.scrape import document_id
from ..http_client import HttpClient
from . import motions as motion_parser
from . import njt, novella, pdftext

logger = logging.getLogger(__name__)

SOURCE = "parlamonitor-lawdiff"
MAX_PDF_BYTES = 80 * 1024 * 1024
_REFERER = {"Referer": "https://www.parlament.hu/"}
# Amending instructions whose law could not be identified are only shown when
# they plainly name another kind of act; the rest are not amendments of a law
# (a bill's references to its own provisions, a treaty's text).
_SHOWN_UNPARSED = {"rendelet", "melléklet", "hatályon kívül helyezés",
                   "módosított szöveggel hatályba lépés", "ismeretlen szerkezet"}
# How much of the bill's own wording an unresolved instruction quotes: each
# quote, and the whole entry.
_QUOTE_LIMIT = 600
_TEXT_LIMIT = 900


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- the document cache -----------------------------------------------------------

class DocCache:
    """Extracted documents, one xz JSON per URL and extraction kind.

    Published irományok are static, so an extraction is redone only when
    :data:`.pdftext.EXTRACT_VERSION` moves."""

    def __init__(self, http: HttpClient, root: Path, budget: njt.Budget, *,
                 force: bool = False):
        self.http = http
        self.root = Path(root)
        self.budget = budget
        self.force = force
        self.fetched = 0
        self.reused = 0

    def get(self, url: str, kind: str) -> dict:
        """``{scan, lines}`` (kind ``lines``) or ``{scan, lines}`` of marked runs
        (kind ``marked``); ``{error}`` when the document could not be had."""
        f = self.root / f"{document_id(url)}.{kind}.json.xz"
        if f.exists() and not self.force:
            try:
                cached = json.loads(lzma.decompress(f.read_bytes()))
                if cached.get("extractVersion") == pdftext.EXTRACT_VERSION \
                        and cached.get("url") == url:
                    self.reused += 1
                    return cached
            except (OSError, ValueError, lzma.LZMAError):
                pass
        self.budget.spend()
        got = self.http.get_capped(url, max_bytes=MAX_PDF_BYTES, headers=_REFERER)
        self.http.polite_sleep()
        self.fetched += 1
        if got.over_cap:
            out = {"error": "túl nagy dokumentum"}
        elif got.data is None:
            out = {"error": f"HTTP {got.status}"}
        elif got.data[:5] != b"%PDF-":
            out = {"error": "nem PDF"}
        else:
            pdf = got.data
            scan = pdftext.is_scan(pdf)
            lines = [] if scan else (pdftext.lines(pdf) if kind == "lines"
                                     else pdftext.marked_lines(pdf))
            out = {"scan": scan, "lines": lines}
        out.update(url=url, kind=kind, extractVersion=pdftext.EXTRACT_VERSION,
                   fetchedAt=_now_iso())
        # Only a document actually read is kept: a 404 or an error page is asked
        # again on a later pass rather than remembered as the answer.
        if "error" not in out:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_bytes(lzma.compress(json.dumps(out, ensure_ascii=False).encode("utf-8")))
            tmp.replace(f)
        return out


# --- choosing documents ------------------------------------------------------------

def _motions(rec: dict) -> list[dict]:
    return [m for m in (rec.get("detail") or {}).get("motions") or [] if m.get("textUrl")]


def _consolidated(rec: dict) -> dict | None:
    """The latest egységes javaslat — the bill as the House is about to vote on it."""
    cands = [m for m in _motions(rec) if motion_parser.is_consolidated(m.get("type"))]
    return max(cands, key=lambda m: m.get("billNumberSort") or 0) if cands else None


def _amendments(rec: dict) -> list[dict]:
    return sorted((m for m in _motions(rec) if motion_parser.is_amendment(m.get("type"))),
                  key=lambda m: m.get("billNumberSort") or 0)


def _act(rec: dict) -> dict | None:
    """The act a promulgated bill became, as njt names it."""
    h = (rec.get("detail") or {}).get("header") or {}
    number, date = h.get("promulgationNumber"), h.get("promulgationDate")
    if rec.get("status") != "kihirdetve" or not number or not date:
        return None
    try:
        n = novella.roman(str(number).strip().rstrip("."))
    except KeyError:
        return None
    year = int(str(date)[:4])
    doc = njt.doc_id(year, n)
    return {"label": f"{year}. évi {str(number).strip().rstrip('.')}. törvény",
            "docId": doc, "url": njt.law_url(doc), "date": str(date)[:10]}


# --- one bill -------------------------------------------------------------------

def _fingerprint(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()[:20]


def _version_info(v: dict | None) -> dict | None:
    if not v:
        return None
    return {"version": v["version"], "from": (v.get("comingIntoForce") or "")[:10] or None,
            "to": (v.get("expiresOn") or "")[:10] or None}


def _unresolved_entry(ins: dict, quotes: list[str], why: str) -> dict:
    text = novella.restore(ins["src"], quotes, _QUOTE_LIMIT)
    if len(text) > _TEXT_LIMIT:
        text = text[:_TEXT_LIMIT].rsplit(" ", 1)[0] + " …"
    return {"provision": ins["at"], "docId": ins.get("doc"), "reason": why, "text": text}


def _law_entry(doc: str, title: str | None, abbr: str | None, method: str,
               base: dict | None, target: dict | None, changes: list[dict]) -> dict:
    ver = (target or base or {}).get("version")
    return {"docId": doc, "title": title, "abbr": abbr, "url": njt.law_url(doc),
            "method": method, "base": base, "target": target,
            "changes": [{**c, "url": njt.law_url(doc, ver, c["jh"])} for c in changes]}


def _plan(store: njt.NjtStore, parsed: dict, act: dict | None) -> list[dict]:
    """Which njt states each amended law is read from — cheap (version lists
    only), so a bill's fingerprint is known before anything heavy is loaded.

    An adopted bill compares the state in force on its promulgation day with
    the newest one; a pending bill is applied to the newest one."""
    docs: list[str] = []
    for ins in parsed["instructions"]:
        if ins.get("doc") and ins["doc"] not in docs and ins["op"] != "none":
            docs.append(ins["doc"])
    plan = []
    for doc in docs:
        versions = store.versions(doc)
        latest = njt.latest_version(versions)
        if not latest:
            plan.append({"doc": doc, "missing": True})
            continue
        before = (njt.version_on(versions, act["date"]) or latest) if act else None
        plan.append({"doc": doc, "latest": latest, "before": before})
    return plan


def _law_changes(store: njt.NjtStore, parsed: dict, act: dict | None,
                 plan: list[dict]) -> tuple[list, list]:
    """The law half of a bill record: per-law changes and the instructions that
    could not be applied (in the bill's own words)."""
    instrs = parsed["instructions"]
    quotes = parsed["quotes"]
    doc_abbr = {doc: ab for ab, doc in parsed["abbr"].items()}
    laws: list[dict] = []
    unresolved = [_unresolved_entry(ins, quotes, ins["why"]) for ins in instrs
                  if ins["op"] == "unparsed" and ins.get("why") in _SHOWN_UNPARSED]
    for p in plan:
        doc = p["doc"]
        if p.get("missing"):
            unresolved += [_unresolved_entry(ins, quotes, "a törvény nem érhető el az njt-n")
                           for ins in instrs if ins.get("doc") == doc
                           and ins["op"] not in ("none", "unparsed")]
            continue
        latest = p["latest"]
        whole = [ins for ins in instrs if ins.get("doc") == doc and ins["op"] == "repeal_law"]
        if whole:
            # The whole law is repealed: one change, named by the law's title
            # rather than every one of its provisions shown struck through.
            base_v = p["before"] if act else latest
            title = store.law(doc, base_v["version"])["title"]
            laws.append(_law_entry(doc, title, doc_abbr.get(doc), "computed",
                                   _version_info(base_v), None,
                                   [{"jh": "", "ref": "a törvény egésze", "before": title or doc,
                                     "after": "", "provisions": [i["at"] for i in whole],
                                     "ops": ["repeal_law"]}]))
            continue
        if act:
            before = store.law(doc, p["before"]["version"])
            after = store.law(doc, latest["version"])
            found = novella.attributed_changes(before["units"], after["units"], act["docId"])
            if found:
                laws.append(_law_entry(doc, after["title"] or before["title"], doc_abbr.get(doc),
                                       "njt", _version_info(p["before"]), _version_info(latest),
                                       found))
                continue
            # njt has not caught up with the act yet (it has three working days):
            # apply the final text to the state it amended, until it does.
            base_v, base = p["before"], before
        else:
            base_v, base = latest, store.law(doc, latest["version"])
        pred, log = novella.apply(base["units"], instrs, doc)
        changes = novella.changes(base["units"], pred, log)
        unresolved += [_unresolved_entry(res["ins"], quotes, res["why"] or "nem alkalmazható")
                       for res in log
                       if not res["ok"] and res["ins"]["op"] not in ("none", "unparsed")]
        if changes:
            laws.append(_law_entry(doc, base["title"], doc_abbr.get(doc), "computed",
                                   _version_info(base_v), None, changes))
    # Where njt itself shows what the act changed in a law, what our parser
    # could not read of the same law is beside the point.
    official = {law["docId"] for law in laws if law["method"] == "njt"}
    seen: set[tuple] = set()
    kept = []
    for u in unresolved:
        key = (u["provision"], u["reason"], u["text"])
        if u["docId"] in official or key in seen:
            continue          # one item with two failed replacements is one entry
        seen.add(key)
        kept.append(u)
    return laws, kept


def _provisions(lines: list[dict]) -> list[tuple[str, str]]:
    """A bill text's own sections, quotes restored: ``[("1. §", text), …]``."""
    tmpl, quotes = novella.template(novella.tokenize(novella.body(lines)))
    out = []
    for key, text in novella.own_sections(tmpl):
        out.append((key or "cím", novella.norm(novella.restore(text, quotes))))
    return out


def _evolution(main: dict, egys: dict, rec: dict, egys_motion: dict) -> dict | None:
    a = _provisions(main["lines"])
    b = _provisions(egys["lines"])
    if len(a) < 2 or len(b) < 2:
        return None
    da, db = dict(a), dict(b)
    keys = list(dict.fromkeys([k for k, _ in a] + [k for k, _ in b]))
    changed = [{"key": k, "before": da.get(k, ""), "after": db.get(k, "")}
               for k in keys if da.get(k, "") != db.get(k, "")]
    return {"from": {"number": rec.get("billNumber"), "url": rec.get("textUrl")},
            "to": {"number": egys_motion.get("billNumber"), "url": egys_motion.get("textUrl"),
                   "type": egys_motion.get("type")},
            "provisions": changed, "unchanged": len(keys) - len(changed)}


def build_record(rec: dict, docs: DocCache, store: njt.NjtStore, previous: dict | None,
                 *, force: bool = False) -> dict:
    """Everything this stage says about one bill. Raises :class:`njt.BudgetExhausted`
    when the pass runs out of requests before the record is complete."""
    act = _act(rec)
    egys_m = _consolidated(rec)
    amends = _amendments(rec)
    doc_key = [rec.get("textUrl"), egys_m and egys_m.get("textUrl"),
               [m["textUrl"] for m in amends], rec.get("status"), act]
    versions = [novella.PARSER_VERSION, pdftext.EXTRACT_VERSION, njt.PARSE_VERSION]
    # An adopted bill read off njt is final: same documents, same code → reuse.
    if not force and previous and previous.get("final") \
            and previous.get("docKey") == _fingerprint(doc_key, versions):
        return previous

    main = docs.get(rec["textUrl"], "lines") if rec.get("textUrl") else None
    egys = docs.get(egys_m["textUrl"], "lines") if egys_m else None

    src_doc, src_meta = None, None
    for d, meta in ((egys, egys_m and {"number": egys_m.get("billNumber"),
                                        "url": egys_m.get("textUrl"), "kind": "consolidated"}),
                    (main, {"number": rec.get("billNumber"), "url": rec.get("textUrl"),
                            "kind": "bill"})):
        if d and not d.get("error") and not d.get("scan") and novella.body(d["lines"]):
            src_doc, src_meta = d, meta
            break
    # "No diff because the text is a scan" — not "some document was a scan":
    # a scanned original with a born-digital egységes javaslat still has one.
    scan = src_doc is None and any(d and d.get("scan") for d in (main, egys))

    parsed = novella.parse_bill(src_doc["lines"]) if src_doc else None
    plan = _plan(store, parsed, act) if parsed else []
    used = [[p["doc"], p.get("before") and p["before"]["version"],
             p.get("latest") and p["latest"]["version"]] for p in plan]
    fingerprint = _fingerprint(doc_key, versions, used)
    if not force and previous and previous.get("fingerprint") == fingerprint:
        return previous
    laws, unresolved = _law_changes(store, parsed, act, plan) if parsed else ([], [])

    evolution = None
    if main and egys and not main.get("scan") and not egys.get("scan") \
            and not main.get("error") and not egys.get("error"):
        evolution = _evolution(main, egys, rec, egys_m)

    amendments = []
    for m in amends:
        got = docs.get(m["textUrl"], "marked")
        entry = {"number": m.get("billNumber"), "type": m.get("type"), "url": m["textUrl"],
                 "scan": bool(got.get("scan")), "error": got.get("error"), "points": []}
        if not got.get("scan") and not got.get("error"):
            res = motion_parser.parse(got["lines"])
            entry["points"] = res["points"]
            entry["marked"] = res["marked"]
        amendments.append(entry)

    final = bool(act) and bool(laws) and all(l["method"] == "njt" for l in laws)
    stats = {}
    if parsed:
        ops = [i["op"] for i in parsed["instructions"]]
        stats = {"instructions": sum(op not in ("none",) for op in ops),
                 "unparsed": ops.count("unparsed"),
                 "changes": sum(len(l["changes"]) for l in laws),
                 "unresolved": len(unresolved)}
    return {
        "billId": rec["billId"], "billNumber": rec.get("billNumber"),
        "computedAt": _now_iso(),
        "docKey": _fingerprint(doc_key, versions),
        "fingerprint": fingerprint,
        "final": final,
        "source": src_meta, "scan": scan,
        "act": act, "laws": laws, "unresolved": unresolved,
        "evolution": evolution, "amendments": amendments, "stats": stats,
    }


# --- the pass ----------------------------------------------------------------------

def candidates(registry: dict) -> list[dict]:
    """The registry's törvényjavaslatok that have a text to read, in the order a
    budgeted pass should take them: bills still in Parliament first (nothing
    else answers what they would change), newest first; then the adopted ones,
    most recently promulgated first."""
    bills = [r for r in registry.get("data") or []
             if r.get("billId") and r.get("mainType") == "T" and r.get("textUrl")]
    pending = sorted((r for r in bills if r.get("status") != "kihirdetve"),
                     key=lambda r: r.get("submittedDate") or "", reverse=True)
    adopted = sorted((r for r in bills if r.get("status") == "kihirdetve"),
                     key=lambda r: ((r.get("detail") or {}).get("header") or {})
                     .get("promulgationDate") or "", reverse=True)
    return pending + adopted


def fetch_law_changes(http: HttpClient, paths: Paths, cycle: int, registry: dict, *,
                      limit: int | None = None, force: bool = False,
                      only: set[str] | None = None) -> dict:
    """Compute (or reuse) every bill's record; returns the processed registry.

    ``limit`` caps the requests of this pass (``None`` = no cap); ``only``
    restricts the pass to some bill numbers (a hand-run check of one bill)."""
    previous = load_previous(paths, cycle) or {}
    prev_by_id = {r["billId"]: r for r in previous.get("data") or []}
    if not pdftext.available():
        logger.warning("PyMuPDF is not installed — law changes cannot be computed; "
                       "keeping %d previous record(s)", len(prev_by_id))
        return {"meta": {**(previous.get("meta") or {}), "cycle": cycle,
                         "pymupdf": False}, "data": list(prev_by_id.values())}

    budget = njt.Budget(limit)
    docs = DocCache(http, paths.lawdiff_docs_dir(cycle), budget, force=force)
    store = njt.NjtStore(http, paths.njt_dir(), budget=budget)
    records: dict[str, dict] = {}
    computed = reused = errors = pending = 0
    for rec in candidates(registry):
        if only and rec.get("billNumber") not in only:
            if rec["billId"] in prev_by_id:
                records[rec["billId"]] = prev_by_id[rec["billId"]]
            continue
        prev = prev_by_id.get(rec["billId"])
        if budget.exhausted:
            if prev:
                records[rec["billId"]] = prev
            pending += 1
            continue
        try:
            out = build_record(rec, docs, store, prev, force=force)
        except (njt.BudgetExhausted, njt.Unavailable) as e:
            if isinstance(e, njt.Unavailable):
                logger.warning("Law changes for %s postponed: %s", rec.get("billNumber"), e)
            if prev:
                records[rec["billId"]] = prev
            pending += 1
            continue
        except Exception:                                    # noqa: BLE001 (SCR-5)
            logger.exception("Law changes for %s failed", rec.get("billNumber"))
            if prev:
                records[rec["billId"]] = prev
            errors += 1
            continue
        if out is prev or (prev and prev.get("fingerprint") == out.get("fingerprint")):
            records[rec["billId"]] = prev
            reused += 1
        else:
            records[rec["billId"]] = out
            computed += 1
    data = sorted(records.values(), key=lambda r: r.get("billNumber") or "")
    counts = {
        "bills": len(data),
        "withChanges": sum(1 for r in data if r.get("laws")),
        "laws": sum(len(r.get("laws") or []) for r in data),
        "changes": sum(len(l["changes"]) for r in data for l in r.get("laws") or []),
        "unresolved": sum(len(r.get("unresolved") or []) for r in data),
        "amendments": sum(len(r.get("amendments") or []) for r in data),
        "points": sum(len(a.get("points") or []) for r in data for a in r.get("amendments") or []),
        "computed": computed, "reused": reused, "errors": errors, "pending": pending,
        "requests": budget.spent, "documentsFetched": docs.fetched,
    }
    logger.info("Law changes cycle %s: %d bill(s) (%d computed, %d reused, %d pending, "
                "%d error(s)) — %d law(s), %d change(s), %d amendment point(s); %d request(s)",
                cycle, counts["bills"], computed, reused, pending, errors, counts["laws"],
                counts["changes"], counts["points"], budget.spent)
    return {"meta": {"cycle": cycle, "source": SOURCE, "scrapedAt": _now_iso(),
                     "pymupdf": True, "limit": limit, "counts": counts},
            "data": data}


def content_fingerprint(registry: dict) -> str:
    """What the loader would see — the records minus their timestamps — so a
    pass that changed nothing does not rewrite the file."""
    rows = [{k: v for k, v in r.items() if k != "computedAt"}
            for r in registry.get("data") or []]
    return _fingerprint(rows)


def load_previous(paths: Paths, cycle: int) -> dict | None:
    f = paths.law_changes_file(cycle)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Could not read %s (%s)", f, e)
        return None


def save_law_changes(paths: Paths, cycle: int, registry: dict) -> None:
    out = paths.law_changes_file(cycle)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)
    logger.info("Wrote %s (%d bills)", out, len(registry.get("data") or []))
