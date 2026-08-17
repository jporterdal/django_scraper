"""Tests for the rate-limit awareness machinery (tracking/ratelimit/).

Offline only: everything here exercises ``InMemoryBudgetStore`` — no live
Redis, no network (matches the design's "Tests: Offline" non-goal for
RedisBudgetStore, whose Lua script mirrors this same decision logic).
"""

import threading
from concurrent.futures import ThreadPoolExecutor

from django.test import SimpleTestCase, TestCase

from tracking.models import Source
from tracking.ratelimit import (
    DEFER,
    READY,
    SHORT_WAIT,
    BudgetSnapshot,
    InMemoryBudgetStore,
    MeterSnapshot,
    RateLimitPolicy,
    default_scope,
    extract_graphql_cost,
    extract_ietf,
    extract_x_ratelimit,
    get_profile,
    parse_retry_after,
    reconcile_response,
    record_429,
    try_acquire,
)
from tracking.ratelimit.pacer import decide


class FakeResponse:
    def __init__(self, headers=None, json_data=None, json_error=False):
        self.headers = headers or {}
        self._json_data = json_data
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("invalid json")
        return self._json_data


class ProfileRegistryTests(SimpleTestCase):
    def test_blank_and_none_disable_dynamic_awareness(self):
        self.assertIsNone(get_profile(""))
        self.assertIsNone(get_profile("none"))
        self.assertIsNone(get_profile("None"))
        self.assertIsNone(get_profile(None))

    def test_registered_profile_resolves(self):
        profile = get_profile("ietf")
        self.assertEqual(profile.key, "ietf")

    def test_unknown_profile_raises(self):
        with self.assertRaises(KeyError):
            get_profile("not-a-real-profile")

    def test_default_scope_is_source_key(self):
        source = Source(key="cc")
        self.assertEqual(default_scope(source), "source:cc")


class IetfExtractorTests(SimpleTestCase):
    def test_split_headers_reset_is_delta(self):
        response = FakeResponse(
            headers={
                "RateLimit-Limit": "100",
                "RateLimit-Remaining": "42",
                "RateLimit-Reset": "30",
            }
        )
        meters, learned_cost = extract_ietf(response, now=1000.0)
        self.assertIsNone(learned_cost)
        meter = meters["request"]
        self.assertEqual(meter.remaining, 42)
        self.assertEqual(meter.limit, 100)
        self.assertEqual(meter.reset_at, 1030.0)

    def test_combined_header_reset_is_delta(self):
        response = FakeResponse(
            headers={"RateLimit": "limit=50, remaining=10, reset=5"}
        )
        meters, _ = extract_ietf(response, now=1000.0)
        meter = meters["request"]
        self.assertEqual(meter.remaining, 10)
        self.assertEqual(meter.reset_at, 1005.0)

    def test_missing_headers_returns_no_meters(self):
        meters, learned_cost = extract_ietf(FakeResponse(), now=1000.0)
        self.assertEqual(meters, {})
        self.assertIsNone(learned_cost)


class XRateLimitExtractorTests(SimpleTestCase):
    def test_reset_is_epoch_not_delta(self):
        response = FakeResponse(
            headers={
                "X-RateLimit-Limit": "60",
                "X-RateLimit-Remaining": "5",
                "X-RateLimit-Reset": "1700",
            }
        )
        meters, _ = extract_x_ratelimit(response, now=1000.0)
        meter = meters["request"]
        self.assertEqual(meter.remaining, 5)
        # Epoch form: reset_at is the header value itself, NOT now + value.
        self.assertEqual(meter.reset_at, 1700.0)

    def test_missing_headers_returns_no_meters(self):
        meters, _ = extract_x_ratelimit(FakeResponse(), now=1000.0)
        self.assertEqual(meters, {})


