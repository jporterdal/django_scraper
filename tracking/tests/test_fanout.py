"""Fan-out unit of work (D8), terminalize/DONE (D9), give-up (D10), idempotent
enqueue locks (D11), and profiled 429 handling (D6) — tasks 4-6.

Offline only: the in-memory lock/budget stores stand in for Redis (matching
the "Tests: Offline" contract used throughout ``tracking/ratelimit``). Redis
paths (``RedisUnitLock``, ``RedisBudgetStore``) were hand-verified against a
local ``redis-server`` instead — see the api-rate-limit-awareness change
summary.
"""

import time
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from tracking.locks import (
    InMemoryUnitLock,
    lock_ttl_for_eta,
    reset_in_memory_lock,
    unit_lock_key,
)
from tracking.models import FetchJob, WebUpdate
from tracking.ratelimit import DEFER, PaceDecision
from tracking.ratelimit.pacer import RateLimitPolicy
from tracking.scrape import (
    UnitResult,
    _dedupe_unit_candidates,
    _should_give_up,
    terminalize,
)
from tracking.tasks import dispatch_fan_out, fetch_one
from tracking.tests.factories import make_linked_item, make_search_result


class InMemoryUnitLockTests(SimpleTestCase):
    """D11 lock decision logic, exercised against the fake (no Redis)."""

    def test_acquire_then_acquire_fails(self):
        lock = InMemoryUnitLock()
        self.assertTrue(lock.acquire("k", 60))
        self.assertFalse(lock.acquire("k", 60))

    def test_release_then_acquire_succeeds(self):
        lock = InMemoryUnitLock()
        lock.acquire("k", 60)
        lock.release("k")
        self.assertTrue(lock.acquire("k", 60))

    def test_expired_ttl_allows_reacquire(self):
        lock = InMemoryUnitLock()
        self.assertTrue(lock.acquire("k", 0.01))
        time.sleep(0.03)
        self.assertTrue(lock.acquire("k", 60))

    def test_release_of_unheld_key_is_a_noop(self):
        lock = InMemoryUnitLock()
        lock.release("never-held")  # must not raise

    def test_lock_ttl_for_eta_uses_minimum_when_immediate(self):
        self.assertEqual(lock_ttl_for_eta(None), 300.0)
        self.assertEqual(lock_ttl_for_eta(1000.0, now=1000.0), 300.0)

    def test_lock_ttl_for_eta_adds_grace_past_minimum(self):
        # eta 500s out -> 500 + 120 grace = 620, which exceeds the 300s floor.
        self.assertEqual(lock_ttl_for_eta(1500.0, now=1000.0), 620.0)


class DispatchFanOutLockTests(TestCase):
    """D11: dispatch_fan_out skips duplicate enqueue when the lock is held."""

    @classmethod
    def setUpTestData(cls):
        cls.source, cls.item, cls.item_source = make_linked_item()

    def setUp(self):
        reset_in_memory_lock()

    def test_enqueues_one_task_per_item_source(self):
        with patch("tracking.tasks.fetch_one") as mock_fetch_one:
            webupdate = dispatch_fan_out()

        self.assertEqual(webupdate.total_searches, 1)
        mock_fetch_one.assert_called_once_with(
            webupdate.pk, self.item_source.pk, attempt=0
        )

    def test_pre_held_lock_skips_enqueue(self):
        # Simulate a lock already held for this exact (webupdate, item_source)
        # pair by acquiring it with the same key dispatch_fan_out will derive
        # for the WebUpdate it is about to create (pk is deterministic for a
        # fresh test-DB sequence).
        webupdate = WebUpdate.objects.create(status=WebUpdate.Status.PENDING, total_searches=1)
        from tracking.locks import get_unit_lock

        lock = get_unit_lock()
        lock.acquire(unit_lock_key(webupdate.pk, self.item_source.pk), 300)

        with patch("tracking.models.WebUpdate.objects.create", return_value=webupdate), \
                patch("tracking.tasks.fetch_one") as mock_fetch_one:
            dispatch_fan_out()

        mock_fetch_one.assert_not_called()

    def test_zero_item_sources_marks_done_immediately(self):
        """No units to fan out to means terminalize() never runs, so nothing
        else would flip PENDING -> DONE and the progress UI would poll
        forever (e.g. a schedule's tag has active items but none are wired
        to an ItemSource yet)."""
        with patch("tracking.tasks.fetch_one") as mock_fetch_one:
            webupdate = dispatch_fan_out(item_ids=[])

        self.assertEqual(webupdate.total_searches, 0)
        self.assertEqual(webupdate.status, WebUpdate.Status.DONE)
        mock_fetch_one.assert_not_called()


