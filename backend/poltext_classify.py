#!/usr/bin/env python3
"""Try the poltextlab XLM-RoBERTa classifiers on a sample of the transcript DB.

Two gated Hugging Face models, run over the same units so their outputs can be
compared side by side:

  * ``poltextlab/xlm-roberta-large-pooled-emotions10-v3``  — emotion labels
  * ``poltextlab/xlm-roberta-large-pooled-cap-minor-v5``   — CAP minor topic codes

Both are gated: request access on the model pages, then export a token
(``export HF_TOKEN=hf_...``) or run ``huggingface-cli login`` on the GPU box.

The work is split into three subcommands so the heavy step can run elsewhere —
``sample`` needs only sqlite3 (no torch), ``run`` needs the GPU and
``pip install "transformers>=4.40" torch sentencepiece`` (``--fp16`` fits the
two 560M-parameter models in ~1.2 GB of VRAM each; they are loaded one after
the other, never together):

    # here, next to the DB (writes a small JSONL, a few MB at most)
    python poltext_classify.py sample --db parlamonitor.db -n 200 -o sample.jsonl

    # on the GPU box, after scp'ing poltext_classify.py + sample.jsonl
    python poltext_classify.py labels                       # check access + label sets
    python poltext_classify.py run -i sample.jsonl -o results.jsonl --fp16

    # anywhere (pure stdlib)
    python poltext_classify.py report -i results.jsonl --examples 3

``run`` also accepts ``--db`` directly and samples on the fly, for the case
where the DB itself lives on the GPU machine.

Units. Both models are trained on sentence/quasi-sentence length input and cap
at 512 tokens, so the default unit is the sentence. ``--unit paragraph`` and
``--unit speech`` are available and then run over sliding token windows whose
probabilities are averaged, but treat those numbers as a rough aggregate rather
than as what the models were fitted on.

Text is cleaned with the same normalization as ``export_nlp_datasets.py``: the
"NAGY JÁNOS (TISZA):" speaker attribution is stripped and stage directions
(taps, közbeszólás, the half-hourly "(13.20)" stamps) are removed, so the models
see what was said rather than what the shorthand writer noted.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sqlite3
import sys
from collections import Counter, defaultdict

WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

MODELS = {
    "emotions": "poltextlab/xlm-roberta-large-pooled-emotions10-v3",
    "cap": "poltextlab/xlm-roberta-large-pooled-cap-minor-v5",
}

# CAP major topics (2019 master codebook). The minor model emits 3-4 digit minor
# codes; the major is the code's leading digits, which is enough to read the
# output without the full minor codebook. Pass --cap-codebook to name the minors.
CAP_MAJOR = {
    1: "Macroeconomics",
    2: "Civil Rights",
    3: "Health",
    4: "Agriculture",
    5: "Labor",
    6: "Education",
    7: "Environment",
    8: "Energy",
    9: "Immigration",
    10: "Transportation",
    12: "Law and Crime",
    13: "Social Welfare",
    14: "Housing",
    15: "Domestic Commerce",
    16: "Defense",
    17: "Technology",
    18: "Foreign Trade",
    19: "International Affairs",
    20: "Government Operations",
    21: "Public Lands",
    23: "Culture",
    99: "Other",
}


# --------------------------------------------------------------------------
# sampling (sqlite3 only — must import cleanly without torch/transformers)
# --------------------------------------------------------------------------

CANDIDATE_SQL = """
SELECT sp.uid, sp.period_number, sp.speech_index, sp.session_id, s.date, s.sitting,
       sp.felszolalas_tipus, sp.duration,
       sp.person_id, sp.speaker_label, sp.speaker_status, sp.speaker_office,
       f.label AS faction, p.is_mp,
       ai.title AS agenda_title, ai.type AS agenda_type
FROM speech sp
JOIN session s           ON s.id = sp.session_id
LEFT JOIN faction f      ON f.id = sp.faction_id
LEFT JOIN person p       ON p.person_id = sp.person_id
LEFT JOIN agenda_item ai ON ai.id = sp.agenda_item_id
WHERE sp.has_text = 1
  AND COALESCE(sp.procedural, 0) = 0
  {period_clause}
