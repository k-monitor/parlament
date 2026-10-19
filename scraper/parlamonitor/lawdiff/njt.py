"""The Nemzeti Jogszabálytár (njt.hu): time states of a law, read as units (BILL-14).

njt is the official consolidated text of Hungarian law, free and without login
for every time state — past, current and the *future* ones a promulgated
amendment has already scheduled (Gov. Decree 338/2011 obliges it to publish
those within three working days of promulgation). Three things make it usable
as data rather than as a page:

* **Every unit is addressable.** The law is server-rendered HTML in which each
  section, paragraph, point and sub-point is a ``<div>`` preceded by
  ``<span class="jhId" id="SZ26@BE4@POA">`` — § 26 (4) a). The id grammar is
  ``SZ<§>`` (``14/D. §`` → ``SZ14D``) ``@BE<paragraph>`` ``@PO<point>``
  ``@AP<sub-point>``, with ``FE<roman>`` for a chapter and ``AC<n>`` for a
  subtitle; a § without numbered paragraphs keeps its points under ``BE0``.
  A paragraph's *closing* text (``bekezdesZaro``) carries no id of its own and
  is filed here as ``<paragraph>@ZARO``.
* **Every time state is listed.** ``POST /ajax/collectAllDocumentVersion.json``
  (form body ``documentId=…``; a GET is refused with 405) returns each
  version's validity window; ``/jogszabaly/<id>.<version>`` serves that state.
* **Every change says where it came from.** A unit's footnote names the act
  that set its current text and links that act's own provision
  (``<a href="jogszabaly/2026-55-00-00" data-jhid="SZ2@BE1">``), which is how
  an adopted bill's changes are attributed without parsing the bill at all.

One trap: a long law is **truncated** in the page — past a point the units are
``div.pH.borderStart`` placeholders the reader's browser fills on scroll — and
one ``POST /ajax/njtGetBlock.json`` returns all of the rest. The page itself
already carries every footnote body, the deferred blocks' included.

robots.txt allows ``/jogszabaly/*`` and disallows only ``/search/*``.
"""

from __future__ import annotations

import json
import logging
import lzma
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from ..http_client import HttpClient

logger = logging.getLogger(__name__)

BASE = "https://njt.jog.gov.hu"
# What a reader is sent to: the short, well-known host (it redirects to BASE and
# keeps the path, and the browser keeps the #fragment).
PUBLIC_BASE = "https://njt.hu"

# How long to wait before asking again for a page njt served without its text.
EMPTY_RETRY_WAIT = 10.0

# Bumped whenever `parse` changes what it extracts, so cached parses are redone
# from the stored page instead of being trusted.
# 11: sub-points of a numbered point (``betusAlPont``), provisions set as
#    slices (``szelet`` with a § id) and unnumbered subtitles (``tagoloCim``)
#    are units; a § body njt
#    numbers internally ("886") is addressed as the §'s ``@BE0``, a chapter's
#    title as the chapter's ``@CIM``; ids are canonical (``canonical``).
PARSE_VERSION = 11

NODE_CLASSES = frozenset({
    "szakasz", "bekezdesNyito", "betusPontNyito", "szamosPontNyito",
    "ketbetusAlPont", "betusAlPont", "bekezdesZaro", "pontZaro", "alcim", "fejezet",
    "tagoloCim", "tagolo", "focim", "focimCim", "konyv", "konyvcim",
    "fejezetCim", "resz", "reszcim", "preambulum"})
_POINT_CLASSES = frozenset({"bekezdesNyito", "betusPontNyito", "szamosPontNyito"})
# A heading's title is the unit after it, with no id of its own.
_TITLE_OF = {"fejezetCim": "fejezet", "reszcim": "resz", "focimCim": "focim",
             "konyvcim": "konyv"}
_UNTITLED_SECTION = re.compile(r"^((?:\d+:)?\d+(?:/[A-Z])?\. §)\s*(.*)$")


class BudgetExhausted(Exception):
    """This pass has spent its share of requests; the rest waits for the next."""


