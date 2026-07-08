"""Link recognized names to K-Monitor's public affairs database (NEL, primary source).

K-Monitor (a corruption-watchdog NGO) runs a sajtóadatbázis at
``adatbazis.k-monitor.hu`` that tags every article with the **persons** and
**institutions** it concerns. Each tag has a stable page —
``…/adatbazis/cimkek/<slug>`` — collecting the news about that entity. For a
civic-tech site on parliament, that is the most valuable destination we can offer
for a name spoken in the chamber, so K-Monitor is the **primary** link target
(Wikipedia is only a fallback, resolved in :mod:`app.wikidata`).

K-Monitor has no cross-reference ID to Wikidata/Felicitas, so the join is by
**name** — done here, loader-side, where we hold both the transcript's normalized
NER keys (``entity`` table) and the MP roster (``person`` table):

* :func:`load_index` fetches the two tag-list pages once and builds a normalized
  ``{name_key: [tag, ...]}`` index (institutions indexed under several surface forms
  so both "Magyar Nemzeti Bank" and "MNB" hit the same tag). Disk-cached.
* :func:`resolve_links` is the **single writer** of ``entity_link``: it merges each
  name's K-Monitor matches with the Wikidata candidates gathered by
  :func:`app.wikidata.resolve_candidates` into one ordered ``links_json``.
* :func:`resolve_representatives` sets ``person.kmonitor_url`` for the MP profile page.

Everything degrades gracefully: a failed fetch yields an empty index (names simply
stay K-Monitor-unlinked, retried next run — SCR-5); the module is gated by
``PARLAMONITOR_KMONITOR_LINKS``.
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from .config import settings

logger = logging.getLogger("parlamonitor.kmonitor")

# Bump to invalidate the on-disk tag-index cache after a parse/logic change.
_CACHE_VERSION = "km-v2"   # v2: parenthetical qualifiers no longer indexed as keys
_MAX_TAGS_PER_KEY = 5   # cap the alternatives kept per normalized name (ambiguity)
_MAX_LINKS = 4          # cap on total destinations rendered per entity

# One list entry on a tag-list page:
#   <div class="keywords-list__item"><a href="adatbazis/cimkek/<slug>">Name</a> (123)</div>
# The article-count parenthetical is optional (a few tags carry none).
_ITEM_RE = re.compile(
    r'keywords-list__item"><a href="adatbazis/cimkek/([a-z0-9-]+)">'
    r'([^<]+)</a>(?:\s*\((\d+)\))?')


def _norm(s: str) -> str:
    """Case/space-folded match key. Names carry the same accents on both sides
    (proper nouns), so folding case + collapsing whitespace is enough — accents are
    kept (folding them would merge distinct names)."""
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


def _tag_url(slug: str) -> str:
    return settings.kmonitor_base_url.rstrip("/") + "/adatbazis/cimkek/" + slug


def _looks_like_acronym(s: str) -> bool:
    """True for a short abbreviation/initialism worth indexing as an alternate name
    for a tag — "MNB", "NAV", "EU", "MTVA", "AmCham", "1MDB". False for a proper-noun
    *qualifier* a tag name carries in parentheses to disambiguate it — "(Szlovénia)",
    "(Debrecen)", "(2019)": those are NOT other names for the entity, so indexing them
    would make an unrelated country/city/year name resolve to that tag (the "Planet TV
    (Szlovénia)" ⇒ "Szlovénia" false match this guards against)."""
    compact = re.sub(r"[.\s]+", "", s)
    if not (2 <= len(compact) <= 8):
        return False
    letters = [c for c in compact if c.isalpha()]
    if not letters or not any(c.isupper() for c in letters):
        return False
    # A Titlecase word — one leading capital, the rest lowercase — is a proper noun
    # ("Szlovénia", "Debrecen", "Eximbank"), not an abbreviation.
    if len(letters) > 1 and letters[0].isupper() and all(c.islower() for c in letters[1:]):
        return False
    return True


def _institution_keys(name: str) -> set[str]:
    """The surface forms an institution tag should be findable under: the full
    display name, the name with any parentheticals removed, and the leading run
    before the first "(". A parenthetical is added as its own key only when it is a
    genuine alternate name — an acronym ("Magyar Nemzeti Bank (MNB)" → "MNB") or a
    multi-word expansion ("BKV (Budapesti Közlekedési Vállalat)" → the full phrase) —
    but NOT a single proper-noun qualifier ("Planet TV (Szlovénia)"), which would
    otherwise make the country name "Szlovénia" resolve to an unrelated media tag."""
    keys = {_norm(name)}
    base = re.sub(r"\s*\([^)]*\)\s*", " ", name).strip()
    if base:
        keys.add(_norm(base))
    lead = name.split("(", 1)[0].strip()
    if lead:
        keys.add(_norm(lead))
    for inner in re.findall(r"\(([^)]*)\)", name):
        inner = inner.strip()
        if inner and (_looks_like_acronym(inner) or len(inner.split()) >= 2):
            keys.add(_norm(inner))
    return {k for k in keys if k}


def _parse_page(text: str, kind: str) -> list[dict]:
    """Parse a tag-list page into ``[{slug, name, count, kind}]`` (deduped by slug,
    most-tagged first)."""
    by_slug: dict[str, dict] = {}
    for slug, raw_name, raw_count in _ITEM_RE.findall(text or ""):
        name = html.unescape(raw_name).strip()
        if not name:
            continue
        count = int(raw_count) if raw_count else 0
        prev = by_slug.get(slug)
        if prev is None or count > prev["count"]:
            by_slug[slug] = {"slug": slug, "name": name, "count": count, "kind": kind}
    return sorted(by_slug.values(), key=lambda t: t["count"], reverse=True)


def _build_index(persons_html: str, institutions_html: str) -> dict[str, list[dict]]:
    """Normalized ``{name_key: [tag, ...]}`` index from the two pages. A tag is
    ``{slug, name, url, count, kind}`` (kind 'person'|'org'); each key's list is
    ordered by article count (probability proxy) and capped."""
    index: dict[str, list[dict]] = {}

    def _add(key: str, tag: dict):
        bucket = index.setdefault(key, [])
        if any(t["slug"] == tag["slug"] for t in bucket):
            return
        bucket.append(tag)

    for t in _parse_page(persons_html, "person"):
        tag = {"slug": t["slug"], "name": t["name"], "url": _tag_url(t["slug"]),
               "count": t["count"], "kind": "person"}
        _add(_norm(t["name"]), tag)
    for t in _parse_page(institutions_html, "org"):
        tag = {"slug": t["slug"], "name": t["name"], "url": _tag_url(t["slug"]),
               "count": t["count"], "kind": "org"}
        for key in _institution_keys(t["name"]):
            _add(key, tag)

    for key, bucket in index.items():
        bucket.sort(key=lambda t: t["count"], reverse=True)
        del bucket[_MAX_TAGS_PER_KEY:]
    return index


def _default_fetch(url: str) -> str:
    """Fetch a tag-list page. The site 403s a bare requests UA, so send our
    descriptive one (verified to pass)."""
    import requests

    r = requests.get(url, headers={"User-Agent": settings.kmonitor_user_agent,
                                    "Accept": "text/html"}, timeout=90)
    r.raise_for_status()
    return r.text


def _cache_path(cache_dir) -> Path:
    return Path(cache_dir) / "kmonitor-tags.json"


def load_index(cache_dir=None, *, fetch=None) -> dict[str, list[dict]]:
    """The normalized K-Monitor tag index, from disk cache when present, else fetched
    (and cached). Returns ``{}`` when K-Monitor linking is disabled or a fetch fails
    (so the rest of resolution proceeds without K-Monitor)."""
    if not settings.kmonitor_links:
        return {}
    cache_path = _cache_path(cache_dir) if cache_dir else None
    if cache_path and cache_path.exists():
        try:
            loaded = json.loads(cache_path.read_text())
            if loaded.get("version") == _CACHE_VERSION and loaded.get("index"):
                return loaded["index"]
        except (OSError, ValueError):
            logger.warning("Could not read K-Monitor tag cache %s; refetching", cache_path)

    fetch = fetch or _default_fetch
    try:
        persons_html = fetch(settings.kmonitor_persons_url)
        institutions_html = fetch(settings.kmonitor_institutions_url)
    except Exception as exc:
        logger.warning("K-Monitor tag fetch failed (%s); names stay K-Monitor-unlinked "
                       "this run", exc)
        return {}

    index = _build_index(persons_html, institutions_html)
    n_person = sum(1 for b in index.values() for t in b if t["kind"] == "person")
    n_org = sum(1 for b in index.values() for t in b if t["kind"] == "org")
    logger.info("K-Monitor tag index: %d keys (%d person-tag refs, %d org-tag refs)",
                len(index), n_person, n_org)
    if cache_path:
        try:
            cache_path.write_text(json.dumps(
                {"version": _CACHE_VERSION,
                 "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "index": index}, ensure_ascii=False))
        except OSError as exc:
            logger.warning("Could not write K-Monitor tag cache %s (%s)", cache_path, exc)
    return index


def _match(index: dict, key: str, kind: str) -> list[dict]:
    """K-Monitor tags matching a normalized name key, filtered to the entity's kind
    (persons for PER, institutions for ORG), most-tagged first."""
    want = "person" if kind == "PER" else "org"
    hits = [t for t in index.get(_norm(key), []) if t["kind"] == want]
    return sorted(hits, key=lambda t: t.get("count", 0), reverse=True)


def resolve_links(conn, kinds: dict, wikidata_candidates: dict, index: dict) -> int:
    """(Re)build ``entity_link`` — the single writer — by merging, per distinct name,
    its K-Monitor matches with the Wikidata candidates into one ordered ``links_json``:

    * an internal MP ``profile`` link first, when a Wikidata candidate's QID is a
      known representative (our own richer page);
    * then K-Monitor tag links (the primary source), ordered by article count;
    * else — only when there is no K-Monitor match — Wikipedia links, ordered by
      sitelinks (the fallback).

    ``ambiguous`` marks a name that matched more than one candidate in its chosen
    source (the UI shows the alternatives as separate badges). Returns the number of
    names that got at least one link."""
    qid_to_mp = {r["wikidata_id"]: (r["person_id"], r["label"]) for r in conn.execute(
        "SELECT wikidata_id, person_id, label FROM person WHERE wikidata_id IS NOT NULL")}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    conn.execute("DELETE FROM entity_link")
    linked = 0
    for key, kind in kinds.items():
        kind = kind if kind in ("PER", "ORG") else "PER"
        km = _match(index, key, kind)
        wd = wikidata_candidates.get(key) or []

        links: list[dict] = []
        # Internal MP profile (only meaningful for people), detected via a matched
        # Wikidata QID equalling a known representative's.
        if kind == "PER":
            for c in wd:
                mp = qid_to_mp.get(c.get("wikidata_id"))
                if mp:
                    links.append({"type": "profile", "person_id": mp[0],
                                  "label": mp[1] or c.get("label")})
                    break

        if km:
            for t in km:
                links.append({"type": "kmonitor", "url": t["url"], "label": t["name"]})
            ambiguous = 1 if len(km) > 1 else 0
        else:
            for c in wd:
                links.append({"type": "wikipedia", "url": c["wikipedia_url"],
                              "label": c.get("label"), "description": c.get("description"),
                              "wikidata_id": c.get("wikidata_id")})
            ambiguous = 1 if len(wd) > 1 else 0

        links = links[:_MAX_LINKS]
        if not links:
            continue
        conn.execute(
            "INSERT INTO entity_link(entity_key, kind, ambiguous, links_json, resolved_at) "
            "VALUES (?,?,?,?,?)",
            (key, kind, ambiguous, json.dumps(links, ensure_ascii=False), now))
        linked += 1
    conn.commit()
    n_km = conn.execute("SELECT COUNT(*) FROM entity_link "
                        "WHERE links_json LIKE '%\"kmonitor\"%'").fetchone()[0]
    logger.info("entity_link: %d of %d names linked (%d with a K-Monitor tag)",
                linked, len(kinds), n_km)
    return linked


def resolve_representatives(conn, index: dict) -> int:
    """Set ``person.kmonitor_url`` on each MP whose name matches a K-Monitor person
    tag (the most-tagged one when several share the name). Clears the column first so
    a removed/renamed tag doesn't linger. Returns the number of MPs matched."""
    rows = conn.execute("SELECT person_id, label FROM person WHERE label IS NOT NULL").fetchall()
    conn.execute("UPDATE person SET kmonitor_url = NULL")
    matched = 0
    for r in rows:
        hits = [t for t in index.get(_norm(r["label"]), []) if t["kind"] == "person"]
        if not hits:
            continue
        best = max(hits, key=lambda t: t.get("count", 0))
        conn.execute("UPDATE person SET kmonitor_url = ? WHERE person_id = ?",
                     (best["url"], r["person_id"]))
        matched += 1
    conn.commit()
    logger.info("person.kmonitor_url set for %d of %d people", matched, len(rows))
    return matched
