"""Phase 3 Step 3 — Huey background updates + HTMX progress endpoint.

These tests must pass with NO Redis running: Huey is configured in immediate
(eager) mode under the test suite, so enqueuing ``run_web_update_task`` executes
it inline in-process. All fetches are mocked; there is no network access.
"""

from unittest.mock import MagicMock, patch

from django.test import Client, TestCase
from django.urls import reverse

from huey.contrib.djhuey import HUEY

from .models import ItemSource, SearchableItem, SearchResult, Source, WebUpdate
from .scrape import FetchOutcome
from .tasks import run_web_update_task


def _ok_outcome(result_count=0):
    return FetchOutcome(
        ok=True, http_status=200, error_message="", result_count=result_count
    )


def _mock_parser(results):
    parser = MagicMock()
    parser.results = results
    return parser


class BackgroundUpdateTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.source, _ = Source.objects.update_or_create(
            key="cc",
            defaults={
                "name": "Test Source",
                "parser_key": "cc",
                "base_search_url": "https://example.com/search?s={term}",
            },
        )
        self.item = SearchableItem.objects.create(text="test item", active=True)
        ItemSource.objects.create(item=self.item, source=self.source)

    def test_huey_runs_in_immediate_mode(self):
        # Guards the "no Redis needed for tests" contract for Step 3.
        self.assertTrue(HUEY.immediate)

    @patch("tracking.scrape.Fetcher.from_settings", return_value=MagicMock())
    @patch("tracking.scrape._run_parser_search")
    def test_post_runs_task_inline_and_marks_done(self, mock_run_parser, _mock_fetcher):
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
        # Immediate mode ran the task inline, so the run is already finished.
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
    def test_task_scopes_to_item_ids(self, mock_run_parser, _mock_fetcher):
        # A second active item-source that must NOT be searched when scoped.
        other = SearchableItem.objects.create(text="other item", active=True)
        ItemSource.objects.create(item=other, source=self.source)
        mock_run_parser.return_value = _ok_outcome(result_count=0)

        webupdate = WebUpdate.objects.create(
            status=WebUpdate.Status.PENDING, total_searches=1
        )
        with patch.dict(
            "tracking.parsers.sources",
            {"cc": MagicMock(return_value=_mock_parser([]))},
        ):
            run_web_update_task(webupdate.pk, item_ids=[self.item.pk])

        webupdate.refresh_from_db()
        self.assertEqual(webupdate.status, WebUpdate.Status.DONE)
        # Only the single scoped item-source was searched.
        self.assertEqual(webupdate.total_searches, 1)
        self.assertEqual(webupdate.completed_searches, 1)

    def test_task_marks_failed_when_run_web_update_raises(self):
        webupdate = WebUpdate.objects.create(
            status=WebUpdate.Status.PENDING, total_searches=1
        )
        with patch(
            "tracking.scrape.run_web_update", side_effect=RuntimeError("boom")
        ):
            # Immediate mode captures the task exception; it does not propagate.
            run_web_update_task(webupdate.pk)

        webupdate.refresh_from_db()
        self.assertEqual(webupdate.status, WebUpdate.Status.FAILED)

    def test_task_missing_webupdate_is_noop(self):
        # Should not raise even though the row does not exist.
        result = run_web_update_task(999999)
        # In immediate mode the return value is a Result wrapper; the task itself
        # returns None for a missing WebUpdate.
        self.assertIsNone(result())


class UpdateProgressViewTests(TestCase):
    def setUp(self):
        self.client = Client()

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
