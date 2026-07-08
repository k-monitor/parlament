"""Gather Wikidata / Wikipedia candidates for names recognized in transcripts (NEL).

The loader's :func:`app.loader.rebuild_entity_mentions` fills the ``entity`` table
with PERSON and ORGANISATION mentions, each keyed by a normalized name
(``entity_key``) and tagged ``kind`` (``PER``/``ORG``). This module resolves every
distinct name to an ordered list of Wikidata candidates — and thence Wikipedia
articles — which the K-Monitor resolver (:mod:`app.kmonitor`) then merges with
K-Monitor matches into the ``entity_link`` table the transcript reads.

Matching keeps **multiple candidates ordered by probability** (the requirements
decision): a name resolves to the humans (PER) or non-human items (ORG) that carry
that Hungarian label/alias, ranked by Wikipedia sitelink count (a notability proxy).
The top few are returned so the UI can offer the alternatives as separate badges;
Wikidata is the **fallback** source, used only where K-Monitor has no matching tag.

Results — including "no match" negatives — are cached on disk
(``wikidata-entity-cache.json``) so a re-run queries only names it has not seen
before. Any network failure degrades gracefully: the names simply stay unresolved
that run (SCR-5), to be retried next time.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import settings

logger = logging.getLogger("parlamonitor.wikidata")

# Bump to invalidate the on-disk resolution cache after a logic change.
# wdent-v2: kind-aware queries (PER humans / ORG non-humans) + multi-candidate.
_CACHE_VERSION = "wdent-v2"
_BATCH = 50            # names per SPARQL query (VALUES list)
_SLEEP = 1.0          # politeness delay between batches (also the retry backoff unit)
_RETRIES = 3          # attempts per batch before skipping it
_MAX_CONSECUTIVE_FAIL = 5   # give up the run if this many batches fail in a row
_TOP_N = 3            # candidates kept per name (ambiguous names offer alternatives)

# Common projection for both queries: the input label echoed back (?name, so
# candidates group per name), the item, its sitelink count (notability proxy), the
# hu/en article, and a short Hungarian label + description for the tooltip.
_SELECT = "SELECT ?name ?item ?sitelinks ?huArticle ?enArticle ?huLabel ?huDesc WHERE"
_COMMON = """
  ?item wikibase:sitelinks ?sitelinks .
  FILTER(?sitelinks > 0)
  OPTIONAL { ?huArticle schema:about ?item ; schema:isPartOf <https://hu.wikipedia.org/> . }
  OPTIONAL { ?enArticle schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> . }
  OPTIONAL { ?item rdfs:label ?huLabel . FILTER(LANG(?huLabel) = "hu") }
  OPTIONAL { ?item schema:description ?huDesc . FILTER(LANG(?huDesc) = "hu") }
"""

# PER: only humans (Q5) — a person name resolves to a person.
_QUERY_PER = _SELECT + " {\n  VALUES ?name { %s }\n" \
    "  ?item rdfs:label|skos:altLabel ?name .\n" \
    "  ?item wdt:P31 wd:Q5 .\n" + _COMMON + "}\n"

# ORG: anything that is NOT a human — organisations, institutions, parties, bodies.
# (A looser filter than an explicit organisation-class list, which would miss the
# many bespoke institution types; ranking by sitelinks keeps the notable one on top,
# and the result is only ever a flagged fallback where K-Monitor has no tag.)
_QUERY_ORG = _SELECT + " {\n  VALUES ?name { %s }\n" \
    "  ?item rdfs:label|skos:altLabel ?name .\n" \
    "  FILTER NOT EXISTS { ?item wdt:P31 wd:Q5 }\n" + _COMMON + "}\n"

_QUERY_BY_KIND = {"PER": _QUERY_PER, "ORG": _QUERY_ORG}


def _qid(uri: str) -> str | None:
    qid = (uri or "").rstrip("/").rsplit("/", 1)[-1]
    return qid if qid.startswith("Q") else None


def _escape(name: str) -> str:
    """A name as a Hungarian-tagged SPARQL literal for a VALUES clause."""
    return '"%s"@hu' % name.replace("\\", "\\\\").replace('"', '\\"')


def _default_fetch(names: list[str], kind: str) -> dict[str, list[dict]]:
    """Query Wikidata for a batch of ``kind`` (PER/ORG) names → ``{name:
    [candidate, ...]}``.

    A candidate is ``{qid, sitelinks, wikipedia_url, label, description}``. Raises
    on a network/HTTP error so the caller can stop and keep prior results."""
    import requests

    values = " ".join(_escape(n) for n in names)
    query = _QUERY_BY_KIND[kind] % values
    r = requests.get(
        settings.wikidata_endpoint,
        params={"query": query, "format": "json"},
        headers={"Accept": "application/sparql-results+json",
                 "User-Agent": settings.wikidata_user_agent},
        timeout=90)
    r.raise_for_status()
    out: dict[str, list[dict]] = {}
    for row in r.json().get("results", {}).get("bindings", []):
        name = (row.get("name") or {}).get("value")
        qid = _qid((row.get("item") or {}).get("value", ""))
        if not name or not qid:
            continue
        out.setdefault(name, []).append({
            "qid": qid,
            "sitelinks": int((row.get("sitelinks") or {}).get("value", 0) or 0),
            "wikipedia_url": (row.get("huArticle") or {}).get("value")
            or (row.get("enArticle") or {}).get("value"),
            "label": (row.get("huLabel") or {}).get("value"),
            "description": (row.get("huDesc") or {}).get("value"),
        })
    return out


def _top(candidates: list[dict]) -> list[dict]:
    """The most-notable distinct candidates for a name, ordered by sitelinks desc,
    capped to ``_TOP_N``. Candidates without a usable (hu/en) Wikipedia article are
    dropped — a Wikidata item with no article is no use as an inline link."""
    by_qid: dict[str, dict] = {}
    for c in candidates:
        if not c.get("wikipedia_url"):
            continue
        prev = by_qid.get(c["qid"])
        if prev is None or c["sitelinks"] > prev["sitelinks"]:
            by_qid[c["qid"]] = c
    ranked = sorted(by_qid.values(), key=lambda c: c["sitelinks"], reverse=True)
    return [{"wikidata_id": c["qid"], "wikipedia_url": c["wikipedia_url"],
             "label": c.get("label"), "description": c.get("description"),
             "sitelinks": c["sitelinks"]}
            for c in ranked[:_TOP_N]]


def _cache_path(cache_dir) -> Path:
    return Path(cache_dir) / "wikidata-entity-cache.json"


def _read_kinds(conn) -> dict[str, str]:
    """Map each distinct ``entity_key`` to its (majority) ``kind`` — fallback for a
    caller that passes no ``kinds`` (the loader computes it once and passes it in)."""
    try:
        rows = conn.execute(
            "SELECT entity_key, kind, COUNT(*) c FROM entity "
            "GROUP BY entity_key, kind").fetchall()
    except Exception:  # pre-NEL DB
        return {}
    best: dict[str, tuple[int, str]] = {}
    for key, kind, c in rows:
        kind = kind or "PER"
        if key not in best or c > best[key][0]:
            best[key] = (c, kind)
    return {k: v[1] for k, v in best.items()}


def resolve_candidates(conn, cache_dir=None, kinds=None, *, fetch=None, skip=None) -> dict[str, list[dict]]:
    """Resolve every distinct ``entity.entity_key`` to its ordered Wikidata/Wikipedia
    candidate list. Returns ``{entity_key: [candidate, ...]}`` (empty list for a name
    with no usable match); does NOT write the DB (:mod:`app.kmonitor` assembles and
    writes ``entity_link``).

    ``kinds`` maps each key to ``PER``/``ORG`` (computed once by the loader); when
    omitted it is read from the ``entity`` table. ``skip`` is a set of names not to
    query — the loader passes ORG names already matched in K-Monitor, since Wikipedia
    is only a fallback there and the unfiltered ORG query is the costly/noisy one
    (PER names are always queried, for the bounded Q5 MP-detection). ``fetch`` is the
    batch resolver (injected in tests). Returns early (``{}``) when entity linking is
    disabled or there is nothing to resolve."""
    if not settings.entity_links:
        return {}
    fetch = fetch or _default_fetch
    if kinds is None:
        kinds = _read_kinds(conn)
    if not kinds:
        return {}
    skip = skip or set()

    cache_path = _cache_path(cache_dir) if cache_dir else None
    cache: dict = {"version": _CACHE_VERSION, "keys": {}}
    if cache_path and cache_path.exists():
        try:
            loaded = json.loads(cache_path.read_text())
            if loaded.get("version") == _CACHE_VERSION:
                cache = loaded
        except (OSError, ValueError):
            logger.warning("Could not read Wikidata entity cache %s; recomputing", cache_path)

    known = cache["keys"]   # cache key "KIND::name" -> [candidate, ...]

    def _ck(kind, name):
        return f"{kind}::{name}"

    # Names to query, grouped by kind (the SPARQL differs per kind). Skipped names
    # (ORG already in K-Monitor) are neither queried nor cached.
    todo: dict[str, list[str]] = {"PER": [], "ORG": []}
    for name, kind in kinds.items():
        kind = kind if kind in _QUERY_BY_KIND else "PER"
        if name in skip:
            continue
        if _ck(kind, name) not in known:
            todo[kind].append(name)
    n_todo = sum(len(v) for v in todo.values())
    logger.info("Wikidata resolution: %d names (%d to query: %d PER, %d ORG)",
                len(kinds), n_todo, len(todo["PER"]), len(todo["ORG"]))

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    consecutive_fail = 0
    aborted = False
    for kind in ("PER", "ORG"):
        if aborted:
            break
        names = todo[kind]
        for i in range(0, len(names), _BATCH):
            batch = names[i:i + _BATCH]
            # Retry a batch a few times (WDQS throws transient 429/502s under load);
            # on persistent failure skip it — those names stay unresolved and are
            # retried next run — and give up entirely only if several batches in a
            # row fail (the endpoint is down).
            found = None
            for attempt in range(_RETRIES):
                try:
                    found = fetch(batch, kind)
                    break
                except Exception as exc:
                    wait = _SLEEP * (attempt + 1)
                    logger.warning("Wikidata query failed (%s); retry %d/%d in %.0fs",
                                   exc, attempt + 1, _RETRIES, wait)
                    time.sleep(wait)
            if found is None:
                consecutive_fail += 1
                if consecutive_fail >= _MAX_CONSECUTIVE_FAIL:
                    logger.warning("Wikidata unreachable; leaving remaining names unresolved")
                    aborted = True
                    break
                continue
            consecutive_fail = 0
            for name in batch:
                known[_ck(kind, name)] = _top(found.get(name) or [])
            if cache_path:
                try:
                    cache_path.write_text(json.dumps(cache, ensure_ascii=False))
                except OSError:
                    pass
            if i + _BATCH < len(names):
                time.sleep(_SLEEP)

    cache["resolved_at"] = now
    if cache_path:
        try:
            cache_path.write_text(json.dumps(cache, ensure_ascii=False))
        except OSError:
            pass

    result: dict[str, list[dict]] = {}
    for name, kind in kinds.items():
        kind = kind if kind in _QUERY_BY_KIND else "PER"
        result[name] = [] if name in skip else (known.get(_ck(kind, name)) or [])
    linked = sum(1 for v in result.values() if v)
    logger.info("Wikidata candidates: %d of %d names have a Wikipedia match",
                linked, len(result))
    return result
