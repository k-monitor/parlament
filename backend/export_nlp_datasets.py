#!/usr/bin/env python3
"""Export NLP-experimentation datasets from the runtime DB (read-only).

Two JSONL datasets, one electoral cycle at a time:

  1. ``cycle<N>-speeches.jsonl``  — one line per speech, with verbatim + cleaned
     text, the DB's sentence segmentation and speaker/agenda metadata. Intended
     for readability (e.g. Hungarian Flesch variants, sentence/word length) and
     lexical-diversity (TTR/MTLD/vocd-D) measurements.
  2. ``cycle<N>-qa.jsonl``       — one line per question-and-answer exchange
     (interpelláció / azonnali kérdés / kérdés), question text and answer text
     side by side, plus the rejoinders and the MP's accept/reject reaction.

Both are derived data — regenerate rather than commit. Usage:

    python export_nlp_datasets.py --period 43 --db parlamonitor.db --out ../exports

The transcript conventions this normalizes (``text_clean`` / ``sentences``):

  * the leading speaker attribution of a speech ("NAGY JÁNOS (TISZA):",
    "DR. X, a Nemzeti Választási Bizottság elnöke:") is stripped off the first
    sentence and kept separately in ``speaker_prefix``;
  * editorial stage directions in parentheses — applause, heckling, laughter,
    the chair's bell, the periodic wall-clock stamps "(13.20)", "(sic!)" — are
    removed, including nested ones; a sentence that consists only of such a
    parenthetical is dropped entirely (``n_sentences_dropped`` counts them).

Legal/textual parentheses ("13. § (2) bekezdése", "(EU) 2016/679") are kept.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict

# --- text normalization ----------------------------------------------------

WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# A speech's first sentence opens with the transcript's speaker attribution: an
# ALL-CAPS name run, then optionally a faction and/or an office in ordinary case
# ("(TISZA)", "gazdasági és energetikai miniszter", ", a Nemzeti Választási
# Bizottság elnöke"), closed by a colon.
SPEAKER_PREFIX_RE = re.compile(
    r"""^(?P<prefix>
            (?P<caps>
                (?:[A-ZÁÉÍÓÖŐÚÜŰ][A-ZÁÉÍÓÖŐÚÜŰ.\-]*[ ]+){1,5}  # given names / DR.
                [A-ZÁÉÍÓÖŐÚÜŰ][A-ZÁÉÍÓÖŐÚÜŰ.\-]*                # surname
            )
            (?P<rest>[^:\n]{0,160})?                             # faction / office
        ):\s""",
    re.VERBOSE,
)

# Cues that mark a parenthetical as an editorial stage direction rather than
# part of what was said. Matched case-insensitively anywhere in the content.
STAGE_CUES = re.compile(
    r"""(taps|derültség|zaj\b|moraj|közbeszól|közbekiabál|felkiált|felzúdulás
        |csenget|cseng[őo]|pissz|nevetés|ováció|éljenzés|helyeslés|szórványos
        |az\ elnök|elnök\ csenget|jelzi\ az\ id|jelzi\ a\ hozzászólási
        |felolvassa|elfoglalja|elhelyezi|hozzáteszi|feláll|állva|kézfelemelés
        |mikrofon\ nélkül|szónoki\ emelvény|sic!|a\ jegyz[őo]|folyamatos\ zaj
        |hujjog|tapsol|helyet\ foglal|fordulva|szembefordulva|felé\ mutat
        |felemelt\ kéz|kézzel\ jelez|jelez\.|ülésterem)""",
    re.IGNORECASE | re.VERBOSE,
)
# Wall-clock stamps the transcript inserts every half hour: "(13.20)", "(9:50)".
CLOCK_RE = re.compile(r"^\s*\d{1,2}[.:]\d{2}\s*\.?\s*$")
# Innermost parenthetical, so repeated passes peel nested ones.
INNER_PAREN_RE = re.compile(r"\(([^()]*)\)")


def _is_stage_direction(content: str) -> bool:
    return bool(CLOCK_RE.match(content)) or bool(STAGE_CUES.search(content))


def strip_stage_directions(text: str) -> str:
    """Remove editorial parentheticals (innermost-first, so nesting works)."""
    prev = None
    out = text
    while out != prev:
        prev = out
        out = INNER_PAREN_RE.sub(
            lambda m: "" if _is_stage_direction(m.group(1)) else m.group(0), out
        )
    # tidy the whitespace/punctuation the removals leave behind
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def split_speaker_prefix(first_sentence: str) -> tuple[str | None, str]:
    m = SPEAKER_PREFIX_RE.match(first_sentence)
    if not m:
        return None, first_sentence
    # Guard against a genuine sentence that merely opens with a capitalized word:
    # only the NAME run itself has to be all-caps (the office after it is not).
    caps = [c for c in m.group("caps") if c.isalpha()]
    if len(caps) < 5 or not all(c.isupper() for c in caps):
        return None, first_sentence
    return m.group("prefix").strip(), first_sentence[m.end():]


def counts(text: str) -> dict:
    return {"n_chars": len(text), "n_words": len(WORD_RE.findall(text))}


# --- DB access -------------------------------------------------------------

SPEECH_SQL = """
SELECT sp.uid, sp.origin_id, sp.speech_uuid, sp.session_id, s.date, s.sitting,
       sp.period_number, sp.speech_index, sp.felszolalas_tipus, sp.procedural,
       sp.person_id, sp.speaker_label, sp.speaker_status, sp.speaker_office,
       f.label            AS faction,
       p.label_full, p.is_mp, p.is_advocate, p.nationality,
       p.highest_education, p.wikidata_id, p.constituency,
       sp.time_start, sp.duration, sp.has_text,
       sp.agenda_item_id, ai.ord AS agenda_ord, ai.title AS agenda_title,
       ai.official_title AS agenda_official_title,
       ai.type AS agenda_type, ai.native_type AS agenda_native_type