class TerminalizeTests(TestCase):
    """D9 barrier (idempotent + CAS DONE) and D12 per-unit dedup."""

    @classmethod
    def setUpTestData(cls):
        cls.source, cls.item, cls.item_source = make_linked_item()

    def _candidates(self, price=9.99, instock=1, title="Widget"):
        return [
            {"title": title, "price": price, "category": "Hardware", "instock": instock}
        ]

    def test_success_stores_and_bumps_counters(self):
        webupdate = WebUpdate.objects.create(status=WebUpdate.Status.PENDING, total_searches=1)
        job = terminalize(
            webupdate, self.item, self.source, self.item.text, "https://x",
            FetchJob.Status.SUCCESS, http_status=200, candidates=self._candidates(),
        )
        webupdate.refresh_from_db()
        self.assertEqual(job.stored_count, 1)
        self.assertEqual(webupdate.status, WebUpdate.Status.DONE)
        self.assertEqual(webupdate.completed_searches, 1)
        self.assertEqual(webupdate.result_count, 1)
        self.assertEqual(webupdate.error_count, 0)

    def test_duplicate_terminalize_is_a_noop(self):
        webupdate = WebUpdate.objects.create(status=WebUpdate.Status.PENDING, total_searches=1)
        first = terminalize(
            webupdate, self.item, self.source, self.item.text, "", FetchJob.Status.EMPTY
        )
        second = terminalize(
            webupdate, self.item, self.source, self.item.text, "", FetchJob.Status.EMPTY
        )
        self.assertEqual(first.pk, second.pk)
        webupdate.refresh_from_db()
        # Only counted once, not twice.
        self.assertEqual(webupdate.completed_searches, 1)
        self.assertEqual(FetchJob.objects.filter(webupdate=webupdate).count(), 1)

    def test_last_unit_cas_flips_running_to_done(self):
        webupdate = WebUpdate.objects.create(status=WebUpdate.Status.PENDING, total_searches=2)
        other_source, other_item, _ = make_linked_item(item_text="second widget")

        terminalize(
            webupdate, self.item, self.source, self.item.text, "", FetchJob.Status.EMPTY
        )
        webupdate.refresh_from_db()
        self.assertEqual(webupdate.status, WebUpdate.Status.RUNNING)

        terminalize(
            webupdate, other_item, other_source, other_item.text, "", FetchJob.Status.EMPTY
        )
        webupdate.refresh_from_db()
        self.assertEqual(webupdate.status, WebUpdate.Status.DONE)

    def test_error_status_bumps_error_count(self):
        webupdate = WebUpdate.objects.create(status=WebUpdate.Status.PENDING, total_searches=1)
        terminalize(
            webupdate, self.item, self.source, self.item.text, "",
            FetchJob.Status.GIVE_UP, error_message="gave up",
        )
        webupdate.refresh_from_db()
        self.assertEqual(webupdate.error_count, 1)
        self.assertEqual(webupdate.status, WebUpdate.Status.DONE)

    def test_dedupe_unit_candidates_skips_unchanged_and_within_unit_dupe(self):
        prior_update = WebUpdate.objects.create(status=WebUpdate.Status.DONE)
        make_search_result(
            self.item, self.source, prior_update, title="Widget", price=9.99, instock=1
        )
        this_update = WebUpdate.objects.create(status=WebUpdate.Status.PENDING, total_searches=1)

        candidates = [
            {"title": "Widget", "price": 9.99, "category": "Hardware", "instock": 1},  # unchanged
            {"title": "New Item", "price": 5.0, "category": "Hardware", "instock": 1},
            {"title": "New Item", "price": 5.0, "category": "Hardware", "instock": 1},  # dupe
        ]
        survivors, skipped = _dedupe_unit_candidates(
            candidates, self.item, self.source, this_update
        )
        self.assertEqual([c["title"] for c in survivors], ["New Item"])
        self.assertEqual(skipped, 2)


class GiveUpPolicyTests(TestCase):
    """D10: attempt cap and wall-clock give-up predicate."""

    def test_attempt_cap_triggers_give_up(self):
        webupdate = WebUpdate.objects.create()
        policy = RateLimitPolicy(max_defer_attempts=5, max_run_wall_clock_seconds=1800)
        self.assertTrue(_should_give_up(webupdate, attempt=5, policy=policy, now=time.time()))
        self.assertFalse(_should_give_up(webupdate, attempt=4, policy=policy, now=time.time()))

    def test_wall_clock_triggers_give_up(self):
        webupdate = WebUpdate.objects.create()
        policy = RateLimitPolicy(max_defer_attempts=5, max_run_wall_clock_seconds=1800)
        far_future = webupdate.timestamp.timestamp() + 3600
        self.assertTrue(_should_give_up(webupdate, attempt=0, policy=policy, now=far_future))


