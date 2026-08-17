"""Idempotent enqueue locks for fan-out unit tasks (D11 / task 5.5).

A Redis ``SET NX`` lock keyed ``fetchone:{webupdate_id}:{item_source_id}``
guards against duplicate scheduling of the same unit's work: a duplicate
enqueue (e.g. two racing dispatch calls, or a Huey redelivery) skips
scheduling a second task when the lock is already held; a Defer replaces the
lock with a fresh TTL before scheduling the retry; terminalize deletes it.

Structured as a small ``UnitLock`` interface — mirroring
``ratelimit.store.BudgetStore`` — so the decision logic that *uses* the lock
(skip-if-locked, replace-on-defer, delete-on-terminalize) is unit-testable
against ``InMemoryUnitLock`` without a live Redis server. ``InMemoryUnitLock``
is only valid within a single process, which is fine for immediate-mode Huey
under tests — there is no real concurrent delivery to race against there.
"""

import functools
import threading
import time

DEFAULT_LOCK_TTL_SECONDS = 300.0
LOCK_GRACE_SECONDS = 120.0


class UnitLock:
    """Interface implemented by ``InMemoryUnitLock`` and ``RedisUnitLock``."""

    def acquire(self, key, ttl_seconds):
        raise NotImplementedError

    def release(self, key):
        raise NotImplementedError


class InMemoryUnitLock(UnitLock):
    """Process-local lock for tests / Redis-less dev."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries = {}  # key -> expires_at (epoch seconds)

    def acquire(self, key, ttl_seconds):
        now = time.time()
        with self._lock:
            expires_at = self._entries.get(key)
            if expires_at is not None and expires_at > now:
                return False
            self._entries[key] = now + ttl_seconds
            return True

    def release(self, key):
        with self._lock:
            self._entries.pop(key, None)


class RedisUnitLock(UnitLock):
    """Production lock: Redis ``SET NX EX`` per unit key (D11)."""

    def __init__(self, url):
        import redis as redis_lib

        self._redis = redis_lib.from_url(url)

    def acquire(self, key, ttl_seconds):
        return bool(
            self._redis.set(key, "1", nx=True, ex=max(1, int(round(ttl_seconds))))
        )

    def release(self, key):
        self._redis.delete(key)


@functools.lru_cache(maxsize=None)
def _redis_lock_for(url):
    return RedisUnitLock(url)


_in_memory_singleton = InMemoryUnitLock()


def get_unit_lock():
    """Redis in production when ``REDIS_URL`` is set; in-memory otherwise.

    Returns a process-wide singleton per backend (cached by URL for Redis),
    mirroring ``ratelimit.store.get_budget_store``.
    """
    from django.conf import settings

    url = (getattr(settings, "REDIS_URL", "") or "").strip()
    if url:
        return _redis_lock_for(url)
    return _in_memory_singleton


def unit_lock_key(webupdate_id, item_source_id):
    return f"fetchone:{webupdate_id}:{item_source_id}"


def reset_in_memory_lock():
    """Test helper: clear the process-wide in-memory lock singleton.

    Django ``TestCase`` wraps each test in a rolled-back transaction, so
    autoincrement pks — and therefore lock keys, which are keyed on
    ``webupdate_id``/``item_source_id`` pks — commonly repeat across test
    methods. Without a reset, a lock acquired (and never released, e.g.
    because a test mocked ``fetch_one`` entirely) in one test can still read
    as held in a later, unrelated test reusing the same pks. Call this from
    ``setUp`` in tests that exercise ``dispatch_fan_out``/``fetch_one``.
    """
    _in_memory_singleton._entries.clear()


def lock_ttl_for_eta(eta, now=None):
    """TTL for a requeue lock: time until ``eta`` plus grace (D11 / task 5.5).

    Minimum ``DEFAULT_LOCK_TTL_SECONDS`` (300s) for an immediate/near-term
    ``eta`` (or ``None``, e.g. give-up requeues that don't happen).
    """
    now = now if now is not None else time.time()
    if eta is None:
        return DEFAULT_LOCK_TTL_SECONDS
    return max(DEFAULT_LOCK_TTL_SECONDS, max(0.0, eta - now) + LOCK_GRACE_SECONDS)
