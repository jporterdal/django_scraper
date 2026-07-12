"""Step 8 — ingest dedup, carry-forward list/detail UI, scrape-history transparency."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from tracking.models import FetchJob, ItemSource, SearchResult, Source, WebUpdate
from tracking.scrape import FetchOutcome, run_web_update
from tracking.tests.base import AuthedClientTestCase
from tracking.tests.factories import make_item, make_item_source, make_linked_item, make_source
from tracking.views import SearchableItemDetailView, SearchableListView


def _ok_outcome(result_count=0):
    return FetchOutcome(
        ok=True, http_status=200, error_message="", result_count=result_count
    )


def _mock_parser(results):
    parser = MagicMock()
    parser.results = results
    return parser


class IngestDedupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source, cls.item, cls.item_source = make_linked_item(item_text="widget")

    def setUp(self):
        self.fetcher = MagicMock()
        self.parser_results = [
            {"title": "Widget A", "price": 49.99, "category": "Hardware", "instock": 1},
            {"title": "Widget B", "price": 59.99, "category": "Hardware", "instock": 1},
        ]

    def _run(self, results=None):
        results = self.parser_results if results is None else results
        mock_run_parser = patch(
            "tracking.scrape._run_parser_search",
            return_value=_ok_outcome(result_count=len(results)),
        )
        mock_parser = _mock_parser(results)
        with mock_run_parser, patch.dict(
            "tracking.parsers.sources",
            {"cc": MagicMock(return_value=mock_parser)},
        ):
            return run_web_update(fetcher=self.fetcher)

    def test_identical_second_run_stores_zero_with_counters(self):
        self._run()
        self.assertEqual(SearchResult.objects.count(), 2)

        stats = self._run()
        webupdate = WebUpdate.objects.order_by("-timestamp").first()
        job = FetchJob.objects.filter(webupdate=webupdate).get()

        self.assertEqual(SearchResult.objects.count(), 2)
        self.assertEqual(stats.result_count, 0)
        self.assertEqual(webupdate.result_count, 0)
        self.assertEqual(webupdate.skipped_duplicate_count, 2)
        self.assertEqual(job.result_count, 2)
        self.assertEqual(job.stored_count, 0)

    def test_changed_price_third_run_stores_one(self):
        self._run()
        self._run()
        changed = [
            {"title": "Widget A", "price": 44.99, "category": "Hardware", "instock": 1},
            {"title": "Widget B", "price": 59.99, "category": "Hardware", "instock": 1},
        ]
        stats = self._run(results=changed)

        self.assertEqual(stats.result_count, 1)
        self.assertEqual(SearchResult.objects.count(), 3)
        webupdate = WebUpdate.objects.order_by("-timestamp").first()
        self.assertEqual(webupdate.skipped_duplicate_count, 1)

    def test_within_batch_duplicate_stores_one(self):
        duped = [
            {"title": "Widget A", "price": 49.99, "category": "Hardware", "instock": 1},
            {"title": "Widget A", "price": 49.99, "category": "Hardware", "instock": 1},
        ]
        stats = self._run(results=duped)

        self.assertEqual(stats.result_count, 1)
        self.assertEqual(SearchResult.objects.count(), 1)
        webupdate = WebUpdate.objects.get()
        self.assertEqual(webupdate.skipped_duplicate_count, 1)


class ItemListDedupUITests(AuthedClientTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.source, cls.item, cls.item_source = make_linked_item(item_text="carry widget")
        cls.never_item = make_item(text="never fetched", active=True)

    def _seed_stored_price(self, price=49.99, title="Widget A"):
        update = WebUpdate.objects.create(status=WebUpdate.Status.DONE)
        SearchResult.objects.create(
            title=title,
            search_term=self.item.text,
            price=price,
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

    def test_carry_forward_price_unchanged_badge_and_data_order(self):
        self._seed_stored_price()
        deduped = WebUpdate.objects.create(status=WebUpdate.Status.DONE)
        FetchJob.objects.create(
            webupdate=deduped,
            item=self.item,
            source=self.source,
            search_term=self.item.text,
            status=FetchJob.Status.SUCCESS,
            result_count=2,
            stored_count=0,
        )

        response = self.client.get(reverse("view_terms"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "$49.99")
        self.assertContains(response, "Widget A")
        self.assertContains(response, "Unchanged")
        self.assertContains(response, 'data-order="49.99"')

    def test_never_checked_badge_and_priceless_sort_sentinel(self):
        response = self.client.get(reverse("view_terms"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Never checked")
        self.assertContains(response, 'data-order="-1"')

    def test_status_rollup_failed_when_mixed_success_and_error(self):
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
            name="Other",
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

        request = RequestFactory().get("/")
        view = SearchableListView()
        view.request = request
        view.object_list = list(view.get_queryset())
        view.get_context_data()
        annotated = next(i for i in view.object_list if i.pk == self.item.pk)
        self.assertEqual(annotated.list_status, "failed")
        self.assertEqual(annotated.list_status_label, "Failed")


class ItemDetailDedupUITests(AuthedClientTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.source, cls.item, cls.item_source = make_linked_item(item_text="detail widget")

    def _at(self, hour, minute, day=12):
        return datetime(2026, 7, day, hour, minute, tzinfo=ZoneInfo("UTC"))

    def test_chart_note_unchanged_case(self):
        unchanged_ts = self._at(14, 0)
        unchanged = WebUpdate.objects.create(status=WebUpdate.Status.DONE)
        WebUpdate.objects.filter(pk=unchanged.pk).update(timestamp=unchanged_ts)
        FetchJob.objects.create(
            webupdate=unchanged,
            item=self.item,
            source=self.source,
            search_term=self.item.text,
            status=FetchJob.Status.SUCCESS,
            result_count=3,
            stored_count=0,
        )

        response = self.client.get(reverse("item_detail", args=[self.item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Parsed 3, stored 0, unchanged")

    def test_chart_note_price_changed_case(self):
        first = WebUpdate.objects.create(status=WebUpdate.Status.DONE)
        SearchResult.objects.create(
            title="Widget",
            search_term=self.item.text,
            price=49.99,
            category="Hardware",
            item=self.item,
            instock=1,
            source=self.source,
            update=first,
        )
        FetchJob.objects.create(
            webupdate=first,
            item=self.item,
            source=self.source,
            search_term=self.item.text,
            status=FetchJob.Status.SUCCESS,
            result_count=1,
            stored_count=1,
        )

        changed_ts = self._at(16, 0, day=13)
        changed = WebUpdate.objects.create(status=WebUpdate.Status.DONE)
        WebUpdate.objects.filter(pk=changed.pk).update(timestamp=changed_ts)
        SearchResult.objects.create(
            title="Widget",
            search_term=self.item.text,
            price=44.99,
            category="Hardware",
            item=self.item,
            instock=1,
            source=self.source,
            update=changed,
        )
        FetchJob.objects.create(
            webupdate=changed,
            item=self.item,
            source=self.source,
            search_term=self.item.text,
            status=FetchJob.Status.SUCCESS,
            result_count=1,
            stored_count=1,
        )

        response = self.client.get(reverse("item_detail", args=[self.item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Parsed 1, stored 1, price changed")

    def test_chart_carry_forward_hollow_and_solid_points(self):
        first_ts = self._at(10, 0)
        first = WebUpdate.objects.create(status=WebUpdate.Status.DONE)
        WebUpdate.objects.filter(pk=first.pk).update(timestamp=first_ts)
        SearchResult.objects.create(
            title="Widget",
            search_term=self.item.text,
            price=49.99,
            category="Hardware",
            item=self.item,
            instock=1,
            source=self.source,
            update=first,
        )
        FetchJob.objects.create(
            webupdate=first,
            item=self.item,
            source=self.source,
            search_term=self.item.text,
            status=FetchJob.Status.SUCCESS,
            result_count=1,
            stored_count=1,
        )

        unchanged_ts = self._at(14, 0)
        unchanged = WebUpdate.objects.create(status=WebUpdate.Status.DONE)
        WebUpdate.objects.filter(pk=unchanged.pk).update(timestamp=unchanged_ts)
        FetchJob.objects.create(
            webupdate=unchanged,
            item=self.item,
            source=self.source,
            search_term=self.item.text,
            status=FetchJob.Status.SUCCESS,
            result_count=2,
            stored_count=0,
        )

        request = RequestFactory().get("/")
        view = SearchableItemDetailView()
        view.object = self.item
        context = view.get_context_data()
        chart_data = json.loads(context["chart_data_json"])
        series = chart_data[self.source.key]

        self.assertEqual(series["prices"], [49.99, 49.99])
        self.assertEqual(series["point_styles"], ["solid", "hollow"])
        self.assertIn("confirmed, unchanged", series["tooltips"][1])
        self.assertIn("price changed", series["tooltips"][0])


class ScrapeHistoryDedupUITests(AuthedClientTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.source, cls.item, _ = make_linked_item(item_text="history widget")

    def test_summary_shows_stored_and_unchanged(self):
        update = WebUpdate.objects.create(
            status=WebUpdate.Status.DONE,
            result_count=0,
            skipped_duplicate_count=5,
        )
        response = self.client.get(reverse("view_updates"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(update.skipped_duplicate_count))

    def test_fetch_jobs_partial_parsed_stored_skipped_and_unchanged_badge(self):
        update = WebUpdate.objects.create(status=WebUpdate.Status.DONE)
        FetchJob.objects.create(
            webupdate=update,
            item=self.item,
            source=self.source,
            search_term=self.item.text,
            status=FetchJob.Status.SUCCESS,
            result_count=4,
            stored_count=0,
        )
        response = self.client.get(reverse("webupdate_fetch_jobs", args=[update.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Parsed")
        self.assertContains(response, "Stored")
        self.assertContains(response, "Skipped")
        self.assertContains(response, "Unchanged")
        self.assertContains(response, ">4<")
        self.assertContains(response, ">0<")