class Unavailable(Exception):
    """njt answered, but not with the law: now and then it serves the page
    shell with an empty body (a 200 of ~38 kB instead of the text). Never
    cached; the bill waits for a later pass."""


class Budget:
    """A per-pass cap on network requests, shared by every fetcher of a pass so
    a first run over a whole cycle is paced over several (SCR-4)."""

    def __init__(self, limit: int | None):
        self.limit = limit if limit and limit > 0 else None
        self.spent = 0

    def spend(self, n: int = 1) -> None:
        if self.limit is not None and self.spent + n > self.limit:
            raise BudgetExhausted()
        self.spent += n

    @property
    def exhausted(self) -> bool:
        return self.limit is not None and self.spent >= self.limit


def doc_id(year: int | str, number: int | str) -> str:
    """njt's id for an Act (törvény): ``2019. évi LXXX.`` → ``2019-80-00-00``."""
    return f"{int(year)}-{int(number)}-00-00"


def law_url(doc: str, version: int | None = None, jh: str | None = None) -> str:
    """A link to a law (optionally one time state, optionally one unit)."""
    url = f"{PUBLIC_BASE}/jogszabaly/{doc}"
    if version is not None:
        url += f".{version}"
    return url + (f"#{jh}" if jh else "")


# --- parsing ----------------------------------------------------------------

def _squash(parts: list[str]) -> str:
    return re.sub(r"\s+", " ", "".join(parts).replace("\xa0", " ")).strip()


