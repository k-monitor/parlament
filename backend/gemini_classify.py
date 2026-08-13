#!/usr/bin/env python3
"""Classify transcript paragraphs into CAP minor topics with the Gemini API.

Reads a JSONL of ``{"text": "..."}`` rows — e.g. what
``export_paragraph_sample.py`` writes — and asks Gemini for the CAP minor topic
code of each one. The answer is an **array of codes in declining order of fit**:
the first is the best label, any further ones are alternatives the model
considers genuinely plausible, and ``999`` ("No Policy Content") is the answer
when nothing can be decided.

    export GEMINI_API_KEY=...
    python gemini_classify.py run -i ../exports/paragraph-sample.jsonl \\
                                  -o ../exports/paragraph-sample-cap.jsonl

This is the LLM counterpart to ``poltext_classify.py``, which runs the
fine-tuned ``poltextlab/xlm-roberta-large-pooled-cap-minor-v5`` over the same
codebook — run both on one input and the outputs are directly comparable.

Cost and safety
---------------
Nothing is sent anywhere until you run ``run`` without ``--dry-run``. Inspect
the exact request first:

    python gemini_classify.py labels                 # parse + print the codebook
    python gemini_classify.py run -i in.jsonl --dry-run    # print one payload, no call
    python gemini_classify.py run -i in.jsonl -o out.jsonl --limit 40   # small live test

``run`` is **resumable and append-only**: every finished row is flushed to the
output with its input line number as ``id``, and a re-run skips the ids already
there. Interrupt it, re-run the same command, and it picks up where it stopped
rather than paying for the whole file again.

Requests are **batched** (``--batch-size``, default 10 paragraphs per call) to
cut request count; the model must echo each item's id back, and any batch that
comes back short, misaligned or unparseable is automatically retried one
paragraph at a time so a single bad item cannot lose its 9 neighbours.

Requests are also **throttled**: ``--sleep`` (default 4 s) is the minimum gap
between two requests, enforced on a shared clock so it holds across
``--concurrency`` workers, retries and one-by-one splits alike — the run cannot
burst past one request per ``--sleep`` seconds. ``--sleep 0`` turns it off. At
the default 10-per-batch, 10k paragraphs is 1000 requests ≈ 67 minutes, which
``run`` prints up front before sending anything.

Only the standard library is used — the API is called over plain HTTPS, so
there is no ``google-genai`` install to keep in sync (neither SDK is present in
this project's environment). The key is passed in the ``x-goog-api-key`` header
rather than the URL, so it cannot leak into logs or error messages.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# --------------------------------------------------------------------------
# codebook
# --------------------------------------------------------------------------

# CAP minor topics, verbatim. Kept as text rather than a dict literal so it stays
# diffable against the published codebook; --codebook swaps in a file of the same
# shape ("(code) Major - Minor" per line).
CODEBOOK_TEXT = """
(100) Macroeconomics – General
(101) Macroeconomics – Interest Rates
(103) Macroeconomics – Unemployment Rate
(104) Macroeconomics – Monetary Policy
(105) Macroeconomics – National Budget
(107) Macroeconomics – Tax Code
(108) Macroeconomics – Industrial Policy
(110) Macroeconomics – Price Control
(199) Macroeconomics – Other
(200) Civil Rights – General
(201) Civil Rights – Minority Discrimination
(202) Civil Rights – Gender Discrimination
(204) Civil Rights – Age Discrimination
(205) Civil Rights – Handicap Discrimination
(206) Civil Rights – Voting Rights
(207) Civil Rights – Freedom of Speech
(208) Civil Rights – Right to Privacy
(209) Civil Rights – Anti-Government
(299) Civil Rights – Other
(300) Health – General
(301) Health – Health Care Reform
(302) Health – Insurance
(321) Health – Drug Industry
(322) Health – Medical Facilities
(323) Health – Insurance Providers
(324) Health – Medical Liability
(325) Health – Manpower
(331) Health – Disease Prevention
(332) Health – Infants and Children
(333) Health – Mental Health
(334) Health – Long-term Care
(335) Health – Drug Coverage and Cost
(341) Health – Tobacco Abuse
(342) Health – Drug and Alcohol Abuse
(398) Health – R&D
(399) Health – Other
(400) Agriculture – General
(401) Agriculture – Trade
(402) Agriculture – Subsidies to Farmers
(403) Agriculture – Food Inspection & Safety
(404) Agriculture – Food Marketing & Promotion
(405) Agriculture – Animal and Crop Disease
(408) Agriculture – Fisheries & Fishing
(498) Agriculture – R&D
(499) Agriculture – Other
(500) Labor – General
(501) Labor – Worker Safety
(502) Labor – Employment Training
(503) Labor – Employee Benefits
(504) Labor – Labor Unions
(505) Labor – Fair Labor Standards
(506) Labor – Youth Employment
(529) Labor – Migrant and Seasonal
(599) Labor – Other
(600) Education – General
(601) Education – Higher
(602) Education – Elementary & Secondary
(603) Education – Underprivileged
(604) Education – Vocational
(606) Education – Special
(607) Education – Excellence
(698) Education – R&D
(699) Education – Other
(700) Environment – General
(701) Environment – Drinking Water
(703) Environment – Waste Disposal
(704) Environment – Hazardous Waste
(705) Environment – Air Pollution
(707) Environment – Recycling
(708) Environment – Indoor Hazards
(709) Environment – Species & Forest
(711) Environment – Land and Water Conservation
(798) Environment – R&D
(799) Environment – Other
(800) Energy – General
(801) Energy – Nuclear
(802) Energy – Electricity
(803) Energy – Natural Gas & Oil
(805) Energy – Coal
(806) Energy – Alternative & Renewable
(807) Energy – Conservation
(898) Energy – R&D
(899) Energy – Other
(900) Immigration – Immigration
(999) No Policy Content
(1000) Transportation – General
(1001) Transportation – Mass
(1002) Transportation – Highways
(1003) Transportation – Air Travel
(1005) Transportation – Railroad Travel
(1007) Transportation – Maritime
(1010) Transportation – Infrastructure
(1098) Transportation – R&D
(1099) Transportation – Other
(1200) Law and Crime – General
(1201) Law and Crime – Agencies
(1202) Law and Crime – White Collar Crime
(1203) Law and Crime – Illegal Drugs
(1204) Law and Crime – Court Administration
(1205) Law and Crime – Prisons
(1206) Law and Crime – Juvenile Crime
(1207) Law and Crime – Child Abuse
(1208) Law and Crime – Family Issues
(1210) Law and Crime – Criminal & Civil Code
(1211) Law and Crime – Crime Control
(1227) Law and Crime – Police
(1299) Law and Crime – Other
(1300) Social Welfare – General
(1302) Social Welfare – Low-Income Assistance
(1303) Social Welfare – Elderly Assistance
(1304) Social Welfare – Disabled Assistance
(1305) Social Welfare – Volunteer Associations
(1308) Social Welfare – Child Care
(1399) Social Welfare – Other
(1400) Housing – General
(1401) Housing – Community Development
(1403) Housing – Urban Development
(1404) Housing – Rural Housing
(1405) Housing – Rural Development
(1406) Housing – Low-Income Assistance
(1407) Housing – Veterans
(1408) Housing – Elderly
(1409) Housing – Homeless
(1498) Housing – R&D
(1499) Housing – Other
(1500) Domestic Commerce – General
(1501) Domestic Commerce – Banking
(1502) Domestic Commerce – Securities & Commodities
(1504) Domestic Commerce – Consumer Finance
(1505) Domestic Commerce – Insurance Regulation
(1507) Domestic Commerce – Bankruptcy
(1520) Domestic Commerce – Corporate Management
(1521) Domestic Commerce – Small Businesses
(1522) Domestic Commerce – Copyrights and Patents
(1523) Domestic Commerce – Disaster Relief
(1524) Domestic Commerce – Tourism
(1525) Domestic Commerce – Consumer Safety
(1526) Domestic Commerce – Sports Regulation
(1598) Domestic Commerce – R&D
(1599) Domestic Commerce – Other
(1600) Defense – General
(1602) Defense – Alliances
(1603) Defense – Intelligence
(1604) Defense – Readiness
(1605) Defense – Nuclear Arms
(1606) Defense – Military Aid
(1608) Defense – Personnel Issues
(1610) Defense – Procurement
(1611) Defense – Installations & Land
(1612) Defense – Reserve Forces
(1614) Defense – Hazardous Waste
(1615) Defense – Civil
(1616) Defense – Civilian Personnel
(1617) Defense – Contractors
(1619) Defense – Foreign Operations
(1620) Defense – Claims against Military
(1698) Defense – R&D
(1699) Defense – Other
(1700) Technology – General
(1701) Technology – Space
(1704) Technology – Commercial Use of Space
(1705) Technology – Science Transfer
(1706) Technology – Telecommunications
(1707) Technology – Broadcast
(1708) Technology – Weather Forecasting
(1709) Technology – Computers
(1798) Technology – R&D
(1799) Technology – Other
(1800) Foreign Trade – General
(1802) Foreign Trade – Trade Agreements
(1803) Foreign Trade – Exports
(1804) Foreign Trade – Private Investments
(1806) Foreign Trade – Competitiveness
(1807) Foreign Trade – Tariff & Imports
(1808) Foreign Trade – Exchange Rates
(1899) Foreign Trade – Other
(1900) International Affairs – General
(1901) International Affairs – Foreign Aid
(1902) International Affairs – Resources Exploitation
(1905) International Affairs – Developing Countries
(1906) International Affairs – International Finance
(1910) International Affairs – Western Europe
(1921) International Affairs – Specific Country
(1925) International Affairs – Human Rights
(1926) International Affairs – Organizations
(1927) International Affairs – Terrorism
(1929) International Affairs – Diplomats
(1999) International Affairs – Other
(2000) Government Operations – General
(2001) Government Operations – Intergovernmental Relations
(2002) Government Operations – Bureaucracy
(2003) Government Operations – Postal Service
(2004) Government Operations – Employees
(2005) Government Operations – Appointments
(2006) Government Operations – Currency
(2007) Government Operations – Procurement & Contractors
(2008) Government Operations – Property Management
(2009) Government Operations – Tax Administration
(2010) Government Operations – Scandals
(2011) Government Operations – Branch Relations
(2012) Government Operations – Political Campaigns
(2013) Government Operations – Census & Statistics
(2014) Government Operations – Capital City
(2015) Government Operations – Claims against the Government
(2030) Government Operations – National Holidays
(2099) Government Operations – Other
(2100) Public Lands – General
(2101) Public Lands – National Parks
(2102) Public Lands – Indigenous Affairs
(2103) Public Lands – Public Lands
(2104) Public Lands – Water Resources
(2105) Public Lands – Dependencies & Territories
(2199) Public Lands – Other
(2300) Culture – General
"""

UNDECIDED = "999"
_CODE_LINE_RE = re.compile(r"^\((\d+)\)\s*(.+?)\s*$")


def parse_codebook(text: str) -> dict[str, str]:
    """``"(100) Macroeconomics – General"`` lines → ``{"100": "Macroeconomics – General"}``."""
    book: dict[str, str] = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _CODE_LINE_RE.match(line)
        if not m:
            raise ValueError(f"codebook line {lineno} is not '(code) Label': {line!r}")
        code, label = m.group(1), m.group(2)
        if code in book:
            raise ValueError(f"codebook line {lineno}: duplicate code {code}")
        book[code] = label
    if UNDECIDED not in book:
        raise ValueError(f"codebook must contain the fallback code {UNDECIDED}")
    return book


def load_codebook(path: str | None) -> dict[str, str]:
    if path:
        return parse_codebook(Path(path).read_text(encoding="utf-8"))
    return parse_codebook(CODEBOOK_TEXT)


# --------------------------------------------------------------------------
# prompt + response schema
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a political-science coder applying the Comparative Agendas Project \
(CAP) minor-topic codebook to paragraphs from the verbatim record of the \
Hungarian National Assembly (Országgyűlés). The paragraphs are in Hungarian.

For each numbered paragraph, decide which CAP minor topic the paragraph is \
ABOUT — the policy subject under discussion, not the speaker's tone, party, or \
rhetorical target.

Return, for every paragraph, an array of codes ordered by DECLINING suitability:

  * the FIRST code is the single best-fitting topic;
  * add further codes ONLY where the paragraph is genuinely ambiguous or spans \
more than one topic, most plausible first — usually that means one code, \
rarely more than three;
  * if the paragraph carries no identifiable policy content, or the topic \
simply cannot be decided (pure procedure, greetings, personal attacks, \
thanks, quorum and voting talk), return exactly ["999"].

Coding rules:
  * Use only codes from the codebook given below. Never invent a code.
  * Prefer the specific minor topic over its "General" catch-all when the \
paragraph clearly supports it; fall back to "General" for the major topic when \
it does not.
  * Judge the paragraph on its own content. Do not guess from the surrounding \
debate, which you cannot see.
  * Code what the paragraph is about even when the speaker is criticising, \
opposing or ridiculing the policy.

Answer with JSON only: one object per paragraph, echoing the paragraph's "id" \
exactly as given, with its ordered "codes" array.

CAP minor-topic codebook:
{codebook}
"""


