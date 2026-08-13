#!/usr/bin/env python3
"""Targeted mining of extra training paragraphs for the rare CAP labels.

Uniform sampling has run out of road: doubling the coded set from 5k to 10k
paragraphs still leaves 69 of 183 labels under 20 examples and 31 labels at
zero, because the topic distribution is Zipfian. Reaching 20 examples of the
rarest code by random draw would mean coding on the order of 200k paragraphs —
more than the 480k pool holds at that rate, and ~22 hours of paced API calls.

So this searches for them instead. For every under-target label it runs a
keyword query over the *same* 480k paragraph pool the random sample came from,
takes a diverse top-K, and writes them out as candidates for
``gemini_classify.py`` to code. Confirmed positives are then folded back in.

    python mine_rare_labels.py index                       # build the searchable pool
    python mine_rare_labels.py plan   -c ../exports/paragraph-class.jsonl
    python mine_rare_labels.py queries -c ../exports/paragraph-class.jsonl -o queries.json
    #   ^ Gemini drafts Hungarian search terms per label — EDIT THIS FILE, it is
    #     the highest-leverage step and you know the domain better than it does
    python mine_rare_labels.py mine   -q queries.json -o ../exports/mine-candidates.jsonl
    python gemini_classify.py run -i ../exports/mine-candidates.jsonl \\
                                  -o ../exports/mine-class.jsonl
    python mine_rare_labels.py merge  -c ../exports/paragraph-class.jsonl \\
                                      -m ../exports/mine-class.jsonl \\
                                      -o ../exports/paragraph-class-augmented.jsonl

Only mine what is actually mineable
-----------------------------------
``plan`` sorts the starved labels into three groups, because only one of them
responds to retrieval:

  * **mineable** — real topics that a uniform sample simply missed (Coal,
    Fisheries, Tobacco, Age Discrimination, Housing–Elderly). Keyword search
    finds these; the corpus has thousands of sentences on each.
  * **catch-all** — the ``– Other`` and ``– R&D`` codes. These are starved by
    the *coding prompt*, which tells the model to prefer the specific minor
    topic, not by the corpus: 10 R&D codes share 31 assignments in 10k rows.
    They have no keywords by definition, so retrieval cannot help. Merge them
    into their major topic, or accept they are unlearnable.
  * **structural** — topics Hungary does not debate (Space, Commercial Use of
    Space, Dependencies & Territories, Indigenous Affairs). Nothing to find.

Keep the evaluation honest
--------------------------
Mined paragraphs are a **biased** sample: they were retrieved *because* they
contain the query terms, so a model trained and tested on them learns and is
rewarded for the keyword shortcut. ``merge`` therefore tags every mined row
with ``"mined": true``, and ``train_cap.py split`` must keep them out of
validation and test — those stay drawn from the uniform sample, so the reported
numbers remain an estimate of real-world performance rather than of retrieval
performance.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

CATCH_ALL_RE = re.compile(r"–\s*(Other|R&D)\s*$")


# --------------------------------------------------------------------------
# index: make the 480k paragraph pool searchable
# --------------------------------------------------------------------------

def cmd_index(args) -> int:
    """Rebuild the sampling pool into a standalone FTS5 database.

    Same filters and cleaning as ``export_paragraph_sample.py`` — this searches
    exactly the population the random sample was drawn from, so a mined
    paragraph is one the sample could have turned up and didn't.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from export_paragraph_sample import (RawParagraphs, clean_paragraph,
                                         paragraphs_from_db, qualifying_speeches)

    src = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    speeches = qualifying_speeches(src, args.min_duration, args.exclude_procedural)
    agenda = {r["uid"]: r["title"] for r in src.execute(
        "SELECT sp.uid, ai.title FROM speech sp "
        "LEFT JOIN agenda_item ai ON ai.id = sp.agenda_item_id")}

    out = Path(args.index)
    if out.exists() and not args.force:
        sys.exit(f"{out} exists — pass --force to rebuild")
    out.unlink(missing_ok=True)
    dst = sqlite3.connect(out)
    dst.executescript("""
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE para (
            pid INTEGER PRIMARY KEY,
            speech_uid TEXT, period INTEGER, para_ix INTEGER,
            agenda TEXT, text TEXT, norm TEXT
        );
        -- same tokenizer as the app's sentence index, so Hungarian accents and
        -- casing behave identically to search on the site
        CREATE VIRTUAL TABLE para_fts USING fts5(
            text, content='para', content_rowid='pid',
            tokenize="unicode61 remove_diacritics 2");
    """)

    raw = RawParagraphs(Path(args.data))
    rows, pid = [], 0
    for sp in speeches:
        paras = paragraphs_from_db(src, sp["uid"]) or raw.get(sp["session_id"],
                                                              sp["speech_uuid"])
        if not paras:
            continue
        for ix, para in enumerate(paras):
            cleaned = clean_paragraph(para, is_first=(ix == 0))
            if cleaned is None or len(cleaned) < args.min_chars:
                continue
            pid += 1
            rows.append((pid, sp["uid"], sp["period_number"], ix,
                         agenda.get(sp["uid"]), cleaned, normalize(cleaned)))
            if len(rows) >= 20000:
                _flush(dst, rows)
                rows = []
    _flush(dst, rows)
    dst.execute("INSERT INTO para_fts(para_fts) VALUES('rebuild')")
    dst.execute("CREATE INDEX idx_para_norm ON para(norm)")
    dst.execute("CREATE INDEX idx_para_speech ON para(speech_uid)")
    dst.execute("CREATE INDEX idx_para_agenda ON para(agenda)")
    # A second index over debate titles. Plenary business is organised by topic,
    # so "the debate was called 'a halgazdálkodásról szóló törvény'" is far
    # stronger evidence of aboutness than "the paragraph contains the word fish".
    dst.executescript("""
        CREATE TABLE agenda_ix (aid INTEGER PRIMARY KEY, title TEXT UNIQUE);
        CREATE VIRTUAL TABLE agenda_fts USING fts5(
            title, content='agenda_ix', content_rowid='aid',
            tokenize="unicode61 remove_diacritics 2");
    """)
    dst.execute("INSERT INTO agenda_ix(title) "
                "SELECT DISTINCT agenda FROM para WHERE agenda IS NOT NULL")
    dst.execute("INSERT INTO agenda_fts(agenda_fts) VALUES('rebuild')")
    dst.commit()
    n_ag = dst.execute("SELECT COUNT(*) FROM agenda_ix").fetchone()[0]
    print(f"indexed {n_ag} distinct debate titles")
    n = dst.execute("SELECT COUNT(*) FROM para").fetchone()[0]
    print(f"indexed {n} paragraphs into {out} "
          f"({out.stat().st_size / 2**20:.0f} MiB)")
    dst.close()
    src.close()
    return 0


