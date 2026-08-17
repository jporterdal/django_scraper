"""Fan-out background updates (D8) + HTMX progress endpoint.

These tests must pass with NO Redis running: Huey is configured in immediate
(eager) mode under the test suite, so enqueuing ``fetch_one`` executes it
inline in-process. All fetches are mocked; there is no network access.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse

from huey.contrib.djhuey import HUEY

from tracking.locks import reset_in_memory_lock
from tracking.models import FetchJob, SearchResult, WebUpdate
from tracking.tests.factories import make_item, make_item_source
from tracking.tests.base import AuthedClientTestCase, LinkedSourceTestCase
from tracking.scrape import FetchOutcome
from tracking.tasks import dispatch_fan_out, fetch_one


def _ok_outcome(result_count=0):
    return FetchOutcome(
        ok=True, http_status=200, error_message="", result_count=result_count
    )


def _mock_parser(results):
    parser = MagicMock()
    parser.results = results
    return parser


class BackgroundUpdateTests(LinkedSourceTestCase):
    def setUp(self):
        super().setUp()
        # D11's in-memory lock singleton is process-wide; TestCase's rolled
        # back transactions reuse small pks across tests, so reset it each
        # test (see tracking.locks.reset_in_memory_lock).
        reset_in_memory_lock()

    def test_huey_runs_in_immediate_mode(self):
        # Guards the "no Redis needed for tests" contract for Step 3.
        self.assertTrue(HUEY.immediate)

    @patch("tracking.scrape.Fetcher.from_settings", return_value=MagicMock())
    @patch("tracking.scrape._run_parser_search")
    def test_post_runs_fan_out_inline_and_marks_done(self, mock_run_parser, _mock_fetcher):
        mock_run_parser.return_value = _ok_outcome(result_count=1)
        mock_parser = _mock_parser([
            {"title": "Widget", "price": 9.99, "category": "Hardware", "instock": 1},
        ])
        with patch.dict(
            "tracking.parsers.sources",
            {"cc": MagicMock(return_value=mock_parser)},
        ):
            response = self.client.post(reverse("update"), {"mode": "all"})

        self.assertEqual(response.status_code, 302)
        webupdate = WebUpdate.objects.get()
        # Immediate mode ran the fan-out's single unit task inline, so the
        # run is already finished.
        self.assertEqual(webupdate.status, WebUpdate.Status.DONE)
        self.assertEqual(webupdate.total_searches, 1)
        self.assertEqual(webupdate.completed_searches, 1)
        self.assertEqual(webupdate.result_count, 1)
        self.assertEqual(webupdate.error_count, 0)
        self.assertEqual(
            SearchResult.objects.filter(update=webupdate).count(), 1
        )

    @patch("tracking.scrape.Fetcher.from_settings", return_value=MagicMock())
    @patch("tracking.scrape._run_parser_search")
    def test_dispatch_scopes_to_item_ids(self, mock_run_parser, _mock_fetcher):
        # A second active item-source that must NOT be searched when scoped.
        other = make_item(text="other item", active=True)
        make_item_source(other, self.source)
        mock_run_parser.return_value = _ok_outcome(result_count=0)

        with patch.dict(
            "tracking.parsers.sources",
            {"cc": MagicMock(return_value=_mock_parser([]))},
        ):
            webupdate = dispatch_fan_out(item_ids=[self.item.pk])

        webupdate.refresh_from_db()
        self.assertEqual(webupdate.status, WebUpdate.Status.DONE)
        # Only the single scoped item-source was searched.
        self.assertEqual(webupdate.total_searches, 1)
        self.assertEqual(webupdate.completed_searches, 1)

    def test_fetch_one_terminalizes_failed_on_unexpected_error(self):
        webupdate = WebUpdate.objects.create(
            status=WebUpdate.Status.PENDING, total_searches=1
        )
        with patch(
            "tracking.scrape.fetch_one_unit", side_effect=RuntimeError("boom")
        ):
            # Immediate mode captures the task exception; it does not
            # propagate out of the outer call.
            fetch_one(webupdate.pk, self.item_source.pk)

        webupdate.refresh_from_db()
        # A single unexpected unit failure terminalizes that unit as failed
        # and still closes the barrier (D9) — WebUpdate.FAILED is reserved
        # for fan-out/orchestrator failure only, not a single unit error.
        self.assertEqual(webupdate.status, WebUpdate.Status.DONE)
        self.assertEqual(webupdate.completed_searches, 1)
        self.assertEqual(webupdate.error_count, 1)
        job = FetchJob.objects.get(webupdate=webupdate)
        self.assertEqual(job.status, FetchJob.Status.HTTP_ERROR)

    def test_fetch_one_missing_webupdate_is_noop(self):
        # Should not raise even though the row does not exist.
        result = fetch_one(999999, self.item_source.pk)
        # In immediate mode the return value is a Result wrapper; the task
        # itself returns None for a missing WebUpdate.
        self.assertIsNone(result())

    def test_fetch_one_missing_item_source_is_noop(self):
        webupdate = WebUpdate.objects.create(
            status=WebUpdate.Status.PENDING, total_searches=1
        )
        result = fetch_one(webupdate.pk, 999999)
        self.assertIsNone(result())


class UpdateProgressViewTests(AuthedClientTestCase):
    def test_progress_running_keeps_polling(self):
        webupdate = WebUpdate.objects.create(
            status=WebUpdate.Status.RUNNING,
            total_searches=4,
            completed_searches=1,
            result_count=2,
        )
        response = self.client.get(
            reverse("update_progress", args=[webupdate.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Updating prices")
        # Still polling while RUNNING.
        self.assertContains(response, "hx-trigger")

    def test_progress_done_stops_polling(self):
        webupdate = WebUpdate.objects.create(
            status=WebUpdate.Status.DONE,
            total_searches=4,
            completed_searches=4,
            result_count=7,
        )
        response = self.client.get(
            reverse("update_progress", args=[webupdate.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Update complete")
        # No polling trigger once finished.
        self.assertNotContains(response, "hx-trigger")

    def test_progress_failed_state(self):
        webupdate = WebUpdate.objects.create(
            status=WebUpdate.Status.FAILED,
            total_searches=4,
            completed_searches=2,
        )
        response = self.client.get(
            reverse("update_progress", args=[webupdate.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Update failed")
        self.assertNotContains(response, "hx-trigger")

    def test_progress_unknown_pk_404(self):
        response = self.client.get(reverse("update_progress", args=[424242]))
        self.assertEqual(response.status_code, 404)

    def test_list_page_shows_progress_container_when_update_param(self):
        webupdate = WebUpdate.objects.create(status=WebUpdate.Status.RUNNING)
        response = self.client.get(
            reverse("view_terms"), {"update": webupdate.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, reverse("update_progress", args=[webupdate.pk])
        )
        self.assertContains(response, "htmx.org")