class GraphqlCostExtractorTests(SimpleTestCase):
    def test_extracts_cost_meter_and_learned_cost(self):
        response = FakeResponse(
            json_data={
                "extensions": {
                    "cost": {
                        "requestedQueryCost": 15,
                        "actualQueryCost": 12,
                        "throttleStatus": {
                            "maximumAvailable": 1000,
                            "currentlyAvailable": 700,
                            "restoreRate": 50,
                        },
                    }
                }
            }
        )
        meters, learned_cost = extract_graphql_cost(response, now=1000.0)
        meter = meters["cost"]
        self.assertEqual(meter.remaining, 700)
        self.assertEqual(meter.limit, 1000)
        # deficit=300, restoreRate=50 -> 6s to refill.
        self.assertEqual(meter.reset_at, 1006.0)
        self.assertEqual(learned_cost, {"cost": 12})

    def test_non_json_response_returns_no_meters(self):
        meters, learned_cost = extract_graphql_cost(
            FakeResponse(json_error=True), now=1000.0
        )
        self.assertEqual(meters, {})
        self.assertIsNone(learned_cost)

    def test_missing_cost_extension_returns_no_meters(self):
        meters, _ = extract_graphql_cost(FakeResponse(json_data={}), now=1000.0)
        self.assertEqual(meters, {})


class RetryAfterTests(SimpleTestCase):
    def test_delta_seconds_form(self):
        response = FakeResponse(headers={"Retry-After": "30"})
        self.assertEqual(parse_retry_after(response, now=1000.0), 1030.0)

    def test_http_date_form(self):
        response = FakeResponse(
            headers={"Retry-After": "Thu, 01 Jan 1970 00:20:00 GMT"}
        )
        self.assertEqual(parse_retry_after(response, now=0.0), 1200.0)

    def test_missing_header_returns_none(self):
        self.assertIsNone(parse_retry_after(FakeResponse(), now=1000.0))


def _policy(**overrides):
    defaults = dict(
        headroom_pct=0.5,
        min_interval=0.0,
        fair_interval=True,
        short_wait_threshold=5.0,
        max_requests_per_run=10_000,
        max_cost_per_run=100_000,
    )
    defaults.update(overrides)
    return RateLimitPolicy(**defaults)


