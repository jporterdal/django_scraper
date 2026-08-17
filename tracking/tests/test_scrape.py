import json
from unittest.mock import MagicMock, patch

from django.test import RequestFactory

from tracking.models import ItemSource, SearchableItem, SearchResult, Source, WebUpdate
from tracking.scrape import FetchOutcome, run_web_update
from tracking.tests.base import LinkedSourceTestCase
from tracking.tests.factories import make_item, make_item_source, make_source
from tracking.views import SearchableListView


class ScrapeOrchestratorTests(LinkedSourceTestCase):
    def setUp(self):
        self.fetcher = MagicMock()

    @patch("tracking.scrape._run_parser_search")
    def test_stores_parser_results(self, mock_run_parser):
        mock_parser = MagicMock()
        mock_parser.results = [
            {
                "title": "Test Product",
                "price": 19.99,
                "category": "Hardware",
                "instock": 1,
            }
        ]
        mock_run_parser.return_value = FetchOutcome(
            ok=True, http_status=200, error_message="", result_count=1
        )

        with patch.dict(
            "tracking.parsers.sources",
            {"cc": MagicMock(return_value=mock_parser)},
        ):
            stats = run_web_update(fetcher=self.fetcher)

        self.assertEqual(stats.result_count, 1)
        self.assertEqual(stats.error_count, 0)
        self.assertEqual(SearchResult.objects.count(), 1)
        self.assertEqual(WebUpdate.objects.count(), 1)

    @patch("tracking.scrape._run_parser_search")
    def test_http_failure_counts_as_error(self, mock_run_parser):
        mock_run_parser.return_value = FetchOutcome(
            ok=False, http_status=404, error_message="HTTP 404", result_count=0
        )
        with patch.dict(
            "tracking.parsers.sources",
            {"cc": MagicMock(return_value=MagicMock())},
        ):
            stats = run_web_update(fetcher=self.fetcher)

        self.assertEqual(stats.result_count, 0)
        self.assertEqual(stats.error_count, 1)

    def test_unknown_parser_key_counts_as_error(self):
        ItemSource.objects.all().delete()
        bad_source = Source.objects.create(
            name="Bad Source",
            key="bad",
            parser_key="bad",
            base_search_url="https://example.com/search?s={term}",
        )
        ItemSource.objects.create(item=self.item, source=bad_source)

        stats = run_web_update(fetcher=self.fetcher)

        self.assertEqual(stats.error_count, 1)
        self.assertEqual(stats.result_count, 0)
        self.fetcher.get.assert_not_called()

    @patch("tracking.scrape._run_parser_search")
    def test_rate_limit_pause_between_searches(self, mock_run_parser):
        item_two = make_item(text="second item", active=True)
        make_item_source(item_two, self.source)
        mock_parser = MagicMock(results=[])
        mock_run_parser.return_value = FetchOutcome(
            ok=True, http_status=200, error_message="", result_count=0
        )

        with patch.dict(
            "tracking.parsers.sources",
            {"cc": MagicMock(return_value=mock_parser)},
        ):
            stats = run_web_update(fetcher=self.fetcher)

        self.assertEqual(stats.search_count, 2)
        # Fan-out (D8): fixed-delay pacing now happens per unit, inside
        # fetch_one_unit, rather than "skip the first item in a shared loop" —
        # so a synchronous run of 2 item-sources calls wait() twice, not once.
        self.assertEqual(self.fetcher.wait.call_count, 2)

