"""Phase 3 Step 1 — JSON API pagination.

Covers the pagination hooks on the parsers and the paginating orchestrator
``_run_parser_search``. All fetches use a fake fetcher / mocked responses — no
network.
"""

from unittest.mock import MagicMock

from django.test import SimpleTestCase, TestCase

from .models import ItemSource, SearchableItem, Source
from .parsers import ShopifyParser, StorepassParser
from .scrape import _run_parser_search, run_web_update


def _json_response(payload, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


def _shopify_hit(title="Lightning Bolt", price=1.0, qty=1, condition="NM"):
    return {
        "_source": {
            "title": title,
            "MTG_Set_Name": "Alpha",
            "variants": [
                {
                    "price": price,
                    "inventoryQuantity": qty,
                    "selectedOptions": [{"name": "Condition", "value": condition}],
                }
            ],
        }
    }


def _shopify_page(num_hits):
    return {"hits": {"hits": [_shopify_hit() for _ in range(num_hits)]}}


F2F_URL = (
    "https://facetofacegames.com/apps/prod-indexer/search"
    "/pageSize/100/page/1/keyword/Lightning+Bolt"
)

STOREPASS_URL = (
    "https://store.storepass.co/saas/search"
    "?store_id=Q5MjnQr1MA&name=Lightning+Bolt&limit=30"
)


class FakeFetcher:
    """Returns a fixed queue of responses from ``.get()`` and records calls."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.get_calls = []
        self.wait = MagicMock()

    def get(self, url, headers=None):
        self.get_calls.append((url, headers))
        return self._responses.pop(0)


class ShopifyNextPageUrlTests(SimpleTestCase):
    def test_increments_page_segment_for_non_empty_page(self):
        parser = ShopifyParser(term="Lightning Bolt")
        response = _json_response(_shopify_page(1))
        next_url = parser.next_page_url(response, F2F_URL, 2)
        self.assertEqual(
            next_url,
            "https://facetofacegames.com/apps/prod-indexer/search"
            "/pageSize/100/page/2/keyword/Lightning+Bolt",
        )

    def test_returns_none_for_empty_hits(self):
        parser = ShopifyParser(term="Lightning Bolt")
        response = _json_response(_shopify_page(0))
        self.assertIsNone(parser.next_page_url(response, F2F_URL, 2))

    def test_returns_none_when_no_page_segment(self):
        parser = ShopifyParser(term="x")
        response = _json_response(_shopify_page(1))
        url = "https://example.com/search?keyword=x"
        self.assertIsNone(parser.next_page_url(response, url, 2))


class StorepassNextPageUrlTests(SimpleTestCase):
    def test_returns_next_page_when_more_pages_exist(self):
        parser = StorepassParser(term="Lightning Bolt")
        response = _json_response(
            {"products": [], "current_page": 1, "pages": 3}
        )
        next_url = parser.next_page_url(response, STOREPASS_URL, 2)
        self.assertIsNotNone(next_url)
        self.assertIn("page=2", next_url)
        self.assertIn("store_id=Q5MjnQr1MA", next_url)

    def test_returns_none_on_last_page(self):
        parser = StorepassParser(term="Lightning Bolt")
        response = _json_response(
            {"products": [], "current_page": 3, "pages": 3}
        )
        self.assertIsNone(parser.next_page_url(response, STOREPASS_URL, 2))

    def test_returns_none_without_pagination_tokens(self):
        parser = StorepassParser(term="Lightning Bolt")
        response = _json_response({"products": []})
        self.assertIsNone(parser.next_page_url(response, STOREPASS_URL, 2))


class RunParserSearchPaginationTests(SimpleTestCase):
    def test_accumulates_results_and_stops_at_empty_page(self):
        parser = ShopifyParser(term="Lightning Bolt")
        fetcher = FakeFetcher(
            [
                _json_response(_shopify_page(1)),
                _json_response(_shopify_page(1)),
                _json_response(_shopify_page(0)),
            ]
        )

        outcome = _run_parser_search(parser, fetcher, F2F_URL, max_pages=5)

        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.result_count, 2)
        self.assertEqual(len(parser.results), 2)
        # Page 1 + page 2 + the empty page that signals the stop.
        self.assertEqual(len(fetcher.get_calls), 3)
        # wait() is called between page requests (before page 2 and page 3).
        self.assertEqual(fetcher.wait.call_count, 2)

    def test_stops_at_max_pages_cap(self):
        parser = ShopifyParser(term="Lightning Bolt")
        fetcher = FakeFetcher([_json_response(_shopify_page(1)) for _ in range(5)])

        outcome = _run_parser_search(parser, fetcher, F2F_URL, max_pages=2)

        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.result_count, 2)
        self.assertEqual(len(fetcher.get_calls), 2)
        self.assertEqual(fetcher.wait.call_count, 1)

    def test_second_page_url_is_page_two(self):
        parser = ShopifyParser(term="Lightning Bolt")
        fetcher = FakeFetcher(
            [
                _json_response(_shopify_page(1)),
                _json_response(_shopify_page(0)),
            ]
        )

        _run_parser_search(parser, fetcher, F2F_URL, max_pages=3)

        self.assertIn("/page/2/", fetcher.get_calls[1][0])

    def test_keeps_earlier_pages_when_later_page_fails(self):
        parser = ShopifyParser(term="Lightning Bolt")
        fetcher = FakeFetcher(
            [
                _json_response(_shopify_page(1)),
                _json_response(_shopify_page(0), status_code=500),
            ]
        )

        outcome = _run_parser_search(parser, fetcher, F2F_URL, max_pages=3)

        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.result_count, 1)
        self.assertEqual(len(fetcher.get_calls), 2)


class SinglePageRegressionTests(SimpleTestCase):
    def test_max_pages_one_does_single_fetch(self):
        parser = ShopifyParser(term="Lightning Bolt")
        fetcher = FakeFetcher([_json_response(_shopify_page(3))])

        outcome = _run_parser_search(parser, fetcher, F2F_URL, max_pages=1)

        self.assertTrue(outcome.ok)
        self.assertEqual(len(fetcher.get_calls), 1)
        fetcher.wait.assert_not_called()

    def test_max_pages_one_matches_direct_parse(self):
        payload = _shopify_page(3)

        direct = ShopifyParser(term="Lightning Bolt")
        direct.parse_response(_json_response(payload))

        via_search = ShopifyParser(term="Lightning Bolt")
        _run_parser_search(
            via_search, FakeFetcher([_json_response(payload)]), F2F_URL, max_pages=1
        )

        self.assertEqual(via_search.results, direct.results)

    def test_http_failure_on_first_page(self):
        parser = ShopifyParser(term="Lightning Bolt")
        fetcher = FakeFetcher([_json_response({}, status_code=404)])

        outcome = _run_parser_search(parser, fetcher, F2F_URL, max_pages=5)

        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.http_status, 404)
        self.assertEqual(len(fetcher.get_calls), 1)


class RunWebUpdatePaginationTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            key="f2f",
            name="Face to Face Games",
            parser_key="shopify",
            base_search_url=(
                "https://facetofacegames.com/apps/prod-indexer/search"
                "/pageSize/100/page/1/keyword/{term}"
            ),
            max_pages=3,
        )
        self.item = SearchableItem.objects.create(text="Lightning Bolt", active=True)
        ItemSource.objects.create(item=self.item, source=self.source)

    def test_run_web_update_fetches_multiple_pages(self):
        fetcher = FakeFetcher(
            [
                _json_response(_shopify_page(1)),
                _json_response(_shopify_page(1)),
                _json_response(_shopify_page(0)),
            ]
        )

        stats = run_web_update(fetcher=fetcher)

        self.assertEqual(stats.result_count, 2)
        self.assertEqual(stats.error_count, 0)
        self.assertEqual(len(fetcher.get_calls), 3)

    def test_default_max_pages_is_single_page(self):
        self.source.max_pages = 1
        self.source.save()
        fetcher = FakeFetcher([_json_response(_shopify_page(2))])

        stats = run_web_update(fetcher=fetcher)

        self.assertEqual(stats.result_count, 2)
        self.assertEqual(len(fetcher.get_calls), 1)
