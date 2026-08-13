#!/usr/bin/env python3
"""Sample random paragraphs from long speeches, parenthetical-free (read-only).

Draws a uniform random sample of source paragraphs taken from speeches of at
least ``--min-duration`` seconds, with every parenthesised span removed and a
``--min-chars`` floor applied to what is left. Output is one JSON object per
line, ``{"text": "..."}`` and nothing else, for feeding a model or an annotator.

    python export_paragraph_sample.py --n 10000 --out ../exports/paragraph-sample.jsonl

Where the paragraphs come from
------------------------------
A speech's paragraph structure lives in ``sentence.paragraph`` (0-based source
paragraph index, so the reader can re-group a speech into blocks). That column
is back-filled only for cycles 39/40/43; for 41/42 it is NULL, so this script
falls back to the same source the back-fill itself used — the raw scraper HTML
in ``<data>/original/plenary/raw-<session>-day.json``, split on its paragraph
breaks. Both paths therefore yield the transcript's own paragraphs, and all five
cycles are represented. ``--source`` forces one path or the other.

What gets cleaned or dropped
----------------------------
  * The transcript's speaker attribution that opens a speech ("NAGY JÁNOS
    (TISZA):", "DR. X, a Nemzeti Választási Bizottság elnöke:", bare "ELNÖK:")
    is stripped from that speech's first paragraph — it is a record-keeping
    label, not speech.
  * Every parenthesised span is removed, innermost-first so nesting is handled:
    the editorial stage directions (applause, heckling, the chair's bell), the
    wall-clock stamps — and, as asked, the textual ones too ("13. § (2)
    bekezdése"). A paragraph left holding an unbalanced parenthesis is dropped
    rather than emitted half-cleaned.
  * Speeches whose timing is the known whole-day-offset echo (every speech in an
    unsegmented sitting inheriting the day's start/end, giving fake multi-hour
    durations) are excluded — otherwise they would flood a duration filter.
    Detected as 2+ speeches of one sitting sharing an exact start *and* end.

The sample is drawn by reservoir sampling in one streaming pass, so the pool is
never held in memory and ``--seed`` makes the draw reproducible.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sqlite3
import sys
from collections import Counter
from html import unescape
from pathlib import Path

# --- paragraph recovery from raw scraper HTML (mirrors migrate_paragraphs_and_titles) ---

_TAG_RE = re.compile(r"<[^>]+>")
_PARA_BREAK_RE = re.compile(r"</p\s*>|<br\s*/?>", re.I)


def html_to_text(html: str) -> str:
    t = _PARA_BREAK_RE.sub("\n", html or "")
    t = _TAG_RE.sub("", t)
    t = unescape(t)
    t = t.replace("\xa0", " ")
    t = re.sub(r"[^\S\n]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t)
    return t.strip()


# --- text normalization ----------------------------------------------------

# The speaker attribution opening a speech: an ALL-CAPS name run (or a bare
# "ELNÖK"), optionally trailed by a faction/office in ordinary case, then a
# colon. Applied only to a speech's first paragraph, anchored at its start.
SPEAKER_PREFIX_RE = re.compile(
    r"""^(?:
            [A-ZÁÉÍÓÖŐÚÜŰ][A-ZÁÉÍÓÖŐÚÜŰ.\-]+                # DR. / ELNÖK / surname
            (?:[ ]+[A-ZÁÉÍÓÖŐÚÜŰ][A-ZÁÉÍÓÖŐÚÜŰ.\-]*){0,5}   # further name tokens
            (?:[^:\n]{0,160})?                               # faction / office
        ):\s*""",
    re.VERBOSE,
)

# Innermost parenthetical, so repeated passes peel nested ones.
INNER_PAREN_RE = re.compile(r"\(([^()]*)\)")
# Whitespace stranded before punctuation once a span between them is gone.
_ORPHAN_PUNCT_RE = re.compile(r"\s+([,.;:!?%])")


def strip_parentheticals(text: str) -> str | None:
    """Remove every parenthesised span. None if parentheses are unbalanced."""
    prev = None
    out = text
    while out != prev:
        prev = out
        out = INNER_PAREN_RE.sub("", out)
    if "(" in out or ")" in out:
        return None
    return out


def tidy(text: str) -> str:
    t = re.sub(r"\s+", " ", text)
    t = _ORPHAN_PUNCT_RE.sub(r"\1", t)
    t = re.sub(r"\s+", " ", t)
    # a removed span can leave a dangling separator at either end
    t = t.strip().strip("-–—,;: ").strip()
    return t


def clean_paragraph(text: str, is_first: bool) -> str | None:
    if is_first:
        text = SPEAKER_PREFIX_RE.sub("", text, count=1)
    stripped = strip_parentheticals(text)
    if stripped is None:
        return None
    return tidy(stripped)


# --- speech selection ------------------------------------------------------

SPEECH_SQL = """
WITH echoed AS (
    SELECT session_id, time_start, time_end
    FROM speech
    WHERE time_start IS NOT NULL AND time_end IS NOT NULL
    GROUP BY session_id, time_start, time_end
    HAVING COUNT(*) >= 2
)
SELECT sp.uid, sp.session_id, sp.speech_uuid, sp.period_number, sp.duration,
       sp.procedural
