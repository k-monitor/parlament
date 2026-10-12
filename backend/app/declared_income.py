"""Declared income (REP-19): the monthly amounts in an EVNYR declaration, read
as numbers, for the profile's income card beside the parliamentary fee (REP-17).

An EVNYR asset declaration (REP-18) has four lists whose entries each carry a
*havi átlagos jövedelem*, the average monthly income the entry brings in:

* ``jovedelemnyilatkozat.jelenlegiTevekenysegek``: income-earning activities now;
* ``gazdasagiErdekeltseg.tagsagok``: posts in organisations;
* ``gazdasagiErdekeltseg.tarsasagiErdekeltsegek``: interests in companies;
* ``jovedelemnyilatkozat.korabbiFoglalkozasok``: occupations in the three
  years before.

That field is **free text**, and declarants fill it every way there is:
``2380000 (2025-ben)``, ``745 000 HUF``, ``nettó 3.461.192 Ft``, ``nettó 1.746
Ft`` (one thousand seven hundred, not one and a bit). :func:`parse_amount` reads
one such text into a number, :func:`for_person` collects a declaration's entries
into the profile's list.

What the reading may and may not do, because the result is a figure printed
against a named person (TRUST-1):

* **The text is kept.** Every reading carries ``raw``, and the profile shows it
  wherever it says more than the number does (a year, a remark, a range).
* **Nothing is added to what was written.** Gross or net, a period, approximate:
  each is reported only when the text says so. The one reading the text does
  not spell out is the currency of a bare number, which is taken as forints and
  flagged (``currency_stated: False``): the form is in forints, and a foreign
  amount has an exchange-rate field of its own beside it.
* **Nothing is summed or converted.** The entries mix gross and net, monthly and
  yearly, now and three years ago, so a total over them would be a number nobody
  declared. A foreign amount stays in its currency unless the declarant also
  wrote the forint figure.
* **What cannot be read unambiguously is not guessed at.** Several unrelated
  amounts come back as ``multiple``, words only as ``unparsed`` (or ``none``
  when the words say there is no pay), and the profile shows the text as filed.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# --- reading one amount -----------------------------------------------------

# Letters, Hungarian ő/ű (Latin Extended-A) included: the guards below keep a
# word from matching inside another ("Ft" in "Kft.", "min" in "minden").
_L = "A-Za-zÀ-ÖØ-öø-ÿĀ-ž"
_NA = f"(?<![{_L}])"
_END = f"(?![{_L}])"
_W = "[a-záéíóöőúüű]"

_MONTH = ("(?:január|február|március|április|május|június|július|augusztus|"
          "szeptember|október|november|december|jan|febr?|márc|ápr|máj|jún|"
          "júl|aug|szept?|okt|nov|dec)")
# Hungarian case endings a date or year takes ("2025-ben", "29-től", "1-jétől").
_DATE_SUFFIX = ("(?:\\s*-?\\s*(?:ig|től|tól|ától|étől|jétől|jéig|áig|éig|án|én|"
                "jén|ján|je|ja|ei|ai|i|ben|ban|óta))")
_YEAR = "(?:19|20)\\d{2}"

# What is a date and never an amount. Masked out (blanked, same length) before
# the numbers are looked for, so "2380000 (2025-ben)" holds one number, not two.
_DATE_MASKS = [re.compile(p, re.I) for p in (
    # 2026.09.01., 2026-09-01, 2026. 09. 01-től
    f"\\b{_YEAR}\\s*[./-]\\s*\\d{{1,2}}\\s*[./-]\\s*\\d{{1,2}}\\.?{_DATE_SUFFIX}?",
    # [2026.] augusztus [29-től]: a day only when no digits follow it, so
    # "december 100 000 Ft" keeps its amount.
    f"(?:\\b{_YEAR}\\.?\\s*)?{_NA}{_MONTH}{_W}*\\.?"
    f"(?:\\s*\\d{{1,2}}(?!\\d)(?!\\s\\d)\\.?{_DATE_SUFFIX}?)?",
    # 2023.01
    f"\\b{_YEAR}\\s*[./]\\s*\\d{{1,2}}\\b\\.?",
    # 2025-ben, 2025. évi, 2024-es, 2025 óta
    f"\\b{_YEAR}(?:\\s*-\\s*|\\.\\s*|\\s*)"
    f"(?:ben|ban|től|tól|ig|óta|ra|re|as|es|ös|ik|beli|i|évi|év|évben|évtől|évig|évre|évek)\\b",
    # (2025)
    f"\\(\\s*{_YEAR}\\s*\\)",
)]

_NUM = re.compile(r"""
    (?<![\d.,'A-Za-z])
    (?:
        (?P<g>\d{1,3}(?P<sep>[ .,'])\d{3}(?:(?P=sep)\d{3})*)   # 1 234 567 / 1.234.567
        (?:(?!(?P=sep))[.,](?P<gd>\d{1,2})(?!\d))?             # ,50 after the groups
      | (?P<p>\d+)(?:[.,](?P<pd>\d+))?                         # 2380000 / 211448,29
    )
    (?!\d)
""", re.X)

# A number that counts something other than money: a share, people, hours.
_UNIT_AFTER = re.compile(
    f"\\s*(?:%|százalék|fő{_END}|db{_END}|darab|órá?{_END}|óra|nap{_END}|hét{_END}|"
    f"hónap|havi|év{_END}|m2|m²|ha{_END}|hektár|alkalom|x{_END})", re.I)

_SCALE = re.compile(
    f"\\s*(?:(?P<bn>milliárd{_W}*|mrd\\.?)"
    f"|(?P<mn>milli[óo]{_W}*|mill?\\.(?!\\d)|mio\\.?{_END}|M(?=Ft|HUF|\\s|$|[,.;)/])|m(?=\\s*Ft))"
    f"|(?P<th>ezer{_W}*|e(?=\\s*Ft)|E(?=\\s*Ft)))")
_SCALE_FACTOR = {"bn": 1_000_000_000, "mn": 1_000_000, "th": 1_000}

_CURRENCIES = (
    ("HUF", f"{_NA}(?:Ft\\.?|HUF|forint{_W}*){_END}"),
    ("EUR", f"(?:{_NA}(?:EUR|eur[oó]{_W}*){_END}|€)"),
    ("USD", f"(?:{_NA}(?:USD|dollár{_W}*|dollar{_W}*){_END}|US\\$|\\$)"),
    ("GBP", f"(?:{_NA}(?:GBP|angol font{_W}*){_END}|£)"),
    ("CHF", f"{_NA}(?:CHF|svájci frank{_W}*){_END}"),
    ("PLN", f"(?:{_NA}(?:PLN|zlot[yi]{_W}*|złot{_W}*){_END}|zł)"),
    ("RON", f"{_NA}(?:RON|lej{_W}*){_END}"),
    ("CZK", f"(?:{_NA}CZK{_END}|Kč)"),
)
_CUR_RES = [(code, re.compile(p, re.I)) for code, p in _CURRENCIES]
_CUR_ANY = "|".join(f"(?:{p})" for _, p in _CURRENCIES)
# After the number: "500 000 Ft", "500.000,- Ft", "500 000,-Ft".
_CUR_AFTER = re.compile(f"\\s*(?:[,.]-\\s*)?(?P<cur>{_CUR_ANY})", re.I)
# Glued to a scale letter ("500 eFt", "2 MFt"), where the letter guard would
# otherwise refuse it.
_CUR_GLUED = re.compile("(?P<cur>Ft\\.?|HUF|EUR)" + _END, re.I)
# Before it: "EUR 2 000", "€2000".
_CUR_BEFORE = re.compile(f"(?P<cur>{_CUR_ANY})\\s*$", re.I)
# Where the stretch between two amounts divides into "said of the first" and
# "said of the second": at the first punctuation or conjunction in it. Without
# one, all of it goes to the second, since Hungarian puts the qualifier first
# ("bruttó 1 309 493 Ft nettó 870 813 Ft").
_GAP_SPLIT = re.compile(f"[,;:()\\[\\]/]|{_NA}(?:és|illetve|valamint|vagy){_END}", re.I)

_NET = re.compile(f"{_NA}(?:nett[óo]|net){_END}", re.I)
_GROSS = re.compile(f"{_NA}(?:brutt[óo]|gross){_END}", re.I)
_APPROX = re.compile(
    f"(?:{_NA}(?:kb|cca|circa|körülbelül|mintegy|megközelítőleg|hozzávetőleg{_W}*|"
    f"legfeljebb|maximum|max|legalább|minimum|min|több mint|kevesebb mint){_END}|~|≈)",
    re.I)
_PERIODS = (
    ("year", f"{_NA}(?:évi|éves|évente|évenként|egy évre){_END}|/\\s*év{_END}|per\\s*év{_END}"),
    ("month", f"{_NA}(?:havi|havonta|hó|egy hónapra){_END}|/\\s*hónap{_END}|per\\s*hó{_END}"),
    ("week", f"{_NA}(?:heti|hetente){_END}|/\\s*hét{_END}"),
    ("day", f"{_NA}(?:napi|naponta){_END}|/\\s*nap{_END}"),
    ("hour", f"{_NA}(?:óradíj|órabér|óránként){_END}|/\\s*óra{_END}"),
    ("occasion", f"{_NA}(?:alkalmanként|alkalmi|eseti|megbízásonként){_END}|/\\s*alkalom{_END}"),
    ("once", f"{_NA}(?:egyszeri|egyösszegű|egy összegben){_END}"),
)
_PERIOD_RES = [(k, re.compile(p, re.I)) for k, p in _PERIODS]

# Two amounts joined like this are one range: "100 000 - 200 000 Ft",
# "1-2 millió", "100 000 Ft-tól 200 000 Ft-ig", "100 és 200 ezer Ft között".
_RANGE_JOIN = re.compile(r"(?:-|–|—|-?\s*t[óo]l|-?\s*től|és|\.\.\.?|…)", re.I)
# Words that say there is no pay, for a text with no number in it.
_NONE = re.compile(
    f"^\\s*(?:[-–—/]+|0|nem|nincs{_W}*|n/?a|nulla|semmi)\\s*\\.?\\s*$"
    f"|{_NA}(?:nincs|nem részesül{_W}*|nem kap{_W}*|díjazás nélkül|térítésmentes{_W}*|"
    f"ingyenes{_W}*|társadalmi munká{_W}*|önkéntes{_W}*){_END}", re.I)


def _currency_of(text: str) -> Optional[str]:
    return next((code for code, rx in _CUR_RES if rx.fullmatch(text.strip())), None)


def _value(m: re.Match, scale: Optional[str]) -> float:
    """The number a token stands for. A thousands group is a group ("1.746 Ft"
    is 1746: there are no fractional forints), unless a scale word follows a
    single group, where it can only be a decimal ("1,5 millió", "1.500 millió")."""
    if m.group("g") is not None:
        sep, g = m.group("sep"), m.group("g")
        if scale and sep in ".," and g.count(sep) == 1 and m.group("gd") is None:
            return float(g.replace(sep, "."))
        v = float(g.replace(sep, ""))
        if m.group("gd"):
            v += float("0." + m.group("gd"))
        return v
    return float(m.group("p") + ("." + m.group("pd") if m.group("pd") else ""))


def _closest(rx: re.Pattern, before: str, after: str):
    """The qualifier nearest the number: just after it ("Ft/év"), else the last
    one before it ("évi 6 000 000")."""
    m = rx.search(after)
    if m:
        return m
    found = list(rx.finditer(before))
    return found[-1] if found else None


def _candidates(text: str, masked: str) -> list[dict]:
    """Every amount-shaped number in the masked text, with its scale, currency
    and the span it occupies with them."""
    out = []
    for m in _NUM.finditer(masked):
        start, end = m.span()
        tail = masked[end:]
        if _UNIT_AFTER.match(tail):
            continue
        scale = None
        sm = _SCALE.match(tail)
        if sm:
            scale = next(k for k in ("bn", "mn", "th") if sm.group(k))
            end += sm.end()
        currency = None
        cm = _CUR_AFTER.match(masked, end)
        if cm is None and sm and masked[end - 1:end].isalpha():
            cm = _CUR_GLUED.match(masked, end)
        if cm:
            currency = _currency_of(cm.group("cur"))
            end = cm.end()
        start_full = start
        if currency is None:
            bm = _CUR_BEFORE.search(masked[:start])
            if bm:
                currency = _currency_of(bm.group("cur"))
                start_full = bm.start()
        value = _value(m, scale) * (_SCALE_FACTOR[scale] if scale else 1)
        out.append({
            "value": value, "currency": currency, "scale": scale,
            "start": start_full, "end": end,
            "yearlike": (m.group("p") is not None and not m.group("pd")
                         and len(m.group("p")) == 4 and 1950 <= value <= 2099
                         and currency is None and scale is None),
        })
    # The qualifiers that belong to each number: its share of the stretch
    # between it and each neighbour, or the whole text when it is alone.
    splits = []
    for a, b in zip(out, out[1:]):
        gm = _GAP_SPLIT.search(masked, a["end"], b["start"])
        splits.append(gm.start() if gm else a["end"])
    for i, c in enumerate(out):
        lo = splits[i - 1] if i else 0
        hi = splits[i] if i < len(splits) else len(masked)
        before, after = masked[lo:c["start"]], masked[c["end"]:hi]
        if len(out) > 1:
            before, after = before[-30:], after[:30]
        c["basis"] = ("net" if _closest(_NET, before, after)
                      else "gross" if _closest(_GROSS, before, after) else None)
        c["approx"] = bool(_closest(_APPROX, before, after))
        c["period"] = None
        best = None
        for kind, rx in _PERIOD_RES:
            am = rx.search(after)
            if am and (best is None or (0, am.start()) < best[0]):
                best = ((0, am.start()), kind)
            for bm in rx.finditer(before):
                key = (1, len(before) - bm.end())
                if best is None or key < best[0]:
                    best = (key, kind)
        if best:
            c["period"] = best[1]
    return out


def _num(v: Optional[float]):
    if v is None:
        return None
    return int(v) if float(v).is_integer() else round(v, 2)


def _huf(v: Optional[float]) -> Optional[int]:
    return None if v is None else int(round(v))


def _reading(raw: str, status: str) -> dict:
    return {"raw": raw, "status": status, "amount": None, "amount_max": None,
            "currency": None, "currency_stated": False, "amount_huf": None,
            "amount_huf_max": None, "basis": None, "period": "month",
            "period_stated": False, "approx": False, "alt": None, "values": None}


def _fill(out: dict, c: dict, hi: Optional[dict] = None) -> dict:
    """Set the reading from one candidate (``hi``: the top of a range)."""
    currency = c["currency"] or (hi or {}).get("currency")
    out["currency_stated"] = currency is not None
    out["currency"] = currency or "HUF"
    out["amount"] = _num(c["value"])
    out["amount_max"] = _num(hi["value"]) if hi else None
    if out["currency"] == "HUF":
        out["amount_huf"] = _huf(c["value"])
        out["amount_huf_max"] = _huf(hi["value"]) if hi else None
    out["basis"] = c["basis"] or (hi or {}).get("basis")
    period = c["period"] or (hi or {}).get("period")
    out["period_stated"] = period is not None
    out["period"] = period or "month"
    out["approx"] = c["approx"] or bool((hi or {}).get("approx"))
    return out


def _brief(c: dict) -> dict:
    return {"amount": _num(c["value"]), "currency": c["currency"],
            "basis": c["basis"], "period": c["period"]}


def parse_amount(text: Optional[str]) -> Optional[dict]:
    """Read one *havi átlagos jövedelem* text into a number, or ``None`` for an
    empty field.

    ``status`` is ``ok`` (one amount, a range, or an amount restated another
    way), ``multiple`` (several amounts that are not one restated), ``none``
    (words saying there is no pay) or ``unparsed``. An ``ok`` reading has
    ``amount`` in ``currency`` (``amount_max`` for the top of a range) and
    ``amount_huf`` when the forint figure is known: a forint amount, a bare
    number (``currency_stated`` false), or a foreign one the declarant also gave
    in forints. ``basis`` (net/gross) is ``None`` unless the text says;
    ``period`` is the field's own month unless the text names another
    (``period_stated``). ``alt`` is the second statement of the same income: the
    net beside the gross, the year beside the month, the foreign amount beside
    its forints."""
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    norm = unicodedata.normalize("NFC", raw)
    # Same length as `norm`, so positions carry over between the two.
    norm = re.sub("[    ]", " ", norm)
    masked = norm
    for rx in _DATE_MASKS:
        masked = rx.sub(lambda m: " " * len(m.group(0)), masked)

    cands = _candidates(norm, masked)
    if len(cands) > 1 and any(not c["yearlike"] for c in cands):
        # A bare 4-digit 19xx/20xx beside a real amount is a year the masks did
        # not catch ("2380000 2025 átlag"), not a second income.
        cands = [c for c in cands if not c["yearlike"]]

    if not cands:
        return _reading(raw, "none" if _NONE.search(norm) else "unparsed")
    if len(cands) == 1:
        return _fill(_reading(raw, "ok"), cands[0])

    out = _reading(raw, "ok")
    if len(cands) == 2:
        a, b = cands
        between = masked[a["end"]:b["start"]].strip()
        same_cur = (a["currency"] or "HUF") == (b["currency"] or "HUF")
        if _RANGE_JOIN.fullmatch(between) and (not a["currency"] or not b["currency"] or same_cur):
            # "1-2 millió Ft": the scale and currency written once serve both.
            if a["scale"] is None and b["scale"] is not None:
                a = {**a, "value": a["value"] * _SCALE_FACTOR[b["scale"]]}
            lo, hi = sorted((a, b), key=lambda c: c["value"])
            return _fill(out, lo, hi)
        foreign = [c for c in (a, b) if c["currency"] not in (None, "HUF")]
        forint = [c for c in (a, b) if c["currency"] == "HUF"]
        if len(foreign) == 1 and len(forint) == 1:
            # "211448,29 EUR (77083474 HUF)": the declarant's own forint figure.
            _fill(out, foreign[0])
            out["amount_huf"] = _huf(forint[0]["value"])
            out["alt"] = _brief(forint[0])
            return out
        if same_cur and {a["basis"], b["basis"]} == {"gross", "net"}:
            # "bruttó 1 309 493 Ft, nettó 870 813 Ft": lead with the gross, the
            # basis the published fee is in.
            gross, net = (a, b) if a["basis"] == "gross" else (b, a)
            _fill(out, gross)
            out["alt"] = _brief(net)
            return out
        periods = {a["period"] or "month", b["period"] or "month"}
        if same_cur and "month" in periods and len(periods) == 2:
            # "havi 500 000 Ft (évi 6 000 000 Ft)": the field asks for the month.
            month, other = (a, b) if (a["period"] or "month") == "month" else (b, a)
            _fill(out, month)
            out["alt"] = _brief(other)
            return out
    out = _reading(raw, "multiple")
    out["values"] = [_brief(c) for c in cands]
    return out


# --- a declaration's income list --------------------------------------------

# The form's income-bearing lists, in the order the card shows them: what the
# declarant earns now, then the three years before. (path, name field, detail
# fields, payer field, paid-flag field, when)
INCOME_LISTS = (
    ("jovedelemnyilatkozat.jelenlegiTevekenysegek", "megnevezes", (),
     "kifizetoSzemelye", "reszesulDijazasban", "current"),
    ("gazdasagiErdekeltseg.tagsagok", "szervezetNeve", ("tagsag",),
     None, "reszesulDijazasban", "current"),
    ("gazdasagiErdekeltseg.tarsasagiErdekeltsegek", "tarsasagNeve",
     ("erdekeltsegFormaja", "tulajdoniArany"), None, "reszesulDijazasban", "current"),
    ("jovedelemnyilatkozat.korabbiFoglalkozasok", "megnevezes", (),
     None, "reszesultDijazasban", "previous"),
)

# An entry that is the parliamentary mandate itself. An MP may list it among
# their current activities; the card then says the official figure is the
# published fee, rather than showing two amounts as if they were two incomes.
_PARLIAMENTARY = re.compile(
    "országgyűlési képviselő|képviselői (?:tisztelet|megbíz|javadalm|díj)"
    "|országgyűlés hivatal", re.I)


def _paid(value) -> Optional[bool]:
    v = str(value or "").strip().casefold()
    return True if v == "igen" else False if v == "nem" else None


def income_entries(content: dict) -> tuple[list[dict], int]:
    """The income entries of one declaration's contents, grouped by list, and
    how many entries were left out for saying they bring no pay.

    An entry is income when it states an amount or says it is paid. A post that
    says it is unpaid ("nem") and gives no amount is a membership, not an income,
    and is only counted. A current activity with neither flag nor amount is kept:
    the list it is in *is* the income-earning activities, so it is shown with its
    amount marked as not given."""
    groups, unpaid = [], 0
    for path, name_f, detail_fs, payer_f, paid_f, when in INCOME_LISTS:
        items = []
        for e in (content or {}).get(path) or []:
            if not isinstance(e, dict):
                continue
            paid = _paid(e.get(paid_f))
            income = parse_amount(e.get("haviJovedelem"))
            nothing = income is None or income["status"] == "none" or income["amount"] == 0
            if paid is False and nothing:
                unpaid += 1
                continue
            if income is None and paid is None and path != INCOME_LISTS[0][0]:
                continue
            name = (e.get(name_f) or "").strip() or None
            payer = (e.get(payer_f) or "").strip() or None if payer_f else None
            detail = " · ".join(v for f in detail_fs if (v := (e.get(f) or "").strip())) or None
            items.append({
                "name": name, "detail": detail, "payer": payer, "paid": paid,
                "rate": (e.get("atvaltasiArfolyam") or "").strip() or None,
                "income": income,
                "parliamentary": when == "current" and bool(
                    _PARLIAMENTARY.search(f"{name or ''} {payer or ''}")),
            })
        if items:
            groups.append({"path": path, "when": when, "items": items})
    return groups, unpaid


def for_person(declarations: list) -> Optional[dict]:
    """The income declared in the person's newest EVNYR declaration, or ``None``
    when they have none (or its contents are not served).

    Takes the profile's ``asset_declarations`` list, which already carries each
    EVNYR declaration's contents. Only the newest one is read: it is the
    declarant's current statement, and the older ones stay listed (and openable)
    in the declarations panel. A declaration whose public part lists no income
    still returns the block, with no groups, since "the declaration lists no
    other income" is itself what the card has to say."""
    evnyr = [d for d in declarations or []
             if d.get("source") == "evnyr" and isinstance(d.get("content"), dict)]
    if not evnyr:
        return None
    d = max(evnyr, key=lambda d: (d.get("finalizedAt") or "", d.get("id") or ""))
    groups, unpaid = income_entries(d["content"])
    entries = [i for g in groups for i in g["items"]]
    return {
        "declaration": {k: d.get(k) for k in
                        ("id", "type", "finalizedAt", "url", "pdfUrl", "snapshotAt")},
        "groups": groups,
        "unpaid": unpaid,
        "entries": len(entries),
        "parsed": sum(1 for i in entries
                      if i["income"] and i["income"]["status"] == "ok"),
    }