def build_system_prompt(book: dict[str, str]) -> str:
    listing = "\n".join(f"({code}) {label}" for code, label in book.items())
    return SYSTEM_PROMPT.format(codebook=listing)


def response_schema(book: dict[str, str]) -> dict:
    """Force valid, aligned output: an id echo plus an enum-constrained code list."""
    return {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "id": {"type": "INTEGER"},
                "codes": {
                    "type": "ARRAY",
                    "items": {"type": "STRING", "enum": list(book)},
                    "minItems": 1,
                },
            },
            "required": ["id", "codes"],
            "propertyOrdering": ["id", "codes"],
        },
    }


def build_user_text(batch: list[dict]) -> str:
    parts = [
        f"Code the following {len(batch)} paragraph(s). "
        "Return one object per paragraph, with the same id.\n"
    ]
    for rec in batch:
        parts.append(f"--- id: {rec['id']} ---\n{rec['text']}\n")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Gemini REST client (stdlib only)
# --------------------------------------------------------------------------

RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}


class GeminiError(RuntimeError):
    pass


class Gemini:
    def __init__(self, api_key: str, model: str, api_version: str = "v1beta",
                 base: str = "https://generativelanguage.googleapis.com",
                 temperature: float = 0.0, thinking_budget: int | None = None,
                 max_retries: int = 5, timeout: float = 120.0,
                 sleep: float = 4.0):
        self.api_key = api_key
        self.model = model
        self.url = f"{base}/{api_version}/models/{model}:generateContent"
        self.temperature = temperature
        self.thinking_budget = thinking_budget
        self.max_retries = max_retries
        self.timeout = timeout
        self.sleep = max(0.0, sleep)
        self._rng = random.Random(0)
        self._lock = threading.Lock()
        self._next_at = 0.0   # monotonic clock the next request may start at

    def payload(self, system: str, user_text: str, schema: dict) -> dict:
        gen: dict = {
            "temperature": self.temperature,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        }
        if self.thinking_budget is not None:
            gen["thinkingConfig"] = {"thinkingBudget": self.thinking_budget}
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
            "generationConfig": gen,
        }

    def pace(self) -> None:
        """Block until ``--sleep`` seconds have passed since the last request began.

        A shared clock rather than a per-thread ``time.sleep``, so the interval
        holds across ``--concurrency`` workers too: the rate is always at most
        one request per ``sleep`` seconds, whatever the worker count. Retries and
        the one-by-one batch splits go through here as well, so a run cannot
        burst past the limit while recovering from an error.
        """
        if not self.sleep:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                if now >= self._next_at:
                    self._next_at = now + self.sleep
                    return
                wait = self._next_at - now
            time.sleep(wait)   # slept outside the lock so other workers can queue

    def generate(self, body: dict) -> dict:
        """POST with backoff on the retryable statuses. Returns the parsed response."""
        data = json.dumps(body).encode("utf-8")
        last = ""
        for attempt in range(self.max_retries + 1):
            self.pace()
            req = urllib.request.Request(
                self.url, data=data, method="POST",
                headers={"Content-Type": "application/json",
                         "x-goog-api-key": self.api_key},
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:600]
                last = f"HTTP {exc.code}: {detail}"
                if exc.code not in RETRY_STATUS or attempt == self.max_retries:
                    raise GeminiError(last) from None
                wait = exc.headers.get("retry-after")
                delay = float(wait) if wait and wait.isdigit() else 2.0 ** attempt
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                if attempt == self.max_retries:
                    raise GeminiError(last) from None
                delay = 2.0 ** attempt
            with self._lock:
                jitter = self._rng.uniform(0, 0.5)
            time.sleep(min(delay + jitter, 60.0))
        raise GeminiError(last or "exhausted retries")


