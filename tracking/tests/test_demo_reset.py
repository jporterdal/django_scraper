"""demo-sandbox-reset: seed restore, history, boot command, idle gating, concurrency (tasks 5.1-5.6)."""

import tempfile
import time
from datetime import timedelta
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from tracking.demo import dataset
from tracking.demo.reset import perform_reset
from tracking.models import (
    DemoState,
    FetchJob,
    ObservedCategoryValue,
    SearchableItem,
    SearchResult,
    Source,
    Tag,
    UpdateSchedule,
    WebUpdate,
)

from .demo_base import DEMO_REQUEST_SETTINGS, SEED_ITEM_PKS, SEED_TAG_PKS, DemoTestCase, run_demo_subprocess

# design D9: an in-request reset must stay quick for the visitor who triggers it.
RESET_TIME_BUDGET_SECONDS = 2.5
SEED_TEXTS = sorted(item["text"] for item in dataset.seed()["items"])


class DemoStateMigrationTests(TestCase):
    def test_no_missing_migrations(self):
        call_command("makemigrations", "tracking", "--check", "--dry-run", stdout=StringIO())


class ResetRestoresSeedTests(DemoTestCase):
    def _seed_only(self):
        self.assertEqual(sorted(SearchableItem.objects.values_list("text", flat=True)), SEED_TEXTS)
        self.assertEqual(sorted(SearchableItem.objects.values_list("pk", flat=True)), SEED_ITEM_PKS)
        self.assertEqual(sorted(Tag.objects.values_list("pk", flat=True)), SEED_TAG_PKS)
        self.assertEqual(
            sorted(Source.objects.values_list("key", flat=True)),
            sorted(s["key"] for s in dataset.sources()),
        )
        self.assertEqual(UpdateSchedule.objects.count(), len(dataset.seed()["schedules"]))

    def test_visitor_data_removed(self):
        visitor = self.visitor_item()
        Tag.objects.create(name="Visitor tag")
        visitor_run = WebUpdate.objects.create()
        SearchResult.objects.create(
            title="x", search_term="x", price=1.0, item=visitor, update=visitor_run,
            source_id="dragonhoard",
        )
        perform_reset(force=True)
        self._seed_only()
        self.assertFalse(WebUpdate.objects.filter(pk=visitor_run.pk).exists())
        self.assertFalse(SearchResult.objects.filter(item_id=visitor.pk).exists())

    def test_visitor_pks_never_reused_and_stale_links_404(self):
        first = self.visitor_item(text="First visitor item")
        perform_reset(force=True)
        second = self.visitor_item(text="Second visitor item")
        self.assertGreater(second.pk, first.pk)
        self.assertEqual(self.client.get(f"/item/{first.pk}/").status_code, 404)
        for pk in SEED_ITEM_PKS:
            self.assertEqual(self.client.get(f"/item/{pk}/").status_code, 200)

    def test_seed_metadata_and_presets(self):
        for spec in dataset.seed()["items"]:
            item = SearchableItem.objects.get(pk=spec["pk"])
            self.assertEqual(item.metadata.status, "matched")
            self.assertEqual(item.metadata.external_id, spec["metadata_external_id"])
            self.assertEqual(item.itemsource_set.count(), len(spec["sources"]))

    def test_not_due_reset_does_nothing(self):
        visitor = self.visitor_item()
        self.assertFalse(perform_reset())
        self.assertTrue(SearchableItem.objects.filter(pk=visitor.pk).exists())


