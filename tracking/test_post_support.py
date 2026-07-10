"""Phase 4 Step 1 — POST JSON API support.

Covers ``Fetcher.post``, ``Source.http_method`` / ``request_body_template`` /
``build_request_body``, relaxed ``build_search_url`` for POST, and POST wiring in
``_run_parser_search`` / ``run_web_update``. No network.
"""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from .fetcher import Fetcher, ResponseTooLargeError
from .models import ItemSource, SearchableItem, Source
from .scrape import _run_parser_search, run_web_update
from .test_payload_size import FakeResponse


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


class FetcherPostTests(SimpleTestCase):
    def _fetcher_with_mock_session(self, response, max_response_bytes=1_000_000):
        fetcher = Fetcher(delay_seconds=0, max_response_bytes=max_response_bytes)
        fetcher._session = MagicMock()
        fetcher._session.post.return_value = response
        return fetcher

    def test_post_calls_session_with_json(self):
        response = FakeResponse(content=b"{}")
        fetcher = self._fetcher_with_mock_session(response)
        body = {"q": "widget", "page": 1}

        result = fetcher.post("https://example.com/api/search", json=body)

        fetcher._session.post.assert_called_once_with(
            "https://example.com/api/search",
            json=body,
            timeout=fetcher.timeout,
            headers=None,
        )
        self.assertIs(result, response)

    def test_post_applies_size_cap(self):
        response = FakeResponse(content=b"x" * 2000)
        fetcher = self._fetcher_with_mock_session(response, max_response_bytes=1000)

        with self.assertRaises(ResponseTooLargeError):
            fetcher.post("https://example.com/api/search", json={"q": "x"})

    def test_get_delegates_to_request(self):
        response = FakeResponse(content=b"ok")
        fetcher = Fetcher(delay_seconds=0)
        fetcher._session = MagicMock()
        fetcher._session.get.return_value = response

        result = fetcher.get("https://example.com/search")

        fetcher._session.get.assert_called_once_with(
            "https://example.com/search",
            timeout=fetcher.timeout,
            headers=None,
        )
        self.assertIs(result, response)


class SourceBuildRequestBodyTests(TestCase):
    def test_substitutes_term_in_nested_string_leaves(self):
        source = Source(
            key="post",
            name="POST Source",
            parser_key="shopify",
            base_search_url="https://example.com/api/search",
            http_method=Source.HttpMethod.POST,
            request_body_template={
                "q": "{term}",
                "context": {"query": "find {term} please", "page": 1},
                "tags": ["{term}", "static"],
            },
        )
        body = source.build_request_body("rtx 5070")
        self.assertEqual(
            body,
            {
                "q": "rtx 5070",
                "context": {"query": "find rtx 5070 please", "page": 1},
                "tags": ["rtx 5070", "static"],
            },
        )

    def test_returns_none_for_get(self):
        source = Source(
            key="get",
            name="GET Source",
            parser_key="shopify",
            base_search_url="https://example.com/search?q={term}",
            request_body_template={"q": "{term}"},
        )
        self.assertIsNone(source.build_request_body("widget"))

    def test_returns_none_for_empty_template(self):
        source = Source(
            key="post",
            name="POST Source",
            parser_key="shopify",
            base_search_url="https://example.com/api/search",
            http_method=Source.HttpMethod.POST,
            request_body_template={},
        )
        self.assertIsNone(source.build_request_body("widget"))

    def test_does_not_mutate_template(self):
        template = {"q": "{term}"}
        source = Source(
            key="post",
            name="POST Source",
            parser_key="shopify",
            base_search_url="https://example.com/api/search",
            http_method=Source.HttpMethod.POST,
            request_body_template=template,
        )
        source.build_request_body("widget")
        self.assertEqual(template, {"q": "{term}"})


class SourceBuildRequestHeadersTests(TestCase):
    def test_substitutes_url_encoded_term_in_string_leaves(self):
        source = Source(
            key="wt",
            name="POST Source",
            parser_key="wtfilters",
            base_search_url="https://example.com/api/search",
            http_method=Source.HttpMethod.POST,
            request_headers={
                "Referer": "https://example.com/search?q={term}",
                "Origin": "https://example.com",
            },
        )
        headers = source.build_request_headers("Lightning Bolt")
        self.assertEqual(
            headers,
            {
                "Referer": "https://example.com/search?q=Lightning+Bolt",
                "Origin": "https://example.com",
            },
        )

    def test_returns_none_for_empty_headers(self):
        source = Source(
            key="get",
            name="GET Source",
            parser_key="shopify",
            base_search_url="https://example.com/search?q={term}",
        )
        self.assertIsNone(source.build_request_headers("widget"))

    def test_does_not_mutate_stored_headers(self):
        headers_template = {"Referer": "https://example.com/search?q={term}"}
        source = Source(
            key="wt",
            name="POST Source",
            parser_key="wtfilters",
            base_search_url="https://example.com/api/search",
            request_headers=headers_template,
        )
        source.build_request_headers("widget")
        self.assertEqual(
            headers_template,
            {"Referer": "https://example.com/search?q={term}"},
        )


