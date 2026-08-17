"""Shared budget stores (D2): in-memory (tests / no Redis) and Redis (production).

Both backends implement the same interface:

* ``get_snapshot(scope)`` — read-only, for inspection/logging.
* ``update_meters(scope, meters, learned_cost=None)`` — merge freshly
  extracted vendor meters into the snapshot after a response.
* ``set_exhausted_until(scope, until_ts)`` — hard cooldown from a 429
  ``Retry-After`` (D6 / task 2.5).
* ``try_acquire(scope, costs, run_id, policy, now)`` — atomic check-and-reserve
  (D13); see ``pacer.decide`` for the shared algorithm and
  ``RedisBudgetStore._LUA_TRY_ACQUIRE`` for its Lua transliteration.
"""

import functools
import json
import threading

from .budget import BudgetSnapshot, MeterSnapshot
from .pacer import PaceDecision, decide


class BudgetStore:
    """Interface implemented by ``InMemoryBudgetStore`` and ``RedisBudgetStore``."""

    def get_snapshot(self, scope):
        raise NotImplementedError

    def update_meters(self, scope, meters, learned_cost=None):
        raise NotImplementedError

    def set_exhausted_until(self, scope, until_ts):
        raise NotImplementedError

    def try_acquire(self, scope, costs, run_id, policy, now):
        raise NotImplementedError


class InMemoryBudgetStore(BudgetStore):
    """Process-local store for tests and Redis-less dev (D2).

    A single ``threading.Lock`` stands in for the Redis Lua script's
    atomicity: ``try_acquire`` reads snapshot + run-spent, calls
    ``pacer.decide``, and — only on Ready — applies the reservation, all
    while holding the lock, so concurrent callers in the same process never
    both observe Ready when only one estimated cost was available.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._snapshots = {}  # scope -> BudgetSnapshot
        self._run_spent = {}  # run_id -> dict[unit] -> spent

    def _get_or_create(self, scope):
        snapshot = self._snapshots.get(scope)
        if snapshot is None:
            snapshot = BudgetSnapshot(scope=scope)
            self._snapshots[scope] = snapshot
        return snapshot

    def get_snapshot(self, scope):
        with self._lock:
            snapshot = self._snapshots.get(scope)
            if snapshot is None:
                return BudgetSnapshot(scope=scope)
            return BudgetSnapshot(
                scope=snapshot.scope,
                meters=dict(snapshot.meters),
                next_allowed_at=snapshot.next_allowed_at,
                exhausted_until=snapshot.exhausted_until,
            )

    def update_meters(self, scope, meters, learned_cost=None):
        with self._lock:
            snapshot = self._get_or_create(scope)
            snapshot.meters.update(meters)

    def set_exhausted_until(self, scope, until_ts):
        with self._lock:
            snapshot = self._get_or_create(scope)
            snapshot.exhausted_until = until_ts

    def try_acquire(self, scope, costs, run_id, policy, now):
        with self._lock:
            snapshot = self._get_or_create(scope)
            run_spent = self._run_spent.setdefault(run_id, {})

            decision = decide(snapshot, run_spent, costs, policy, now)

            if decision.kind == "ready":
                # Bump run-spend for every Ready — including the cold-start
                # fallback case — since it represents an actual HTTP send
                # this run; only known meters get their remaining decremented.
                for unit, cost in costs.items():
                    meter = snapshot.meters.get(unit)
                    if meter is not None and meter.remaining is not None:
                        snapshot.meters[unit] = MeterSnapshot(
                            unit=meter.unit,
                            remaining=max(0, meter.remaining - cost),
                            limit=meter.limit,
                            reset_at=meter.reset_at,
                        )
                    run_spent[unit] = run_spent.get(unit, 0) + cost
                snapshot.next_allowed_at = now + policy.min_interval

            return decision


class RedisBudgetStore(BudgetStore):
    """Production store: Redis hash per scope + a Lua check-and-reserve (D13).

    ``ratelimit:{scope}`` holds ``{unit}:remaining`` / ``{unit}:limit`` /
    ``{unit}:reset_at`` per meter plus ``next_allowed_at`` / ``exhausted_until``.
    ``ratelimit:run:{run_id}`` holds ``{unit}`` -> spent-this-run, expiring
    after a day so run caps don't leak forever.
    """

    # Mirrors pacer.decide(); keep the two in sync when changing either.
    # KEYS[1] = budget hash, KEYS[2] = run-cap hash.
    # ARGV[1] = JSON {now, headroom_pct, min_interval, fair_interval,
    #                 short_wait_threshold, costs: {unit: cost}, max_per_run: {unit: cap}}
    _LUA_TRY_ACQUIRE = """
local req = cjson.decode(ARGV[1])
local now = req.now
local headroom = req.headroom_pct
local min_interval = req.min_interval
local fair_interval = req.fair_interval
local short_wait_threshold = req.short_wait_threshold
local costs = req.costs
local max_per_run = req.max_per_run

local flat = redis.call('HGETALL', KEYS[1])
local b = {}
for i = 1, #flat, 2 do b[flat[i]] = flat[i + 1] end

local exhausted_until = tonumber(b['exhausted_until'])
if exhausted_until and now < exhausted_until then
  return cjson.encode({kind = 'defer', eta = exhausted_until, reason = 'retry_after'})
end

local spent_flat = redis.call('HGETALL', KEYS[2])
local spent = {}
for i = 1, #spent_flat, 2 do spent[spent_flat[i]] = tonumber(spent_flat[i + 1]) end

for unit, cost in pairs(costs) do
  local cap = max_per_run[unit]
  local already = spent[unit] or 0
  if cap and (already + cost) > cap then
    return cjson.encode({kind = 'defer', reason = 'per_run_cap', unit = unit})
  end