FROM speech sp
JOIN session s          ON s.id = sp.session_id
LEFT JOIN faction f     ON f.id = sp.faction_id
LEFT JOIN person p      ON p.person_id = sp.person_id
LEFT JOIN agenda_item ai ON ai.id = sp.agenda_item_id
WHERE sp.period_number = ?
ORDER BY sp.session_id, sp.speech_index
"""

SENTENCE_SQL = """
SELECT se.speech_id, se.ord, se.paragraph, se.text
FROM sentence se
JOIN speech sp ON sp.uid = se.speech_id
WHERE sp.period_number = ?
ORDER BY se.speech_id, se.ord
"""


def load(db: str, period: int):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    speeches = [dict(r) for r in con.execute(SPEECH_SQL, (period,))]
    sentences: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in con.execute(SENTENCE_SQL, (period,)):
        sentences[r["speech_id"]].append(r)
    meta = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM build_meta")}
    con.close()
    return speeches, sentences, meta


# --- record building -------------------------------------------------------

MARK = "\x00%d\x00"          # sentence boundary marker, survives the cleaning
MARK_RE = re.compile(r"\x00(\d+)\x00")


def build_text(rows) -> dict:
    """Reconstruct a speech's text and its cleaned counterpart."""
    raw_sents = [(r["ord"], r["paragraph"], r["text"]) for r in rows]
    if not raw_sents:
        return {}

    # group into the source paragraphs (NULL paragraph → one block)
    paras: list[list[tuple[int, int | None, str]]] = []
    last_p = object()
    for o, p, t in raw_sents:
        key = 0 if p is None else p
        if key != last_p:
            paras.append([])
            last_p = key
        paras[-1].append((o, p, t))

    text_raw = "\n\n".join(" ".join(t for _o, _p, t in pp) for pp in paras)

    # Cleaning runs per PARAGRAPH, not per sentence: a stage direction can
    # straddle a sentence boundary — "(A képviselő feláll." + "Taps.)" — and
    # would survive a per-sentence pass. Sentence boundaries are carried through
    # as markers, so the cleaned text can be split back into sentences; a
    # parenthetical that swallowed a boundary correctly merges its fragments.
    speaker_prefix = None
    cleaned: list[dict] = []
    kept_paras: list[list[str]] = []
    for pi, pp in enumerate(paras):
        chunks = []
        for si, (o, p, t) in enumerate(pp):
            if pi == 0 and si == 0:
                speaker_prefix, t = split_speaker_prefix(t)
            chunks.append((MARK % o) + t)
        joined = strip_stage_directions("".join(chunks))

        parts = MARK_RE.split(joined)          # ['', ord, text, ord, text, …]
        segs = []
        for i in range(1, len(parts) - 1, 2):
            ordv, txt = int(parts[i]), parts[i + 1].strip()
            if not WORD_RE.search(txt):
                continue
            segs.append({"ord": ordv, "paragraph": pp[0][1], "text": txt})
        if segs:
            cleaned.extend(segs)
            kept_paras.append([s["text"] for s in segs])

    dropped = len(raw_sents) - len(cleaned)
    cparas = kept_paras
    text_clean = "\n\n".join(" ".join(pp) for pp in cparas)

    return {
        "speaker_prefix": speaker_prefix,
        "text": text_raw,
        "text_clean": text_clean,
        "sentences": [c["text"] for c in cleaned],
        "sentence_paragraphs": [c["paragraph"] for c in cleaned],
        "n_sentences": len(cleaned),
        "n_sentences_raw": len(raw_sents),
        "n_sentences_dropped": dropped,
        "n_paragraphs": len(cparas),
        **counts(text_clean),
        "n_chars_raw": len(text_raw),
    }


