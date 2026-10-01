"""The House's **electronic asset-declaration system** (*Elektronikus
Vagyonnyilatkozati Rendszer*, EVNYR) — see :mod:`.scrape`.

Declarations filed from 2026 on are published at
``vagyonnyilatkozat-pub.parlament.hu`` instead of as one PDF per filing on the
MP's adatlap (REP-13), and they are published as **data**: a daily CSV of every
public declaration, plus a page per declaration that links its PDF.

* ``evnyr``  — the site's three documents -> the CSV snapshot's URL, the
  declarations in it, a declaration's PDF link.
* ``scrape`` — fetch + parse + write ``processed/asset-declarations.json``.
"""

from .scrape import fetch_declarations, load_previous, save_declarations  # noqa: F401