@override_settings(DEMO_MODE=True, DEMO_SEED_HISTORY_DAYS=28, **DEMO_REQUEST_SETTINGS)
class SeedHistoryTests(TestCase):
    def test_history_spans_window_and_ends_at_reset(self):
        started = time.perf_counter()
        perform_reset(force=True)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, RESET_TIME_BUDGET_SECONDS, f"reset took {elapsed:.2f}s")

        now = timezone.now()
        timestamps = list(WebUpdate.objects.order_by("timestamp").values_list("timestamp", flat=True))
        self.assertEqual(len(timestamps), 28)
        self.assertLess(now - timestamps[-1], timedelta(minutes=10))
        self.assertGreater(timestamps[-1] - timestamps[0], timedelta(days=26))

        for pk in SEED_ITEM_PKS:
            stamps = SearchResult.objects.filter(item_id=pk).values_list("update__timestamp", flat=True)
            self.assertTrue(stamps, pk)
            self.assertGreater(max(stamps) - min(stamps), timedelta(days=20), pk)

        self.assertEqual(
            set(FetchJob.objects.values_list("status", flat=True)), {FetchJob.Status.SUCCESS}
        )
        self.assertTrue(ObservedCategoryValue.objects.exists())

        history = self.client.get("/view_updates/")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.context["paginator"].count, 28)

        page = self.client.get("/view_terms/")
        for text in SEED_TEXTS:
            self.assertContains(page, text)


class DemoResetCommandTests(DemoTestCase):
    def test_runs_twice_leaving_only_seed_data(self):
        self.visitor_item()
        for _ in range(2):
            out = StringIO()
            call_command("demo_reset", stdout=out)
            self.assertIn("reset", out.getvalue())
        self.assertEqual(sorted(SearchableItem.objects.values_list("pk", flat=True)), SEED_ITEM_PKS)


class DemoResetCommandRefusesTests(TestCase):
    def test_refuses_outside_demo_mode(self):
        SearchableItem.objects.create(text="Production data")
        with self.assertRaises(CommandError):
            call_command("demo_reset", stdout=StringIO())
        self.assertTrue(SearchableItem.objects.filter(text="Production data").exists())


class IdleGatedResetMiddlewareTests(DemoTestCase):
    def _set_state(self, activity_ago, reset_ago):
        now = timezone.now()
        DemoState.objects.filter(pk=1).update(
            last_activity_at=now - activity_ago, last_reset_at=now - reset_ago
        )

    def _visible_texts(self):
        response = self.client.get("/view_terms/")
        return sorted(item.text for item in response.context["object_list"])

    def test_reset_after_idle_hour_serves_fresh_data(self):
        self.visitor_item(text="Visitor leftover")
        self._set_state(activity_ago=timedelta(hours=2), reset_ago=timedelta(hours=2))
        self.assertEqual(self._visible_texts(), SEED_TEXTS)
        state = DemoState.objects.get(pk=1)
        self.assertLess(timezone.now() - state.last_reset_at, timedelta(minutes=1))
        self.assertLess(timezone.now() - state.last_activity_at, timedelta(minutes=1))

    def test_active_visitor_not_reset(self):
        self.visitor_item(text="Visitor leftover")
        self._set_state(activity_ago=timedelta(minutes=5), reset_ago=timedelta(hours=3))
        self.assertIn("Visitor leftover", self._visible_texts())

    def test_recent_reset_not_repeated(self):
        self.visitor_item(text="Visitor leftover")
        self._set_state(activity_ago=timedelta(hours=3), reset_ago=timedelta(minutes=30))
        self.assertIn("Visitor leftover", self._visible_texts())

    def test_idle_threshold_includes_activity_write_interval(self):
        self.visitor_item(text="Visitor leftover")
        # Idle just past DEMO_RESET_IDLE_SECONDS but within the write interval:
        # the stored activity may trail the real last request, so no reset yet.
        self._set_state(activity_ago=timedelta(seconds=3630), reset_ago=timedelta(hours=3))
        self.assertIn("Visitor leftover", self._visible_texts())

    @override_settings(DEMO_RESET_IDLE_SECONDS=600, DEMO_RESET_MIN_INTERVAL_SECONDS=1800)
    def test_overridden_thresholds_honored(self):
        self.visitor_item(text="Visitor leftover")
        self._set_state(activity_ago=timedelta(minutes=9), reset_ago=timedelta(minutes=31))
        self.assertIn("Visitor leftover", self._visible_texts())
        self._set_state(activity_ago=timedelta(minutes=12), reset_ago=timedelta(minutes=29))
        self.assertIn("Visitor leftover", self._visible_texts())
        self._set_state(activity_ago=timedelta(minutes=12), reset_ago=timedelta(minutes=31))
        self.assertEqual(self._visible_texts(), SEED_TEXTS)

    def test_activity_written_at_most_once_per_interval(self):
        DemoState.objects.filter(pk=1).update(last_activity_at=timezone.now() - timedelta(seconds=5))
        stored = DemoState.objects.get(pk=1).last_activity_at
        self.client.get("/view_terms/")
        self.assertEqual(DemoState.objects.get(pk=1).last_activity_at, stored)