def _flush(dst, rows):
    if rows:
        dst.executemany("INSERT INTO para VALUES (?,?,?,?,?,?,?)", rows)


def normalize(text: str) -> str:
    """Key for matching a paragraph against the already-coded set."""
    t = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", t)).strip()


# --------------------------------------------------------------------------
# plan: what is starved, and is it worth mining?
# --------------------------------------------------------------------------

def load_coded(paths: list[str]) -> list[dict]:
    rows = []
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def codebook() -> dict[str, str]:
    try:
        from gemini_classify import load_codebook
        return load_codebook(None)
    except Exception:
        return {}


STRUCTURAL_HINT = {
    "1701", "1704",   # space programmes
    "2105",           # dependencies & territories
    "2102",           # indigenous affairs
    "1007",           # maritime — landlocked
}


def classify_label(code: str, name: str, count: int, target: int) -> str:
    if count >= target:
        return "ok"
    if CATCH_ALL_RE.search(name or ""):
        return "catch-all"
    if code in STRUCTURAL_HINT:
        return "structural"
    return "mineable"


def label_status(rows, book, target):
    counts = Counter(c for r in rows for c in r.get("codes", []))
    out = []
    for code in sorted(book or counts, key=int):
        name = book.get(code, code)
        n = counts.get(code, 0)
        out.append({"code": code, "name": name, "count": n,
                    "need": max(0, target - n),
                    "group": classify_label(code, name, n, target)})
    return out


