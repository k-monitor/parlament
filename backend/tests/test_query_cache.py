"""Unit tests for the aggregate query cache (app/query_cache.py)."""

from __future__ import annotations

import app.query_cache as qc
from app.query_cache import TTLCache, cached_aggregate


def test_ttlcache_hit_and_miss():
    c = TTLCache(maxsize=4, ttl=100)
    assert c.get("a") is None
    c.set("a", 1)
    assert c.get("a") == 1


def test_ttlcache_expiry(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(qc.time, "monotonic", lambda: now[0])
    c = TTLCache(maxsize=4, ttl=30)
    c.set("k", "v")
    now[0] += 29
    assert c.get("k") == "v"       # still fresh
    now[0] += 2                    # 31s elapsed > ttl
    assert c.get("k") is None      # expired


def test_ttlcache_lru_eviction():
    c = TTLCache(maxsize=2, ttl=100)
    c.set("a", 1)
    c.set("b", 2)
    assert c.get("a") == 1         # touch a → b is now least-recently-used
    c.set("c", 3)                  # evicts b
    assert c.get("b") is None
    assert c.get("a") == 1 and c.get("c") == 3


def test_cached_aggregate_memoizes(monkeypatch):
    monkeypatch.setattr(qc.settings, "query_cache_ttl", 300)
    monkeypatch.setattr(qc.settings, "query_cache_size", 16)
    qc._caches.clear()
    monkeypatch.setattr(qc, "current_ident", lambda: ("db", 1, 2, 3))
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return {"n": calls["n"]}

    first = cached_aggregate("t", ("q", 1), compute)
    second = cached_aggregate("t", ("q", 1), compute)
    assert first == second == {"n": 1}
    assert calls["n"] == 1          # computed once, second served from cache
    # A different key recomputes.
    assert cached_aggregate("t", ("q", 2), compute) == {"n": 2}


def test_cached_aggregate_disabled(monkeypatch):
    monkeypatch.setattr(qc.settings, "query_cache_ttl", 0)  # disabled
    qc._caches.clear()
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return calls["n"]

    assert cached_aggregate("t", "k", compute) == 1
    assert cached_aggregate("t", "k", compute) == 2  # recomputed every call
    assert calls["n"] == 2


def test_cached_aggregate_invalidated_on_db_swap(monkeypatch):
    monkeypatch.setattr(qc.settings, "query_cache_ttl", 300)
    monkeypatch.setattr(qc.settings, "query_cache_size", 16)
    qc._caches.clear()
    ident = {"v": ("db", 1, 2, 3)}
    monkeypatch.setattr(qc, "current_ident", lambda: ident["v"])
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return calls["n"]

    assert cached_aggregate("t", "k", compute) == 1
    assert cached_aggregate("t", "k", compute) == 1        # cached under old ident
    ident["v"] = ("db", 1, 2, 4)                            # loader swapped the DB
    assert cached_aggregate("t", "k", compute) == 2        # key changed → recompute
