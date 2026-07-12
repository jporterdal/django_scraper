"""Step 4 — scrape history list + lazy FetchJob expand partial."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from tracking.models import FetchJob, WebUpdate
from tracking.tests.base import AuthedClientTestCase
from tracking.tests.factories import make_item, make_item_source, make_source


class WebUpdateHistoryTests(AuthedClientTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.source = make_source()
        cls.source_b = make_source(
            key="bb",
            name="Other Source",
            parser_key="cc",
            base_search_url="https://example.com/search?s={term}",
        )
        cls.item_a = make_item(text="alpha widget", active=True)
        cls.item_b = make_item(text="beta gadget", active=True)
        make_item_source(cls.item_a, cls.source)
        make_item_source(cls.item_b, cls.source_b)

    def _create_update(self, **kwargs):
        defaults = {
            "status": WebUpdate.Status.DONE,
            "total_searches": 2,
            "completed_searches": 2,
            "result_count": 5,
            "error_count": 1,
        }
        defaults.update(kwargs)
        return WebUpdate.objects.create(**defaults)

    def test_list_page_renders_summary_fields(self):
        update = self._create_update()
        response = self.client.get(reverse("view_updates"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Done")
        self.assertContains(response, "2 / 2")
        self.assertContains(response, "5")
        self.assertContains(response, "1")
        self.assertContains(response, reverse("webupdate_fetch_jobs", args=[update.pk]))

    def test_list_page_pagination(self):
        base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=ZoneInfo("UTC"))
        created = []
        for i in range(26):
            update = WebUpdate.objects.create(result_count=i)
            WebUpdate.objects.filter(pk=update.pk).update(
                timestamp=base + timedelta(minutes=i)
            )
            created.append(update)

        page1 = self.client.get(reverse("view_updates"))
        self.assertEqual(page1.status_code, 200)
        newest = WebUpdate.objects.order_by("-timestamp").first()
        self.assertContains(page1, str(newest.result_count))
        self.assertContains(page1, "Page 1 of 2")
        self.assertContains(page1, "Next")

        page2 = self.client.get(reverse("view_updates"), {"page": 2})
        self.assertEqual(page2.status_code, 200)
        oldest_on_page2 = WebUpdate.objects.order_by("timestamp").first()
        self.assertContains(page2, str(oldest_on_page2.result_count))
        self.assertContains(page2, "Page 2 of 2")
        self.assertContains(page2, "Previous")
        self.assertNotContains(page2, str(newest.result_count))

    def test_list_initial_render_does_not_query_fetch_jobs(self):
        update = self._create_update()
        FetchJob.objects.create(
            webupdate=update,
            item=self.item_a,
            source=self.source,
            search_term="alpha widget",
            status=FetchJob.Status.SUCCESS,
        )
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse("view_updates"))
        self.assertEqual(response.status_code, 200)
        fetch_job_queries = [
            q["sql"] for q in ctx.captured_queries if "tracking_fetchjob" in q["sql"].lower()
        ]
        self.assertEqual(fetch_job_queries, [])

    def test_fetch_jobs_partial_renders_jobs(self):
        update = self._create_update()
        FetchJob.objects.create(
            webupdate=update,
            item=self.item_a,
            source=self.source,
            search_term="alpha widget",
            status=FetchJob.Status.SUCCESS,
            http_status=200,
            result_count=3,
            duration_ms=120,
        )
        FetchJob.objects.create(
            webupdate=update,
            item=self.item_b,
            source=self.source_b,
            search_term="beta gadget",
            status=FetchJob.Status.BLOCKED,
            http_status=403,
            error_message="Access denied by upstream",
            duration_ms=45,
        )
        url = reverse("webupdate_fetch_jobs", args=[update.pk])
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "alpha widget")
        self.assertContains(response, "cc")
        self.assertContains(response, "beta gadget")
        self.assertContains(response, "bb")
        self.assertContains(response, "Blocked")
        # LoginRequiredMiddleware adds session/user lookups on top of the view's
        # bounded FetchJob query (≤2 for the partial itself).
        self.assertLessEqual(len(ctx.captured_queries), 4)

    def test_fetch_jobs_partial_404_for_missing_update(self):
        response = self.client.get(reverse("webupdate_fetch_jobs", args=[99999]))
        self.assertEqual(response.status_code, 404)

    def test_fetch_jobs_partial_idempotent(self):
        update = self._create_update()
        FetchJob.objects.create(
            webupdate=update,
            item=self.item_a,
            source=self.source,
            search_term="alpha widget",
            status=FetchJob.Status.HTTP_ERROR,
            http_status=500,
            error_message="server error",
        )
        url = reverse("webupdate_fetch_jobs", args=[update.pk])
        first = self.client.get(url)
        second = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.content, second.content)
