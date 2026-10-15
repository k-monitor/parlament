"""How well the applier reads bills, measured against njt itself (BILL-14).

For a bill that became law njt shows what it changed (:func:`.novella.attributed_changes`),
so the applier can be checked on it: parse the bill's text (the egységes
javaslat when there is one, as the stage does), apply it to the njt state in
force on the promulgation day, and compare every changed provision with njt's
newest state. Runs from the caches only — no request — so it is cheap to run
before and after a parser change::

    python -m parlamonitor.lawdiff.evaluate ./data --cycle 43 [--bill T/341] [--json out.json]

A provision is counted once, at the outermost unit either side changed (njt
may attribute a § where the applier changed its paragraph). Two things make a
mismatch not the applier's fault, and are counted apart:

* **another act**: njt's state also carries what other acts of the same year
  or later did to the provision (two acts passed the same day both show), or
  njt shows a provision this act did not touch changed anyway (a change an
  earlier act scheduled for later); a *false change* is one njt shows unchanged;
* **the act's number**: the bill names itself "2026. évi … törvény" — it has
  no number yet.

Where the egységes javaslat differs from the text finally adopted (a closing
amendment), the bill's own text is simply not the act's: such bills score low
for a reason no parser can fix (T/174 in cycle 43).
"""

from __future__ import annotations

import argparse
import collections
import json
import lzma
import re
from pathlib import Path

from ..config import Paths
from ..documents.scrape import document_id
from . import motions as motion_parser
from . import njt, novella, scrape

_PLACEHOLDER = re.compile(r"(\d{4}\. évi) (?:[IVXLCDM]+|[.…]+)\.? ?(törvén)")   # törvény, törvénnyel


class _CacheStore(njt.NjtStore):
    """The njt cache, never the network: a version list however old, a state
    only if on disk."""

    def versions(self, doc, *, max_age=None):
        f = self._dir(doc) / "versions.json"
        return json.loads(f.read_text(encoding="utf-8"))["data"] if f.exists() else []

    def law(self, doc, version, *, offline=True):
        return super().law(doc, version, offline=True)


def _canon(text: str) -> str:
    text = re.sub(r"[„”\"“]", '"', (text or "").replace("\xa0", " "))
    return re.sub(r"\s+", " ", re.sub(r"[–—]", "-", text)).strip()


def _year_number(doc: str) -> tuple[int, int]:
    y, n = doc.split("-")[:2]
    return int(y), int(n)


