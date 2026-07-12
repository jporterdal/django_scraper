"""Step 7 — scrape-health indicators on the item list."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.test import RequestFactory, TestCase
from django.urls import reverse

from tracking.models import FetchJob, ItemSource, SearchResult, Source, WebUpdate
from tracking.tests.base import AuthedClientTestCase
from tracking.tests.factories import (
    make_item,
    make_item_source,
    make_linked_item,
    make_source,
)
from tracking.views import ITEM_LIST_STALE_THRESHOLD_HOURS, SearchableListView


class ItemListScrapeHealthTests(AuthedClientTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.source, cls.item, cls.item_source = make_linked_item(item_text="health widget")

    def _annotate_item(self):
        request = RequestFactory().get("/")
        view = SearchableListView()
        view.request = request
        view.object_list = list(view.get_queryset())
        view.get_context_data()
        return next(i for i in view.object_list if i.pk == self.item.pk)

    def _seed_success_fetch(self, *, hours_ago=1):
        checked_at = datetime.now(tz=ZoneInfo("UTC")) - timedelta(hours=hours_ago)
        update = WebUpdate.objects.create(status=WebUpdate.Status.DONE)
        WebUpdate.objects.filter(pk=update.pk).update(timestamp=checked_at)
        SearchResult.objects.create(
            title="Widget",
            search_term=self.item.text,
            price=49.99,
            category="Hardware",
            item=self.item,
            instock=1,
            source=self.source,
            update=update,
        )
        FetchJob.objects.create(
            webupdate=update,
            item=self.item,
            source=self.source,
            search_term=self.item.text,
            status=FetchJob.Status.SUCCESS,
            result_count=1,
            stored_count=1,
        )
        return update

    def test_stale_when_last_check_over_threshold(self):
        self._seed_success_fetch(hours_ago=ITEM_LIST_STALE_THRESHOLD_HOURS + 1)

        annotated = self._annotate_item()
        self.assertTrue(annotated.list_is_stale)

        response = self.client.get(reverse("view_terms"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stale")
        self.assertContains(
            response,
            f"No successful fetch within {ITEM_LIST_STALE_THRESHOLD_HOURS} hours",
        )

    def test_not_stale_when_checked_within_threshold(self):
        self._seed_success_fetch(hours_ago=ITEM_LIST_STALE_THRESHOLD_HOURS - 1)

        annotated = self._annotate_item()
        self.assertFalse(annotated.list_is_stale)

        response = self.client.get(reverse("view_terms"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, ">Stale<")

    def test_stale_when_never_checked_with_sources(self):
        annotated = self._annotate_item()
        self.assertTrue(annotated.list_is_stale)

        response = self.client.get(reverse("view_terms"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Never checked")
        self.assertContains(response, "Stale")

    def test_not_stale_without_item_sources(self):
        self.item.active = True
        self.item.save()
        ItemSource.objects.filter(item=self.item).delete()

        annotated = self._annotate_item()
        self.assertFalse(annotated.list_is_stale)

    def test_not_stale_for_inactive_item_even_when_overdue(self):
        self.item.active = False
        self.item.save()
        self._seed_success_fetch(hours_ago=ITEM_LIST_STALE_THRESHOLD_HOURS + 5)

        annotated = self._annotate_item()
        self.assertFalse(annotated.list_is_stale)

    def test_failure_detail_lists_failed_sources_on_latest_run(self):
        update = WebUpdate.objects.create(status=WebUpdate.Status.DONE)
        FetchJob.objects.create(
            webupdate=update,
            item=self.item,
            source=self.source,
            search_term=self.item.text,
            status=FetchJob.Status.SUCCESS,
            result_count=1,
            stored_count=1,
        )
        other_source = make_source(
            key="bb",
            name="Broken Source",
            parser_key="cc",
            base_search_url="https://example.com/search?s={term}",
        )
        make_item_source(self.item, other_source)
        FetchJob.objects.create(
            webupdate=update,
            item=self.item,
            source=other_source,
            search_term=self.item.text,
            status=FetchJob.Status.HTTP_ERROR,
            http_status=500,
            error_message="server error",
        )
        SearchResult.objects.create(
            title="Widget",
            search_term=self.item.text,
            price=10.0,
            category="Hardware",
            item=self.item,
            instock=1,
            source=self.source,
            update=update,
        )

        annotated = self._annotate_item()
        self.assertEqual(annotated.list_status, "failed")
        self.assertEqual(annotated.list_failed_source_labels, ["Broken Source"])
        self.assertEqual(annotated.list_failed_source_detail, "Broken Source")

        response = self.client.get(reverse("view_terms"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Failed: Broken Source")
        self.assertContains(response, 'title="Failed on latest run: Broken Source"')

    def test_no_failure_detail_when_latest_run_succeeded(self):
        self._seed_success_fetch()

        annotated = self._annotate_item()
        self.assertEqual(annotated.list_status, "updated")
        self.assertEqual(annotated.list_failed_source_labels, [])
        self.assertEqual(annotated.list_failed_source_detail, "")

        response = self.client.get(reverse("view_terms"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Failed: ")
