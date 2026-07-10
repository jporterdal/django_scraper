"""Phase 4 Step 5 — pinned result URL per ItemSource.

When ``ItemSource.pinned_url`` is set, ``run_web_update`` fetches that URL
directly with GET (bypassing search URL construction and POST bodies).
No network.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from .forms import ItemSourceForm
from .models import ItemSource, SearchableItem, Source
from .scrape import run_web_update


class PinnedUrlScrapeTests(TestCase):
    def setUp(self):
        self.source = Source.objects.create(
            name="GET Source",
            key="get",
            parser_key="shopify",
            base_search_url="https://example.com/search?q={term}",
        )
        self.item = SearchableItem.objects.create(text="test item", active=True)
        self.pinned = "https://example.com/product/12345"
        self.item_source = ItemSource.objects.create(
            item=self.item,
            source=self.source,
            pinned_url=self.pinned,
        )

    @patch("tracking.scrape._run_parser_search")
    def test_pinned_url_fetches_directly(self, mock_run_parser):
        mock_parser = MagicMock()
        mock_parser.results = [
            {
                "title": "Pinned Product",
                "price": 29.99,
                "category": "Cards",
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
        args, kwargs = mock_run_parser.call_args
        self.assertEqual(args[2], self.pinned)
        self.assertEqual(kwargs["method"], "GET")
        self.assertIsNone(kwargs["body"])

    @patch("tracking.scrape._run_parser_search")
    def test_pinned_url_skips_build_search_url(self, mock_run_parser):
        mock_parser = MagicMock(results=[])
        mock_run_parser.return_value = MagicMock(
            ok=True, http_status=200, error_message="", result_count=0
        )
        fetcher = MagicMock()

        with (
            patch.dict(
                "tracking.parsers.sources",
                {"shopify": MagicMock(return_value=mock_parser)},
            ),
            patch.object(
                Source,
                "build_search_url",
                side_effect=AssertionError("build_search_url should not be called"),
            ) as mock_build,
        ):
            run_web_update(fetcher=fetcher)

        mock_build.assert_not_called()

    @patch("tracking.scrape._run_parser_search")
    def test_empty_pinned_url_uses_search_url(self, mock_run_parser):
        self.item_source.pinned_url = ""
        self.item_source.save()
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

        args, kwargs = mock_run_parser.call_args
        self.assertEqual(
            args[2],
            "https://example.com/search?q=test+item",
        )
        self.assertEqual(kwargs["method"], Source.HttpMethod.GET)
        self.assertIsNone(kwargs["body"])

    @patch("tracking.scrape._run_parser_search")
    def test_pinned_url_on_post_source_still_uses_get(self, mock_run_parser):
        post_source = Source.objects.create(
            name="POST Source",
            key="post",
            parser_key="shopify",
            base_search_url="https://example.com/api/search",
            http_method=Source.HttpMethod.POST,
            request_body_template={"q": "{term}"},
        )
        ItemSource.objects.all().delete()
        ItemSource.objects.create(
            item=self.item,
            source=post_source,
            pinned_url=self.pinned,
        )
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
        self.assertEqual(kwargs["method"], "GET")
        self.assertIsNone(kwargs["body"])


class PinnedUrlFormTests(TestCase):
    def test_form_exposes_pinned_url_with_help_text(self):
        form = ItemSourceForm()
        self.assertIn("pinned_url", form.fields)
        self.assertIn("search url", form.fields["pinned_url"].help_text.lower())
