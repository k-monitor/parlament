"""Parlamonitor scraper.

A self-contained scraping pipeline for the Hungarian National Assembly
(*Magyar Országgyűlés*), owned end-to-end by this project (requirements §3.1,
SRC-1). It replaces the legacy ``OpenParliamentTV-Tools`` HU pipeline, which is
kept only as reference material — none of it is imported here at runtime.

Two scraper modules, each a self-contained vertical slice (requirements §7,
EXT-1):

* :mod:`parlamonitor.proceedings` — per-sitting plenary proceedings: the speech
  listing, full speech text, the whole-day HLS recording, and v1 positional
  sentence timing. Produces one *session record* per sitting day.
* :mod:`parlamonitor.representatives` — the representative registry: bio, faction
  history, committee memberships, constituency, education, and the per-cycle
  speech / bill counts that the statistics module needs.

Both talk to parlament.hu through the modern token-free **Felicitas** JSON API
(:mod:`parlamonitor.felicitas`); the legacy CGI/PAIR backends are documented in the
reference but not required for v1.
"""

__version__ = "1.0.0"