FROM speech sp
LEFT JOIN echoed e
       ON e.session_id = sp.session_id
      AND e.time_start = sp.time_start
      AND e.time_end   = sp.time_end
WHERE sp.duration >= :min_duration
  AND sp.has_text = 1
  AND e.session_id IS NULL
  {extra}
ORDER BY sp.uid
"""


def qualifying_speeches(conn, min_duration: float, exclude_procedural: bool):
    extra = "AND sp.procedural = 0" if exclude_procedural else ""
    sql = SPEECH_SQL.format(extra=extra)
    return conn.execute(sql, {"min_duration": min_duration}).fetchall()


def paragraphs_from_db(conn, uid: str) -> list[str] | None:
    """Re-group a speech's sentences into its source paragraphs."""
    rows = conn.execute(
        "SELECT paragraph, text FROM sentence WHERE speech_id=? ORDER BY ord", (uid,)
    ).fetchall()
    if not rows or any(r["paragraph"] is None for r in rows):
        return None
    paras: dict[int, list[str]] = {}
    for r in rows:
        paras.setdefault(r["paragraph"], []).append(r["text"].strip())
    return [" ".join(v) for _, v in sorted(paras.items())]


class RawParagraphs:
    """Paragraphs read out of the raw scraper HTML, one sitting-day file cached."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._sid: str | None = None
        self._by_uuid: dict[str, str] = {}
        self.missing_files: set[str] = set()

    def _load(self, session_id: str) -> None:
        if self._sid == session_id:
            return
        self._sid, self._by_uuid = session_id, {}
        path = self.data_dir / "original" / "plenary" / f"raw-{session_id}-day.json"
        if not path.exists():
            self.missing_files.add(session_id)
            return
        raw = json.loads(path.read_text(encoding="utf-8"))
        for s in raw.get("speeches", []):
            if s.get("speech_uuid"):
                self._by_uuid[s["speech_uuid"]] = s.get("text_html") or ""

    def get(self, session_id: str, speech_uuid: str | None) -> list[str] | None:
        if not speech_uuid:
            return None
        self._load(session_id)
        html = self._by_uuid.get(speech_uuid)
        if html is None:
            return None
        return [p.strip() for p in html_to_text(html).split("\n") if p.strip()]


# --- main ------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="parlamonitor.db")
    ap.add_argument("--data", default="../data", help="scraper data dir (raw HTML fallback)")
    ap.add_argument("--out", default="../exports/paragraph-sample.jsonl")
    ap.add_argument("--n", type=int, default=10000, help="sample size")
    ap.add_argument("--min-duration", type=float, default=180.0,
                    help="speech length floor in seconds (default 180 = 3 minutes)")
    ap.add_argument("--min-chars", type=int, default=90,
                    help="paragraph length floor, after cleaning (default 90)")
    ap.add_argument("--seed", type=int, default=20260812)
    ap.add_argument("--exclude-procedural", action="store_true",
                    help="drop chairing / session-management speeches")
    ap.add_argument("--source", choices=("auto", "db", "raw"), default="auto",
                    help="paragraph source: DB segmentation, raw HTML, or DB-then-raw")
    ap.add_argument("--periods", type=int, nargs="*", help="limit to these cycles")
    ap.add_argument("--stats", default=None, help="write a JSON manifest here")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    speeches = qualifying_speeches(conn, args.min_duration, args.exclude_procedural)
    if args.periods:
        keep = set(args.periods)
        speeches = [s for s in speeches if s["period_number"] in keep]
    if not speeches:
        sys.exit("no speeches matched the filters")

    raw = RawParagraphs(Path(args.data))
    rng = random.Random(args.seed)

    reservoir: list[str] = []
    seen = 0                       # paragraphs that passed every filter (the pool)
    per_period = Counter()         # pool size by cycle
    per_source = Counter()         # pool size by paragraph source
    dropped_short = dropped_unbalanced = 0
    speeches_no_paragraphs = 0
    speeches_used = Counter()

    for sp in speeches:
        uid, period = sp["uid"], sp["period_number"]
        paras = source = None
        if args.source in ("auto", "db"):
            paras = paragraphs_from_db(conn, uid)
            source = "db" if paras else None
        if paras is None and args.source in ("auto", "raw"):
            paras = raw.get(sp["session_id"], sp["speech_uuid"])
            source = "raw" if paras else None
        if not paras:
            speeches_no_paragraphs += 1
            continue

        used = False
        for idx, para in enumerate(paras):
            cleaned = clean_paragraph(para, is_first=(idx == 0))
            if cleaned is None:
                dropped_unbalanced += 1
                continue
            if len(cleaned) < args.min_chars:
                dropped_short += 1
                continue
            seen += 1
            per_period[period] += 1
            per_source[source] += 1
            used = True
            # reservoir sampling (Algorithm R): uniform over the whole pool
            if len(reservoir) < args.n:
                reservoir.append(cleaned)
            else:
                j = rng.randrange(seen)
                if j < args.n:
                    reservoir[j] = cleaned
        if used:
            speeches_used[period] += 1

    rng.shuffle(reservoir)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for text in reservoir:
            fh.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")

    n_dup = len(reservoir) - len(set(reservoir))
    lens = sorted(len(t) for t in reservoir)
    manifest = {
        "out": str(out_path),
        "sampled": len(reservoir),
        "pool": seen,
        "seed": args.seed,
        "filters": {
            "min_duration_s": args.min_duration,
            "min_chars": args.min_chars,
            "exclude_procedural": args.exclude_procedural,
            "periods": args.periods,
            "source": args.source,
        },
        "speeches_matched": len(speeches),
        "speeches_without_paragraphs": speeches_no_paragraphs,
        "speeches_contributing_by_period": dict(sorted(speeches_used.items())),
        "pool_by_period": dict(sorted(per_period.items())),
        "pool_by_source": dict(per_source),
        "dropped_below_min_chars": dropped_short,
        "dropped_unbalanced_parens": dropped_unbalanced,
        "sessions_without_raw_file": len(raw.missing_files),
        "sample_exact_duplicate_texts": n_dup,
        "sample_chars": {
            "min": lens[0] if lens else 0,
            "median": lens[len(lens) // 2] if lens else 0,
            "mean": round(sum(lens) / len(lens), 1) if lens else 0,
            "max": lens[-1] if lens else 0,
        },
    }
    if args.stats:
        Path(args.stats).write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