# Speech types that are formulaic (the oath is recited verbatim, partly in
# minority languages) — kept out of the readability set, see README.
EXCLUDED_TYPES = {"Eskü"}

ROLES = {
    "elhangzik az interpelláció/kérdés/azonnali kérdés": "question",
    "interpelláció szóban megválaszolva": "answer",
    "kérdés megválaszolva": "answer",
    "azonnali kérdésre adott képviselői viszonválasz": "mp_rejoinder",
    "azonnali kérdésre adott miniszteri viszonválasz": "minister_rejoinder",
    "képviselő elfogadta a választ": "mp_reaction_accept",
    "képviselő elutasította a választ": "mp_reaction_reject",
    "bejelentés helyettes válaszadó elutasításáról": "substitute_answerer_rejected",
}
QA_AGENDA_TYPES = {"qa", "questioning_of_the_government"}
QA_KIND = {
    "HU-interpellation": "interpellation",
    "HU-immediate_question": "immediate_question",
    "HU-question": "question",
}


def speaker_of(sp: dict) -> dict:
    return {
        "person_id": sp["person_id"],
        "label": sp["speaker_label"],
        "label_full": sp["label_full"],
        "faction": sp["faction"],
        "office": sp["speaker_office"],
        "status": sp["speaker_status"],
        "is_mp": bool(sp["is_mp"]) if sp["is_mp"] is not None else None,
        "is_advocate": bool(sp["is_advocate"]) if sp["is_advocate"] is not None else None,
        "nationality": sp["nationality"],
        "constituency": sp["constituency"],
        "highest_education": sp["highest_education"],
        "wikidata_id": sp["wikidata_id"],
    }


def speech_record(sp: dict, text: dict) -> dict:
    return {
        "uid": sp["uid"],
        "origin_id": sp["origin_id"],
        "session_id": sp["session_id"],
        "date": sp["date"],
        "sitting": sp["sitting"],
        "period_number": sp["period_number"],
        "speech_index": sp["speech_index"],
        "speech_type": sp["felszolalas_tipus"],
        "speaker": speaker_of(sp),
        "agenda": {
            "id": sp["agenda_item_id"],
            "ord": sp["agenda_ord"],
            "title": sp["agenda_title"],
            "official_title": sp["agenda_official_title"],
            "type": sp["agenda_type"],
            "native_type": sp["agenda_native_type"],
        },
        "time_start_s": sp["time_start"],
        "duration_s": sp["duration"],
        **text,
    }