CONCURRENCY_SCRIPT = """
import threading
from datetime import timedelta
from django.core.management import call_command
from django.db import connections
from django.test import Client
from django.test.utils import setup_test_environment
from django.utils import timezone
from tracking.demo import middleware as demo_middleware
from tracking.demo.reset import perform_reset
from tracking.models import DemoState, SearchableItem

setup_test_environment()
call_command("migrate", verbosity=0)
perform_reset(force=True)
SearchableItem.objects.create(text="Visitor leftover")
pre_count = SearchableItem.objects.count()
long_ago = timezone.now() - timedelta(hours=3)
DemoState.objects.filter(pk=1).update(last_reset_at=long_ago, last_activity_at=long_ago)
connections.close_all()

performed = []
real_reset = demo_middleware.perform_reset
def counting_reset(*args, **kwargs):
    result = real_reset(*args, **kwargs)
    performed.append(result)
    return result
demo_middleware.perform_reset = counting_reset

VISITORS = 8
barrier = threading.Barrier(VISITORS + 1)
responses, errors, observed = [], [], set()
stop = threading.Event()

def visitor():
    try:
        client = Client()
        barrier.wait()
        response = client.get("/view_terms/")
        responses.append((response.status_code, response.content.decode()))
    except Exception as exc:
        errors.append(repr(exc))
    finally:
        connections.close_all()

def reader():
    try:
        barrier.wait()
        while not stop.is_set():
            observed.add(SearchableItem.objects.count())
    except Exception as exc:
        errors.append(repr(exc))
    finally:
        connections.close_all()

threads = [threading.Thread(target=visitor) for _ in range(VISITORS)]
reader_thread = threading.Thread(target=reader)
for thread in threads + [reader_thread]:
    thread.start()
for thread in threads:
    thread.join()
stop.set()
reader_thread.join()

seed_texts = %(seed_texts)r
print(json.dumps({
    "errors": errors,
    "statuses": [status for status, _ in responses],
    "all_post_reset": all(
        "Visitor leftover" not in body and all(text in body for text in seed_texts)
        for _, body in responses
    ),
    "resets_performed": performed.count(True),
    "observed_counts": sorted(observed),
    "pre_count": pre_count,
    "post_count": SearchableItem.objects.count(),
}))
"""


class ConcurrentResetTests(SimpleTestCase):
    """File-backed SQLite with the real demo options (WAL + BEGIN IMMEDIATE)."""

    def test_simultaneous_arrivals_reset_exactly_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_demo_subprocess(
                CONCURRENCY_SCRIPT % {"seed_texts": SEED_TEXTS},
                tmp,
                extra_env={"DEMO_SEED_HISTORY_DAYS": "7"},
                timeout=180,
            )
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["statuses"], [200] * 8)
        self.assertTrue(result["all_post_reset"])
        self.assertEqual(result["resets_performed"], 1)
        # A reader outside the middleware only ever sees a complete state.
        self.assertLessEqual(
            set(result["observed_counts"]), {result["pre_count"], result["post_count"]}
        )
        self.assertEqual(result["post_count"], len(SEED_TEXTS))