"""

SENTENCES_SQL = """
SELECT speech_id, ord, paragraph, text
FROM sentence
WHERE speech_id IN ({placeholders})
ORDER BY speech_id, ord
"""

# Formulaic speech types: the oath is recited verbatim (partly in minority
# languages) and the bare procedural announcements carry no topic or emotion.
SKIP_TYPES = {"Eskü", "Napirend előtti felszólalás bejelentése"}


def _build_text():
    """Import the cleaning pipeline from the sibling export script."""
    try:
        from export_nlp_datasets import build_text
    except ImportError as exc:  # pragma: no cover - operator-facing hint
        sys.exit(
            f"cannot import export_nlp_datasets.py ({exc}).\n"
            "Run `sample` from the backend/ directory that holds it — only the\n"
            "`run`/`report` steps are standalone."
        )
    return build_text


def sample_units(
    db: str,
    n_speeches: int,
    unit: str,
    seed: int,
    period: int | None,
    per_speech: int,
    min_words: int,
    min_speech_words: int,
    max_units: int,
) -> tuple[list[dict], dict]:
    build_text = _build_text()
    rng = random.Random(seed)

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    sql = CANDIDATE_SQL.format(
        period_clause="AND sp.period_number = ?" if period else ""
    )
    cands = [
        dict(r)
        for r in con.execute(sql, (period,) if period else ())
        if r["felszolalas_tipus"] not in SKIP_TYPES
    ]
    if not cands:
        sys.exit("no candidate speeches matched the filters")

    # Stratify evenly over the electoral cycles present, so a sample is not
    # dominated by cycle 39 (the largest) or starved of the current one.
    by_period: dict[int, list[dict]] = defaultdict(list)
    for c in cands:
        by_period[c["period_number"]].append(c)
    for rows in by_period.values():
        rng.shuffle(rows)
    order: list[dict] = []
    periods = sorted(by_period)
    for i in range(max(len(v) for v in by_period.values())):
        for p in periods:
            if i < len(by_period[p]):
                order.append(by_period[p][i])

    units: list[dict] = []
    kept_speeches = 0
    seen = 0
    # Walk the shuffled candidates in chunks, fetching each chunk's sentences in
    # one query, until enough speeches clear the length filter.
    for start in range(0, len(order), 200):
        if kept_speeches >= n_speeches or len(units) >= max_units:
            break
        chunk = order[start : start + 200]
        by_uid = {c["uid"]: c for c in chunk}
        rows: dict[str, list] = defaultdict(list)
        q = SENTENCES_SQL.format(placeholders=",".join("?" * len(chunk)))
        for r in con.execute(q, list(by_uid)):
            rows[r["speech_id"]].append(r)

        for c in chunk:  # keep the interleaved (stratified) order
            seen += 1
            if kept_speeches >= n_speeches or len(units) >= max_units:
                break
            txt = build_text(rows.get(c["uid"], []))
            if not txt or txt["n_words"] < min_speech_words:
                continue
            new = _units_for(c, txt, unit, per_speech, min_words, rng)
            if not new:
                continue
            units.extend(new)
            kept_speeches += 1

    con.close()
    meta = {
        "db": os.path.abspath(db),
        "unit": unit,
        "seed": seed,
        "period": period,
        "n_speeches": kept_speeches,
        "n_units": len(units),
        "candidates_considered": seen,
        "candidates_total": len(cands),
        "per_speech": per_speech,
        "min_words": min_words,
        "min_speech_words": min_speech_words,
    }
    return units, meta


def _units_for(c, txt, unit, per_speech, min_words, rng) -> list[dict]:
    """Slice one speech's cleaned text into the requested classification units."""
    base = {
        "speech_uid": c["uid"],
        "period": c["period_number"],
        "session_id": c["session_id"],
        "sitting": c["sitting"],
        "date": c["date"],
        "speech_type": c["felszolalas_tipus"],
        "person_id": c["person_id"],
        "speaker": c["speaker_label"],
        "speaker_office": c["speaker_office"],
        "faction": c["faction"],
        "is_mp": c["is_mp"],
        "agenda_title": c["agenda_title"],
        "agenda_type": c["agenda_type"],
        "speech_n_words": txt["n_words"],
    }

    if unit == "speech":
        pieces = [(0, txt["text_clean"])]
    elif unit == "paragraph":
        pieces = list(enumerate(txt["text_clean"].split("\n\n")))
    else:
        pieces = list(enumerate(txt["sentences"]))

    out = []
    for i, text in pieces:
        text = text.strip()
        if len(WORD_RE.findall(text)) < min_words:
            continue
        out.append(
            {"unit_id": f"{c['uid']}#{unit}{i}", "unit": unit, "idx": i, "text": text, **base}
        )

    if per_speech and unit != "speech" and len(out) > per_speech:
        out = sorted(rng.sample(out, per_speech), key=lambda u: u["idx"])
    return out