end

local next_allowed_at = tonumber(b['next_allowed_at']) or 0
local any_known = false
local max_wait = 0
local reason = 'min_interval'
local winning_unit = ''

for unit, cost in pairs(costs) do
  local remaining_s = b[unit .. ':remaining']
  if remaining_s then
    any_known = true
    local remaining = tonumber(remaining_s)
    local reset_at = tonumber(b[unit .. ':reset_at'])
    local usable = math.floor(remaining * (1 - headroom))
    if usable < cost then
      local wait
      if reset_at then
        wait = math.max(0, reset_at - now)
      else
        wait = math.max(min_interval, 30)
      end
      if wait >= max_wait then
        max_wait, reason, winning_unit = wait, 'headroom', unit
      end
    else
      local wait = math.max(0, next_allowed_at - now)
      local this_reason = 'min_interval'
      if fair_interval and reset_at and usable > 0 then
        local fair = (reset_at - now) / math.max(usable, 1)
        if fair > wait then
          wait, this_reason = fair, 'fair_interval'
        end
      end
      if wait >= max_wait then
        max_wait, reason, winning_unit = wait, this_reason, unit
      end
    end
  end
end

if not any_known then
  for unit, cost in pairs(costs) do
    redis.call('HINCRBY', KEYS[2], unit, cost)
  end
  redis.call('EXPIRE', KEYS[2], 86400)
  redis.call('HSET', KEYS[1], 'next_allowed_at', now + min_interval)
  return cjson.encode({kind = 'ready', fallback = true})
end

if max_wait <= 0 then
  for unit, cost in pairs(costs) do
    local remaining_s = b[unit .. ':remaining']
    if remaining_s then
      local remaining = tonumber(remaining_s)
      redis.call('HSET', KEYS[1], unit .. ':remaining', math.max(0, remaining - cost))
    end
    redis.call('HINCRBY', KEYS[2], unit, cost)
  end
  redis.call('EXPIRE', KEYS[2], 86400)
  redis.call('HSET', KEYS[1], 'next_allowed_at', now + min_interval)
  return cjson.encode({kind = 'ready'})
elseif max_wait < short_wait_threshold then
  return cjson.encode({kind = 'short_wait', wait_seconds = max_wait, reason = reason, unit = winning_unit})
else
  return cjson.encode({kind = 'defer', eta = now + max_wait, reason = reason, unit = winning_unit})
end
"""

    def __init__(self, url):
        import redis as redis_lib

        self._redis = redis_lib.from_url(url)
        self._try_acquire_script = self._redis.register_script(self._LUA_TRY_ACQUIRE)

    def _budget_key(self, scope):
        return f"ratelimit:{scope}"

    def _run_key(self, run_id):
        return f"ratelimit:run:{run_id}"

    def get_snapshot(self, scope):
        flat = self._redis.hgetall(self._budget_key(scope))
        flat = {
            k.decode() if isinstance(k, bytes) else k: (
                v.decode() if isinstance(v, bytes) else v
            )
            for k, v in flat.items()
        }
        meters = {}
        units = {key.split(":")[0] for key in flat if key.endswith(":remaining")}
        for unit in units:
            meters[unit] = MeterSnapshot(
                unit=unit,
                remaining=_to_float(flat.get(f"{unit}:remaining")),
                limit=_to_float(flat.get(f"{unit}:limit")),
                reset_at=_to_float(flat.get(f"{unit}:reset_at")),
            )
        return BudgetSnapshot(
            scope=scope,
            meters=meters,
            next_allowed_at=_to_float(flat.get("next_allowed_at")) or 0.0,
            exhausted_until=_to_float(flat.get("exhausted_until")),
        )

    def update_meters(self, scope, meters, learned_cost=None):
        mapping = {}
        for unit, meter in meters.items():
            mapping[f"{unit}:remaining"] = meter.remaining
            if meter.limit is not None:
                mapping[f"{unit}:limit"] = meter.limit
            if meter.reset_at is not None:
                mapping[f"{unit}:reset_at"] = meter.reset_at
        if mapping:
            self._redis.hset(self._budget_key(scope), mapping=mapping)

    def set_exhausted_until(self, scope, until_ts):
        self._redis.hset(self._budget_key(scope), "exhausted_until", until_ts)

    def try_acquire(self, scope, costs, run_id, policy, now):
        payload = json.dumps(
            {
                "now": now,
                "headroom_pct": policy.headroom_pct,
                "min_interval": policy.min_interval,
                "fair_interval": policy.fair_interval,
                "short_wait_threshold": policy.short_wait_threshold,
                "costs": costs,
                "max_per_run": policy.max_per_run(),
            }
        )
        raw = self._try_acquire_script(
            keys=[self._budget_key(scope), self._run_key(run_id)], args=[payload]
        )
        result = json.loads(raw)
        return PaceDecision(
            kind=result["kind"],
            wait_seconds=result.get("wait_seconds", 0.0),
            eta=result.get("eta"),
            reason=result.get("reason", ""),
            unit=result.get("unit", ""),
            fallback=result.get("fallback", False),
        )


def _to_float(value):
    if value is None:
        return None
    return float(value)


@functools.lru_cache(maxsize=None)
def _redis_store_for(url):
    return RedisBudgetStore(url)


_in_memory_singleton = InMemoryBudgetStore()


def get_budget_store():
    """Redis in production when ``REDIS_URL`` is set; in-memory otherwise (D2).

    Returns a process-wide singleton per backend (cached by URL for Redis) so
    pacing state actually accumulates across calls instead of resetting every
    time a caller asks for "the" store.
    """
    from django.conf import settings

    url = (getattr(settings, "REDIS_URL", "") or "").strip()
    if url:
        return _redis_store_for(url)
    return _in_memory_singleton