def extract_json(resp: dict):
    """Pull the JSON body out of a generateContent response."""
    candidates = resp.get("candidates") or []
    if not candidates:
        fb = resp.get("promptFeedback", {})
        raise GeminiError(f"no candidates (promptFeedback={fb})")
    cand = candidates[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise GeminiError(f"empty candidate (finishReason={cand.get('finishReason')})")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise GeminiError(f"candidate is not JSON ({exc}): {text[:300]}") from None


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------

def normalize_codes(raw, book: dict[str, str]) -> tuple[list[str], bool]:
    """Keep known codes, in order, deduped. Returns (codes, had_unknown)."""
    codes: list[str] = []
    unknown = False
    for c in raw if isinstance(raw, list) else [raw]:
        code = str(c).strip()
        if code not in book:
            unknown = True
            continue
        if code not in codes:
            codes.append(code)
    if not codes:
        codes = [UNDECIDED]
    return codes, unknown


def classify_batch(client: Gemini, system: str, schema: dict,
                   batch: list[dict], book: dict[str, str]) -> dict[int, dict]:
    """One API call for a batch. Raises on misalignment so the caller can split."""
    resp = client.generate(client.payload(system, build_user_text(batch), schema))
    items = extract_json(resp)
    if not isinstance(items, list):
        raise GeminiError(f"expected a JSON array, got {type(items).__name__}")
    by_id: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict) or "id" not in item:
            continue
        try:
            rid = int(item["id"])
        except (TypeError, ValueError):
            continue
        codes, unknown = normalize_codes(item.get("codes"), book)
        by_id[rid] = {"codes": codes, "unknown_codes": unknown}
    missing = [r["id"] for r in batch if r["id"] not in by_id]
    if missing:
        raise GeminiError(f"model omitted {len(missing)} of {len(batch)} ids "
                          f"(first missing: {missing[0]})")
    usage = resp.get("usageMetadata", {})
    return {rid: dict(v, usage=usage) for rid, v in by_id.items()}


