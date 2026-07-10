"""Phase 4 Step 6 — NULL price for out-of-stock SearchResult rows.

Out-of-stock parser results still carry a float in the result contract; scrape
stores ``price=None`` for those rows. Display and export paths must tolerate
NULL without error. No network.
"""

import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase
from django.urls import reverse

from .models import ItemSource, SearchResult, SearchableItem, Source, WebUpdate
from .scrape import run_web_update


class PriceNullScrapeTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="GET Source",
            key="get",
            parser_key="shopify",
            base_search_url="https://example.com/search?q={term}",
        )
        self.item = SearchableItem.objects.create(text="test item", active=True)
        ItemSource.objects.create(item=self.item, source=self.source)

    @patch("tracking.scrape._run_parser_search")
    def test_out_of_stock_results_store_null_price(self, mock_run_parser):
        mock_parser = MagicMock()
        mock_parser.results = [
            {
                "title": "In Stock Widget",
                "price": 19.99,
                "category": "Hardware",
                "instock": 1,
            },
            {
                "title": "Out of Stock Widget",
                "price": 9.99,
                "category": "Hardware",
                "instock": 0,
            },
        ]
        mock_run_parser.return_value = MagicMock(
            ok=True, http_status=200, error_message="", result_count=2
        )
        fetcher = MagicMock()

        with patch.dict(
            "tracking.parsers.sources",
            {"shopify": MagicMock(return_value=mock_parser)},
        ):
            stats = run_web_update(fetcher=fetcher)

        self.assertEqual(stats.result_count, 2)
        results = SearchResult.objects.filter(item=self.item).order_by("title")
        self.assertEqual(results.count(), 2)

        in_stock = results.get(title="In Stock Widget")
        self.assertEqual(in_stock.price, 19.99)
        self.assertEqual(in_stock.instock, 1)

        out_of_stock = results.get(title="Out of Stock Widget")
        self.assertIsNone(out_of_stock.price)
        self.assertEqual(out_of_stock.instock, 0)


class PriceNullDisplayExportTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.source = Source.objects.create(
            name="GET Source",
            key="get",
            parser_key="shopify",
            base_search_url="https://example.com/search?q={term}",
        )
        self.item = SearchableItem.objects.create(text="widget", active=True)
        self.update = WebUpdate.objects.create()

        SearchResult.objects.create(
            title="In Stock Widget",
            search_term="widget",
            price=19.99,
            category="Hardware",
            instock=1,
            item=self.item,
            update=self.update,
            source=self.source,
        )
        SearchResult.objects.create(
            title="Out of Stock Widget",
            search_term="widget",
            price=None,
            category="Hardware",
            instock=0,
            item=self.item,
            update=self.update,
            source=self.source,
        )

    def test_item_detail_renders_null_price_without_error(self):
        response = self.client.get(reverse("item_detail", args=[self.item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "In Stock Widget")
        self.assertContains(response, "Out of Stock Widget")
        self.assertContains(response, "$19.99")
        self.assertNotContains(response, "$None")

    def test_item_list_renders_with_null_oos_price(self):
        response = self.client.get(reverse("view_terms"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "widget")

    def test_csv_export_handles_null_price(self):
        import csv
        from io import StringIO

        response = self.client.get(reverse("export_item_csv", args=[self.item.pk]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("In Stock Widget", body)
        self.assertIn("19.99", body)
        self.assertIn("Out of Stock Widget", body)

        reader = csv.DictReader(StringIO(body))
        by_title = {row["title"]: row for row in reader}
        self.assertEqual(by_title["In Stock Widget"]["price"], "19.99")
        self.assertEqual(by_title["Out of Stock Widget"]["price"], "")

    def test_json_export_serializes_null_price(self):
        response = self.client.get(reverse("export_item_json", args=[self.item.pk]))
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        by_title = {row["title"]: row for row in data}
        self.assertEqual(by_title["In Stock Widget"]["price"], 19.99)
        self.assertIsNone(by_title["Out of Stock Widget"]["price"])
