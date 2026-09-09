"""In-process TTL cache for the league-wide metric reductions.

/api/compare and /api/teams/{team}/defense both start by scanning a whole
season of plays league-wide -- ~46k rows -- and reducing them to one small dict
per team. The reduction is identical for every team pairing and every one of the
32 defenses, so it is worth holding onto for a few minutes.

Single-flight is the load-bearing part, not the caching. Every route in
app/api/routes.py is a sync `def`, which means FastAPI runs it in anyio's worker
threadpool: concurrent requests really do execute in parallel threads. Without a
per-key lock, three cold /api/compare requests arriving together each
materialize that 46k-row scan at the same time -- three copies of the row list
and three copies of the intermediate per-team groupings resident at once. That
is the OOM this module exists to prevent; a plain dict-with-a-TTL would cache
the result but still let the stampede through on every expiry.

The clock is time.monotonic(), not time.time(). A VM that has just booted has
not finished talking to NTP yet, and the step correction that follows would
otherwise either expire every entry at once or freeze them past their TTL.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Hashable

# Distinguishes "no live entry" from an entry whose value is legitimately None.
_MISS = object()


class TTLCache:
    """Thread-safe TTL cache with per-key single-flight on the miss path."""

    def __init__(self, ttl: float = 300.0, maxsize: int = 64):
        self._ttl = ttl
        self._maxsize = maxsize
        self._entries: dict[Hashable, tuple[float, Any]] = {}
        # Parallel dict of per-key locks. Bounded alongside _entries in
        # _evict_locked -- left to itself it grows once per distinct key
        # forever, which for a (scope, season, phase) key is slow but real.
        self._locks: dict[Hashable, threading.Lock] = {}
        self._guard = threading.Lock()

    def get_or_set(self, key: Hashable, fn: Callable[[], Any], ttl: float | None = None) -> Any:
        """Return the cached value for key, computing it via fn() on a miss.

        Only one thread per key ever runs fn(); the rest block and take the
        value it stored.
        """
        value = self._fresh(key)
        if value is not _MISS:
            return value

        with self._lock_for(key):
            # Re-check: whoever held this lock was, by definition, computing
            # this exact key, and has already stored the result by the time we
            # get in. Skipping this check gives you the stampede back, just
            # serialized instead of parallel.
            value = self._fresh(key)
            if value is not _MISS:
                return value
            value = fn()
            self._store(key, value, self._ttl if ttl is None else ttl)
            return value

    def clear(self) -> None:
        """Drop every entry. Used by the tests; nothing in the app calls it."""
        with self._guard:
            self._entries.clear()
            # Keep the locks that are currently held -- a thread is inside fn()
            # for that key, and handing the next caller a fresh lock object
            # would let it compute the same thing alongside.
            self._locks = {k: v for k, v in self._locks.items() if v.locked()}

    def _fresh(self, key: Hashable) -> Any:
        with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                return _MISS
            expires_at, value = entry
            if expires_at <= time.monotonic():
                del self._entries[key]
                return _MISS
            return value

    def _lock_for(self, key: Hashable) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.Lock()
            return lock

    def _store(self, key: Hashable, value: Any, ttl: float) -> None:
        with self._guard:
            self._entries[key] = (time.monotonic() + ttl, value)
            self._evict_locked()

    def _evict_locked(self) -> None:
        """Trim to maxsize, soonest-expiry first. Caller holds _guard."""
        while len(self._entries) > self._maxsize:
            del self._entries[min(self._entries, key=lambda k: self._entries[k][0])]
        for key in [k for k in self._locks if k not in self._entries]:
            # A held lock means a thread is mid-fn() for that key. Dropping it
            # would let the next caller mint a second lock and compute in
            # parallel -- exactly what this class exists to stop.
            if not self._locks[key].locked():
                del self._locks[key]


# Shared by the compare and defense-profile reductions. Both key on
# (scope, season, phase), so the whole app needs a handful of live entries.
league_cache = TTLCache(ttl=300.0, maxsize=64)
