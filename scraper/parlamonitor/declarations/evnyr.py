"""Reading the EVNYR site: the home page, the CSV snapshot, a declaration page.

The site is a client-rendered app whose search sits behind a proof-of-work
challenge (ALTCHA), so it is not scraped. It does not need to be, because the
site publishes a **daily CSV of every public declaration** as its open-data
channel ("A fájlt szabadon feldolgozhatja, és az eredményeket nyilvánosan is
megoszthatja"). That file is regenerated under a date-stamped name, so its URL
is read off the home page's download button rather than built. The CSV carries
everything except the PDF, which only the declaration's own (server-rendered)
page links to.

**The CSV's shape.** One row is not one item. A declaration is a header (name,
organisation, office, type, finalisation time) plus about twenty independent
lists (properties, vehicles, securities, accounts, cash, debts, income,
interests), and the export **zips those lists side by side**. Row *i* repeats
the header and carries the *i*-th entry of every list at once, so a declaration
with seven properties and one car is seven rows with the car in the first.
Row 0's car has nothing to do with row 0's property. Columns are named by their
path in the declaration (``kovetelesek.ertekpapirok.isin``), and that path is
what this module reads back: group the rows by declaration id, then read each
list down its own columns.
"""

from __future__ import annotations

import csv
import io
import re

HOST = "https://vagyonnyilatkozat-pub.parlament.hu"
HOME_URL = HOST + "/"

# The header columns, which every row of a declaration repeats, mapped to the
# record's own keys. ``modositva`` is not in today's export, but the site's own
# search indexes it as a header field ("modified at"), so it is mapped in
# advance rather than left to land as content when it appears.
HEADER_COLUMNS = {
    "nev": "name",
    "szervezet": "organisation",
    "tisztseg": "office",
    "nyilatkozatTipusa": "type",
    "veglegesites": "finalizedAt",
    "modositva": "modifiedAt",
    "schemaVersion": "schemaVersion",
}

_CSV_PATH_RE = re.compile(r"/media/napi/csv-snapshot/[A-Za-z0-9_.\-]+?\.csv")
_STAMP_RE = re.compile(r"_(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})\.csv$")
# A declaration id is a UUID today. Anything id-shaped is accepted, so that a
# change of id scheme upstream degrades into links we can still build rather
# than an empty registry.
_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z\-]{7,63}$")


def snapshot_url(home_html: str) -> str | None:
    """The newest CSV snapshot the home page links to, absolute.

    The names differ only in their timestamp, so the lexically last is the
    newest."""
    paths = sorted(set(_CSV_PATH_RE.findall(home_html or "")))
    return HOST + paths[-1] if paths else None


def snapshot_time(url: str | None) -> str | None:
    """The snapshot's own timestamp, from its file name, as ``YYYY-MM-DDTHH:MM``.

    It is the site's local (Budapest) time. The page prints the same moment
    ("Frissítve: 2026. október 1., 01:00")."""
    m = _STAMP_RE.search(url or "")
    return f"{m.group(1)}T{m.group(2)}:{m.group(3)}" if m else None


def declaration_url(declaration_id: str) -> str:
    """The declaration's public page. It is the record itself (every field,
    plus the PDF button) and resolves for as long as the declaration is public,
    so it is the link that is always safe to publish."""
    return f"{HOST}/nyilatkozat/{declaration_id}"


def pdf_url(page_html: str, declaration_id: str) -> str | None:
    """The PDF the declaration page links to, absolute, or ``None``.

    The file name carries a date and a slug of the name that the CSV has no
    field for (``vagyonnyilatkozat_20260929_dr.hoffman_istvan.pdf`` for a
    declaration finalised on 09-20), so it cannot be built and has to be read.
    The match is anchored on the declaration's own id, so a link to some other
    document on the page can never be taken for it."""
    pat = re.compile(r"/media/nyilatkozat/(?:[^\"'\\\s<>]+/)?"
                     + re.escape(declaration_id)
                     + r"/[^\"'\\\s<>/]+?\.pdf", re.IGNORECASE)
    m = pat.search(page_html or "")
    return HOST + m.group(0) if m else None


def _clean(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _layout(columns: list[str]) -> tuple[dict[str, list[tuple[str, str]]], list[str]]:
    """Split the content columns into **lists** (path -> its columns) and
    **single values**.

    A dotted column belongs to the list named by everything before its last
    segment (``ingatlanok.telepules`` -> the ``ingatlanok`` list). The exception
    is a path that has lists beneath it: ``gazdasagiErdekeltseg`` is a section
    holding ``.tagsagok`` and ``.tarsasagiErdekeltsegek``, so its direct column
    ``gazdasagiErdekeltseg.nyilatkozattetelHelye`` is one value (the place the
    declaration was made), not a list of them. An undotted column
    (``egyebKozlendok``) is a single value too."""
    content = [c for c in columns if c and c != "id" and c not in HEADER_COLUMNS]
    groups = {c.rsplit(".", 1)[0] for c in content if "." in c}
    sections = {g for g in groups if any(o.startswith(g + ".") for o in groups)}
    lists: dict[str, list[tuple[str, str]]] = {}
    scalars: list[str] = []
    for c in content:
        group, _, field = c.rpartition(".")
        if not group or group in sections:
            scalars.append(c)
        else:
            lists.setdefault(group, []).append((c, field))
    return lists, scalars


def parse_csv(text: str) -> list[dict]:
    """Every declaration in a CSV snapshot, in file order.

    Each is ``{id, name, organisation, office, type, finalizedAt, modifiedAt,
    schemaVersion, content}``. ``content`` maps a path in the declaration to
    either a **list of entries** (``"ingatlanok": [{"telepules": ..., ...}]``) or
    a **single text** (``"egyebKozlendok": "..."``). The paths are upstream's own
    names, so they match the field labels of the site's form one to one. Empty
    cells are dropped, an entry with no field filled is no entry, and a list
    with no entries is left out. Values are kept as written, whitespace aside:
    amounts are free text upstream ("Aktuális érték: 211448,29 EUR (77083474
    HUF)"), and reading numbers into them would be inventing a precision the
    declarant never gave.

    Raises ``ValueError`` on a file with no ``id`` column, which is not the
    export this was written against."""
    reader = csv.DictReader(io.StringIO((text or "").lstrip("﻿")))
    columns = list(reader.fieldnames or [])
    if "id" not in columns:
        raise ValueError("the CSV has no 'id' column (columns: %s)"
                         % ", ".join(columns[:10]))
    lists, scalars = _layout(columns)

    rows_by_id: dict[str, list[dict]] = {}
    for row in reader:
        did = _clean(row.get("id"))
        if did and _ID_RE.match(did):
            rows_by_id.setdefault(did, []).append(row)

    out = []
    for did, rows in rows_by_id.items():
        rec: dict = {"id": did}
        for col, key in HEADER_COLUMNS.items():
            rec[key] = next((v for r in rows if (v := _clean(r.get(col)))), None)
        content: dict = {}
        for path, cols in lists.items():
            items = []
            for r in rows:
                item = {field: v for (col, field) in cols if (v := _clean(r.get(col)))}
                if item:
                    items.append(item)
            if items:
                content[path] = items
        for col in scalars:
            # One value, repeated or (in principle) spread over rows. Distinct
            # values are kept in order, never silently reduced to the first.
            values = list(dict.fromkeys(v for r in rows if (v := _clean(r.get(col)))))
            if values:
                content[col] = "\n".join(values)
        rec["content"] = content
        out.append(rec)
    return out