class _LawParser(HTMLParser):
    """One pass over a law page (or its deferred blocks): the addressable units
    in order, the footnote bodies, and the law's title.

    Unit ``div``s are siblings, so a unit ends at the ``</div>`` that balances
    its opening one; inside it, text under a footnote marker (``sup.fnSup``) is
    left out and text under the label spans (``span.jel`` /
    ``span.szakasz-jel``, which nest for an untitled §) is collected apart."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.raw_units: list[dict] = []
        self.notes: dict[str, dict] = {}
        self._pending_jh: str | None = None
        self._unit: dict | None = None
        self._note: dict | None = None
        self._divs = 0                  # nested divs inside the open unit/note
        self._spans: list[bool] = []    # open spans: is it a label span?
        self._sups: list[bool] = []     # open sups: is it a footnote marker?
        self._label_done = False

    # -- helpers
    @staticmethod
    def _classes(attrs: dict) -> set[str]:
        return set((attrs.get("class") or "").split())

    def _text_sink(self) -> dict | None:
        return self._unit if self._unit is not None else self._note

    # -- events
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = self._classes(a)
        sink = self._text_sink()
        if sink is not None:
            if tag == "div":
                self._divs += 1
            elif tag == "span":
                is_label = (self._unit is not None and not self._label_done
                            and bool(cls & {"jel", "szakasz-jel"}))
                self._spans.append(is_label)
            elif tag == "sup":
                marker = "fnSup" in cls
                self._sups.append(marker)
                if marker and self._unit is not None and a.get("data-note-id"):
                    self._unit["fn"].append(a["data-note-id"])
                if self._note is not None and not self._note["text"]:
                    # the footnote's own number, before its text
                    self._sups[-1] = True
            elif tag == "a" and self._note is not None:
                m = re.search(r"jogszabaly/([0-9]{4}-[0-9]+-[0-9]{2}-[0-9]{2})",
                              a.get("href") or "")
                if m:
                    # Where in the note the link ends is where the words saying
                    # what that act did begin (one note can name several acts).
                    self._note["links"].append({"doc": m.group(1), "jh": a.get("data-jhid"),
                                                "open": True,
                                                "start": len("".join(self._note["text"]))})
            elif tag in ("br", "p", "td", "tr", "li"):
                sink["text"].append(" ")
            return
        if tag == "meta" and a.get("name") == "description" and a.get("content"):
            self.title = a["content"].strip()
        elif tag == "span" and "jhId" in cls:
            self._pending_jh = a.get("id")
        elif tag == "div":
            ident = a.get("id") or ""
            unit_cls = cls & NODE_CLASSES
            if not unit_cls and "szelet" in cls and (self._pending_jh or "").startswith("SZ"):
                # some laws set their provisions as "slices" (Mttv. 203. §);
                # an annex's slices have numbers or "RR…" ids and stay out
                unit_cls = {_slice_class(self._pending_jh)}
            if unit_cls and ident.startswith("sc"):
                self._unit = {"jh": self._pending_jh, "cls": next(iter(unit_cls)),
                              "text": [], "label": [], "fn": [], "sc": ident}
                self._pending_jh = None
                self._divs = 0
                self._label_done = False
            elif "footnote" in cls and ident:
                self._note = {"id": ident, "text": [], "links": []}
                self._divs = 0

    def handle_startendtag(self, tag, attrs):
        if tag == "br" and self._text_sink() is not None:
            self._text_sink()["text"].append(" ")
        elif tag == "meta":
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        sink = self._text_sink()
        if sink is None:
            return
        if tag == "div":
            if self._divs:
                self._divs -= 1
                return
            if self._unit is not None:
                self.raw_units.append(self._unit)
                self._unit = None
            else:
                self.notes[self._note["id"]] = _note(self._note)
                self._note = None
            self._spans.clear(); self._sups.clear()
        elif tag == "span" and self._spans:
            was_label = self._spans.pop()
            if was_label and not any(self._spans):
                self._label_done = True
        elif tag == "sup" and self._sups:
            self._sups.pop()
        elif tag == "a" and self._note is not None and self._note["links"] \
                and self._note["links"][-1].get("open"):
            link = self._note["links"][-1]
            link.pop("open")
            link["at"] = len("".join(self._note["text"]))
        elif tag in ("p", "td", "li"):
            sink["text"].append(" ")

    def handle_data(self, data):
        sink = self._text_sink()
        if sink is None or any(self._sups):
            return
        sink["text"].append(data)
        if self._unit is not None and any(self._spans):
            self._unit["label"].append(data)


def _slice_class(jh: str) -> str:
    """The class a provision set as a ``szelet`` would have, from its id."""
    last = jh.split("@")[-1]
    if last.startswith("BE"):
        return "bekezdesNyito"
    if last.startswith("AP"):
        return "ketbetusAlPont"
    if last.startswith("PO"):
        return "szamosPontNyito" if last[2:3].isdigit() else "betusPontNyito"
    return "szakasz" if "@" not in jh else "bekezdesNyito"


def _note(raw: dict) -> dict:
    """A footnote: its text and every act it names, each with what that act did
    (``says``: the words between its link and the next one) — "megállapította
    a 2025. évi XCV. törvény …, nyitó szövegrészét módosította a 2026. évi LV.
    törvény …" names two acts and two different changes."""
    joined = "".join(raw["text"])
    links = [l for l in raw["links"] if "at" in l]
    out = []
    for i, l in enumerate(links):
        # The verb usually follows the act ("a <act> …-ával megállapított",
        # "a <act> … iktatta be") and sometimes leads it ("Hatályon kívül
        # helyezte: <act>"), so both sides are kept, each only up to the
        # neighbouring act.
        after = joined[l["at"]: links[i + 1]["start"] if i + 1 < len(links) else len(joined)]
        before = joined[links[i - 1]["at"] if i else 0: l["start"]]
        out.append({"doc": l["doc"], "jh": l.get("jh"),
                    "after": _squash([after]), "before": _squash([before])})
    return {"text": _squash(raw["text"]), "links": out}


def _feed(text: str) -> _LawParser:
    p = _LawParser()
    p.feed(text)
    p.close()
    return p