def cmd_plan(args) -> int:
    rows = load_coded(args.coded)
    book = codebook()
    st = label_status(rows, book, args.target)
    by = defaultdict(list)
    for s in st:
        by[s["group"]].append(s)

    print(f"{len(rows)} coded rows; target {args.target} examples per label\n")
    order = ("mineable", "catch-all", "structural", "ok")
    for g in order:
        items = by.get(g, [])
        if not items:
            continue
        need = sum(i["need"] for i in items)
        print(f"{g:<11} {len(items):>4} label(s)"
              + (f", {need} example(s) short" if g != "ok" else ""))
    print()
    for g in ("mineable", "catch-all", "structural"):
        items = sorted(by.get(g, []), key=lambda s: s["count"])
        if not items:
            continue
        print(f"--- {g} ---")
        for s in items[: args.show]:
            print(f"  ({s['code']:>4}) {s['name']:<50} have {s['count']:>3}  "
                  f"need {s['need']:>3}")
        if len(items) > args.show:
            print(f"  … and {len(items) - args.show} more")
        print()
    if by.get("catch-all"):
        print("catch-all labels are starved by the CODING PROMPT (it prefers the\n"
              "specific minor topic), not by the corpus — retrieval cannot fix them.\n"
              "Merge them into their major topic or drop them from the label space.")
    return 0


# --------------------------------------------------------------------------
# queries: let Gemini draft Hungarian search terms, then edit them by hand
# --------------------------------------------------------------------------

QUERY_SYSTEM = """\
You build full-text search queries for a corpus of Hungarian parliamentary \
speech (Országgyűlés plenary transcripts, 1990s-2020s).

For each CAP topic code given, produce Hungarian search terms that would appear \
in a paragraph ABOUT that topic. Rules:

  * Hungarian is agglutinative, so give word STEMS that work as prefix matches: \
"vasút" (matches vasúti, vasútnak, vasútvonal), not "vasúti".
  * 6-15 terms per code. Prefer terms that are specific to the topic — a term \
that also fits ten other topics is worse than none.
  * Include the institutional vocabulary an MP would actually use: agency \
names, statute nicknames, programme names, ministry names.
  * "phrases" holds multi-word expressions that should match as a unit.
  * "agenda" holds stems likely to appear in the TITLE of a debate devoted to \
the topic ("halgazdálkodás", "atomenergia", "dohányzás"). These matter most: \
plenary business is organised by topic, so a matching debate title is far \
stronger evidence than the same word appearing somewhere in a paragraph.
  * Return nothing for a topic that Hungarian parliamentary debate would simply \
never cover; an empty terms list is a valid, useful answer.
"""


