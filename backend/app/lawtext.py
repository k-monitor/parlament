"""A law whole, with a bill's changes in place — the full-law view of a law diff
(BILL-14).

The diff list shows each changed provision on its own; this puts the same
changes back inside the text they amend, so a reader sees what a new paragraph
sits between and what the rest of the § says. Its inputs are the njt time state
the changes were read against (``law_text``, units ``[jh, cls, label, text]`` in
document order) and the changes themselves (``law_change`` rows).

Each change replaces its unit and everything under it — the subtree njt keys
under the unit's id (``SZ26@BE4`` → ``SZ26@BE4@POA`` …) — with the same subtree
as it would read, unit by unit, so a replaced § keeps its paragraphs and points
apart rather than reading as one run-on line. A unit new to the law goes after
the unit of the current text the scraper found it following (``anchor``).
"""

from __future__ import annotations

import difflib

from .textdiff import word_diff

# A unit new to the law whose place is not known (a change loaded before the
# scraper recorded anchors): shown after the last unit, not dropped.
_AT_END = object()


def _unit(u: list) -> dict:
    return {"jh": u[0], "cls": u[1], "label": u[2], "text": u[3]}


def _align(before: list[list], after: list[list]) -> list[dict]:
    """A changed subtree unit by unit: matched by unit id, each unit unchanged
    (``eq``), reworded (``mod``), new (``ins``) or gone (``del``), with its text
    as diff segments either way so every unit renders the same."""
    sm = difflib.SequenceMatcher(None, [u[0] for u in before], [u[0] for u in after],
                                 autojunk=False)
    out: list[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for b, a in zip(before[i1:i2], after[j1:j2]):
                same = b[3] == a[3]
                out.append({**_unit(a), "op": "eq" if same else "mod",
                            "diff": [["eq", a[3]]] if same else word_diff(b[3], a[3])})
            continue
        for b in before[i1:i2]:
            out.append({**_unit(b), "op": "del", "diff": [["del", b[3]]] if b[3] else []})
        for a in after[j1:j2]:
            out.append({**_unit(a), "op": "ins", "diff": [["ins", a[3]]] if a[3] else []})
    return out


def _block(i: int, c: dict, before: list[list]) -> dict:
    after = c.get("after_units")
    if after is None:
        # Loaded before the scraper kept the units: the change's flattened
        # text, as the diff list shows it.
        units = [{"jh": c["jh"], "cls": "flat", "label": "", "op": "mod",
                  "diff": word_diff(c["before_text"], c["after_text"])}]
    else:
        units = _align(before, after)
    return {"change": i, "units": units}


def merge(units: list[list], changes: list[dict]) -> list[dict]:
    """The law as blocks in reading order: ``{"unit": {...}}`` for text the
    bill leaves alone, ``{"change": i, "units": [...]}`` for its ``i``-th change
    (``i`` counts the ``changes`` as given).

    ``changes`` carry ``jh``, ``before_text``, ``after_text``, ``after_units``
    (parsed, or None) and ``anchor``."""
    whole = [i for i, c in enumerate(changes) if not c["jh"]]
    if whole:
        # The whole law is repealed: every unit of it goes.
        return [{"change": whole[0], "units": _align(units, [])}]

    base = {u[0] for u in units if u[0]}
    roots = {c["jh"]: i for i, c in enumerate(changes) if c["jh"] in base}
    # Each changed unit's subtree, as njt.subtree reads it (and so as the
    # change's own text was flattened): the unit, everything keyed under it,
    # and any other unit njt gave the same id ("9.", "9/A." and "9/B." can
    # share one), wherever it stands. The block stands at the first of them.
    members: dict[str, list[int]] = {}
    owner: dict[int, str] = {}
    for k, u in enumerate(units):
        root = next((r for r in _self_and_ancestors(u[0] or "") if r in roots), None)
        if root is not None:
            members.setdefault(root, []).append(k)
            owner[k] = root
    placed: dict[object, list[int]] = {}
    for i, c in enumerate(changes):
        if c["jh"] not in base:
            key = c.get("anchor")
            if key is None or (key and key not in base):
                key = _AT_END
            placed.setdefault(key, []).append(i)

    def new_after(key) -> list[dict]:
        return [_block(i, changes[i], []) for i in placed.pop(key, [])]

    out = new_after("")
    for k, u in enumerate(units):
        root = owner.get(k)
        if root is None:
            out.append({"unit": _unit(u)})
            out += new_after(u[0])
        elif members[root][0] == k:
            sub = [units[m] for m in members[root]]
            out.append(_block(roots[root], changes[roots[root]], sub))
            for m in sub:
                out += new_after(m[0])
    for key in list(placed):
        out += new_after(key)
    return out


def _self_and_ancestors(jh: str) -> list[str]:
    """``SZ7@BE4@POA`` → itself, ``SZ7@BE4``, ``SZ7``."""
    parts = jh.split("@")
    return ["@".join(parts[:n]) for n in range(len(parts), 0, -1)]
