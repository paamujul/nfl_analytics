"""TTLCache behaviour, chiefly the single-flight guarantee.

The routes that use this cache are sync `def`s, so FastAPI runs them in anyio's
worker threadpool and concurrent requests really are concurrent threads. These
tests use real threads for that reason -- the stampede this cache prevents is
only reachable with genuine parallelism, and a serial test would pass against a
plain dict-with-a-TTL that has none of the protection.
"""
import threading
import time

from app.cache import TTLCache


def _spawn(n, target):
    threads = [threading.Thread(target=target) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "a worker thread hung"


def test_concurrent_misses_compute_once():
    """Five threads, one cold key -> fn() runs exactly once.

    Without the per-key lock this is 5, which for /api/compare means five
    simultaneous copies of a ~46k-row league scan.
    """
    cache = TTLCache(ttl=60.0)
    calls = []
    calls_lock = threading.Lock()
    start = threading.Barrier(5)
    results = []
    results_lock = threading.Lock()

    def compute():
        with calls_lock:
            calls.append(1)
        time.sleep(0.2)  # long enough that the others are provably still waiting
        return "value"

    def worker():
        start.wait()  # release all five into get_or_set together
        value = cache.get_or_set("k", compute)
        with results_lock:
            results.append(value)

    _spawn(5, worker)

    assert len(calls) == 1
    assert results == ["value"] * 5


def test_expiry_recomputes():
    cache = TTLCache(ttl=0.05)
    calls = []
    cache.get_or_set("k", lambda: calls.append(1) or "a")
    cache.get_or_set("k", lambda: calls.append(1) or "b")
    assert len(calls) == 1, "second call inside the TTL should have been a hit"
    time.sleep(0.06)
    assert cache.get_or_set("k", lambda: calls.append(1) or "c") == "c"
    assert len(calls) == 2


def test_none_is_a_real_cached_value():
    """A cached None must be a hit, not an endless miss."""
    cache = TTLCache(ttl=60.0)
    calls = []
    for _ in range(3):
        assert cache.get_or_set("k", lambda: calls.append(1) or None) is None
    assert len(calls) == 1


def test_eviction_is_soonest_expiry_first():
    cache = TTLCache(ttl=60.0, maxsize=2)
    cache.get_or_set("a", lambda: "a", ttl=1.0)     # expires first
    cache.get_or_set("b", lambda: "b", ttl=60.0)
    cache.get_or_set("c", lambda: "c", ttl=60.0)    # pushes past maxsize

    # Inspect the table rather than re-fetching: a miss on "a" would re-insert
    # it, push back over maxsize and evict "b", masking what we are checking.
    assert set(cache._entries) == {"b", "c"}

    calls = []
    assert cache.get_or_set("a", lambda: calls.append(1) or "a2") == "a2"
    assert calls == [1], "'a' had the soonest expiry and should have been evicted"


def test_lock_table_stays_bounded():
    """The per-key lock dict is what leaks if eviction only trims _entries."""
    cache = TTLCache(ttl=60.0, maxsize=4)
    for i in range(200):
        cache.get_or_set(i, lambda: i)
    assert len(cache._entries) <= 4
    assert len(cache._locks) <= 4


def test_clear_keeps_locks_that_are_held():
    """clear() during an in-flight computation must not break single-flight.

    Dropping a held lock would let the next caller mint a fresh one and compute
    the same key alongside the thread already inside fn().
    """
    cache = TTLCache(ttl=60.0)
    inside = threading.Event()
    release = threading.Event()

    def slow():
        inside.set()
        release.wait(timeout=5)
        return "slow"

    t = threading.Thread(target=lambda: cache.get_or_set("k", slow))
    t.start()
    assert inside.wait(timeout=5)
    cache.clear()
    assert "k" in cache._locks, "a held lock was dropped by clear()"
    release.set()
    t.join(timeout=5)
    assert not t.is_alive()
