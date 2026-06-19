"""Bills (irományok) scraper module — a self-contained vertical slice (EXT-1).

Fetches the cycle's bills from the Felicitas ``iromany`` API and writes one
``bills-<cycle>.json`` registry the loader turns into the ``bill`` /
``bill_sponsor`` tables. Sponsors carry the kepviseloId that joins to an MP
profile, so the module references the shared ``person`` entity rather than
duplicating it (EXT-2).
"""