def compare(before: list[dict], after: list[dict], pred: list[dict],
            computed: list[dict], truth: list[dict], act_doc: str) -> list[dict]:
    """One row per changed provision: ``{jh, kind, before, truth, computed}``,
    kind ``exact`` / ``exact_placeholder`` / ``exact_ws`` / ``differs`` /
    ``missed`` / ``spurious``, prefixed ``other_act_`` where another act also
    changed it (see the module docstring)."""
    t_by = {c["jh"]: c for c in truth}
    c_by = {c["jh"]: dict(c) for c in computed}
    # njt now and then gives a unit another id in its newer state ("cb)" filed
    # as APBB, then APCB); the applier found it by its label in the older one —
    # file such a change under the newer id, so the two are compared
    for jh in list(t_by):
        if jh in c_by:
            continue
        old = novella.resolve(before, jh)
        if old and old != jh and old in c_by and old not in t_by:
            c_by[jh] = c_by.pop(old)
            pred = [{**u, "jh": jh} if u["jh"] == old else u for u in pred]
    # a new unnumbered subtitle gets a made-up id ("TA~30B"); njt gives it its own
    for jh in [j for j in c_by if j.startswith("TA~")]:
        title = next((u["text"] for u in pred if u["jh"] == jh), "")
        real = next((u["jh"] for u in after if u["cls"] == "tagoloCim" and u["text"] == title
                     and u["jh"] not in {x["jh"] for x in before}), None)
        if real:
            c_by[real] = c_by.pop(jh)
            pred = [{**u, "jh": real} if u["jh"] == jh else u for u in pred]
    roots = set(t_by) | set(c_by)

    def top(jh: str) -> str:
        parts = jh.split("@")
        return next(("@".join(parts[:k]) for k in range(1, len(parts))
                     if "@".join(parts[:k]) in roots), jh)

    clusters: dict[str, list[str]] = collections.defaultdict(list)
    for jh in sorted(roots):
        clusters[top(jh)].append(jh)
    act = _year_number(act_doc)

    def other_act(jh: str) -> bool:
        for u in njt.subtree(after, jh):
            for fn in u.get("fn") or []:
                for link in fn.get("links") or []:
                    yn = _year_number(link["doc"])
                    if yn[0] >= act[0] and yn != act:
                        return True
        return False

    def emptied_by_another(jh: str) -> bool:
        """njt empties parts of the provision with nothing naming this act on
        them (a change some other act scheduled for the newest state)."""
        now = {u["jh"]: u for u in njt.subtree(after, jh)}
        for u in njt.subtree(before, jh):
            if not u["text"]:
                continue
            later = now.get(u["jh"])
            names_act = later and any(link["doc"] == act_doc for fn in later.get("fn") or []
                                      for link in fn.get("links") or [])
            if (later is None or not later["text"]) and not names_act:
                return True
        return False

    rows = []
    for jh, members in clusters.items():
        t = _canon(njt.flatten(after, jh))
        c = _canon(njt.flatten(pred, jh))
        if t == c:
            kind = "exact"
        elif _PLACEHOLDER.sub(r"\1 # \2", t) == _PLACEHOLDER.sub(r"\1 # \2", c):
            kind = "exact_placeholder"
        elif t.replace(" ", "") == c.replace(" ", ""):
            kind = "exact_ws"
        else:
            in_t = any(m in t_by for m in members)
            in_c = any(m in c_by for m in members)
            kind = "differs" if in_t and in_c else ("missed" if in_t else "spurious")
            b = _canon(njt.flatten(before, jh))
            # another act's work: it footnotes the provision; or njt shows a
            # provision this act did not touch changed all the same (a change
            # an earlier act scheduled — njt keeps future states up to date) —
            # a false change is one where njt shows the provision unchanged
            if other_act(jh) or (kind == "spurious" and t != b) \
                    or (kind in ("differs", "missed") and emptied_by_another(jh)):
                kind = "other_act_" + kind
        rows.append({"jh": jh, "members": members, "kind": kind,
                     "before": _canon(njt.flatten(before, jh)), "truth": t, "computed": c,
                     "truth_provisions": [p for m in members for p in t_by.get(m, {}).get("provisions", [])],
                     "computed_provisions": [p for m in members
                                             for p in c_by.get(m, {}).get("provisions", [])]})
    return rows


# What became of each amending instruction, and of each provision the act changed.
# "known": the site says it could not apply it (the unresolved list); "silent":
# it shows something, and njt shows something else.
_INSTRUCTION_VERDICTS = ("correct", "known_unparsed", "known_not_applied",
                         "silent_wrong", "unverifiable_other_act", "unverifiable")
_PROVISION_VERDICTS = ("correct", "known_not_applied", "known_partly_applied",
                       "silent_wrong_text", "silent_missed", "silent_false_change",
                       "other_act")


def _at_key(label: str) -> str:
    """A provision of the bill/act as a comparable key: njt's "14. § k) pont" and
    the bill's "14. § k)" are the same."""
    return re.sub(r"\s+|bekezdés|alpont|pont", "", label or "")


def _instructions_at(instrs: list[dict], label: str) -> list[dict]:
    key = _at_key(label)
    exact = [i for i in instrs if _at_key(i["at"]) == key]
    if exact:
        return exact
    # the act's provision is finer than the bill's own paragraph, or coarser
    return [i for i in instrs if key.startswith(_at_key(i["at"])) or _at_key(i["at"]).startswith(key)]


