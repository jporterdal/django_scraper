import json
from unittest.mock import MagicMock

from django.test import SimpleTestCase, TestCase

from tracking.fetcher import Fetcher, ResponseTooLargeError
from tracking.models import FetchJob
from tracking.scrape import run_web_update
from tracking.tests.factories import make_item, make_item_source, make_source


class FakeResponse:
    """Minimal stand-in for a ``requests`` response used in these tests."""

    def __init__(self, content=b"", status_code=200, headers=None, json_data=None):
        if isinstance(content, str):
            content = content.encode()
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_data

    @property
    def text(self):
        return self.content.decode()

    def json(self):
        if self._json is not None:
            return self._json
        return json.loads(self.content)


class FetcherSizeCapTests(SimpleTestCase):
    def _fetcher_returning(self, response, max_response_bytes):
        fetcher = Fetcher(delay_seconds=0, max_response_bytes=max_response_bytes)
        fetcher._session = MagicMock()
        fetcher._session.get.return_value = response
        return fetcher

    def test_body_under_cap_does_not_raise(self):
        response = FakeResponse(content=b"x" * 100)
        fetcher = self._fetcher_returning(response, max_response_bytes=1000)

        self.assertIs(fetcher.get("https://example.com/search"), response)

    def test_body_over_cap_raises(self):
        response = FakeResponse(content=b"x" * 2000)
        fetcher = self._fetcher_returning(response, max_response_bytes=1000)

        with self.assertRaises(ResponseTooLargeError) as ctx:
            fetcher.get("https://example.com/search")

        self.assertEqual(ctx.exception.size, 2000)
        self.assertEqual(ctx.exception.limit, 1000)

    def test_content_length_header_over_cap_raises(self):
        response = FakeResponse(
            content=b"small",
            headers={"Content-Length": "5000000"},
        )
        fetcher = self._fetcher_returning(response, max_response_bytes=1000)

        with self.assertRaises(ResponseTooLargeError) as ctx:
            fetcher.get("https://example.com/search")

        self.assertEqual(ctx.exception.size, 5000000)

    def test_zero_cap_is_unlimited(self):
        response = FakeResponse(content=b"x" * 10_000)
        fetcher = self._fetcher_returning(response, max_response_bytes=0)

        self.assertIs(fetcher.get("https://example.com/search"), response)

    def test_none_cap_is_unlimited(self):
        response = FakeResponse(content=b"x" * 10_000)
        fetcher = self._fetcher_returning(response, max_response_bytes=None)

        self.assertIs(fetcher.get("https://example.com/search"), response)


SHOPIFY_PAGE = {
    "hits": {
        "hits": [
            {
                "_source": {
                    "title": "Test Card",
                    "variants": [
                        {"price": 9.99, "inventoryQuantity": 3, "selectedOptions": []}
                    ],
                }
            }
        ]
    }
}


class PayloadSizeOrchestratorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source = make_source(
            key="shop",
            name="Shopify Source",
            parser_key="shopify",
            base_search_url="https://example.com/search?q={term}",
        )
        cls.item = make_item()
        cls.item_source = make_item_source(cls.item, cls.source)

    def test_oversized_response_recorded_and_counted_as_error(self):
        fetcher = MagicMock()
        fetcher.get.side_effect = ResponseTooLargeError(
            "https://example.com/search?q=test+item", 9_000_000, 8_000_000
        )

        stats = run_web_update(fetcher=fetcher)

        self.assertEqual(stats.error_count, 1)
        self.assertEqual(stats.result_count, 0)
        job = FetchJob.objects.get()
        self.assertEqual(job.status, FetchJob.Status.OVERSIZED)
        self.assertIn("too large", job.error_message)

    def test_run_continues_for_other_items_after_oversized(self):
        item_two = make_item(text="second item", active=True)
        make_item_source(item_two, self.source)

        fetcher = MagicMock()
        fetcher.get.side_effect = [
            ResponseTooLargeError("https://example.com/search", 9_000_000, 8_000_000),
            FakeResponse(json_data=SHOPIFY_PAGE),
        ]

        stats = run_web_update(fetcher=fetcher)

        self.assertEqual(stats.search_count, 2)
        self.assertEqual(stats.error_count, 1)
        self.assertEqual(stats.result_count, 1)
        statuses = set(FetchJob.objects.values_list("status", flat=True))
        self.assertEqual(
            statuses, {FetchJob.Status.OVERSIZED, FetchJob.Status.SUCCESS}
        )

    def test_malformed_json_recorded_as_parse_error(self):
        fetcher = MagicMock()
        fetcher.get.return_value = FakeResponse(content=b"not valid json {")

        stats = run_web_update(fetcher=fetcher)

        self.assertEqual(stats.error_count, 1)
        self.assertEqual(stats.result_count, 0)
        job = FetchJob.objects.get()
        self.assertEqual(job.status, FetchJob.Status.PARSE_ERROR)
        self.assertIn("Invalid JSON", job.error_message)