def cmd_queries(args) -> int:
    from gemini_classify import Gemini, extract_json

    rows = load_coded(args.coded)
    book = codebook()
    st = [s for s in label_status(rows, book, args.target)
          if s["group"] == "mineable" or (args.include_catch_all
                                          and s["group"] == "catch-all")]
    if not st:
        print("nothing under target — no queries needed")
        return 0

    # existing positives give the model real in-corpus wording to imitate
    examples = defaultdict(list)
    for r in rows:
        for c in r.get("codes", [])[:1]:
            if len(examples[c]) < args.examples:
                examples[c].append(r.get("text", "")[:400])

    schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "code": {"type": "STRING"},
                "terms": {"type": "ARRAY", "items": {"type": "STRING"}},
                "phrases": {"type": "ARRAY", "items": {"type": "STRING"}},
                "agenda": {"type": "ARRAY", "items": {"type": "STRING"}},
            },
            "required": ["code", "terms"],
            "propertyOrdering": ["code", "terms", "phrases", "agenda"],
        },
    }
    key = __import__("os").environ.get(args.key_env, "").strip()
    if not key and not args.dry_run:
        sys.exit(f"{args.key_env} is not set")
    client = Gemini(api_key=key or "DRY-RUN", model=args.model,
                    temperature=0.2, sleep=args.sleep)

    out: dict[str, dict] = {}
    batches = [st[i:i + args.batch_size] for i in range(0, len(st), args.batch_size)]
    print(f"drafting terms for {len(st)} label(s) in {len(batches)} request(s)",
          file=sys.stderr)
    for bi, batch in enumerate(batches, 1):
        parts = []
        for s in batch:
            ex = examples.get(s["code"], [])
            block = f"Code {s['code']} — {s['name']}"
            if ex:
                block += "\nExample paragraphs already coded to it:\n" + \
                         "\n".join(f"  * {e}" for e in ex)
            parts.append(block)
        user = ("Produce search terms for these CAP topics.\n\n"
                + "\n\n".join(parts))
        body = client.payload(QUERY_SYSTEM, user, schema)
        if args.dry_run:
            print(json.dumps(body, ensure_ascii=False, indent=2))
            print("\ndry run — no request sent", file=sys.stderr)
            return 0
        try:
            items = extract_json(client.generate(body))
        except Exception as exc:
            print(f"  ! batch {bi} failed: {exc}", file=sys.stderr)
            continue
        for it in items if isinstance(items, list) else []:
            code = str(it.get("code", "")).strip()
            if code:
                out[code] = {"name": book.get(code, code),
                             "terms": [t.strip() for t in it.get("terms") or [] if t.strip()],
                             "phrases": [p.strip() for p in it.get("phrases") or [] if p.strip()],
                             "agenda": [a.strip() for a in it.get("agenda") or [] if a.strip()]}
        print(f"  … {bi}/{len(batches)}", file=sys.stderr)

    for s in st:                       # keep starved labels visible even if unanswered
        out.setdefault(s["code"], {"name": s["name"], "terms": [], "phrases": [],
                                   "agenda": []})
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    empty = [c for c, v in out.items() if not v["terms"] and not v["phrases"]]
    print(f"\nwrote {args.out} — {len(out)} label(s), {len(empty)} with no terms")
    print("EDIT IT before mining: your terms will beat the model's.")
    return 0


# --------------------------------------------------------------------------
# mine: retrieve candidates per starved label
# --------------------------------------------------------------------------

def fts_query(spec: dict) -> str | None:
    """Build an FTS5 MATCH expression: prefix terms and quoted phrases, OR-ed."""
    parts = []
    for t in spec.get("terms") or []:
        t = re.sub(r'["*]', "", t).strip()
        if t:
            parts.append(f'"{t}"*' if " " not in t else f'"{t}"')
    for p in spec.get("phrases") or []:
        p = re.sub(r'["*]', "", p).strip()
        if p:
            parts.append(f'"{p}"')
    return " OR ".join(parts) if parts else None


