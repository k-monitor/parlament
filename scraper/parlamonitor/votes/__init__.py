"""Votes (szavazások) scraper module — a self-contained vertical slice (EXT-1).

Fetches the cycle's roll-call votes from the Felicitas ``szavazas`` API and
writes one ``votes-<cycle>.json`` registry the loader turns into the ``vote`` /
``vote_record`` / ``vote_faction_stat`` / ``vote_subject`` tables. Each per-MP
record carries the kepviseloId that joins to an MP profile, and each vote subject
carries the iromanyId that joins to a bill, so the module references the shared
``person`` and ``bill`` entities rather than duplicating them (EXT-2).
"""