def _verdict_of_row(row: dict, failed_ids: set[int], instrs: list[dict]) -> str:
    kind = row["kind"]
    if kind.startswith("exact"):
        return "correct"
    if kind.startswith("other_act_"):
        return "other_act"
    if kind == "spurious":
        return "silent_false_change"
    linked = [i for p in row["truth_provisions"] for i in _instructions_at(instrs, p)]
    flagged = any(id(i) in failed_ids for i in linked)
    if kind == "differs":
        return "known_partly_applied" if flagged else "silent_wrong_text"
    return "known_not_applied" if flagged else "silent_missed"      # missed


def _input_of(rec: dict, used: str) -> str:
    """Whether the text read is the one adopted: ``final`` (the egységes javaslat
    with nothing amended after it, or a bill the House did not amend) or
    ``amended_after`` (the House amended what we read: no egységes javaslat to
    read, a scanned one, or a closing amendment after it) — where a mismatch
    with njt is the bill having changed, not the parser."""
    egys = scrape._consolidated(rec)
    later = [m for m in (rec.get("detail") or {}).get("motions") or []
             if motion_parser.is_amendment(m.get("type"))
             and (used == "bill" or (m.get("billNumberSort") or 0) > (egys.get("billNumberSort") or 0))
             and re.search(r"összegző|zárószavazás|bizottsági", m.get("type") or "", re.I)]
    return "amended_after" if later else "final"