def read_jsonl(path: str, text_key: str, limit: int | None) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            text = (obj.get(text_key) or "").strip()
            if not text:
                continue
            # `id` stays the input LINE NUMBER — resume keys on it. Everything
            # else the input carried is kept in `extra` and written back out, so
            # provenance survives the round trip (a mining candidate's
            # "mined_for"/"via" would otherwise be lost and could not be rejoined).
            extra = {k: v for k, v in obj.items()
                     if k not in (text_key, "id", "codes", "model")}
            if "id" in obj and obj["id"] != i:
                extra["src_id"] = obj["id"]
            rows.append({"id": i, "text": text, "extra": extra})
            if limit and len(rows) >= limit:
                break
    return rows


def done_ids(path: Path) -> set[int]:
    if not path.exists():
        return set()
    ids: set[int] = set()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:  # a torn last line from a hard kill
                continue
            if isinstance(obj.get("id"), int) and obj.get("codes"):
                ids.add(obj["id"])
    return ids


def cmd_run(args: argparse.Namespace) -> int:
    book = load_codebook(args.codebook)
    system = build_system_prompt(book)
    schema = response_schema(book)

    rows = read_jsonl(args.input, args.text_key, args.limit)
    if not rows:
        sys.exit(f"no usable {args.text_key!r} rows in {args.input}")
    if args.max_chars:
        for r in rows:
            if len(r["text"]) > args.max_chars:
                r["text"] = r["text"][: args.max_chars].rsplit(" ", 1)[0] + " …"

    out_path = Path(args.out) if args.out else None
    already: set[int] = set()
    if out_path and out_path.exists() and not args.overwrite:
        already = done_ids(out_path)
    todo = [r for r in rows if r["id"] not in already]
    batches = [todo[i:i + args.batch_size] for i in range(0, len(todo), args.batch_size)]

    print(f"model      : {args.model}", file=sys.stderr)
    print(f"codebook   : {len(book)} codes", file=sys.stderr)
    print(f"input      : {len(rows)} rows from {args.input}"
          + (f" (skipping {len(already)} already done)" if already else ""),
          file=sys.stderr)
    print(f"to classify: {len(todo)} rows in {len(batches)} request(s) "
          f"of up to {args.batch_size}", file=sys.stderr)
    if args.sleep > 0:
        eta = len(batches) * args.sleep
        span = f"{eta / 60:.0f} min" if eta >= 60 else f"{eta:.0f}s"
        print(f"pacing     : {args.sleep:g}s between requests "
              f"(≥ {span} for {len(batches)} request(s), "
              f"before response time)", file=sys.stderr)
    else:
        print("pacing     : none (--sleep 0)", file=sys.stderr)

    if args.dry_run:
        if not batches:
            print("nothing to do", file=sys.stderr)
            return 0
        client = Gemini(api_key="DRY-RUN", model=args.model,
                        api_version=args.api_version, base=args.base,
                        temperature=args.temperature,
                        thinking_budget=args.thinking_budget)
        body = client.payload(system, build_user_text(batches[0]), schema)
        print(f"\n--- POST {client.url}\n--- headers: "
              f"Content-Type: application/json, x-goog-api-key: <from "
              f"{args.key_env}>\n--- body of request 1 of {len(batches)}:\n",
              file=sys.stderr)
        print(json.dumps(body, ensure_ascii=False, indent=2))
        print(f"\nno request was sent. Drop --dry-run to classify "
              f"{len(todo)} row(s).", file=sys.stderr)
        return 0

    if not todo:
        print("nothing to do — every row is already in the output", file=sys.stderr)
        return 0
    if not out_path:
        sys.exit("-o/--out is required unless --dry-run is given")

    api_key = os.environ.get(args.key_env, "").strip()
    if not api_key:
        sys.exit(f"{args.key_env} is not set — export your Gemini API key first")

    client = Gemini(api_key=api_key, model=args.model, api_version=args.api_version,
                    base=args.base, temperature=args.temperature,
                    thinking_budget=args.thinking_budget,
                    max_retries=args.max_retries, timeout=args.timeout,
                    sleep=args.sleep)

    text_by_id = {r["id"]: r["text"] for r in rows}
    extra_by_id = {r["id"]: r.get("extra") or {} for r in rows}
    write_lock = threading.Lock()
    counts = Counter()
    tokens = Counter()
    fh = out_path.open("w" if args.overwrite else "a", encoding="utf-8")

    def emit(rid: int, rec: dict) -> None:
        row = {"id": rid, "codes": rec["codes"], "model": args.model}
        if not args.no_carry:
            row.update(extra_by_id.get(rid) or {})
        if rec.get("unknown_codes"):
            row["unknown_codes"] = True
        if rec.get("error"):
            row["error"] = rec["error"]
        if not args.no_text:
            row["text"] = text_by_id[rid]
        with write_lock:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            counts["written"] += 1
            for c in rec["codes"]:
                counts[f"code:{c}"] += 1
            u = rec.get("usage") or {}
            tokens["prompt"] += u.get("promptTokenCount", 0) or 0
            tokens["output"] += u.get("candidatesTokenCount", 0) or 0
            tokens["total"] += u.get("totalTokenCount", 0) or 0
            if counts["written"] % args.progress_every == 0:
                print(f"  … {counts['written']}/{len(todo)} classified "
                      f"({counts['split']} batch split(s), {counts['failed']} failed)",
                      file=sys.stderr)

    def handle(batch: list[dict]) -> None:
        try:
            for rid, rec in classify_batch(client, system, schema, batch, book).items():
                emit(rid, rec)
            return
        except GeminiError as exc:
            if len(batch) == 1:
                rid = batch[0]["id"]
                with write_lock:
                    counts["failed"] += 1
                print(f"  ! id {rid} failed: {exc}", file=sys.stderr)
                emit(rid, {"codes": [UNDECIDED], "error": str(exc)})
                return
            with write_lock:
                counts["split"] += 1
            print(f"  ~ batch of {len(batch)} retried one by one: {exc}",
                  file=sys.stderr)
        for rec in batch:  # split: one call per paragraph
            handle([rec])

    started = time.time()
    try:
        if args.concurrency > 1:
            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                list(pool.map(handle, batches))
        else:
            for batch in batches:
                handle(batch)
    except KeyboardInterrupt:
        print("\ninterrupted — re-run the same command to resume", file=sys.stderr)
    finally:
        fh.close()

    elapsed = time.time() - started
    print(f"\nwrote {counts['written']} row(s) to {out_path} in {elapsed:.0f}s"
          f"  (batch splits: {counts['split']}, failed: {counts['failed']})",
          file=sys.stderr)
    if tokens["total"]:
        print(f"tokens: prompt {tokens['prompt']:,}  output {tokens['output']:,}  "
              f"total {tokens['total']:,}", file=sys.stderr)
    top = [(c[5:], n) for c, n in counts.most_common() if c.startswith("code:")][:10]
    if top:
        print("top codes: " + ", ".join(
            f"{code} {book.get(code, '?')} ({n})" for code, n in top), file=sys.stderr)
    return 1 if counts["failed"] else 0