def cmd_mine(args) -> int:
    idx = sqlite3.connect(f"file:{args.index}?mode=ro", uri=True)
    idx.row_factory = sqlite3.Row
    queries = json.loads(Path(args.queries).read_text(encoding="utf-8"))

    seen_norm = set()
    for r in load_coded(args.coded):
        if r.get("text"):
            seen_norm.add(normalize(r["text"]))
    print(f"excluding {len(seen_norm)} already-coded paragraph(s)", file=sys.stderr)

    book = codebook()
    counts = Counter(c for r in load_coded(args.coded) for c in r.get("codes", []))

    picked: dict[int, dict] = {}
    per_label_stats = []
    for code, spec in sorted(queries.items(), key=lambda kv: int(kv[0])):
        need = max(0, args.target - counts.get(code, 0))
        if need <= 0:
            continue
        want = min(args.max_per_label, int(need * args.overshoot))
        expr = fts_query(spec)
        ag_expr = fts_query({"terms": spec.get("agenda") or [],
                             "phrases": spec.get("agenda_phrases") or []})
        if not expr and not ag_expr:
            per_label_stats.append((code, spec.get("name", code), 0, 0, 0, "no terms"))
            continue

        # Debate titles matching the topic. Used to RE-RANK text hits, not as a
        # source of its own: a matching title only says the debate was about the
        # topic, and most paragraphs of a long debate are procedural filler or
        # digressions ("Kérem a jegyzőt, hogy készítse elő az esküokmányokat").
        # "in a debate about X *and* using X's vocabulary" is the precise signal.
        ag_titles: set[str] = set()
        if ag_expr and not args.no_agenda:
            try:
                ag_titles = {r[0] for r in idx.execute(
                    "SELECT g.title FROM agenda_fts a JOIN agenda_ix g ON g.aid = a.rowid "
                    "WHERE agenda_fts MATCH ?", (ag_expr,))}
            except sqlite3.OperationalError as exc:
                per_label_stats.append((code, spec.get("name", code), 0, 0, 0,
                                        f"bad agenda query: {exc}"))

        hits = []
        if expr:
            try:
                hits = idx.execute(
                    "SELECT p.pid, p.speech_uid, p.period, p.agenda, p.text, p.norm, "
                    "       bm25(para_fts) AS rank "
                    "FROM para_fts JOIN para p ON p.pid = para_fts.rowid "
                    "WHERE para_fts MATCH ? ORDER BY rank LIMIT ?",
                    (expr, want * args.pool_factor)).fetchall()
            except sqlite3.OperationalError as exc:
                per_label_stats.append((code, spec.get("name", code), 0, 0, 0,
                                        f"bad query: {exc}"))
                continue
        elif ag_titles:
            # no text terms at all — fall back to the debate, weaker but non-empty
            qs = ",".join("?" * min(len(ag_titles), 200))
            hits = idx.execute(
                "SELECT pid, speech_uid, period, agenda, text, norm, 0.0 AS rank "
                f"FROM para WHERE agenda IN ({qs}) LIMIT ?",
                (*list(ag_titles)[:200], want * args.pool_factor)).fetchall()

        # agenda-confirmed hits first, each group still in BM25 order
        hits = sorted(hits, key=lambda h: (h["agenda"] not in ag_titles, h["rank"]))
        n_ag = sum(1 for h in hits if h["agenda"] in ag_titles)

        # diversity: one debate should not supply the whole class
        per_speech, per_agenda, taken = Counter(), Counter(), 0
        for h in hits:
            if taken >= want:
                break
            if h["norm"] in seen_norm or h["pid"] in picked:
                continue
            if per_speech[h["speech_uid"]] >= args.max_per_speech:
                continue
            ag = h["agenda"] or ""
            if ag and per_agenda[ag] >= args.max_per_agenda:
                continue
            per_speech[h["speech_uid"]] += 1
            per_agenda[ag] += 1
            taken += 1
            picked[h["pid"]] = {
                "text": h["text"],
                "mined_for": code,
                "speech_uid": h["speech_uid"],
                "period": h["period"],
                "agenda": h["agenda"],
                "bm25": round(float(h["rank"]), 3),
                "via": "agenda+text" if h["agenda"] in ag_titles else "text",
            }
        per_label_stats.append((code, spec.get("name", book.get(code, code)),
                                n_ag, len(hits), taken, ""))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for i, (pid, rec) in enumerate(sorted(picked.items())):
            fh.write(json.dumps({"id": f"mine-{pid}", **rec}, ensure_ascii=False) + "\n")

    print(f"\n{'code':>5}  {'label':<44} {'in-debate':>9} {'hits':>7} {'taken':>6}  note")
    for code, name, n_ag, n_hits, taken, note in per_label_stats:
        print(f"{code:>5}  {name[:44]:<44} {n_ag:>9} {n_hits:>7} {taken:>6}  {note}")
    starved = [s for s in per_label_stats if s[4] == 0]
    print(f"\nwrote {len(picked)} candidate(s) to {out}")
    if starved:
        print(f"{len(starved)} label(s) retrieved nothing — likely structural "
              f"absence or terms that need editing:")
        print("  " + ", ".join(f"({c})" for c, *_ in starved))
    print(f"\nnext: python gemini_classify.py run -i {out} -o <coded.jsonl>\n"
          f"      (only rows the coder CONFIRMS get the label — expect a hit rate "
          f"well under 100%)")
    return 0