def evaluate(paths: Paths, cycle: int, only: set[str] | None = None) -> dict:
    registry = json.loads(paths.processed.joinpath(f"bills-{cycle}.json").read_text(encoding="utf-8"))
    store = _CacheStore(None, paths.njt_dir(), budget=njt.Budget(None))
    docs_dir = paths.lawdiff_docs_dir(cycle)
    counts: collections.Counter = collections.Counter()
    ins_counts: collections.Counter = collections.Counter()
    prov_counts: collections.Counter = collections.Counter()
    reasons: collections.Counter = collections.Counter()
    rows: list[dict] = []
    verdicts: list[dict] = []
    bills_in: dict[str, list] = collections.defaultdict(list)
    group = "final"

    def judge(rec, ins, verdict, why=None):
        ins_counts[(group, verdict)] += 1
        if why:
            reasons[f"{verdict}: {why.split(':')[0]}"] += 1
        verdicts.append({"bill": rec.get("billNumber"), "input": group, "at": ins["at"],
                         "op": ins["op"], "doc": ins.get("doc"), "verdict": verdict, "why": why})

    def judge_repeal_law(rec, ins, doc):
        # njt ends a repealed law's time states instead of footnoting a unit
        ended = bool((njt.latest_version(store.versions(doc)) or {}).get("expiresOn"))
        judge(rec, ins, "correct" if ended else "silent_wrong",
              None if ended else "a törvény hatályban maradt")

    for rec in scrape.candidates(registry):
        act = scrape._act(rec)
        if not act or (only and rec.get("billNumber") not in only):
            continue
        parsed = used = None
        egys = scrape._consolidated(rec)
        for kind, url in ([("egys", egys["textUrl"])] if egys else []) + [("bill", rec["textUrl"])]:
            f = docs_dir / f"{document_id(url)}.lines.json.xz"
            if not f.exists():
                continue
            d = json.loads(lzma.decompress(f.read_bytes()))
            if not d.get("scan") and novella.body(d["lines"]):
                parsed, used = novella.parse_bill(d["lines"]), kind
                break
        if parsed is None:
            counts["bills_without_text"] += 1
            continue
        counts["bills"] += 1
        group = _input_of(rec, used)
        bills_in[group].append(rec.get("billNumber"))
        instrs = [i for i in parsed["instructions"] if i["op"] != "none"]
        for ins in instrs:
            if not ins.get("doc"):      # names no law ("nincs törvény", a decree)
                judge(rec, ins, "known_unparsed", ins.get("why"))
        for p in scrape._plan(store, parsed, act):
            mine = [i for i in instrs if i.get("doc") == p["doc"]]
            before = store.law(p["doc"], p["before"]["version"]) if not p.get("missing") else None
            after = store.law(p["doc"], p["latest"]["version"]) if not p.get("missing") else None
            if not before or not after:
                counts["laws_not_cached"] += 1
                for ins in mine:
                    judge(rec, ins, "unverifiable", "a törvény nincs a gyorsítótárban")
                continue
            pred, log = novella.apply(before["units"], parsed["instructions"], p["doc"])
            failed = {id(r["ins"]) for r in log if not r["ok"]}
            truth = novella.attributed_changes(before["units"], after["units"], act["docId"])
            if not truth:
                counts["laws_njt_attributes_nothing"] += 1
                for r in log:
                    ins = r["ins"]
                    if ins["op"] == "repeal_law":
                        judge_repeal_law(rec, ins, p["doc"])
                    elif ins["op"] == "unparsed":
                        judge(rec, ins, "known_unparsed", ins.get("why"))
                    elif not r["ok"]:
                        judge(rec, ins, "known_not_applied", r["why"])
                    else:
                        judge(rec, ins, "unverifiable", "az njt nem jelöli ezt a törvényt")
                continue
            computed = novella.changes(before["units"], pred, log)
            counts["laws"] += 1
            law_rows = compare(before["units"], after["units"], pred, computed, truth,
                               act["docId"])
            for row in law_rows:
                counts[row["kind"]] += 1
                row["verdict"] = _verdict_of_row(row, failed, mine)
                prov_counts[(group, row["verdict"])] += 1
                rows.append({"bill": rec.get("billNumber"), "input": group, "doc": p["doc"], **row})

            def row_of(jh):
                parts = jh.split("@")
                anc = {"@".join(parts[:k]) for k in range(1, len(parts) + 1)}
                return next((r for r in law_rows if r["jh"] in anc), None)

            for r in log:
                ins = r["ins"]
                if ins["op"] == "repeal_law":
                    judge_repeal_law(rec, ins, p["doc"])
                elif ins["op"] == "unparsed":
                    judge(rec, ins, "known_unparsed", ins.get("why"))
                elif not r["ok"]:
                    judge(rec, ins, "known_not_applied", r["why"])
                else:
                    hit = [row_of(jh) for jh in r["touched"]]
                    hit = [h for h in hit if h]
                    if any(h["kind"].startswith("other_act_") for h in hit):
                        judge(rec, ins, "unverifiable_other_act")
                    elif all(h["kind"].startswith("exact") for h in hit):
                        judge(rec, ins, "correct")
                    else:
                        judge(rec, ins, "silent_wrong",
                              next(h["kind"] for h in hit if not h["kind"].startswith("exact")))
    total = sum(n for k, n in counts.items() if k.startswith(("exact", "differs", "missed",
                                                               "spurious", "other_act_")))
    exact = counts["exact"] + counts["exact_placeholder"] + counts["exact_ws"]
    own = total - sum(n for k, n in counts.items() if k.startswith("other_act_"))
    return {"counts": dict(counts), "provisions": total,
            "exact": round(exact / total, 4) if total else None,
            "exact_excluding_other_acts": round(exact / own, 4) if own else None,
            "bills_by_input": {g: sorted(b) for g, b in bills_in.items()},
            "instructions": {g: {k: ins_counts[(g, k)] for k in _INSTRUCTION_VERDICTS}
                             for g in ("final", "amended_after")},
            "instruction_reasons": dict(reasons.most_common()),
            "changed_provisions": {g: {k: prov_counts[(g, k)] for k in _PROVISION_VERDICTS}
                                   for g in ("final", "amended_after")},
            "rows": rows, "verdicts": verdicts}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("data_dir")
    ap.add_argument("--cycle", type=int, required=True)
    ap.add_argument("--bill", action="append", help="only these bills (repeatable)")
    ap.add_argument("--json", help="write every compared provision here")
    a = ap.parse_args(argv)
    out = evaluate(Paths(a.data_dir), a.cycle, set(a.bill) if a.bill else None)
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k not in ("rows", "verdicts")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
