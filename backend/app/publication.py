"""How a sitting day's **publication state** is judged and named.

Two small things that used to live inside single call sites and are now read by
three of them — the proceedings API (§5.6 SIT-2), the server-rendered share cards
(og.py) and the Bluesky announcer (§8.7 SOC-2, app/social.py):

  * :func:`processing_state` — whether a held day is fully published yet. The
    announcer's definition of "a new sitting day is *fully processed*" MUST be the
    same one the site shows a badge for, so the two cannot drift apart.
  * :func:`hu_date` — the Hungarian long date, matching the frontend's locale.
"""

from __future__ import annotations

import re
from datetime import date

# parlament.hu publishes a held sitting in instalments and in no fixed order —
# the speech listing first, then the per-speech video windows (in batches), then,
# days later, the jegyzőkönyv — so a day can be browsable while still incomplete
# (2026-07-27: 110 of 166 speeches timed; 2026-07-28: no transcript at all yet).
# Past this window a still-missing piece is not late but absent for good: a
# genuinely video-only day (VIE-8), like the 2011-autumn sittings whose record was
# never digitised. Calling those "being processed" would be a promise that never
# resolves, so only a recent day is reported as pending. Mirrors the scraper's own
# publication-lag grace (`proceedings/scrape.py:_TEXT_GRACE`), which stops chasing
# a day's missing content at the same age.
PROCESSING_GRACE_DAYS = 30


def processing_state(status: str, day: str | None, speeches: int,
                     with_text: int, with_video: int, *,
                     today: date | None = None) -> str | None:
    """How completely a held sitting has been published, as
    ``complete`` | ``pending`` | ``incomplete`` (SIT-2).

    ``pending`` means a transcript or per-speech video window is still missing but
    young enough that parlament.hu is expected to publish it (the site marks the
    day as being processed and the sync keeps chasing it); ``incomplete`` is the
    same gap on a day old enough that it will not be filled any more. ``None``
    when the day's own `status` already carries the answer (an announced
    `scheduled` sitting, or an `awaiting_media` one with nothing to show yet) or it
    holds no speeches to be complete about."""
    if status != "published" or not speeches:
        return None
    if with_text >= speeches and with_video >= speeches:
        return "complete"
    try:
        held = date.fromisoformat((day or "")[:10])
    except ValueError:
        return "incomplete"
    return ("pending" if ((today or date.today()) - held).days <= PROCESSING_GRACE_DAYS
            else "incomplete")


# --- Hungarian date formatting (matches the frontend's locale) --------------

HU_MONTHS = ("", "január", "február", "március", "április", "május", "június",
             "július", "augusztus", "szeptember", "október", "november",
             "december")


def hu_date(iso: str | None) -> str:
    """`2026-06-18` → `2026. június 18.` (best-effort; passes through on a
    non-ISO value)."""
    if not iso:
        return ""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", iso)
    if not m:
        return iso
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if 1 <= mo <= 12:
        return f"{y}. {HU_MONTHS[mo]} {d}."
    return iso