class SourceBuildSearchUrlPostTests(TestCase):
    def test_post_source_without_term_placeholder(self):
        source = Source(
            key="post",
            name="POST Source",
            parser_key="shopify",
            base_search_url="https://example.com/api/search",
            http_method=Source.HttpMethod.POST,
        )
        url = source.build_search_url("widget")
        self.assertEqual(url, "https://example.com/api/search")

    def test_post_source_with_term_placeholder_still_formats(self):
        source = Source(
            key="post",
            name="POST Source",
            parser_key="shopify",
            base_search_url="https://example.com/api/search?q={term}",
            http_method=Source.HttpMethod.POST,
        )
        url = source.build_search_url("rtx 5070")
        self.assertEqual(url, "https://example.com/api/search?q=rtx+5070")

    def test_post_source_appends_suffix(self):
        source = Source(
            key="post",
            name="POST Source",
            parser_key="shopify",
            base_search_url="https://example.com/api/search",
            http_method=Source.HttpMethod.POST,
        )
        url = source.build_search_url("widget", url_suffix="extra=1")
        self.assertEqual(url, "https://example.com/api/search&extra=1")

    def test_get_source_still_requires_term_placeholder(self):
        source = Source(
            key="get",
            name="GET Source",
            parser_key="shopify",
            base_search_url="https://example.com/search",
        )
        with self.assertRaises(ValueError):
            source.build_search_url("widget")


class RunParserSearchPostTests(SimpleTestCase):
    def test_post_uses_fetcher_post_with_body(self):
        parser = MagicMock()
        parser.results = []
        fetcher = MagicMock()
        response = FakeResponse(content=json.dumps(SHOPIFY_PAGE).encode())
        fetcher.post.return_value = response
        body = {"q": "widget"}

        outcome = _run_parser_search(
            parser,
            fetcher,
            "https://example.com/api/search",
            headers={"Origin": "https://example.com"},
            max_pages=5,
            method="POST",
            body=body,
        )

        fetcher.post.assert_called_once_with(
            "https://example.com/api/search",
            json=body,
            headers={"Origin": "https://example.com"},
        )
        fetcher.get.assert_not_called()
        parser.parse_response.assert_called_once_with(response)
        self.assertTrue(outcome.ok)

    def test_post_does_not_paginate_even_when_max_pages_gt_one(self):
        parser = MagicMock()
        parser.results = []
        parser.next_page_url.return_value = "https://example.com/page/2"
        fetcher = MagicMock()
        fetcher.post.return_value = FakeResponse(content=b"{}")
        fetcher.get.return_value = FakeResponse(content=b"{}")

        _run_parser_search(
            parser,
            fetcher,
            "https://example.com/api/search",
            max_pages=5,
            method="POST",
            body={"q": "widget"},
        )

        fetcher.get.assert_not_called()


class RunWebUpdatePostTests(TestCase):
    def setUp(self):
        self.post_source = Source.objects.create(
            name="POST Source",
            key="post",
            parser_key="shopify",
            base_search_url="https://example.com/api/search",
            http_method=Source.HttpMethod.POST,
            request_body_template={"q": "{term}", "context": {"page": 1}},
            request_headers={
                "Origin": "https://example.com",
                "Referer": "https://example.com/search?q={term}",
            },
        )
        self.get_source = Source.objects.create(
            name="GET Source",
            key="get",
            parser_key="shopify",
            base_search_url="https://example.com/search?q={term}",
        )
        self.item = SearchableItem.objects.create(text="test item", active=True)
        ItemSource.objects.create(item=self.item, source=self.post_source)

    @patch("tracking.scrape._run_parser_search")
    def test_post_source_passes_method_and_body(self, mock_run_parser):
        mock_parser = MagicMock()
        mock_parser.results = [
            {
                "title": "Test Product",
                "price": 19.99,
                "category": "Hardware",
                "instock": 1,
            }
        ]
        mock_run_parser.return_value = MagicMock(
            ok=True, http_status=200, error_message="", result_count=1
        )
        fetcher = MagicMock()

        with patch.dict(
            "tracking.parsers.sources",
            {"shopify": MagicMock(return_value=mock_parser)},
        ):
            stats = run_web_update(fetcher=fetcher)

        self.assertEqual(stats.result_count, 1)
        mock_run_parser.assert_called_once()
        _, kwargs = mock_run_parser.call_args
        self.assertEqual(kwargs["method"], Source.HttpMethod.POST)
        self.assertEqual(
            kwargs["body"],
            {"q": "test item", "context": {"page": 1}},
        )
        self.assertEqual(
            kwargs["headers"],
            {
                "Origin": "https://example.com",
                "Referer": "https://example.com/search?q=test+item",
            },
        )
        fetcher.get.assert_not_called()

    @patch("tracking.scrape._run_parser_search")
    def test_get_source_still_uses_get_path(self, mock_run_parser):
        ItemSource.objects.all().delete()
        ItemSource.objects.create(item=self.item, source=self.get_source)
        mock_parser = MagicMock(results=[])
        mock_run_parser.return_value = MagicMock(
            ok=True, http_status=200, error_message="", result_count=0
        )
        fetcher = MagicMock()

        with patch.dict(
            "tracking.parsers.sources",
            {"shopify": MagicMock(return_value=mock_parser)},
        ):
            run_web_update(fetcher=fetcher)

        _, kwargs = mock_run_parser.call_args
        self.assertEqual(kwargs["method"], Source.HttpMethod.GET)
        self.assertIsNone(kwargs["body"])

    def test_post_source_triggers_fetcher_post_end_to_end(self):
        fetcher = MagicMock()
        fetcher.post.return_value = FakeResponse(json_data=SHOPIFY_PAGE)

        stats = run_web_update(fetcher=fetcher)

        self.assertEqual(stats.result_count, 1)
        fetcher.post.assert_called_once_with(
            "https://example.com/api/search",
            json={"q": "test item", "context": {"page": 1}},
            headers={
                "Origin": "https://example.com",
                "Referer": "https://example.com/search?q=test+item",
            },
        )
        fetcher.get.assert_not_called()
