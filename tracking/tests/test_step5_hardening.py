"""Step 5 — BLOCKED FetchJob status and POST body pagination hook."""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from tracking.models import FetchJob
from tracking.scrape import _run_parser_search, run_web_update
from tracking.tests.factories import make_item, make_item_source, make_source
from tracking.tests.test_payload_size import FakeResponse
from tracking.tests.test_post_support import SHOPIFY_PAGE


class BlockedStatusTests(TestCase):
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

    def test_http_403_records_blocked(self):
        fetcher = MagicMock()
        fetcher.get.return_value = FakeResponse(
            content=b"Forbidden", status_code=403
        )

        stats = run_web_update(fetcher=fetcher)

        self.assertEqual(stats.error_count, 1)
        job = FetchJob.objects.get()
        self.assertEqual(job.status, FetchJob.Status.BLOCKED)
        self.assertEqual(job.http_status, 403)

    def test_html_body_on_json_parser_records_blocked(self):
        fetcher = MagicMock()
        fetcher.get.return_value = FakeResponse(
            content=b"<!DOCTYPE html><html><body>Access denied</body></html>",
            status_code=200,
            headers={"Content-Type": "text/html"},
        )

        stats = run_web_update(fetcher=fetcher)

        self.assertEqual(stats.error_count, 1)
        job = FetchJob.objects.get()
        self.assertEqual(job.status, FetchJob.Status.BLOCKED)
        self.assertIn("Blocked response", job.error_message)


class RunParserSearchPostPaginationTests(SimpleTestCase):
    def test_post_paginates_when_parser_returns_next_page_body(self):
        parser = MagicMock()
        parser.results = [
            {"title": "A", "price": 1.0, "category": "", "instock": 1},
            {"title": "B", "price": 2.0, "category": "", "instock": 1},
        ]
        page1_body = {"q": "widget", "page": 1}
        page2_body = {"q": "widget", "page": 2}
        parser.next_page_body.side_effect = [page2_body, None]

        page1_response = FakeResponse(json_data=SHOPIFY_PAGE)
        page2_response = FakeResponse(json_data=SHOPIFY_PAGE)
        fetcher = MagicMock()
        fetcher.post.side_effect = [page1_response, page2_response]

        outcome = _run_parser_search(
            parser,
            fetcher,
            "https://example.com/api/search",
            max_pages=5,
            method="POST",
            body=page1_body,
        )

        self.assertTrue(outcome.ok)
        self.assertEqual(fetcher.post.call_count, 2)
        fetcher.post.assert_any_call(
            "https://example.com/api/search",
            json=page1_body,
            headers=None,
        )
        fetcher.post.assert_any_call(
            "https://example.com/api/search",
            json=page2_body,
            headers=None,
        )
        parser.parse_response.assert_called_once_with(page1_response)
        parser.parse_next_page.assert_called_once_with(page2_response)
        fetcher.wait.assert_called_once()

    def test_post_without_next_page_body_stays_single_page(self):
        parser = MagicMock()
        parser.results = []
        parser.next_page_body.return_value = None
        fetcher = MagicMock()
        fetcher.post.return_value = FakeResponse(content=json.dumps({}).encode())

        _run_parser_search(
            parser,
            fetcher,
            "https://example.com/api/search",
            max_pages=5,
            method="POST",
            body={"q": "widget"},
        )

        fetcher.post.assert_called_once()
        fetcher.get.assert_not_called()
        fetcher.wait.assert_not_called()