def parse(page: str, blocks: str = "") -> dict:
    """A law page (plus its deferred blocks) as ``{title, units}``.

    Units come in document order as ``{jh, cls, label, text, fn}`` where ``fn``
    lists the footnotes on the unit (``{text, doc, jh}``) — the provenance of its
    current wording."""
    main = _feed(page)
    raw = list(main.raw_units)
    if blocks.strip():
        raw += _feed(blocks).raw_units

    units: list[dict] = []
    last_jh = None
    for r in raw:
        cls = r["cls"]
        label = _squash(r["label"])
        full = _squash(r["text"])
        text = full[len(label):].strip() if label and full.startswith(label) else full
        if cls.endswith("Zaro"):
            # A closing part belongs to the paragraph (bekezdesZaro) or to the
            # point (pontZaro) that its list hangs off, not to the last item.
            cut = "@AP" if cls == "pontZaro" else "@PO"
            jh = (last_jh or "").split(cut)[0] + "@ZARO"
        else:
            jh = r["jh"]
            prev = units[-1] if units else None
            if jh == "TA":
                # an unnumbered subtitle njt left without its number: named
                # after its element, as njt names the others ("TA739")
                jh = "TA" + r["sc"].rsplit("-", 1)[-1]
            if jh and jh.isdigit():
                # njt's internal number, not an address: the body of a §
                # without numbered paragraphs (its "@BE0"), or a chapter's title
                jh = None
                if prev and prev["jh"] and prev["cls"] == "szakasz":
                    jh = f"{prev['jh']}@BE0"
                elif prev and prev["jh"] and prev["cls"] in ("fejezet", "resz"):
                    jh = f"{prev['jh']}@CIM"
            elif not jh and cls in _TITLE_OF and prev and prev["jh"] \
                    and prev["cls"] == _TITLE_OF[cls]:
                jh = f"{prev['jh']}@CIM"    # "II. Fejezet" is one unit, its title the next
            if cls == "szakasz" or (cls in _POINT_CLASSES and jh and "@" in jh):
                last_jh = jh
        raw_jh = r["jh"]
        jh = canonical(jh)
        # An untitled § carries its number inside its first paragraph's label
        # ("145. § (1)"); it belongs to the section.
        m = _UNTITLED_SECTION.match(label)
        if m and cls != "szakasz":
            for prev in reversed(units):
                if prev["cls"] == "szakasz":
                    prev["label"] = prev["label"] or m.group(1)
                    break
            label = m.group(2)
        unit = {"jh": jh, "cls": cls, "label": label, "text": text,
                "fn": [main.notes[f] for f in r["fn"] if f in main.notes]}
        if raw_jh and raw_jh != jh:
            unit["anchor"] = raw_jh      # what the page itself calls it
        units.append(unit)
    return {"title": main.title, "units": units}


def canonical(jh: str | None) -> str | None:
    """One id per unit. njt files the points of a § without numbered
    paragraphs under ``SZ4@BE0@POF`` or ``SZ4@POF`` — both, now and then
    within one § — and a bill cites them as ``4. § f) pont``, so the ``@BE0``
    is dropped below the §'s own opening text (which stays ``SZ4@BE0``). A
    stray dot or bracket some ids carry ("…@AP24.", "…@APZSI)") goes too, and
    so does the slash of a "28/b." point."""
    if not jh:
        return jh
    jh = re.sub(r"[.)]+(?=@|$)", "", re.sub(r"^([^@]+)@BE0@", r"\1@", jh))
    return re.sub(r"@PO(\d+)/([A-Z])", r"@PO\1\2", jh)     # "PO28/B" as the bill cites it


def deferred_blocks(page: str) -> list[dict]:
    """The request body items for the units the page left as placeholders."""
    out = []
    for attrs in re.findall(r'<div id="sc[^"]+" class="pH borderStart"([^>]*)>', page):
        st = re.search(r'data-show-order="(\d+)"', attrs)
        la = re.search(r'data-last-show-order="(\d+)"', attrs)
        if st:
            out.append({"start": int(st.group(1)),
                        "last": int(la.group(1)) if la else None})
    return out


# --- unit helpers -------------------------------------------------------------

