"""In-place migration for two data-quality fixes (2026-07-02):

1. **Agenda titles** were truncated at the first bill code (the old
   ``split_topic_and_bills`` cut ``"Interpelláció megtárgyalása (I/112) …"`` down
   to ``"Interpelláció megtárgyalása ("``). Re-derive ``agenda_item.title`` from
   the intact ``official_title`` with the fixed logic.

2. **Sentence paragraphs**: the ``sentence`` table gains a ``paragraph`` column
   (0-based source-paragraph index) so the reader can re-group a speech into its
   original paragraphs instead of one block. Back-filled from the raw scraper
   HTML in ``<data>/original/plenary/raw-<session>-day.json``.

Idempotent and safe to re-run. Text is never changed, so the FTS index stays
consistent — the ``sentence_au`` trigger is dropped only to avoid needless
re-indexing during the paragraph back-fill, then recreated exactly.

Usage:  python migrate_paragraphs_and_titles.py [DB] [DATA_DIR]
        (defaults: parlamonitor.db  ../data)
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from html import unescape
from pathlib import Path

# --- title fix: mirror scraper agenda.split_topic_and_bills ----------------
_BILL_CODE_RE = re.compile(r"\b[A-ZÁÉÍÓÖŐÚÜŰ]/\d+")


def clean_topic(title: str | None) -> str | None:
    if not title:
        return title
    if not _BILL_CODE_RE.search(title):
        return title.strip()
    t = _BILL_CODE_RE.sub("", title)
    t = re.sub(r"\([\s,;]*\)", "", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip(" ,;–—-")


# --- html→text: mirror scraper segment.html_to_text + clean_text -----------
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


def paragraph_index_map(full: str):
    """Return ``(norm, bounds)``: a whitespace-normalised concatenation of the
    non-empty paragraphs and a list of ``(offset, paragraph_index)`` so any match
    offset maps to a paragraph. Matching on the normalised form is robust to the
    newline-vs-space differences left by sentence segmentation (e.g. timecodes)."""
    paras = [p.strip() for p in full.split("\n") if p.strip()]
    norm = ""
    bounds: list[tuple[int, int]] = []
    for idx, p in enumerate(paras):
        bounds.append((len(norm), idx))
        norm += re.sub(r"\s+", " ", p).strip() + " "
    return norm, bounds


def paragraph_at(bounds, pos: int) -> int:
    lo = 0
    for start, idx in bounds:
        if start <= pos:
            lo = idx
        else:
            break
    return lo


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else "parlamonitor.db"
    data_dir = Path(sys.argv[2] if len(sys.argv) > 2 else "../data")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 1. add the paragraph column if missing
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sentence)")]
    if "paragraph" not in cols:
        conn.execute("ALTER TABLE sentence ADD COLUMN paragraph INTEGER")
        print("added sentence.paragraph column")

    # 2. re-derive truncated agenda titles from official_title
    n_titles = 0
    for r in conn.execute(
            "SELECT id, title, official_title FROM agenda_item "
            "WHERE official_title IS NOT NULL AND official_title != ''"):
        new = clean_topic(r["official_title"])
        if new and new != r["title"]:
            conn.execute("UPDATE agenda_item SET title=? WHERE id=?", (new, r["id"]))
            n_titles += 1
    conn.commit()
    print(f"agenda titles fixed: {n_titles}")

    # 3. back-fill sentence.paragraph from the raw HTML
    trig = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='sentence_au'"
    ).fetchone()
    conn.execute("DROP TRIGGER IF EXISTS sentence_au")
    updates: list[tuple[int, int]] = []
    ok = skipped = no_raw = 0
    try:
        sessions = [r["id"] for r in conn.execute("SELECT id FROM session ORDER BY id")]
        for sid in sessions:
            raw_path = data_dir / "original" / "plenary" / f"raw-{sid}-day.json"
            if not raw_path.exists():
                no_raw += 1
                continue
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            by_uuid = {s["speech_uuid"]: s for s in raw.get("speeches", [])
                       if s.get("speech_uuid")}
            speeches = conn.execute(
                "SELECT uid, speech_uuid FROM speech "
                "WHERE session_id=? AND has_text=1", (sid,)).fetchall()
            for sp in speeches:
                rs = by_uuid.get(sp["speech_uuid"])
                if not rs:
                    skipped += 1
                    continue
                norm, bounds = paragraph_index_map(html_to_text(rs.get("text_html") or ""))
                sents = conn.execute(
                    "SELECT id, text FROM sentence WHERE speech_id=? ORDER BY ord",
                    (sp["uid"],)).fetchall()
                cur = 0
                rowparas: list[tuple[int, int]] = []
                good = True
                for se in sents:
                    ns = re.sub(r"\s+", " ", se["text"]).strip()
                    i = norm.find(ns, cur)
                    if i < 0:
                        good = False
                        break
                    rowparas.append((paragraph_at(bounds, i), se["id"]))
                    cur = i + len(ns)
                if good:
                    updates.extend(rowparas)
                    ok += 1
                else:
                    skipped += 1
        conn.executemany("UPDATE sentence SET paragraph=? WHERE id=?", updates)
        conn.commit()
    finally:
        if trig and trig["sql"]:
            conn.execute(trig["sql"])  # recreate the FTS-sync trigger exactly
            conn.commit()
    print(f"paragraph back-fill: speeches ok={ok} skipped={skipped} "
          f"sessions_without_raw={no_raw} sentence_updates={len(updates)}")
    conn.close()


if __name__ == "__main__":
    main()