# --------------------------------------------------------------------------
# inference
# --------------------------------------------------------------------------


def _hf_token() -> str | None:
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")


def _load(model_id: str, device: str, fp16: bool):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_id, token=_hf_token(), use_fast=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_id,
        token=_hf_token(),
        torch_dtype=torch.float16 if (fp16 and device == "cuda") else None,
    )
    model.to(device).eval()
    return tok, model


def _labels_of(model) -> list[str]:
    cfg = model.config
    n = cfg.num_labels
    id2 = cfg.id2label or {}
    # id2label keys arrive as ints or strings depending on how it was saved.
    return [str(id2.get(i, id2.get(str(i), f"LABEL_{i}"))) for i in range(n)]


def _multilabel(model) -> bool:
    return getattr(model.config, "problem_type", None) == "multi_label_classification"


def predict(
    model_id: str,
    texts: list[str],
    device: str,
    batch_size: int,
    max_length: int,
    fp16: bool,
    windows: bool,
    stride: int,
) -> tuple[list[list[float]], list[str], bool]:
    """Return per-text probability vectors, the label names, and the label mode.

    With ``windows`` the text is split into overlapping token windows and the
    windows' probabilities are averaged, so a speech longer than 512 tokens is
    covered end to end instead of silently truncated.
    """
    import torch

    tok, model = _load(model_id, device, fp16)
    labels = _labels_of(model)
    multi = _multilabel(model)

    enc_kw = dict(truncation=True, max_length=max_length, add_special_tokens=True)
    if windows and not tok.is_fast:
        print(
            "  note: slow tokenizer cannot report window ownership — truncating instead",
            file=sys.stderr,
        )
        windows = False
    if windows:
        enc = tok(
            texts,
            return_overflowing_tokens=True,
            stride=min(stride, max_length // 2),
            **enc_kw,
        )
        owner = list(enc["overflow_to_sample_mapping"])
    else:
        enc = tok(texts, **enc_kw)
        owner = list(range(len(texts)))
    chunks = enc["input_ids"]

    # Sort by length so a batch pads to its own longest member, not the corpus's.
    order = sorted(range(len(chunks)), key=lambda i: len(chunks[i]))
    probs: list[list[float] | None] = [None] * len(chunks)
    done = 0
    with torch.inference_mode():
        for s in range(0, len(order), batch_size):
            idx = order[s : s + batch_size]
            batch = tok.pad(
                {"input_ids": [chunks[i] for i in idx]}, return_tensors="pt"
            ).to(device)
            logits = model(**batch).logits.float()
            p = torch.sigmoid(logits) if multi else torch.softmax(logits, dim=-1)
            for row, i in zip(p.cpu().tolist(), idx):
                probs[i] = row
            done += len(idx)
            print(
                f"  {os.path.basename(model_id)}: {done}/{len(chunks)} chunks",
                end="\r",
                file=sys.stderr,
                flush=True,
            )
    print(file=sys.stderr)

    # Average the windows belonging to the same input text.
    acc = [[0.0] * len(labels) for _ in texts]
    cnt = [0] * len(texts)
    for i, o in enumerate(owner):
        cnt[o] += 1
        for j, v in enumerate(probs[i]):
            acc[o][j] += v
    out = [[v / max(c, 1) for v in row] for row, c in zip(acc, cnt)]

    del model
    if device == "cuda":
        torch.cuda.empty_cache()
    return out, labels, multi


def cap_name(label: str, codebook: dict[str, str]) -> str:
    """Render a CAP minor label as '<code> <minor name> (<major topic>)'."""
    m = re.match(r"^\s*(\d{2,4})\b(.*)$", label)
    if not m:
        return label
    code, rest = m.group(1), m.group(2).strip(" -_:")
    name = codebook.get(code) or rest
    major = CAP_MAJOR.get(int(code) // 100 if len(code) > 2 else int(code), "?")
    return f"{code} {name} ({major})".replace("  ", " ")


def decode(
    probs: list[float], labels: list[str], multi: bool, topk: int, threshold: float
) -> dict:
    ranked = sorted(range(len(probs)), key=lambda i: probs[i], reverse=True)
    top = [{"label": labels[i], "score": round(probs[i], 4)} for i in ranked[:topk]]
    rec = {"top": top, "label": labels[ranked[0]], "score": round(probs[ranked[0]], 4)}
    if multi:
        rec["labels"] = [labels[i] for i in ranked if probs[i] >= threshold]
    return rec


def load_codebook(path: str | None) -> dict[str, str]:
    """Optional CAP minor codebook CSV: first column code, second column name."""
    if not path:
        return {}
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if len(row) >= 2 and row[0].strip().isdigit():
                out[row[0].strip()] = row[1].strip()
    return out


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def table(rows: list[tuple], headers: tuple) -> str:
    cols = list(zip(*([headers] + [tuple(map(str, r)) for r in rows]))) if rows else []
    if not cols:
        return "  (no rows)"
    w = [max(len(c) for c in col) for col in cols]
    fmt = lambda vals: "  " + "  ".join(
        str(v).ljust(w[i]) if i == 0 else str(v).rjust(w[i]) for i, v in enumerate(vals)
    )
    return "\n".join(
        [fmt(headers), "  " + "  ".join("-" * x for x in w)]
        + [fmt(tuple(map(str, r))) for r in rows]
    )


def report(recs: list[dict], models: list[str], codebook: dict, examples: int, out_dir):
    pretty = {"cap": lambda l: cap_name(l, codebook), "emotions": lambda l: l}
    n = len(recs)
    print(f"\n{n} units over {len({r['speech_uid'] for r in recs})} speeches")
    print(
        "periods: "
        + ", ".join(f"{p}={c}" for p, c in sorted(Counter(r["period"] for r in recs).items()))
    )

    for key in models:
        lab = f"{key}_label"
        if not any(lab in r for r in recs):
            continue
        fmt = pretty.get(key, lambda l: l)
        print(f"\n=== {key}: {MODELS[key]} ===")

        cnt = Counter(r[lab] for r in recs if lab in r)
        conf = defaultdict(list)
        for r in recs:
            if lab in r:
                conf[r[lab]].append(r[f"{key}_score"])
        rows = [
            (fmt(l), c, f"{100 * c / n:5.1f}%", f"{sum(conf[l]) / len(conf[l]):.3f}")
            for l, c in cnt.most_common()
        ]
        print(table(rows, ("label", "n", "share", "mean p")))

        low = sum(1 for r in recs if lab in r and r[f"{key}_score"] < 0.5)
        print(f"  {low} units ({100 * low / n:.1f}%) have top-1 probability < 0.5")

        # Faction × top label, as a share of each faction's own units.
        fac = defaultdict(Counter)
        for r in recs:
            if lab in r and r.get("faction"):
                fac[r["faction"]][r[lab]] += 1
        rows = []
        for f, c in sorted(fac.items(), key=lambda kv: -sum(kv[1].values())):
            tot = sum(c.values())
            if tot < 10:
                continue
            top3 = "; ".join(f"{fmt(l)} {100 * v / tot:.0f}%" for l, v in c.most_common(3))
            rows.append((f, tot, top3))
        if rows:
            print("\n  by faction (≥10 units):")
            print(table(rows, ("faction", "n", "top 3 labels")))

        # Speech-level roll-up: the label winning the most units in a speech.
        sp = defaultdict(Counter)
        for r in recs:
            if lab in r:
                sp[r["speech_uid"]][r[lab]] += 1
        agg = Counter(c.most_common(1)[0][0] for c in sp.values())
        rows = [
            (fmt(l), c, f"{100 * c / len(sp):5.1f}%") for l, c in agg.most_common(10)
        ]
        print(f"\n  speech-level majority label (top 10 of {len(agg)}):")
        print(table(rows, ("label", "speeches", "share")))

        if examples:
            print("\n  highest-confidence examples:")
            for l, _ in cnt.most_common():
                ex = sorted(
                    (r for r in recs if r.get(lab) == l),
                    key=lambda r: -r[f"{key}_score"],
                )[:examples]
                print(f"\n  [{fmt(l)}]")
                for e in ex:
                    t = e["text"].replace("\n", " ")
                    t = t[:220] + "…" if len(t) > 220 else t
                    who = e.get("speaker") or "?"
                    fx = f"/{e['faction']}" if e.get("faction") else ""
                    print(f"    {e[f'{key}_score']:.2f} {who}{fx} ({e['date']}): {t}")

    # Cross-tab: do the topic and emotion heads agree on anything interesting?
    if all(f"{k}_label" in recs[0] for k in ("cap", "emotions")):
        cross = defaultdict(Counter)
        for r in recs:
            major = re.match(r"^\s*(\d{2,4})", r["cap_label"])
            key = (
                CAP_MAJOR.get(int(major.group(1)) // 100, "?")
                if major and len(major.group(1)) > 2
                else r["cap_label"]
            )
            cross[key][r["emotions_label"]] += 1
        rows = []
        for topic, c in sorted(cross.items(), key=lambda kv: -sum(kv[1].values())):
            tot = sum(c.values())
            if tot < 10:
                continue
            rows.append(
                (topic, tot, "; ".join(f"{l} {100 * v / tot:.0f}%" for l, v in c.most_common(3)))
            )
        if rows:
            print("\n=== CAP major topic × emotion (≥10 units) ===")
            print(table(rows, ("topic", "n", "top emotions")))

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "units.csv")
        cols = [
            c
            for c in (
                "unit_id speech_uid period date sitting speaker faction speech_type "
                "agenda_type emotions_label emotions_score cap_label cap_score text"
            ).split()
            if any(c in r for r in recs)
        ]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(recs)
        print(f"\nwrote {path}")


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def read_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def write_jsonl(path: str, recs: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def add_sample_args(p):
    p.add_argument("--db", default="parlamonitor.db")
    p.add_argument("-n", "--n-speeches", type=int, default=200)
    p.add_argument("--unit", choices=("sentence", "paragraph", "speech"), default="sentence")
    p.add_argument("--period", type=int, help="restrict to one cycle (default: all, evenly)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument(
        "--per-speech", type=int, default=0,
        help="sample at most N units per speech (0 = all; keep 0 for speech-level roll-ups)",
    )
    p.add_argument("--min-words", type=int, default=5, help="drop units shorter than this")
    p.add_argument("--min-speech-words", type=int, default=30)
    p.add_argument("--max-units", type=int, default=4000)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="pull a sample out of the DB (no GPU needed)")
    add_sample_args(s)
    s.add_argument("-o", "--out", default="sample.jsonl")

    r = sub.add_parser("run", help="classify a sample with the poltextlab models")
    r.add_argument("-i", "--input", help="JSONL from `sample` (omit to sample from --db)")
    add_sample_args(r)
    r.add_argument("-o", "--out", default="results.jsonl")
    r.add_argument("--models", default="emotions,cap", help="comma-separated: emotions,cap")
    r.add_argument("--device", default=None, help="cuda / cpu (default: cuda if available)")
    r.add_argument("--batch-size", type=int, default=32)
    r.add_argument("--max-length", type=int, default=512)
    r.add_argument("--fp16", action="store_true", help="half precision (CUDA only)")
    r.add_argument("--topk", type=int, default=3)
    r.add_argument("--threshold", type=float, default=0.5, help="multi-label cutoff")
    r.add_argument("--windows", action="store_true", help="force sliding windows")
    r.add_argument("--stride", type=int, default=128)
    r.add_argument("--limit", type=int, help="only the first N units (smoke test)")
    r.add_argument("--cap-codebook", help="CSV of CAP minor code,name")
    r.add_argument("--no-report", action="store_true")
    r.add_argument("--examples", type=int, default=2)

    rp = sub.add_parser("report", help="summarize a results file")
    rp.add_argument("-i", "--input", default="results.jsonl")
    rp.add_argument("--examples", type=int, default=2)
    rp.add_argument("--cap-codebook")
    rp.add_argument("--csv-dir", help="also write units.csv here")

    lb = sub.add_parser("labels", help="print each model's label set (checks HF access)")
    lb.add_argument("--models", default="emotions,cap")
    lb.add_argument("--cap-codebook")

    a = ap.parse_args()

    if a.cmd == "sample":
        units, meta = sample_units(
            a.db, a.n_speeches, a.unit, a.seed, a.period, a.per_speech,
            a.min_words, a.min_speech_words, a.max_units,
        )
        write_jsonl(a.out, units)
        with open(a.out.replace(".jsonl", "") + ".meta.json", "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)
        words = sum(len(WORD_RE.findall(u["text"])) for u in units)
        print(
            f"{meta['n_units']} {a.unit} units from {meta['n_speeches']} speeches "
            f"({words} words, median {words // max(len(units), 1)} per unit) → {a.out}"
        )
        return 0

    if a.cmd == "labels":
        codebook = load_codebook(a.cap_codebook)
        for key in [m.strip() for m in a.models.split(",") if m.strip()]:
            _, model = _load(MODELS[key], "cpu", False)
            labels = _labels_of(model)
            mode = "multi-label (sigmoid)" if _multilabel(model) else "single-label (softmax)"
            print(f"\n{key}: {MODELS[key]}  —  {len(labels)} labels, {mode}")
            for i, l in enumerate(labels):
                print(f"  {i:>3}  {cap_name(l, codebook) if key == 'cap' else l}")
        return 0

    if a.cmd == "report":
        recs = read_jsonl(a.input)
        report(recs, list(MODELS), load_codebook(a.cap_codebook), a.examples, a.csv_dir)
        return 0

    # ---- run ----
    if a.input:
        units = read_jsonl(a.input)
    else:
        units, meta = sample_units(
            a.db, a.n_speeches, a.unit, a.seed, a.period, a.per_speech,
            a.min_words, a.min_speech_words, a.max_units,
        )
        print(f"sampled {meta['n_units']} units from {meta['n_speeches']} speeches")
    if a.limit:
        units = units[: a.limit]
    if not units:
        sys.exit("no units to classify")

    device = a.device
    if device is None:
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
    # A sentence never overruns 512 tokens; a paragraph or whole speech does, so
    # those go through sliding windows unless the caller says otherwise.
    unit_kind = units[0].get("unit", "sentence")
    windows = a.windows or unit_kind != "sentence"
    print(f"device={device} units={len(units)} unit={unit_kind} windows={windows} fp16={a.fp16}")

    texts = [u["text"] for u in units]
    keys = [m.strip() for m in a.models.split(",") if m.strip()]
    for key in keys:
        if key not in MODELS:
            sys.exit(f"unknown model '{key}' (known: {', '.join(MODELS)})")
        probs, labels, multi = predict(
            MODELS[key], texts, device, a.batch_size, a.max_length,
            a.fp16, windows, a.stride,
        )
        for u, p in zip(units, probs):
            d = decode(p, labels, multi, a.topk, a.threshold)
            u[f"{key}_label"] = d["label"]
            u[f"{key}_score"] = d["score"]
            u[f"{key}_top"] = d["top"]
            if "labels" in d:
                u[f"{key}_labels"] = d["labels"]

    write_jsonl(a.out, units)
    print(f"wrote {a.out}")
    if not a.no_report:
        report(units, keys, load_codebook(a.cap_codebook), a.examples,
               os.path.dirname(os.path.abspath(a.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