class DecidePureFunctionTests(SimpleTestCase):
    """Direct tests of pacer.decide() — the shared Ready/ShortWait/Defer algorithm."""

    def test_headroom_reserves_half_of_remaining(self):
        snapshot = BudgetSnapshot(
            scope="s",
            meters={"request": MeterSnapshot(unit="request", remaining=100)},
        )
        policy = _policy(min_interval=0.0)
        # usable = floor(100 * 0.5) = 50: cost 50 fits, cost 51 does not.
        ready = decide(snapshot, {}, {"request": 50}, policy, now=1000.0)
        self.assertEqual(ready.kind, READY)
        over = decide(snapshot, {}, {"request": 51}, policy, now=1000.0)
        self.assertEqual(over.kind, DEFER)
        self.assertEqual(over.reason, "headroom")

    def test_fair_interval_smooths_drain(self):
        snapshot = BudgetSnapshot(
            scope="s",
            meters={
                "request": MeterSnapshot(
                    unit="request", remaining=100, reset_at=1100.0
                )
            },
        )
        # usable = floor(100 * 0.5) = 50; fair = (1100-1000)/50 = 2s.
        policy = _policy(min_interval=0.0)
        decision = decide(snapshot, {}, {"request": 1}, policy, now=1000.0)
        self.assertEqual(decision.kind, SHORT_WAIT)
        self.assertAlmostEqual(decision.wait_seconds, 2.0)
        self.assertEqual(decision.reason, "fair_interval")

    def test_short_wait_below_threshold(self):
        snapshot = BudgetSnapshot(
            scope="s",
            meters={
                "request": MeterSnapshot(
                    unit="request", remaining=100, reset_at=1100.0
                )
            },
        )
        policy = _policy(min_interval=0.0, short_wait_threshold=5.0)
        decision = decide(snapshot, {}, {"request": 1}, policy, now=1000.0)
        self.assertEqual(decision.kind, SHORT_WAIT)
        self.assertLess(decision.wait_seconds, 5.0)

    def test_long_wait_defers_with_eta(self):
        snapshot = BudgetSnapshot(
            scope="s",
            meters={
                "request": MeterSnapshot(
                    unit="request", remaining=100, reset_at=1500.0
                )
            },
        )
        # usable = 50; fair = (1500-1000)/50 = 10s >= 5s threshold -> Defer.
        policy = _policy(min_interval=0.0, short_wait_threshold=5.0)
        decision = decide(snapshot, {}, {"request": 1}, policy, now=1000.0)
        self.assertEqual(decision.kind, DEFER)
        self.assertAlmostEqual(decision.eta, 1010.0)

    def test_most_constraining_meter_wins(self):
        snapshot = BudgetSnapshot(
            scope="s",
            meters={
                "request": MeterSnapshot(unit="request", remaining=10_000),
                "cost": MeterSnapshot(unit="cost", remaining=10),
            },
        )
        policy = _policy(min_interval=0.0)
        # request usable is enormous (fine); cost usable = floor(10*0.5)=5 < 6.
        decision = decide(
            snapshot, {}, {"request": 1, "cost": 6}, policy, now=1000.0
        )
        self.assertEqual(decision.kind, DEFER)
        self.assertEqual(decision.reason, "headroom")
        self.assertEqual(decision.unit, "cost")

    def test_retry_after_defers_immediately(self):
        snapshot = BudgetSnapshot(scope="s", exhausted_until=1030.0)
        policy = _policy()
        decision = decide(snapshot, {}, {"request": 1}, policy, now=1000.0)
        self.assertEqual(decision.kind, DEFER)
        self.assertEqual(decision.reason, "retry_after")
        self.assertEqual(decision.eta, 1030.0)

    def test_per_run_cap_defers_without_eta(self):
        snapshot = BudgetSnapshot(scope="s")
        policy = _policy(max_requests_per_run=5)
        decision = decide(
            snapshot, {"request": 5}, {"request": 1}, policy, now=1000.0
        )
        self.assertEqual(decision.kind, DEFER)
        self.assertEqual(decision.reason, "per_run_cap")
        self.assertIsNone(decision.eta)

    def test_cold_start_unknown_meters_ready_with_fallback(self):
        snapshot = BudgetSnapshot(scope="s")
        policy = _policy()
        decision = decide(snapshot, {}, {"request": 1}, policy, now=1000.0)
        self.assertEqual(decision.kind, READY)
        self.assertTrue(decision.fallback)