def qa_turn(sp: dict, text: dict, role: str) -> dict:
    return {
        "role": role,
        "uid": sp["uid"],
        "speech_index": sp["speech_index"],
        "speech_type": sp["felszolalas_tipus"],
        "speaker": speaker_of(sp),
        "duration_s": sp["duration"],
        # same convention as the speeches dataset: `text` verbatim, `text_clean`
        # normalized (speaker attribution + stage directions removed)
        "text": text.get("text", ""),
        "text_clean": text.get("text_clean", ""),
        "speaker_prefix": text.get("speaker_prefix"),
        "sentences": text.get("sentences", []),
        "n_sentences": text.get("n_sentences", 0),
        "n_words": text.get("n_words", 0),
        "n_chars": text.get("n_chars", 0),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("PARLAMONITOR_DB", "parlamonitor.db"))
    ap.add_argument("--period", type=int, default=43)
    ap.add_argument("--out", default="../exports")
    args = ap.parse_args()

    speeches, sentences, build = load(args.db, args.period)
    if not speeches:
        print(f"no speeches for period {args.period} in {args.db}", file=sys.stderr)
        return 1
    os.makedirs(args.out, exist_ok=True)

    texts = {sp["uid"]: build_text(sentences.get(sp["uid"], [])) for sp in speeches}

    # ---- dataset 1: speeches for readability / lexical diversity ----------
    speech_path = os.path.join(args.out, f"cycle{args.period}-speeches.jsonl")
    kept = 0
    skipped = Counter()
    words = 0
    with open(speech_path, "w", encoding="utf-8") as fh:
        for sp in speeches:
            t = texts[sp["uid"]]
            if sp["procedural"]:
                skipped["procedural"] += 1
                continue
            if (sp["felszolalas_tipus"] or "") in EXCLUDED_TYPES:
                skipped["formulaic_type"] += 1
                continue
            if not t or not t.get("text_clean"):
                skipped["no_text"] += 1
                continue
            fh.write(json.dumps(speech_record(sp, t), ensure_ascii=False) + "\n")
            kept += 1
            words += t["n_words"]

    # ---- dataset 2: question/answer exchanges ----------------------------
    by_item: dict[int, list[dict]] = defaultdict(list)
    for sp in speeches:
        if sp["agenda_item_id"] and sp["agenda_type"] in QA_AGENDA_TYPES:
            by_item[sp["agenda_item_id"]].append(sp)

    qa_path = os.path.join(args.out, f"cycle{args.period}-qa.jsonl")
    qa_rows = 0
    qa_unanswered = 0
    qa_complete = 0
    qa_kinds = Counter()
    qa_no_text_sessions: Counter = Counter()
    orphan_questions = 0
    with open(qa_path, "w", encoding="utf-8") as fh:
        for item_id in sorted(by_item):
            group = sorted(by_item[item_id], key=lambda s: s["speech_index"])
            turns = []
            for sp in group:
                role = ROLES.get(sp["felszolalas_tipus"] or "")
                if role is None:
                    if sp["procedural"]:
                        continue  # chairing / formal markers
                    role = "other"
                t = texts[sp["uid"]] or {}
                turns.append(qa_turn(sp, t, role))

            question = next((t for t in turns if t["role"] == "question"), None)
            if question is None:
                continue
            answer = next((t for t in turns if t["role"] == "answer"
                           and t["speech_index"] > question["speech_index"]), None)
            reaction = next((t for t in turns
                             if t["role"].startswith("mp_reaction")), None)
            head = group[0]
            rec = {
                "exchange_id": f"{head['session_id']}-ai{item_id}",
                "agenda_item_id": item_id,
                "session_id": head["session_id"],
                "date": head["date"],
                "sitting": head["sitting"],
                "period_number": args.period,
                "kind": QA_KIND.get(head["agenda_native_type"], head["agenda_native_type"]),
                "agenda_type": head["agenda_type"],
                "agenda_native_type": head["agenda_native_type"],
                "title": head["agenda_title"],
                "official_title": head["agenda_official_title"],
                "asker": question["speaker"],
                "answerer": answer["speaker"] if answer else None,
                "answered": answer is not None,
                # only an interpellation has an accept/reject step; an immediate
                # question ends with the rejoinders, so this stays null there
                "answer_accepted": (None if reaction is None
                                    else reaction["role"] == "mp_reaction_accept"),
                # the side-by-side pair, cleaned — the rest of each turn (verbatim
                # text, sentences, counts, speaker) is under question/answer below
                "question_text": question["text_clean"],
                "answer_text": answer["text_clean"] if answer else None,
                # A held sitting whose transcript parlament.hu has not published
                # yet still yields the exchange's metadata but no text, and one
                # 2026-05-26 answer exists only as a stage direction (the
                # minister's microphone failed). Filter on `text_complete`.
                "has_question_text": question["n_words"] > 0,
                "has_answer_text": bool(answer) and answer["n_words"] > 0,
                "text_complete": question["n_words"] > 0 and bool(answer)
                and answer["n_words"] > 0,
                "question": question,
                "answer": answer,
                "mp_rejoinder": next((t for t in turns
                                      if t["role"] == "mp_rejoinder"), None),
                "minister_rejoinder": next((t for t in turns
                                            if t["role"] == "minister_rejoinder"), None),
                "mp_reaction": reaction,
                "turns": turns,
            }
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            qa_rows += 1
            qa_kinds[rec["kind"]] += 1
            if answer is None:
                qa_unanswered += 1
            if rec["text_complete"]:
                qa_complete += 1
            else:
                qa_no_text_sessions[head["session_id"]] += 1

    # Q&A speeches that sit outside a qa/interpellation agenda item would be
    # missed above — count them so a silent gap is visible.
    for sp in speeches:
        if (sp["felszolalas_tipus"] == "elhangzik az interpelláció/kérdés/azonnali kérdés"
                and sp["agenda_type"] not in QA_AGENDA_TYPES):
            orphan_questions += 1

    manifest = {
        "period_number": args.period,
        "db": os.path.abspath(args.db),
        "db_data_updated_at": build.get("data_updated_at"),
        "speeches_dataset": {
            "file": os.path.basename(speech_path),
            "rows": kept,
            "total_words_clean": words,
            "skipped": dict(skipped),
            "excluded_speech_types": sorted(EXCLUDED_TYPES),
        },
        "qa_dataset": {
            "file": os.path.basename(qa_path),
            "rows": qa_rows,
            "rows_text_complete": qa_complete,
            "by_kind": dict(qa_kinds),
            "unanswered": qa_unanswered,
            "incomplete_text_by_session": dict(qa_no_text_sessions),
            "questions_outside_qa_agenda_items": orphan_questions,
        },
    }
    with open(os.path.join(args.out, f"cycle{args.period}-manifest.json"),
              "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
