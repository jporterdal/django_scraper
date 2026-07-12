"""Step 13 — end-to-end scrape tests with real parsers through run_web_update."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.conf import settings
from django.test import TestCase

from tracking.models import FetchJob, SearchResult, WebUpdate
from tracking.scrape import run_web_update
from tracking.tests.factories import make_item, make_item_source, make_source

FIXTURES = settings.BASE_DIR / "tracking" / "fixtures" / "html"


def fake_http_response(*, text="", json_data=None):
    """Minimal HTTP response stand-in for injected fetcher mocks."""
    response = SimpleNamespace(status_code=200, text=text)
    if json_data is not None:
        response.json = lambda: json_data
    return response


def fake_fetcher(*, text="", json_data=None):
    """Fetcher mock whose get/post return a fixture-backed response; wait is a no-op."""
    fetcher = MagicMock()
    response = fake_http_response(text=text, json_data=json_data)
    fetcher.get.return_value = response
    fetcher.post.return_value = response
    return fetcher


class CCScrapeE2ETests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source = make_source(key="cc", parser_key="cc")
        cls.item = make_item(text="RTX 5070", active=True)
        cls.item_source = make_item_source(cls.item, cls.source)

    def test_cc_html_fixture_through_run_web_update(self):
        html = (FIXTURES / "cc" / "search_results_minimal.html").read_text()
        fetcher = fake_fetcher(text=html)

        stats = run_web_update(fetcher=fetcher)

        self.assertEqual(SearchResult.objects.count(), 2)
        in_stock = SearchResult.objects.get(title="Test GPU RTX 5070")
        self.assertAlmostEqual(in_stock.price, 799.99)
        self.assertEqual(in_stock.instock, 1)
        self.assertEqual(in_stock.search_term, self.item.text)

        oos = SearchResult.objects.get(title="Other Product")
        self.assertIsNone(oos.price)
        self.assertEqual(oos.instock, 0)

        job = FetchJob.objects.get()
        self.assertEqual(job.status, FetchJob.Status.SUCCESS)
        self.assertEqual(job.result_count, 2)
        self.assertEqual(job.stored_count, 2)

        webupdate = WebUpdate.objects.get()
        self.assertEqual(webupdate.status, WebUpdate.Status.DONE)
        self.assertEqual(stats.result_count, 2)
        self.assertEqual(stats.error_count, 0)


class ShopifyScrapeE2ETests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source = make_source(
            key="f2f",
            parser_key="shopify",
            base_search_url="https://example.com/search?q={term}",
        )
        cls.item = make_item(text="Lightning Bolt", active=True)
        cls.item_source = make_item_source(cls.item, cls.source)
        cls.fixture_data = json.loads(
            (FIXTURES / "f2f" / "search_results_sample.json").read_text()
        )

    def test_shopify_json_fixture_through_run_web_update(self):
        fetcher = fake_fetcher(json_data=self.fixture_data)

        stats = run_web_update(fetcher=fetcher)

        results = list(SearchResult.objects.order_by("title"))
        self.assertGreaterEqual(len(results), 1)

        for row in results:
            self.assertIn(row.instock, (0, 1))
            if row.instock == 1:
                self.assertIsInstance(row.price, float)
            else:
                self.assertIsNone(row.price)
            self.assertTrue(row.title)

        instock_rows = [row for row in results if row.instock == 1]
        self.assertTrue(instock_rows, "expected at least one in-stock row")
        self.assertTrue(
            any("Lightning Bolt" in row.title for row in results),
            "expected at least one row title containing the search term",
        )
        self.assertTrue(
            any("(NM)" in row.title for row in results),
            "expected at least one row title with a condition tag like (NM)",
        )

        job = FetchJob.objects.get()
        self.assertEqual(job.status, FetchJob.Status.SUCCESS)
        self.assertGreaterEqual(job.result_count, 1)
        self.assertEqual(job.stored_count, len(results))

        webupdate = WebUpdate.objects.get()
        self.assertEqual(webupdate.status, WebUpdate.Status.DONE)
        self.assertEqual(stats.result_count, len(results))
        self.assertEqual(stats.error_count, 0)