# --------------------------------------------------------------------------
# merge: fold confirmed positives back into the training pool
# --------------------------------------------------------------------------

def cmd_merge(args) -> int:
    base = load_coded(args.coded)
    mined = load_coded(args.mined)
    book = codebook()

    # `mined_for` says which starved label a candidate was retrieved for, and is
    # what --confirmed-only tests. Older gemini_classify runs replaced the input
    # id with a line number and dropped every other field, so rejoin on the text
    # itself when it is missing — otherwise the confirm check silently passes
    # everything and the merge quietly imports the rejects too.
    if args.candidates and any("mined_for" not in r for r in mined):
        want = {normalize(c["text"]): c.get("mined_for")
                for c in load_coded([args.candidates]) if c.get("text")}
        rejoined = 0
        for r in mined:
            if "mined_for" not in r and r.get("text"):
                target = want.get(normalize(r["text"]))
                if target:
                    r["mined_for"] = target
                    rejoined += 1
        print(f"rejoined mined_for onto {rejoined}/{len(mined)} row(s) "
              f"from {args.candidates}")
    missing = sum(1 for r in mined if "mined_for" not in r)
    if missing and args.confirmed_only:
        sys.exit(f"{missing} mined row(s) have no 'mined_for' — pass "
                 f"--candidates <mine-candidates.jsonl> to rejoin it, or "
                 f"--keep-all to import them unchecked")

    before = Counter(c for r in base for c in r.get("codes", []))
    kept, rejected = [], 0
    seen = {normalize(r["text"]) for r in base if r.get("text")}
    for r in mined:
        codes = r.get("codes") or []
        text = r.get("text") or ""
        if not codes or not text:
            continue
        key = normalize(text)
        if key in seen:
            continue
        if args.confirmed_only:
            target = r.get("mined_for")
            if target and target not in codes:
                rejected += 1
                continue
        seen.add(key)
        kept.append({**r, "mined": True})

    after = Counter(before)
    for r in kept:
        after.update(r["codes"])

    out = Path(args.out)
    with out.open("w", encoding="utf-8") as fh:
        for r in base:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        for r in kept:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    gained = [(c, before.get(c, 0), after[c]) for c in sorted(after, key=int)
              if after[c] != before.get(c, 0)]
    print(f"base {len(base)} + mined {len(kept)} kept "
          f"({rejected} rejected: coder did not confirm the target label)")
    print(f"labels >= {args.target}: {sum(1 for v in before.values() if v >= args.target)}"
          f" -> {sum(1 for v in after.values() if v >= args.target)}")
    print(f"labels at 0:  {sum(1 for c in (book or after) if before.get(c, 0) == 0)}"
          f" -> {sum(1 for c in (book or after) if after.get(c, 0) == 0)}")
    print(f"\n{'code':>5}  {'label':<46} {'before':>7} {'after':>6}")
    for c, b, a in gained[: args.show]:
        print(f"{c:>5}  {book.get(c, c)[:46]:<46} {b:>7} {a:>6}")
    if len(gained) > args.show:
        print(f"  … and {len(gained) - args.show} more")
    print(f"\nwrote {out}")
    print("mined rows carry \"mined\": true — keep them OUT of val/test when "
          "splitting, or your metrics measure retrieval, not classification.")
    return 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def coded_arg(p):
        p.add_argument("-c", "--coded", nargs="+",
                       default=["../exports/paragraph-class.jsonl"],
                       help="already-coded JSONL(s) from gemini_classify.py")

    i = sub.add_parser("index", help="build a searchable FTS5 copy of the paragraph pool")
    i.add_argument("--db", default="parlamonitor.db")
    i.add_argument("--data", default="../data")
    i.add_argument("--index", default="../exports/capmine.db")
    i.add_argument("--min-duration", type=float, default=180.0)
    i.add_argument("--min-chars", type=int, default=90)
    i.add_argument("--exclude-procedural", action="store_true")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_index)

    p = sub.add_parser("plan", help="report which labels are starved, and why")
    coded_arg(p)
    p.add_argument("--target", type=int, default=25)
    p.add_argument("--show", type=int, default=40)
    p.set_defaults(func=cmd_plan)

    q = sub.add_parser("queries", help="draft Hungarian search terms per starved label")
    coded_arg(q)
    q.add_argument("-o", "--out", default="queries.json")
    q.add_argument("--target", type=int, default=25)
    q.add_argument("--model", default="gemini-3.5-flash-lite")
    q.add_argument("--batch-size", type=int, default=8)
    q.add_argument("--examples", type=int, default=3,
                   help="existing positives to show the model per label")
    q.add_argument("--include-catch-all", action="store_true")
    q.add_argument("--sleep", type=float, default=4.0)
    q.add_argument("--key-env", default="GEMINI_API_KEY")
    q.add_argument("--dry-run", action="store_true")
    q.set_defaults(func=cmd_queries)

    m = sub.add_parser("mine", help="retrieve candidate paragraphs for starved labels")
    coded_arg(m)
    m.add_argument("-q", "--queries", default="queries.json")
    m.add_argument("-o", "--out", default="../exports/mine-candidates.jsonl")
    m.add_argument("--index", default="../exports/capmine.db")
    m.add_argument("--target", type=int, default=25)
    m.add_argument("--overshoot", type=float, default=3.0,
                   help="retrieve this many times the shortfall — the coder rejects most")
    m.add_argument("--max-per-label", type=int, default=120)
    m.add_argument("--pool-factor", type=int, default=6,
                   help="BM25 rows to consider per row taken, for diversity filtering")
    m.add_argument("--max-per-speech", type=int, default=2)
    m.add_argument("--max-per-agenda", type=int, default=6)
    m.add_argument("--no-agenda", action="store_true",
                   help="skip debate-title retrieval, use paragraph text only")
    m.set_defaults(func=cmd_mine)

    g = sub.add_parser("merge", help="fold confirmed mined rows into the coded set")
    coded_arg(g)
    g.add_argument("-m", "--mined", nargs="+", required=True,
                   help="coded mined candidates (output of gemini_classify.py run)")
    g.add_argument("--candidates", default="../exports/mine-candidates.jsonl",
                   help="the `mine` output, to rejoin mined_for when the coder "
                        "did not carry it through")
    g.add_argument("-o", "--out", default="../exports/paragraph-class-augmented.jsonl")
    g.add_argument("--target", type=int, default=25)
    g.add_argument("--show", type=int, default=40)
    g.add_argument("--confirmed-only", action="store_true", default=True,
                   help="keep a mined row only if the coder gave it the target label")
    g.add_argument("--keep-all", dest="confirmed_only", action="store_false",
                   help="keep every coded mined row, whatever label it got")
    g.set_defaults(func=cmd_merge)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