class ScrapeUrlIntegrationTests(LinkedSourceTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source = make_source(
            base_search_url="https://example.com/search?s={term}&pickup=62",
        )
        cls.item = make_item()
        cls.item_source = make_item_source(cls.item, cls.source)

    def setUp(self):
        self.fetcher = MagicMock()
        self.fetcher.get.return_value = MagicMock(status_code=200, text="<html></html>")

    @patch("tracking.scrape._run_parser_search")
    def test_passes_built_url_to_parser_search(self, mock_run_parser):
        mock_run_parser.return_value = FetchOutcome(
            ok=True, http_status=200, error_message="", result_count=0
        )
        mock_parser = MagicMock(results=[])
        with patch.dict(
            "tracking.parsers.sources",
            {"cc": MagicMock(return_value=mock_parser)},
        ):
            run_web_update(fetcher=self.fetcher)

        expected_url = "https://example.com/search?s=test+item&pickup=62"
        mock_run_parser.assert_called_once_with(
            mock_parser,
            self.fetcher,
            expected_url,
            headers=None,
            max_pages=1,
            method="GET",
            body=None,
            pacing=None,
        )

class SearchTermAndSummaryQueryTests(LinkedSourceTestCase):
    def setUp(self):
        self.fetcher = MagicMock()

    @patch("tracking.scrape._run_parser_search")
    def test_stores_search_term_on_each_result(self, mock_run_parser):
        mock_parser = MagicMock()
        mock_parser.results = [
            {
                "title": "Product A",
                "price": 19.99,
                "category": "Hardware",
                "instock": 1,
            },
            {
                "title": "Product B",
                "price": 29.99,
                "category": "Hardware",
                "instock": True,
            },
        ]
        mock_run_parser.return_value = FetchOutcome(
            ok=True, http_status=200, error_message="", result_count=1
        )

        with patch.dict(
            "tracking.parsers.sources",
            {"cc": MagicMock(return_value=mock_parser)},
        ):
            run_web_update(fetcher=self.fetcher)

        self.assertEqual(SearchResult.objects.count(), 2)
        for sr in SearchResult.objects.all():
            self.assertEqual(sr.search_term, self.item.text)

    @patch("tracking.scrape._run_parser_search")
    def test_stores_all_parser_results_not_only_matched(self, mock_run_parser):
        mock_parser = MagicMock()
        mock_parser.results = [
            {
                "title": "In Stock Widget",
                "price": 100.0,
                "category": "Hardware",
                "instock": 1,
            },
            {
                "title": "Out of Stock Widget",
                "price": 1.0,
                "category": "Hardware",
                "instock": 0,
            },
        ]
        mock_run_parser.return_value = FetchOutcome(
            ok=True, http_status=200, error_message="", result_count=1
        )

        with patch.dict(
            "tracking.parsers.sources",
            {"cc": MagicMock(return_value=mock_parser)},
        ):
            run_web_update(fetcher=self.fetcher)

        self.assertEqual(SearchResult.objects.count(), 2)
        titles = set(SearchResult.objects.values_list("title", flat=True))
        self.assertEqual(
            titles,
            {"In Stock Widget", "Out of Stock Widget"},
        )

    def test_latest_minprice_uses_in_stock_only(self):
        webupdate = WebUpdate.objects.create()
        SearchResult.objects.create(
            title="In Stock",
            search_term=self.item.text,
            price=100.0,
            category="Hardware",
            item=self.item,
            instock=1,
            source=self.source,
            update=webupdate,
        )
        SearchResult.objects.create(
            title="Out of Stock",
            search_term=self.item.text,
            price=1.0,
            category="Hardware",
            item=self.item,
            instock=0,
            source=self.source,
            update=webupdate,
        )

        request = RequestFactory().get("/")
        view = SearchableListView()
        view.request = request
        annotated_item = view.get_queryset().get(pk=self.item.pk)

        self.assertEqual(annotated_item.latest_known_minprice, 100.0)
        self.assertEqual(annotated_item.latest_known_minprice_title, "In Stock")

class ScrapeHeaderTests(LinkedSourceTestCase):
    """Phase 2 Step 2 — per-source request headers flow through the orchestrator."""

    @classmethod
    def setUpTestData(cls):
        cls.source = make_source(request_headers={"Accept": "application/json"})
        cls.item = make_item()
        cls.item_source = make_item_source(cls.item, cls.source)

    def setUp(self):
        self.fetcher = MagicMock()

    @patch("tracking.scrape._run_parser_search")
    def test_run_web_update_passes_request_headers(self, mock_run_parser):
        mock_run_parser.return_value = FetchOutcome(
            ok=True, http_status=200, error_message="", result_count=0
        )
        mock_parser = MagicMock(results=[])
        with patch.dict(
            "tracking.parsers.sources",
            {"cc": MagicMock(return_value=mock_parser)},
        ):
            run_web_update(fetcher=self.fetcher)

        self.assertEqual(
            mock_run_parser.call_args.kwargs["headers"],
            {"Accept": "application/json"},
        )

    @patch("tracking.scrape._run_parser_search")
    def test_run_web_update_substitutes_term_in_request_headers(self, mock_run_parser):
        self.source.request_headers = {
            "Accept": "application/json",
            "Referer": "https://example.com/search?q={term}",
        }
        self.source.save(update_fields=["request_headers"])
        mock_run_parser.return_value = FetchOutcome(
            ok=True, http_status=200, error_message="", result_count=0
        )
        mock_parser = MagicMock(results=[])
        with patch.dict(
            "tracking.parsers.sources",
            {"cc": MagicMock(return_value=mock_parser)},
        ):
            run_web_update(fetcher=self.fetcher)

        self.assertEqual(
            mock_run_parser.call_args.kwargs["headers"],
            {
                "Accept": "application/json",
                "Referer": "https://example.com/search?q=test+item",
            },
        )
