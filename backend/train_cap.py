#!/usr/bin/env python3
"""Finetune huBERT for multi-label CAP topic classification of transcript paragraphs.

Trains ``SZTAKI-HLT/hubert-base-cc`` on the Gemini-coded paragraphs written by
``gemini_classify.py`` — rows of ``{"text": ..., "codes": ["206", "200", ...]}``.
Every code on a row is a positive label, so this is genuine **multi-label**
classification (a sigmoid head + BCE loss), not single-label softmax: 61% of the
coded rows carry more than one CAP topic.

Two steps, deliberately split so only the second one needs a GPU:

    # anywhere (numpy + stdlib only — no torch)
    python train_cap.py split -i ../exports/paragraph-class.jsonl -o capdata

    # on the 3060 box, after copying capdata/ and this file across
    python train_cap.py train -d capdata -o runs/hubert-cap

``split`` writes ``train/val/test.jsonl`` plus a ``labels.json`` that pins the
label space, so the training box needs nothing but those files. ``train``
evaluates on **validation** after every epoch, keeps the best checkpoint by
validation micro-F1, and only at the very end touches **test** — once, with the
decision threshold that validation chose.

Fitting a 12 GB card
--------------------
huBERT is BERT-base (110M params, 512-token limit). At the defaults —
``--max-length 256 --batch-size 16 --fp16`` — a step needs roughly 4 GB, so a
12 GB 3060 has plenty of room; ``--batch-size 32 --max-length 512`` still fits
in about 9 GB. Padding is dynamic (to the longest text in each batch), and the
coded paragraphs are short (median 519 characters, ~95% under 1100), so most
batches are far below the cap. Use ``--grad-accum`` if you drop the batch size
and want to keep the effective one.

Dependencies are deliberately minimal — ``pip install torch transformers numpy``.
No ``datasets``, ``accelerate`` or ``sklearn``: the training loop, the splitter
and the metrics are all written out here, which is a few dozen extra lines in
exchange for not breaking when those libraries rename an argument.

The long tail
-------------
176 distinct codes appear in 5000 rows, but ~18 of them appear once or twice —
too few to place in three splits at all. ``--min-label-count`` (default 3) drops
those from the label space; a row keeps its surviving codes, and is dropped only
if it has none left. Set it to 1 to train on every code as-is.

Splitting is **iteratively stratified** (Sechidis et al. 2011) over the full
label sets rather than on one label per row, which keeps rare codes proportional
across train/val/test — plain random splitting strands them in one split.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

DEFAULT_MODEL = "SZTAKI-HLT/hubert-base-cc"
UNDECIDED = "999"


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

def read_rows(path: str, text_key: str, codes_key: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for ln, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            text = (obj.get(text_key) or "").strip()
            codes = obj.get(codes_key) or []
            if not text or not codes:
                continue
            rows.append({
                "id": obj.get("id", ln),
                "text": text,
                "codes": [str(c) for c in codes],
                "mined": bool(obj.get("mined")),
            })
    return rows


def label_names(labels: list[str]) -> dict[str, str]:
    """Resolve code → human label, from the sibling codebook when importable."""
    try:
        from gemini_classify import load_codebook
        book = load_codebook(None)
    except Exception:
        return {c: c for c in labels}
    return {c: book.get(c, c) for c in labels}


def major_of(code: str, names: dict[str, str]) -> str:
    """CAP major topic, taken from the codebook name so 999 stays its own group."""
    name = names.get(code, code)
    if "–" in name:
        return name.split("–")[0].strip()
    if name != code:
        return name                       # "No Policy Content", "Immigration"
    return code[:-2] or code              # numeric fallback: no codebook available


def build_label_space(rows: list[dict], min_count: int) -> tuple[list[str], Counter]:
    counts = Counter(c for r in rows for c in r["codes"])
    labels = sorted((c for c, n in counts.items() if n >= min_count), key=int)
    return labels, counts


def apply_label_space(rows: list[dict], labels: list[str]) -> tuple[list[dict], int]:
    keep = set(labels)
    out, dropped = [], 0
    for r in rows:
        codes = [c for c in r["codes"] if c in keep]
        if not codes:
            dropped += 1
            continue
        out.append({**r, "codes": codes})
    return out, dropped


def targets(rows, label2id: dict[str, int], secondary_weight: float) -> np.ndarray:
    """Binary (or soft) target matrix.

    ``codes`` is ordered by the coder's declining confidence, so
    ``--secondary-weight`` below 1.0 turns the trailing alternatives into soft
    positives — the primary code stays 1.0 and BCE handles fractional targets
    directly. At the default 1.0 this is a plain multi-label indicator matrix.
    """
    y = np.zeros((len(rows), len(label2id)), dtype=np.float32)
    for i, r in enumerate(rows):
        for rank, c in enumerate(r["codes"]):
            j = label2id.get(c)
            if j is None:
                continue
            y[i, j] = max(y[i, j], 1.0 if rank == 0 else secondary_weight)
    return y


# --------------------------------------------------------------------------
# iterative stratification (Sechidis, Tsoumakas & Vlahavas 2011)
# --------------------------------------------------------------------------

def iterative_stratify(label_sets: list[set[int]], n_labels: int,
                       proportions: list[float], seed: int = 0) -> list[int]:
    """Assign each sample to a fold, keeping every label's share proportional.

    Repeatedly takes the *rarest* label still unplaced and gives one of its
    samples to whichever fold is furthest below its quota for that label —
    which is what keeps a code with three examples from landing entirely in
    test, as a random split routinely does.
    """
    n, k = len(label_sets), len(proportions)
    rng = random.Random(seed)

    need_fold = [p * n for p in proportions]
    label_count = [0] * n_labels
    for s in label_sets:
        for i in s:
            label_count[i] += 1
    need = [[label_count[i] * p for p in proportions] for i in range(n_labels)]

    remaining = [set() for _ in range(n_labels)]
    for idx, s in enumerate(label_sets):
        for i in s:
            remaining[i].add(idx)

    assignment = [-1] * n
    pending = {i for i, s in enumerate(label_sets) if s}

    while pending:
        active = [i for i in range(n_labels) if remaining[i]]
        if not active:
            break
        # rarest remaining label; ties broken by label id for determinism
        lbl = min(active, key=lambda i: (len(remaining[i]), i))
        idx = rng.choice(sorted(remaining[lbl]))

        cands = [j for j in range(k) if need[lbl][j] == max(need[lbl])]
        if len(cands) > 1:
            top = max(need_fold[j] for j in cands)
            cands = [j for j in cands if need_fold[j] == top]
        fold = cands[0] if len(cands) == 1 else rng.choice(cands)

        assignment[idx] = fold
        for i in label_sets[idx]:
            remaining[i].discard(idx)
            need[i][fold] -= 1
        need_fold[fold] -= 1
        pending.discard(idx)

    for idx in range(n):                       # samples that carried no label
        if assignment[idx] < 0:
            fold = max(range(k), key=lambda j: need_fold[j])
            assignment[idx] = fold
            need_fold[fold] -= 1
    return assignment


def make_splits(rows, labels, ratios, seed, strategy="iterative"):
    label2id = {c: i for i, c in enumerate(labels)}
    if strategy == "iterative":
        sets = [{label2id[c] for c in r["codes"] if c in label2id} for r in rows]
        folds = iterative_stratify(sets, len(labels), ratios, seed)
    else:
        idx = list(range(len(rows)))
        random.Random(seed).shuffle(idx)
        n_tr = int(ratios[0] * len(rows))
        n_va = int(ratios[1] * len(rows))
        folds = [0] * len(rows)
        for rank, i in enumerate(idx):
            folds[i] = 0 if rank < n_tr else (1 if rank < n_tr + n_va else 2)
    out = ([], [], [])
    for r, f in zip(rows, folds):
        out[f].append(r)
    return out


# --------------------------------------------------------------------------
# metrics (numpy only)
# --------------------------------------------------------------------------

def _prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def score(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """Micro / macro / per-sample precision, recall, F1 for binary indicators."""
    t, p = y_true.astype(bool), y_pred.astype(bool)
    tp = (t & p).sum(0).astype(float)
    fp = (~t & p).sum(0).astype(float)
    fn = (t & ~p).sum(0).astype(float)

    mi_p, mi_r, mi_f = _prf(tp.sum(), fp.sum(), fn.sum())
    per = [_prf(tp[i], fp[i], fn[i]) for i in range(y_true.shape[1])]
    support = t.sum(0)
    seen = support > 0
    ma_f = float(np.mean([per[i][2] for i in range(len(per)) if seen[i]])) if seen.any() else 0.0

    stp = (t & p).sum(1).astype(float)
    sp = p.sum(1).astype(float)
    st = t.sum(1).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        s_p = np.where(sp > 0, stp / np.maximum(sp, 1), 0.0)
        s_r = np.where(st > 0, stp / np.maximum(st, 1), 0.0)
        s_f = np.where(s_p + s_r > 0, 2 * s_p * s_r / np.maximum(s_p + s_r, 1e-9), 0.0)
    return {
        "micro_precision": mi_p, "micro_recall": mi_r, "micro_f1": mi_f,
        "macro_f1": ma_f,
        "samples_precision": float(s_p.mean()), "samples_recall": float(s_r.mean()),
        "samples_f1": float(s_f.mean()),
        "subset_accuracy": float((t == p).all(1).mean()),
        "hamming_loss": float((t != p).mean()),
        "labels_predicted_per_sample": float(sp.mean()),
        "labels_true_per_sample": float(st.mean()),
    }


def binarize(probs: np.ndarray, threshold, min_labels: int = 1) -> np.ndarray:
    """Threshold, then force at least ``min_labels`` predictions per row.

    Every paragraph carries at least one code, so an all-zero row is always
    wrong; taking the argmax back is free recall.
    """
    thr = np.asarray(threshold, dtype=np.float32).reshape(1, -1)
    pred = (probs >= thr).astype(np.int8)
    if min_labels > 0:
        order = np.argsort(-probs, axis=1)
        for i in range(probs.shape[0]):
            need = min_labels - int(pred[i].sum())
            for j in order[i][:max(0, need)]:
                pred[i, j] = 1
    return pred


def tune_threshold(y_true, probs, min_labels=1, grid=None) -> tuple[float, float]:
    """Pick the single global threshold that maximizes micro-F1 on this split."""
    grid = grid if grid is not None else np.arange(0.05, 0.96, 0.025)
    best_t, best_f = 0.5, -1.0
    for t in grid:
        f = score(y_true, binarize(probs, float(t), min_labels))["micro_f1"]
        if f > best_f:
            best_t, best_f = float(t), f
    return best_t, best_f


def tune_per_label(y_true, probs, base: float, min_support: int = 10) -> np.ndarray:
    """Per-label thresholds for labels with enough validation support."""
    thr = np.full(probs.shape[1], base, dtype=np.float32)
    grid = np.arange(0.05, 0.96, 0.025)
    for j in range(probs.shape[1]):
        t_col = y_true[:, j].astype(bool)
        if t_col.sum() < min_support:
            continue
        best_t, best_f = base, -1.0
        for t in grid:
            p_col = probs[:, j] >= t
            tp = float((t_col & p_col).sum())
            f = _prf(tp, float((~t_col & p_col).sum()), float((t_col & ~p_col).sum()))[2]
            if f > best_f:
                best_t, best_f = float(t), f
        thr[j] = best_t
    return thr


def primary_ids(rows, label2id: dict[str, int]) -> np.ndarray:
    """Index of each row's FIRST code — the coder's best-fit label.

    Taken from the row rather than from ``argmax`` of the target matrix, which
    at the default ``--secondary-weight 1.0`` ties every code of a row at 1.0
    and would silently return the lowest label id instead.
    """
    out = np.zeros(len(rows), dtype=np.int64)
    for i, r in enumerate(rows):
        for c in r["codes"]:
            if c in label2id:
                out[i] = label2id[c]
                break
    return out


def full_report(y_true, probs, threshold, labels, names, min_labels=1,
                primary=None) -> dict:
    pred = binarize(probs, threshold, min_labels)
    m = score(y_true, pred)

    if primary is None:                              # falls back to any true label
        primary = y_true.argmax(1)
    top1 = probs.argmax(1)
    m["primary_top1_accuracy"] = float((top1 == primary).mean())
    m["primary_in_predicted"] = float(pred[np.arange(len(pred)), primary].mean())

    groups = sorted({major_of(c, names) for c in labels})
    gidx = {g: i for i, g in enumerate(groups)}
    G = np.zeros((len(labels), len(groups)), dtype=np.float32)
    for j, c in enumerate(labels):
        G[j, gidx[major_of(c, names)]] = 1.0
    gt = (y_true.astype(bool).astype(np.float32) @ G) > 0
    gp = (pred.astype(np.float32) @ G) > 0
    gm = score(gt.astype(np.int8), gp.astype(np.int8))
    m["major_micro_f1"] = gm["micro_f1"]
    m["major_macro_f1"] = gm["macro_f1"]
    m["major_top1_accuracy"] = float(
        (G[top1].argmax(1) == G[primary].argmax(1)).mean())
    return m


def per_label_rows(y_true, probs, threshold, labels, names, min_labels=1):
    pred = binarize(probs, threshold, min_labels)
    t, p = y_true.astype(bool), pred.astype(bool)
    thr = np.asarray(threshold, dtype=np.float32).reshape(-1)
    out = []
    for j, code in enumerate(labels):
        tp = float((t[:, j] & p[:, j]).sum())
        fp = float((~t[:, j] & p[:, j]).sum())
        fn = float((t[:, j] & ~p[:, j]).sum())
        pr, rc, f1 = _prf(tp, fp, fn)
        out.append({
            "code": code, "name": names.get(code, code),
            "major": major_of(code, names),
            "support": int(t[:, j].sum()), "predicted": int(p[:, j].sum()),
            "precision": round(pr, 4), "recall": round(rc, 4), "f1": round(f1, 4),
            "threshold": round(float(thr[j] if thr.size > 1 else thr[0]), 3),
        })
    out.sort(key=lambda r: (-r["support"], r["code"]))
    return out


# --------------------------------------------------------------------------
# split command
# --------------------------------------------------------------------------

def cmd_split(args) -> int:
    rows = read_rows(args.input, args.text_key, args.codes_key)
    if not rows:
        sys.exit(f"no usable rows in {args.input}")
    # Verbatim-repeated paragraphs (formulaic chairing announcements: "a
    # zárószavazásra jövő heti ülésünkön kerül sor") would otherwise put the same
    # text in two splits and quietly inflate the score.
    n_dup = 0
    if args.dedupe:
        seen_text, unique = set(), []
        for r in rows:
            key = re.sub(r"\s+", " ", r["text"]).strip().casefold()
            if key in seen_text:
                n_dup += 1
                continue
            seen_text.add(key)
            unique.append(r)
        rows = unique

    labels, counts = build_label_space(rows, args.min_label_count)
    if not labels:
        sys.exit("label space is empty — lower --min-label-count")
    rows, dropped = apply_label_space(rows, labels)
    names = label_names(labels)

    ratios = [args.train_ratio, args.val_ratio,
              round(1.0 - args.train_ratio - args.val_ratio, 6)]
    if min(ratios) <= 0:
        sys.exit(f"bad split ratios {ratios}")
    # Retrieved (mined) rows are a biased sample — they exist because they matched
    # a query. Splitting them across val/test would measure how well the model
    # reproduces the retrieval, not how it does on real transcript text, and would
    # read as a large but fictitious gain. They go to train only; val and test stay
    # drawn from the uniform sample.
    mined = [r for r in rows if r.get("mined")] if args.mined_train_only else []
    natural = [r for r in rows if not r.get("mined")] if args.mined_train_only else rows
    tr, va, te = make_splits(natural, labels, ratios, args.seed, args.split_strategy)
    tr = tr + mined

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, part in (("train", tr), ("val", va), ("test", te)):
        with (out / f"{name}.jsonl").open("w", encoding="utf-8") as fh:
            for r in part:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    label2id = {c: i for i, c in enumerate(labels)}
    cov = {}
    for name, part in (("train", tr), ("val", va), ("test", te)):
        c = Counter(x for r in part for x in r["codes"])
        cov[name] = {"rows": len(part), "labels_present": len(c)}
    missing_in_train = [c for c in labels
                        if not any(c in r["codes"] for r in tr)]

    meta = {
        "labels": labels,
        "names": names,
        "label_counts": {c: counts[c] for c in labels},
        "min_label_count": args.min_label_count,
        "dropped_labels": sorted((set(counts) - set(labels)), key=int),
        "dropped_rows_without_labels": dropped,
        "duplicate_texts_removed": n_dup,
        "mined_rows_forced_into_train": len(mined),
        "mined_train_only": args.mined_train_only,
        "split_strategy": args.split_strategy,
        "ratios": ratios,
        "seed": args.seed,
        "coverage": cov,
        "labels_absent_from_train": missing_in_train,
        "source": str(args.input),
    }
    (out / "labels.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    print(f"rows            : {len(rows)} usable "
          f"({dropped} dropped for having only rare codes"
          + (f", {n_dup} exact-duplicate text(s) removed" if n_dup else "") + ")")
    print(f"labels          : {len(labels)} kept (min count {args.min_label_count}), "
          f"{len(meta['dropped_labels'])} dropped")
    print(f"split           : train {len(tr)} / val {len(va)} / test {len(te)}  "
          f"[{args.split_strategy}]")
    if mined:
        print(f"mined rows      : {len(mined)} forced into train "
              f"(val/test stay a uniform sample)")
    for name in ("train", "val", "test"):
        print(f"  {name:<5} {cov[name]['rows']:>5} rows, "
              f"{cov[name]['labels_present']:>3}/{len(labels)} labels present")
    if missing_in_train:
        print(f"WARNING: {len(missing_in_train)} label(s) absent from train: "
              f"{', '.join(missing_in_train[:10])}")
    print(f"wrote           : {out}/train.jsonl, val.jsonl, test.jsonl, labels.json")
    return 0


# --------------------------------------------------------------------------
# train command (torch imported lazily so `split` stays dependency-free)
# --------------------------------------------------------------------------

# Both of these live at module level on purpose. DataLoader workers are started
# with 'forkserver' on Python 3.14+ (it was 'fork' before), which PICKLES the
# dataset and collate_fn — a class defined inside cmd_train is a local object and
# cannot be pickled. Neither subclasses torch's Dataset, so that this module still
# imports without torch and `split` keeps working on a machine that has none;
# DataLoader only needs __len__/__getitem__ for a map-style dataset.

class _Rows:
    def __init__(self, rows, y):
        self.rows, self.y = rows, y

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        return self.rows[i]["text"], self.y[i]


class _Collate:
    """Tokenize a batch, padding only to the longest text in it."""

    def __init__(self, tok, max_length):
        self.tok, self.max_length = tok, max_length

    def __call__(self, batch):
        import torch
        texts = [b[0] for b in batch]
        ys = np.stack([b[1] for b in batch])
        enc = self.tok(texts, truncation=True, max_length=self.max_length,
                       padding=True, return_tensors="pt")
        enc["labels"] = torch.from_numpy(ys)
        return enc



def cmd_train(args) -> int:
    import torch
    from torch.utils.data import DataLoader
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    from transformers import get_linear_schedule_with_warmup

    data = Path(args.data)
    meta = json.loads((data / "labels.json").read_text(encoding="utf-8"))
    labels: list[str] = meta["labels"]
    names: dict[str, str] = meta.get("names", {c: c for c in labels})
    label2id = {c: i for i, c in enumerate(labels)}

    parts = {}
    for name in ("train", "val", "test"):
        parts[name] = read_rows(str(data / f"{name}.jsonl"), "text", "codes")
        if not parts[name]:
            sys.exit(f"{data/f'{name}.jsonl'} is empty — run `split` first")
        if args.limit_rows:
            parts[name] = parts[name][: args.limit_rows]
    if args.limit_rows:
        print(f"SMOKE TEST: --limit-rows {args.limit_rows} — metrics are meaningless")

    seed = args.seed
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    if device == "cuda" and not torch.cuda.is_available():
        sys.exit("--device cuda but torch reports no CUDA device")
    print(f"device      : {device}"
          + (f" ({torch.cuda.get_device_name(0)}, "
             f"{torch.cuda.get_device_properties(0).total_memory/2**30:.1f} GiB)"
             if device == "cuda" else ""))
    print(f"model       : {args.model}")
    print(f"labels      : {len(labels)}")
    print(f"rows        : train {len(parts['train'])} / val {len(parts['val'])} "
          f"/ test {len(parts['test'])}")

    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.model,
        num_labels=len(labels),
        problem_type="multi_label_classification",
        id2label={i: c for c, i in label2id.items()},
        label2id=label2id,
    ).to(device)

    Y = {k: targets(v, label2id, args.secondary_weight) for k, v in parts.items()}
    PRIM = {k: primary_ids(v, label2id) for k, v in parts.items()}

    Rows, collate = _Rows, _Collate(tok, args.max_length)

    loaders = {
        "train": DataLoader(Rows(parts["train"], Y["train"]), batch_size=args.batch_size,
                            shuffle=True, collate_fn=collate, num_workers=args.workers,
                            drop_last=False, pin_memory=(device == "cuda")),
        "val": DataLoader(Rows(parts["val"], Y["val"]), batch_size=args.eval_batch_size,
                          shuffle=False, collate_fn=collate, num_workers=args.workers),
        "test": DataLoader(Rows(parts["test"], Y["test"]), batch_size=args.eval_batch_size,
                           shuffle=False, collate_fn=collate, num_workers=args.workers),
    }

    pos_weight = None
    if args.pos_weight == "balanced":
        pos = Y["train"].sum(0)
        w = (len(Y["train"]) - pos) / np.maximum(pos, 1.0)
        pos_weight = torch.tensor(np.clip(w, 1.0, args.pos_weight_cap),
                                  dtype=torch.float32, device=device)
        print(f"pos_weight  : balanced, capped at {args.pos_weight_cap} "
              f"(median {float(np.median(pos_weight.cpu().numpy())):.1f})")
    loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight)

    decay = [p for n, p in model.named_parameters()
             if p.requires_grad and not any(k in n for k in ("bias", "LayerNorm.weight"))]
    no_decay = [p for n, p in model.named_parameters()
                if p.requires_grad and any(k in n for k in ("bias", "LayerNorm.weight"))]
    optim = torch.optim.AdamW(
        [{"params": decay, "weight_decay": args.weight_decay},
         {"params": no_decay, "weight_decay": 0.0}], lr=args.lr)

    # ceil, not floor: the loop also steps on the last partial accumulation group,
    # so a floor here would run the scheduler past total_steps and flatten the LR
    steps_per_epoch = max(1, math.ceil(len(loaders["train"]) / args.grad_accum))
    total_steps = steps_per_epoch * args.epochs
    sched = get_linear_schedule_with_warmup(
        optim, int(args.warmup_ratio * total_steps), total_steps)

    dev_type = device.split(":")[0]           # GradScaler/autocast want "cuda", not "cuda:0"
    amp_dtype = torch.bfloat16 if args.bf16 else (torch.float16 if args.fp16 else None)
    scaler = torch.amp.GradScaler(dev_type, enabled=(amp_dtype == torch.float16))
    print(f"precision   : {'bf16' if args.bf16 else 'fp16' if args.fp16 else 'fp32'}"
          f"  |  effective batch {args.batch_size * args.grad_accum}"
          f"  |  {total_steps} optimizer steps")

    @torch.no_grad()
    def infer(loader):
        model.eval()
        out, gold = [], []
        for batch in loader:
            gold.append(batch.pop("labels").numpy())
            batch = {k: v.to(device, non_blocking=True) for k, v in batch.items()}
            if amp_dtype is not None:
                with torch.autocast(device_type=dev_type, dtype=amp_dtype):
                    logits = model(**batch).logits
            else:
                logits = model(**batch).logits
            out.append(torch.sigmoid(logits.float()).cpu().numpy())
        return np.concatenate(out), np.concatenate(gold)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    history, best = [], {"micro_f1": -1.0, "epoch": -1, "state": None, "threshold": 0.5}
    started = time.time()

    for epoch in range(1, args.epochs + 1):
        model.train()
        running, n_seen, t0 = 0.0, 0, time.time()
        optim.zero_grad(set_to_none=True)
        for step, batch in enumerate(loaders["train"], 1):
            y = batch.pop("labels").to(device, non_blocking=True)
            batch = {k: v.to(device, non_blocking=True) for k, v in batch.items()}
            if amp_dtype is not None:
                with torch.autocast(device_type=dev_type, dtype=amp_dtype):
                    logits = model(**batch).logits
                loss = loss_fn(logits.float(), y)
            else:
                loss = loss_fn(model(**batch).logits, y)
            scaler.scale(loss / args.grad_accum).backward()
            running += loss.item() * len(y)
            n_seen += len(y)
            if step % args.grad_accum == 0 or step == len(loaders["train"]):
                scaler.unscale_(optim)
                torch.nn.utils.clip_grad_norm_(model.parameters(), args.max_grad_norm)
                scaler.step(optim)
                scaler.update()
                optim.zero_grad(set_to_none=True)
                sched.step()

        probs, gold = infer(loaders["val"])
        thr, _ = tune_threshold((gold > 0).astype(np.int8), probs, args.min_labels)
        m = full_report((gold > 0).astype(np.int8), probs, thr, labels, names,
                        args.min_labels, PRIM['val'])
        m.update(epoch=epoch, train_loss=running / max(n_seen, 1),
                 threshold=thr, seconds=round(time.time() - t0, 1))
        history.append(m)
        mem = (f", {torch.cuda.max_memory_allocated()/2**30:.1f} GiB peak"
               if device == "cuda" else "")
        print(f"epoch {epoch:>2}/{args.epochs}  loss {m['train_loss']:.4f}  "
              f"val micro-F1 {m['micro_f1']:.4f}  macro-F1 {m['macro_f1']:.4f}  "
              f"top-1 {m['primary_top1_accuracy']:.4f}  major-F1 {m['major_micro_f1']:.4f}  "
              f"@thr {thr:.3f}  ({m['seconds']:.0f}s{mem})")

        if m["micro_f1"] > best["micro_f1"]:
            best = {"micro_f1": m["micro_f1"], "epoch": epoch, "threshold": thr,
                    "state": {k: v.detach().cpu().clone()
                              for k, v in model.state_dict().items()}}
        elif args.patience and epoch - best["epoch"] >= args.patience:
            print(f"early stop: no val improvement for {args.patience} epoch(s)")
            break

    if best["state"] is None:
        sys.exit("training produced no checkpoint")
    print(f"\nbest epoch  : {best['epoch']} (val micro-F1 {best['micro_f1']:.4f}), "
          f"trained in {time.time()-started:.0f}s")
    model.load_state_dict(best["state"])

    # The two ways this run can be quietly wrong: it never plateaued, or the head
    # collapsed to predicting almost nothing. Both look like a finished run.
    if best["epoch"] == args.epochs and len(history) >= 2:
        gain = history[-1]["micro_f1"] - history[-2]["micro_f1"]
        print(f"NOTE: the best epoch was the LAST one and val micro-F1 was still "
              f"rising ({gain:+.4f} on the final epoch) — this run is epoch-limited, "
              f"not converged. Re-run with more --epochs.")
    last = history[-1]
    if last["labels_predicted_per_sample"] < 0.75 * last["labels_true_per_sample"]:
        print(f"NOTE: predicting {last['labels_predicted_per_sample']:.2f} label(s) "
              f"per row against {last['labels_true_per_sample']:.2f} true, at a "
              f"threshold of {last['threshold']:.3f}. With {len(labels)} labels the "
              f"positive rate is ~"
              f"{100 * last['labels_true_per_sample'] / len(labels):.1f}%, and plain "
              f"BCE tends to collapse toward predicting nothing at that sparsity — "
              f"try --pos-weight balanced.")

    # thresholds are chosen on validation only, then applied unchanged to test
    val_probs, val_gold = infer(loaders["val"])
    val_gold = (val_gold > 0).astype(np.int8)
    thr_global, _ = tune_threshold(val_gold, val_probs, args.min_labels)
    thr = thr_global
    if args.per_label_threshold:
        thr = tune_per_label(val_gold, val_probs, thr_global, args.threshold_min_support)
        print(f"thresholds  : global {thr_global:.3f}; per-label tuned for "
              f"{int((thr != thr_global).sum())} label(s)")
    else:
        print(f"threshold   : {thr_global:.3f} (tuned on validation)")

    val_m = full_report(val_gold, val_probs, thr, labels, names, args.min_labels,
                        PRIM['val'])
    test_probs, test_gold = infer(loaders["test"])
    test_gold = (test_gold > 0).astype(np.int8)
    test_m = full_report(test_gold, test_probs, thr, labels, names, args.min_labels,
                         PRIM['test'])

    print("\n--- test (held out until now) ---")
    for k in ("micro_f1", "macro_f1", "samples_f1", "subset_accuracy",
              "primary_top1_accuracy", "primary_in_predicted",
              "major_micro_f1", "major_top1_accuracy",
              "labels_predicted_per_sample", "labels_true_per_sample"):
        print(f"  {k:<28} {test_m[k]:.4f}")

    model.save_pretrained(out_dir / "model")
    tok.save_pretrained(out_dir / "model")
    thr_out = float(thr) if np.ndim(thr) == 0 else [round(float(x), 4) for x in thr]
    (out_dir / "metrics.json").write_text(json.dumps({
        "model": args.model, "labels": labels, "names": names,
        "best_epoch": best["epoch"], "threshold": thr_out,
        "min_labels": args.min_labels,
        "args": {k: v for k, v in vars(args).items() if k != "func"},
        "history": history, "validation": val_m, "test": test_m,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    rep = per_label_rows(test_gold, test_probs, thr, labels, names, args.min_labels)
    with (out_dir / "test-per-label.csv").open("w", encoding="utf-8") as fh:
        fh.write("code,name,major,support,predicted,precision,recall,f1,threshold\n")
        for r in rep:
            fh.write(f'{r["code"]},"{r["name"]}","{r["major"]}",{r["support"]},'
                     f'{r["predicted"]},{r["precision"]},{r["recall"]},{r["f1"]},'
                     f'{r["threshold"]}\n')

    pred = binarize(test_probs, thr, args.min_labels)
    with (out_dir / "test-predictions.jsonl").open("w", encoding="utf-8") as fh:
        for i, row in enumerate(parts["test"]):
            order = np.argsort(-test_probs[i])
            fh.write(json.dumps({
                "id": row["id"],
                "gold": row["codes"],
                "pred": [labels[j] for j in order if pred[i, j]],
                "scores": {labels[j]: round(float(test_probs[i, j]), 4)
                           for j in order[:5]},
                "text": row["text"],
            }, ensure_ascii=False) + "\n")

    print(f"\nsaved       : {out_dir}/model, metrics.json, test-per-label.csv, "
          f"test-predictions.jsonl")
    return 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("split", help="build stratified train/val/test (no torch)")
    s.add_argument("-i", "--input", required=True, help="JSONL from gemini_classify.py")
    s.add_argument("-o", "--out", default="capdata", help="directory to write")
    s.add_argument("--text-key", default="text")
    s.add_argument("--codes-key", default="codes")
    s.add_argument("--min-label-count", type=int, default=3,
                   help="drop codes rarer than this from the label space (1 = keep all)")
    s.add_argument("--train-ratio", type=float, default=0.70)
    s.add_argument("--val-ratio", type=float, default=0.15)
    s.add_argument("--split-strategy", choices=("iterative", "random"),
                   default="iterative")
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--dedupe", action="store_true", default=True,
                   help="drop verbatim-repeated paragraphs before splitting")
    s.add_argument("--no-dedupe", dest="dedupe", action="store_false")
    s.add_argument("--mined-train-only", action="store_true", default=True,
                   help="rows tagged \"mined\": true go to train only (default), so\n"
                        "val/test stay a uniform sample and metrics stay honest")
    s.add_argument("--mined-anywhere", dest="mined_train_only", action="store_false",
                   help="let mined rows into val/test too — inflates the scores")
    s.set_defaults(func=cmd_split)

    t = sub.add_parser("train", help="finetune huBERT on the splits (needs a GPU)")
    t.add_argument("-d", "--data", default="capdata", help="directory from `split`")
    t.add_argument("-o", "--out", default="runs/hubert-cap")
    t.add_argument("--model", default=DEFAULT_MODEL)
    t.add_argument("--epochs", type=int, default=8)
    t.add_argument("--batch-size", type=int, default=16)
    t.add_argument("--eval-batch-size", type=int, default=64)
    t.add_argument("--grad-accum", type=int, default=1)
    t.add_argument("--max-length", type=int, default=256)
    t.add_argument("--lr", type=float, default=2e-5)
    t.add_argument("--weight-decay", type=float, default=0.01)
    t.add_argument("--warmup-ratio", type=float, default=0.1)
    t.add_argument("--max-grad-norm", type=float, default=1.0)
    t.add_argument("--patience", type=int, default=3,
                   help="stop after N epochs without val micro-F1 gain (0 = never)")
    t.add_argument("--secondary-weight", type=float, default=1.0,
                   help="target for non-primary codes (1.0 = plain multi-label)")
    t.add_argument("--pos-weight", choices=("none", "balanced"), default="none",
                   help="BCE positive-class weighting for rare labels")
    t.add_argument("--pos-weight-cap", type=float, default=10.0)
    t.add_argument("--min-labels", type=int, default=1,
                   help="force at least N predictions per row (every row has >=1 code)")
    t.add_argument("--per-label-threshold", action="store_true",
                   help="tune a threshold per label on validation, not one global")
    t.add_argument("--threshold-min-support", type=int, default=10)
    t.add_argument("--fp16", action="store_true", default=True)
    t.add_argument("--no-fp16", dest="fp16", action="store_false")
    t.add_argument("--bf16", action="store_true", help="use bf16 instead of fp16")
    t.add_argument("--device", default=None)
    t.add_argument("--limit-rows", type=int, default=0,
                   help="truncate every split to N rows — end-to-end smoke test")
    t.add_argument("--workers", type=int, default=0,
                   help="DataLoader worker processes; 0 (default) tokenizes\n"
                        "inline, which is cheap for short paragraphs and\n"
                        "avoids worker start-up entirely")
    t.add_argument("--seed", type=int, default=42)
    t.set_defaults(func=cmd_train)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