def subtree(units: list[dict], jh: str) -> list[dict]:
    return [u for u in units if u["jh"] == jh or (u["jh"] or "").startswith(jh + "@")]


def flatten(units: list[dict], jh: str) -> str:
    """A unit and everything under it as one line of text, labels included —
    the form both sides of a change are compared and shown in. Label-only
    leftovers (a repealed point njt still lists) are dropped, and a unit with
    nothing but labels left reads as empty: a repealed § is gone, and one
    whose number is refilled is new."""
    sub = subtree(units, jh)
    # a chapter's number is its text ("III. Fejezet"): it stays when the
    # chapter is repealed, as a § keeps its bare number
    if not any(u["text"] for u in sub if u["cls"] != "fejezet"):
        return ""
    parts = [f"{u['label']} {u['text']}".strip() for u in sub
             if u["text"] or (u["cls"] == "szakasz" and u["label"])]
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


_LABEL_PARTS = re.compile(r"^(SZ|BE|PO|AP|FE|AC|RE)((?:\d+:)?\d*)([A-Z]*)$")


def unit_label(jh: str) -> str:
    """Human label of a unit id: ``SZ14D@BE4A@POB`` → ``14/D. § (4a) bekezdés b) pont``."""
    out = []
    for part in (jh or "").split("@"):
        if part == "ZARO":
            out.append("záró szövegrész")
            continue
        if part == "CIM":
            out.append("címe")
            continue
        if part == "PR":
            out.append("preambulum")
            continue
        if re.fullmatch(r"TA\d+", part):      # a subtitle without a number
            out.append("alcím")
            continue
        if re.fullmatch(r"FC\d+", part):
            out.append(f"{part[2:]}. Cím")
            continue
        if part.startswith("KO") and part[2:].isalpha():
            out.append(f"{part[2:].capitalize()} Könyv")
            continue
        if part.startswith("RE") and part[2:].isalpha() and not re.fullmatch(r"[IVXLCDM]+", part[2:]):
            out.append(f"{part[2:].capitalize()} Rész")
            continue
        m = _LABEL_PARTS.match(part)
        if not m:
            continue
        kind, num, let = m.groups()
        if kind == "SZ":
            out.append(f"{num}{'/' + let if let else ''}. §")
        elif kind == "BE":
            if num != "0":
                out.append(f"({num}{let.lower()}) bekezdés")
        elif kind == "PO":
            out.append(f"{num}{let.lower()}. pont" if num else f"{let.lower()}) pont")
        elif kind == "AP":
            out.append(f"{num}. alpont" if num else f"{let.lower()}) alpont")
        elif kind == "FE":
            out.append(f"{let}. Fejezet")
        elif kind == "AC":
            out.append(f"{num}{'/' + let if let else ''}. alcím")
        elif kind == "RE":
            out.append(f"{let or num}. Rész")
    return " ".join(out)


# --- the store ----------------------------------------------------------------

def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