class InMemoryBudgetStoreTryAcquireTests(SimpleTestCase):
    def test_ready_reserves_before_send(self):
        store = InMemoryBudgetStore()
        store.update_meters(
            "s", {"request": MeterSnapshot(unit="request", remaining=100)}
        )
        policy = _policy(min_interval=1.0)
        decision = try_acquire(store, "s", {"request": 40}, "run-1", policy, now=1000.0)
        self.assertEqual(decision.kind, READY)
        snapshot = store.get_snapshot("s")
        self.assertEqual(snapshot.meters["request"].remaining, 60)
        self.assertEqual(snapshot.next_allowed_at, 1001.0)

    def test_short_wait_never_mutates_store(self):
        store = InMemoryBudgetStore()
        store.update_meters(
            "s",
            {"request": MeterSnapshot(unit="request", remaining=100, reset_at=1100.0)},
        )
        policy = _policy(min_interval=0.0)
        before = store.get_snapshot("s")
        decision = try_acquire(store, "s", {"request": 1}, "run-1", policy, now=1000.0)
        self.assertEqual(decision.kind, SHORT_WAIT)
        after = store.get_snapshot("s")
        self.assertEqual(before.meters["request"].remaining, after.meters["request"].remaining)
        self.assertEqual(before.next_allowed_at, after.next_allowed_at)

    def test_short_wait_caller_must_reacquire_after_sleep(self):
        # Simulates D3: sleep, then re-invoke try_acquire rather than sending.
        store = InMemoryBudgetStore()
        store.update_meters(
            "s", {"request": MeterSnapshot(unit="request", remaining=100)}
        )
        policy = _policy(min_interval=2.0, fair_interval=False)

        first = try_acquire(store, "s", {"request": 1}, "run-1", policy, now=1000.0)
        self.assertEqual(first.kind, READY)  # reserves; next_allowed_at = 1002.0

        second = try_acquire(store, "s", {"request": 1}, "run-1", policy, now=1000.0)
        self.assertEqual(second.kind, SHORT_WAIT)
        self.assertAlmostEqual(second.wait_seconds, 2.0)

        third = try_acquire(
            store, "s", {"request": 1}, "run-1", policy, now=1000.0 + second.wait_seconds
        )
        self.assertEqual(third.kind, READY)

    def test_per_run_cap_still_lets_barrier_close(self):
        """Capped units get a Defer with no eta so the caller can terminalize
        (give up) instead of requeuing forever — see task 3.6."""
        store = InMemoryBudgetStore()
        policy = _policy(min_interval=0.0, max_requests_per_run=2)
        for _ in range(2):
            decision = try_acquire(store, "s", {"request": 1}, "run-1", policy, now=1000.0)
            self.assertEqual(decision.kind, READY)
        capped = try_acquire(store, "s", {"request": 1}, "run-1", policy, now=1000.0)
        self.assertEqual(capped.kind, DEFER)
        self.assertEqual(capped.reason, "per_run_cap")
        self.assertIsNone(capped.eta)

    def test_concurrent_acquire_reserves_at_most_once(self):
        """Two workers racing for a scope with usable allowing only one Ready
        (task 3.8 / spec 'Concurrent acquire reserves at most once')."""
        store = InMemoryBudgetStore()
        store.update_meters(
            "s", {"request": MeterSnapshot(unit="request", remaining=1)}
        )
        policy = _policy(headroom_pct=0.0, min_interval=0.0)
        barrier = threading.Barrier(2)

        def attempt():
            barrier.wait(timeout=5)
            return try_acquire(store, "s", {"request": 1}, "run-1", policy, now=1000.0)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))

        ready_count = sum(1 for r in results if r.kind == READY)
        self.assertEqual(ready_count, 1)

    def test_set_exhausted_until_forces_defer(self):
        store = InMemoryBudgetStore()
        store.set_exhausted_until("s", 1030.0)
        policy = _policy()
        decision = try_acquire(store, "s", {"request": 1}, "run-1", policy, now=1000.0)
        self.assertEqual(decision.kind, DEFER)
        self.assertEqual(decision.reason, "retry_after")


class ReconcileTests(SimpleTestCase):
    def test_reconcile_response_updates_store(self):
        store = InMemoryBudgetStore()
        profile = get_profile("ietf")
        response = FakeResponse(
            headers={
                "RateLimit-Limit": "100",
                "RateLimit-Remaining": "77",
                "RateLimit-Reset": "10",
            }
        )
        reconcile_response(store, "s", profile, response, now=1000.0)
        snapshot = store.get_snapshot("s")
        self.assertEqual(snapshot.meters["request"].remaining, 77)

    def test_record_429_sets_exhausted_until(self):
        store = InMemoryBudgetStore()
        response = FakeResponse(headers={"Retry-After": "15"})
        until_ts = record_429(store, "s", response, now=1000.0)
        self.assertEqual(until_ts, 1015.0)
        self.assertEqual(store.get_snapshot("s").exhausted_until, 1015.0)

    def test_record_429_without_retry_after_is_noop(self):
        store = InMemoryBudgetStore()
        until_ts = record_429(store, "s", FakeResponse(), now=1000.0)
        self.assertIsNone(until_ts)
        self.assertIsNone(store.get_snapshot("s").exhausted_until)


class SourceRateLimitProfileTests(TestCase):
    def test_default_profile_is_blank(self):
        source = Source.objects.create(
            key="t1", name="t", parser_key="cc", base_search_url="https://x/{term}"
        )
        self.assertEqual(source.rate_limit_profile, "")
        self.assertIsNone(get_profile(source.rate_limit_profile))

    def test_profile_can_be_set(self):
        source = Source.objects.create(
            key="t2",
            name="t",
            parser_key="shopify",
            base_search_url="https://x/{term}",
            rate_limit_profile="graphql_cost",
        )
        self.assertEqual(get_profile(source.rate_limit_profile).key, "graphql_cost")