# --------------------------------------------------------------------------
# labels / report
# --------------------------------------------------------------------------

def cmd_labels(args: argparse.Namespace) -> int:
    book = load_codebook(args.codebook)
    for code, label in book.items():
        print(f"({code}) {label}")
    print(f"\n{len(book)} codes; fallback = ({UNDECIDED}) {book[UNDECIDED]}",
          file=sys.stderr)
    if args.prompt:
        print("\n--- system prompt ---\n", file=sys.stderr)
        print(build_system_prompt(book))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    book = load_codebook(args.codebook)
    rows = [json.loads(l) for l in Path(args.input).read_text(encoding="utf-8").splitlines() if l.strip()]
    if not rows:
        sys.exit(f"{args.input} is empty")
    first = Counter(r["codes"][0] for r in rows if r.get("codes"))
    n_multi = sum(1 for r in rows if len(r.get("codes") or []) > 1)
    n_err = sum(1 for r in rows if r.get("error"))
    print(f"{len(rows)} rows — {n_multi} with more than one code, "
          f"{first[UNDECIDED]} undecided ({UNDECIDED}), {n_err} errored\n")
    width = max(len(book.get(c, "?")) for c in first) if first else 10
    for code, n in first.most_common(args.top):
        bar = "█" * max(1, round(40 * n / first.most_common(1)[0][1]))
        print(f"  {code:>4}  {book.get(code, '?'):<{width}}  {n:>6}  {bar}")
    if len(first) > args.top:
        print(f"  … and {len(first) - args.top} more codes")
    return 0


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="classify a JSONL of texts with the Gemini API")
    r.add_argument("-i", "--input", required=True, help="JSONL with a text field per row")
    r.add_argument("-o", "--out", help="output JSONL (append/resume; required unless --dry-run)")
    r.add_argument("--model", default="gemini-3.5-flash-lite")
    r.add_argument("--text-key", default="text", help="input field to classify")
    r.add_argument("--batch-size", type=int, default=10,
                   help="paragraphs per request (default 10; 1 = one call each)")
    r.add_argument("--concurrency", type=int, default=1, help="parallel requests")
    r.add_argument("--sleep", type=float, default=4.0,
                   help="seconds between API requests, enforced across all workers "
                        "(default 4; 0 disables the throttle)")
    r.add_argument("--limit", type=int, help="only the first N rows (smoke test)")
    r.add_argument("--max-chars", type=int, default=0,
                   help="truncate a paragraph longer than this (0 = never)")
    r.add_argument("--temperature", type=float, default=0.0)
    r.add_argument("--thinking-budget", type=int, default=None,
                   help="thinkingConfig.thinkingBudget; omitted entirely if unset")
    r.add_argument("--max-retries", type=int, default=5)
    r.add_argument("--timeout", type=float, default=120.0)
    r.add_argument("--overwrite", action="store_true",
                   help="truncate the output instead of resuming into it")
    r.add_argument("--no-text", action="store_true", help="write codes without the text")
    r.add_argument("--no-carry", action="store_true",
                   help="drop the input's extra fields instead of copying them through")
    r.add_argument("--dry-run", action="store_true",
                   help="print the first request and exit without calling the API")
    r.add_argument("--progress-every", type=int, default=200)
    r.add_argument("--key-env", default="GEMINI_API_KEY", help="env var holding the API key")
    r.add_argument("--api-version", default="v1beta")
    r.add_argument("--base", default="https://generativelanguage.googleapis.com")
    r.add_argument("--codebook", help="override the built-in codebook with a file")
    r.set_defaults(func=cmd_run)

    lb = sub.add_parser("labels", help="print the codebook (and optionally the prompt)")
    lb.add_argument("--codebook")
    lb.add_argument("--prompt", action="store_true", help="also print the system prompt")
    lb.set_defaults(func=cmd_labels)

    rp = sub.add_parser("report", help="summarize a results file")
    rp.add_argument("-i", "--input", required=True)
    rp.add_argument("--top", type=int, default=25)
    rp.add_argument("--codebook")
    rp.set_defaults(func=cmd_report)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