class NjtStore:
    """Fetches njt time states on demand and keeps them on disk.

    A state that was in force (or past) when fetched never changes, so its page
    is fetched once and kept (xz-compressed — a long law's page is over a
    megabyte, its xz a tenth of that); the parse is cached beside it under
    ``PARSE_VERSION`` so a parser change re-parses from disk instead of
    refetching. The version *list* goes stale — a newly promulgated amendment
    adds a state — so it is refetched once older than ``versions_ttl`` seconds.

    A **future** state is not final: njt regenerates it in place, under the
    same version number, when a later act amends the law before it starts (the
    Szja's state from 2027-01-01, an old id, cites a mid-2026 act). So each
    page is kept with the version ids njt listed when it was fetched
    (``v<n>.meta.json``), and a page of a state that was still to come is
    fetched again once the law's list holds an id it did not."""

    def __init__(self, http: HttpClient, root: Path, *, budget: Budget,
                 versions_ttl: float = 24 * 3600):
        self.http = http
        self.root = Path(root)
        self.budget = budget
        self.versions_ttl = versions_ttl
        self._mem: dict[tuple[str, int], dict] = {}

    def _dir(self, doc: str) -> Path:
        return self.root / doc

    def versions(self, doc: str, *, max_age: float | None = None) -> list[dict]:
        """The law's time states, newest first, from cache when fresh enough."""
        f = self._dir(doc) / "versions.json"
        ttl = self.versions_ttl if max_age is None else max_age
        cached = None
        if f.exists():
            try:
                cached = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = None
        if cached and _now() - cached.get("fetchedAt", 0) <= ttl:
            return cached["data"]
        try:
            self.budget.spend()
        except BudgetExhausted:
            if cached:      # stale is better than nothing when out of budget
                return cached["data"]
            raise
        body = self.http.post_text(f"{BASE}/ajax/collectAllDocumentVersion.json",
                                   data={"documentId": doc},
                                   headers={"X-Requested-With": "XMLHttpRequest"})
        self.http.polite_sleep()
        try:
            data = json.loads(body).get("data") or []
        except ValueError:
            logger.warning("njt %s: version list was not JSON", doc)
            data = []
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({"fetchedAt": _now(), "fetchedAtIso": _iso(_now()),
                                   "data": data}, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(f)
        return data

    def law(self, doc: str, version: int, *, offline: bool = False) -> dict | None:
        """One time state, parsed: ``{docId, version, title, units}``.

        ``offline`` reads the disk cache only — a state never fetched is None
        rather than a request, and a cached one is taken as it is — and does not
        keep the state in memory (a bulk read of many laws, one at a time)."""
        key = (doc, int(version))
        if key in self._mem:
            return self._mem[key]
        d = self._dir(doc)
        parsed_f = d / f"v{int(version)}.parsed.json.xz"
        refresh = not offline and self.stale(doc, int(version))
        if parsed_f.exists() and not refresh:
            try:
                cached = json.loads(lzma.decompress(parsed_f.read_bytes()))
                if cached.get("parseVersion") == PARSE_VERSION:
                    if not offline:
                        self._mem[key] = cached
                    return cached
            except (OSError, ValueError, lzma.LZMAError):
                pass
        got = self._page(doc, int(version), offline=offline, refresh=refresh)
        if got is None:
            return None
        page, blocks = got
        parsed = {"docId": doc, "version": int(version), "parseVersion": PARSE_VERSION,
                  **parse(page, blocks)}
        d.mkdir(parents=True, exist_ok=True)
        tmp = parsed_f.with_suffix(".tmp")
        tmp.write_bytes(lzma.compress(json.dumps(parsed, ensure_ascii=False)
                                      .encode("utf-8")))
        tmp.replace(parsed_f)
        if not offline:
            self._mem[key] = parsed
        return parsed

    # -- a future state's freshness

    def _listed(self, doc: str) -> list[dict]:
        """The version list as last fetched — never a request."""
        try:
            return json.loads((self._dir(doc) / "versions.json")
                              .read_text(encoding="utf-8"))["data"]
        except (OSError, ValueError, KeyError, TypeError):
            return []

    def _meta(self, doc: str, version: int) -> dict | None:
        try:
            return json.loads((self._dir(doc) / f"v{version}.meta.json")
                              .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def fetched_at(self, doc: str, version: int) -> str | None:
        """When the cached page of a state was fetched (ISO), if it is cached."""
        meta = self._meta(doc, version)
        if meta:
            return meta.get("fetchedAt")
        page_f = self._dir(doc) / f"v{version}.html.xz"
        return _iso(page_f.stat().st_mtime) if page_f.exists() else None

    def stale(self, doc: str, version: int) -> bool:
        """Whether a cached page may be out of date: its state was still to come
        when it was fetched, and the law has gained a state since (njt then
        regenerates the future ones in place). A page cached before this was
        tracked is taken as stale once, if its state was to come then."""
        fetched = self.fetched_at(doc, version)
        if fetched is None:
            return False
        listed = self._listed(doc)
        entry = next((v for v in listed if v.get("version") == version), None)
        if entry is None or (entry.get("comingIntoForce") or "")[:10] <= fetched[:10]:
            return False
        meta = self._meta(doc, version)
        if meta is None:
            return True
        return bool({v.get("version") for v in listed} - set(meta.get("knownVersions") or []))

    # -- pages

    def _fetch_page(self, doc: str, version: int) -> str:
        for attempt in range(2):
            self.budget.spend()
            page = self.http.get_text(f"{BASE}/jogszabaly/{doc}.{version}")
            self.http.polite_sleep()
            if 'class="jhId"' in page:
                return page
            if not attempt:
                logger.info("njt %s.%s came back without its text; retrying", doc, version)
                time.sleep(EMPTY_RETRY_WAIT)
        raise Unavailable(f"njt {doc}.{version}: no law text in the page")

    def _fetch_blocks(self, doc: str, version: int, wanted: list[dict]) -> str:
        self.budget.spend()
        blocks = self.http.post_text(
            f"{BASE}/ajax/njtGetBlock.json",
            json_body={"documentId": f"{doc}.{version}", "data": wanted},
            headers={"X-Requested-With": "XMLHttpRequest"})
        self.http.polite_sleep()
        return blocks

    def _page(self, doc: str, version: int, *, offline: bool = False,
              refresh: bool = False) -> tuple[str, str] | None:
        d = self._dir(doc)
        page_f = d / f"v{version}.html.xz"
        blocks_f = d / f"v{version}.blocks.html.xz"
        if page_f.exists() and not refresh:
            page = lzma.decompress(page_f.read_bytes()).decode("utf-8")
            wanted = deferred_blocks(page)
            if not wanted:
                return page, ""
            if blocks_f.exists():
                return page, lzma.decompress(blocks_f.read_bytes()).decode("utf-8")
            if offline:
                return None
            blocks = self._fetch_blocks(doc, version, wanted)
            blocks_f.write_bytes(lzma.compress(blocks.encode("utf-8")))
            return page, blocks
        if offline:
            return None
        # A state read whole — its page and deferred blocks — is kept only
        # once both are in hand, so a refreshed page never pairs with old blocks.
        page = self._fetch_page(doc, version)
        wanted = deferred_blocks(page)
        blocks = self._fetch_blocks(doc, version, wanted) if wanted else ""
        d.mkdir(parents=True, exist_ok=True)
        page_f.write_bytes(lzma.compress(page.encode("utf-8")))
        if wanted:
            blocks_f.write_bytes(lzma.compress(blocks.encode("utf-8")))
        elif blocks_f.exists():
            blocks_f.unlink()
        entry = next((v for v in self._listed(doc) if v.get("version") == version), {})
        (d / f"v{version}.meta.json").write_text(json.dumps({
            "fetchedAt": _iso(_now()), "comingIntoForce": entry.get("comingIntoForce"),
            "knownVersions": sorted(v.get("version") for v in self._listed(doc)
                                    if v.get("version") is not None)}), encoding="utf-8")
        return page, blocks


# --- choosing time states -----------------------------------------------------

def real_versions(versions: list[dict]) -> list[dict]:
    """Drop the gazette state (as first published) — it is not a time state."""
    return [v for v in versions if not v.get("gazetteState")]


def latest_version(versions: list[dict]) -> dict | None:
    """The future-most known state: the law as it will read once everything
    already promulgated is in force."""
    vs = real_versions(versions)
    return max(vs, key=lambda v: (v.get("comingIntoForce") or "", v["version"])) if vs else None


def version_on(versions: list[dict], day: str) -> dict | None:
    """The state in force on ``day`` (``YYYY-MM-DD``)."""
    best = None
    for v in real_versions(versions):
        start = (v.get("comingIntoForce") or "")[:10]
        end = (v.get("expiresOn") or "9999-12-31")[:10]
        if start <= day < end:
            if best is None or start > (best.get("comingIntoForce") or "")[:10]:
                best = v
    return best