class FetchOneDeferTests(TestCase):
    """D10/D11 requeue vs give-up, exercised through the Huey ``fetch_one`` wrapper."""

    @classmethod
    def setUpTestData(cls):
        cls.source, cls.item, cls.item_source = make_linked_item()

    def setUp(self):
        reset_in_memory_lock()

    def test_defer_requeues_with_incremented_attempt(self):
        webupdate = WebUpdate.objects.create(status=WebUpdate.Status.PENDING, total_searches=1)
        decision = PaceDecision(kind=DEFER, eta=time.time() + 2, reason="fair_interval")

        with patch(
            "tracking.scrape.fetch_one_unit",
            return_value=UnitResult(deferred=True, decision=decision),
        ), patch("tracking.tasks.fetch_one.schedule") as mock_schedule:
            fetch_one(webupdate.pk, self.item_source.pk, attempt=0)

        mock_schedule.assert_called_once()
        self.assertEqual(
            mock_schedule.call_args.kwargs["kwargs"]["attempt"], 1
        )
        self.assertFalse(
            FetchJob.objects.filter(webupdate=webupdate).exists()
        )
        webupdate.refresh_from_db()
        self.assertEqual(webupdate.completed_searches, 0)

    def test_max_attempts_gives_up_instead_of_requeue(self):
        webupdate = WebUpdate.objects.create(status=WebUpdate.Status.PENDING, total_searches=1)
        decision = PaceDecision(kind=DEFER, eta=time.time() + 2, reason="fair_interval")

        with patch(
            "tracking.scrape.fetch_one_unit",
            return_value=UnitResult(deferred=True, decision=decision),
        ), patch("tracking.tasks.fetch_one.schedule") as mock_schedule:
            fetch_one(webupdate.pk, self.item_source.pk, attempt=5)

        mock_schedule.assert_not_called()
        job = FetchJob.objects.get(webupdate=webupdate)
        self.assertEqual(job.status, FetchJob.Status.GIVE_UP)
        webupdate.refresh_from_db()
        self.assertEqual(webupdate.status, WebUpdate.Status.DONE)

    def test_per_run_cap_defer_gives_up_immediately(self):
        # eta=None (per_run_cap) is the signal to give up regardless of
        # attempt count (design.md D3/D10).
        webupdate = WebUpdate.objects.create(status=WebUpdate.Status.PENDING, total_searches=1)
        decision = PaceDecision(kind=DEFER, eta=None, reason="per_run_cap")

        with patch(
            "tracking.scrape.fetch_one_unit",
            return_value=UnitResult(deferred=True, decision=decision),
        ), patch("tracking.tasks.fetch_one.schedule") as mock_schedule:
            fetch_one(webupdate.pk, self.item_source.pk, attempt=0)

        mock_schedule.assert_not_called()
        job = FetchJob.objects.get(webupdate=webupdate)
        self.assertEqual(job.status, FetchJob.Status.GIVE_UP)

    def test_duplicate_delivery_after_terminal_is_a_noop(self):
        webupdate = WebUpdate.objects.create(status=WebUpdate.Status.PENDING, total_searches=1)
        FetchJob.objects.create(
            webupdate=webupdate, item=self.item, source=self.source,
            search_term=self.item.text, status=FetchJob.Status.SUCCESS,
        )
        with patch("tracking.scrape.fetch_one_unit") as mock_unit:
            fetch_one(webupdate.pk, self.item_source.pk, attempt=0)

        mock_unit.assert_not_called()


class Profiled429Tests(TestCase):
    """D6: a profiled source's HTTP 429 defers via the store, not FetchJob.BLOCKED."""

    @classmethod
    def setUpTestData(cls):
        from tracking.tests.factories import make_source

        cls.source, cls.item, cls.item_source = make_linked_item(
            source=make_source(key="cc", rate_limit_profile="ietf"),
        )

    def test_429_defers_instead_of_blocking(self):
        fetcher = MagicMock()
        response = MagicMock(status_code=429, headers={"Retry-After": "30"})
        fetcher.get.return_value = response

        with patch.dict(
            "tracking.parsers.sources", {"cc": MagicMock(return_value=MagicMock(results=[]))}
        ):
            from tracking.scrape import fetch_one_unit

            webupdate = WebUpdate.objects.create(
                status=WebUpdate.Status.PENDING, total_searches=1
            )
            result = fetch_one_unit(webupdate, self.item_source, fetcher=fetcher)

        self.assertTrue(result.deferred)
        self.assertEqual(result.decision.reason, "retry_after")
        self.assertFalse(FetchJob.objects.filter(webupdate=webupdate).exists())
        # 429 must not have been retried transparently by urllib3 (profiled
        # send uses the 503-only session) — confirmed indirectly here by the
        # single fetcher.get call the mock fetcher records.
        self.assertEqual(fetcher.get.call_count, 1)
        self.assertTrue(fetcher.get.call_args.kwargs.get("profiled"))
