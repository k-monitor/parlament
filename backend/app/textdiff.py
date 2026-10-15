"""Word-level diff of two provision texts, for the law and bill diffs (BILL-14/15).

The scraper stores each changed provision whole on both sides; the diff is made
here, when served, so a better diff never needs a reload. Two choices make it
read like an editor's markup rather than a machine's:

* **Words, not characters.** A legal text changes by words and phrases
  ("2026" → "2027", a clause inserted); a character diff of a reworded
  sentence shreds it into fragments no reader can follow.
* **No confetti.** When a provision is rewritten wholesale, a plain LCS still
  finds the articles and conjunctions the two versions share ("a", "az", "és")
  and interleaves dozens of one-word islands of "unchanged" text. Short equal
  runs sandwiched between changes are folded into the change on both sides
  (diff-match-patch's semantic cleanup, simplified).
"""

from __future__ import annotations

import difflib
import re

_TOKEN = re.compile(r"\s+|[^\s]+")
# An unchanged run this short (in characters, whitespace included) between two
# changes reads as noise rather than as text the two versions share.
_ISLAND = 12


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text or "")


def _merge(ops: list[list[str]]) -> list[list[str]]:
    out: list[list[str]] = []
    for op, text in ops:
        if not text:
            continue
        if out and out[-1][0] == op:
            out[-1][1] += text
        else:
            out.append([op, text])
    return out


def _regroup(ops: list[list[str]]) -> list[list[str]]:
    """Within each run of changes, put all deletions before all insertions."""
    out: list[list[str]] = []
    dels, ins = [], []
    for op, text in ops + [["eq", ""]]:
        if op == "del":
            dels.append(text)
        elif op == "ins":
            ins.append(text)
        else:
            if dels:
                out.append(["del", "".join(dels)])
            if ins:
                out.append(["ins", "".join(ins)])
            dels, ins = [], []
            if text:
                out.append(["eq", text])
    return _merge(out)


def word_diff(before: str | None, after: str | None) -> list[list[str]]:
    """``[[op, text], …]`` with op ``eq`` / ``del`` / ``ins``; concatenating the
    ``eq``+``del`` texts gives ``before``, the ``eq``+``ins`` texts ``after``."""
    a, b = _tokens(before or ""), _tokens(after or "")
    if not a and not b:
        return []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ops: list[list[str]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            ops.append(["eq", "".join(a[i1:i2])])
            continue
        if i2 > i1:
            ops.append(["del", "".join(a[i1:i2])])
        if j2 > j1:
            ops.append(["ins", "".join(b[j1:j2])])
    ops = _regroup(_merge(ops))
    # Fold short unchanged islands between two changes into both sides.
    changed = True
    while changed:
        changed = False
        for k in range(1, len(ops) - 1):
            op, text = ops[k]
            if op != "eq" or len(text) > _ISLAND:
                continue
            if ops[k - 1][0] != "eq" and ops[k + 1][0] != "eq":
                ops[k:k + 1] = [["del", text], ["ins", text]]
                ops = _regroup(_merge(ops))
                changed = True
                break
    return ops


def changed_share(ops: list[list[str]]) -> float:
    """How much of the longer side is changed — 1.0 for a new or deleted text."""
    eq = sum(len(t) for op, t in ops if op == "eq")
    before = eq + sum(len(t) for op, t in ops if op == "del")
    after = eq + sum(len(t) for op, t in ops if op == "ins")
    longest = max(before, after)
    return 0.0 if not longest else 1.0 - eq / longest
