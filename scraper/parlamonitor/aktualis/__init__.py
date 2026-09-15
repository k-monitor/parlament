"""The **Aktuális** portal page and the sitting agenda it publishes (NR-1).

`parlament.hu`'s Felicitas API exposes what the House has *done*; what it is
*about to do* is published only as human documents linked from one portal page,
`/web/guest/aktualis`. This module is the vertical slice for that page (EXT-1):
it reads the page's own link list, mirrors the **napirend** (NR) PDF behind it,
and parses that PDF into the structured agenda the site can render.

* ``page`` — the portal page's links, and the House Committee's next meeting.
* ``nr``   — the NR PDF's text (via ``pdftotext``) -> days, items, details.
* ``scrape`` — fetch + parse + write ``processed/aktualis.json``.
"""
