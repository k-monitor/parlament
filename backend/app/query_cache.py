"""In-process TTL/LRU cache for expensive read-only aggregates (NFR-1).

`/search/trend`, `/search/breakdown` and the module `/facets` endpoints each
re-scan the full FTS match set (or a whole table) on every call — and the SPA
fires trend+breakdown alongside *every* search, with identical arguments across
pagination. They are pure functions of (query, filters) over a read-only DB, so
we memoize their JSON payloads per worker process with a short TTL.

The DB file's identity (path+device+inode+mtime, from ``db.current_ident``) is
folded into every cache key, so the loader's atomic ``os.replace`` swap (DB-4)
invalidates the cache transparently: a superseded DB's entries are simply never
looked up again and age out under the LRU bound. This is defense-in-depth behind
the CDN (caching.py) — it absorbs cache-miss bursts (a popular term hitting a
cold edge, many edges) and un-CDN'd deployments, without ever serving data older
than the current DB.

Disabled (every call recomputes) when ``settings.query_cache_ttl <= 0``.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Hashable

from .config import settings
from .db import current_ident


class TTLCache:
    """A tiny thread-safe LRU cache with per-entry TTL. Bounded to ``maxsize``
    entries; the least-recently-used is evicted first. Not a general-purpose
    cache — just enough for memoizing aggregate endpoint payloads."""

    def __init__(self, maxsize: int, ttl: int):
        self.maxsize = max(1, maxsize)
        self.ttl = ttl
        self._store: "OrderedDict[Hashable, tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Hashable):
        now = time.monotonic()
        with self._lock:
            hit = self._store.get(key)
            if hit is None:
                return None
            expires_at, value = hit
            if expires_at < now:
                self._store.pop(key, None)
                return None
            self._store.move_to_end(key)
            return value

    def set(self, key: Hashable, value: Any) -> None:
        now = time.monotonic()
        with self._lock:
            self._store[key] = (now + self.ttl, value)
            self._store.move_to_end(key)
            while len(self._store) > self.maxsize:
                self._store.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


# One cache per logical endpoint, so a flood of one kind can't evict another's
# entries. Created lazily so a runtime change to the cache size (tests) is honoured.
_caches: dict[str, TTLCache] = {}
_caches_lock = threading.Lock()


def _cache_for(name: str) -> TTLCache:
    with _caches_lock:
        cache = _caches.get(name)
        if cache is None:
            cache = TTLCache(settings.query_cache_size, settings.query_cache_ttl)
            _caches[name] = cache
        return cache


def cached_aggregate(name: str, params: Hashable, compute: Callable[[], Any]) -> Any:
    """Return ``compute()``'s result, memoized under ``(name, params)`` scoped to
    the current DB file. ``params`` must be a hashable snapshot of everything that
    affects the result (the query string + all active filters). Falls straight
    through to ``compute()`` when caching is disabled (ttl <= 0)."""
    if settings.query_cache_ttl <= 0:
        return compute()
    cache = _cache_for(name)
    key = (current_ident(), params)
    hit = cache.get(key)
    if hit is not None:
        return hit
    value = compute()
    cache.set(key, value)
    return value
