import json
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.messages import get_messages
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .fetcher import Fetcher
from .models import CC_DEFAULT_SEARCH_URL, FetchJob, ItemSource, SearchableItem, SearchResult, Source, Tag, WebUpdate
from .scrape import FetchOutcome, WebUpdateStats, run_web_update
from .views import SearchableListView


class UpdateFromWebViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.source, _ = Source.objects.update_or_create(
            key="cc",
            defaults={
                "name": "Test Source",
                "base_search_url": "https://example.com/search?s={term}",
            },
        )
        self.item = SearchableItem.objects.create(text="test item", active=True)
        ItemSource.objects.create(item=self.item, source=self.source)

    def test_get_redirects_without_scraping(self):
        with patch("tracking.views.SearchResult.update_from_web") as mock_update:
            response = self.client.get(reverse("update"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("view_terms"))
        mock_update.assert_not_called()

    def test_post_all_active_calls_update_without_item_filter(self):
        with patch(
            "tracking.views.SearchResult.update_from_web",
            return_value=WebUpdateStats(result_count=3, error_count=0, search_count=1),
        ) as mock_update:
            response = self.client.post(reverse("update"), {"mode": "all"})

        self.assertEqual(response.status_code, 302)
        mock_update.assert_called_once_with(items=None)
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Stored 3 price result(s)", str(messages[0]))

    def test_post_selected_passes_filtered_items(self):
        with patch(
            "tracking.views.SearchResult.update_from_web",
            return_value=WebUpdateStats(result_count=1, error_count=0, search_count=1),
        ) as mock_update:
            response = self.client.post(
                reverse("update"),
                {"mode": "selected", "item_ids": [str(self.item.pk)]},
            )

        self.assertEqual(response.status_code, 302)
        items_arg = mock_update.call_args.kwargs["items"]
        self.assertEqual(list(items_arg.values_list("pk", flat=True)), [self.item.pk])

    def test_post_selected_with_no_checkboxes_shows_warning(self):
        with patch("tracking.views.SearchResult.update_from_web") as mock_update:
            response = self.client.post(reverse("update"), {"mode": "selected"})

        self.assertEqual(response.status_code, 302)
        mock_update.assert_not_called()
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(str(messages[0]), "No items selected.")

    def test_post_selected_inactive_items_shows_warning(self):
        self.item.active = False
        self.item.save()

        with patch("tracking.views.SearchResult.update_from_web") as mock_update:
            response = self.client.post(
                reverse("update"),
                {"mode": "selected", "item_ids": [str(self.item.pk)]},
            )

        mock_update.assert_not_called()
        messages = list(get_messages(response.wsgi_request))
        self.assertIn("No active items in selection", str(messages[0]))

    def test_post_with_no_configured_sources_shows_warning(self):
        ItemSource.objects.all().delete()

        with patch("tracking.views.SearchResult.update_from_web") as mock_update:
            response = self.client.post(reverse("update"), {"mode": "all"})

        mock_update.assert_not_called()
        messages = list(get_messages(response.wsgi_request))
        self.assertIn("No items with configured sources", str(messages[0]))


class FetcherTests(TestCase):
    @patch("tracking.fetcher.time.sleep")
    def test_wait_sleeps_for_configured_delay(self, mock_sleep):
        Fetcher(delay_seconds=2.0, jitter_seconds=0.0).wait()
        mock_sleep.assert_called_once_with(2.0)

    @patch("tracking.fetcher.time.sleep")
    def test_wait_skipped_when_delay_is_zero(self, mock_sleep):
        Fetcher(delay_seconds=0).wait()
        mock_sleep.assert_not_called()


class ScrapeOrchestratorTests(TestCase):
    def setUp(self):
        self.source, _ = Source.objects.update_or_create(
            key="cc",
            defaults={
                "name": "Test Source",
                "base_search_url": "https://example.com/search?s={term}",
            },
        )
        self.item = SearchableItem.objects.create(text="test item", active=True)
        ItemSource.objects.create(item=self.item, source=self.source)
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
            base_search_url="https://example.com/search?s={term}",
        )
        ItemSource.objects.create(item=self.item, source=bad_source)

        stats = run_web_update(fetcher=self.fetcher)

        self.assertEqual(stats.error_count, 1)
        self.assertEqual(stats.result_count, 0)
        self.fetcher.get.assert_not_called()

    @patch("tracking.scrape._run_parser_search")
    def test_rate_limit_pause_between_searches(self, mock_run_parser):
        item_two = SearchableItem.objects.create(text="second item", active=True)
        ItemSource.objects.create(item=item_two, source=self.source)
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
        self.fetcher.wait.assert_called_once()


class SourceModelTests(TestCase):
    def test_build_search_url_encodes_term(self):
        source = Source(
            key="cc",
            name="Canada Computers",
            base_search_url=CC_DEFAULT_SEARCH_URL,
        )
        url = source.build_search_url("rtx 5070")
        self.assertEqual(
            url,
            "https://www.canadacomputers.com/en/search?s=rtx+5070&pickup=62",
        )

    def test_build_search_url_appends_suffix(self):
        source = Source(
            key="cc",
            name="Canada Computers",
            base_search_url=CC_DEFAULT_SEARCH_URL,
        )
        url = source.build_search_url("widget", url_suffix="extra=1")
        self.assertIn("extra=1", url)
        self.assertIn("widget", url)

    def test_build_search_url_requires_term_placeholder(self):
        source = Source(
            key="xx",
            name="Bad",
            base_search_url="https://example.com/search",
        )
        with self.assertRaises(ValueError):
            source.build_search_url("widget")


class ScrapeUrlIntegrationTests(TestCase):
    def setUp(self):
        self.source, _ = Source.objects.update_or_create(
            key="cc",
            defaults={
                "name": "Test Source",
                "base_search_url": "https://example.com/search?s={term}&pickup=62",
            },
        )
        self.item = SearchableItem.objects.create(text="test item", active=True)
        ItemSource.objects.create(item=self.item, source=self.source)
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
        mock_run_parser.assert_called_once_with(mock_parser, self.fetcher, expected_url)


class TagFilterTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.gpu_tag = Tag.objects.create(name="GPU", color="#3498db")
        self.mtg_tag = Tag.objects.create(name="MTG", color="#9b59b6")
        self.gpu_item = SearchableItem.objects.create(text="rtx 5070")
        self.mtg_item = SearchableItem.objects.create(text="lightning bolt")
        self.gpu_item.tags.add(self.gpu_tag)
        self.mtg_item.tags.add(self.mtg_tag)

    def test_list_shows_all_items_without_filter(self):
        response = self.client.get(reverse("view_terms"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "rtx 5070")
        self.assertContains(response, "lightning bolt")

    def test_list_filtered_by_tag(self):
        response = self.client.get(reverse("view_terms"), {"tag": self.gpu_tag.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "rtx 5070")
        self.assertNotContains(response, "lightning bolt")

    def test_list_includes_tag_filter_buttons(self):
        response = self.client.get(reverse("view_terms"))
        self.assertContains(response, "Filter by tag:")
        self.assertContains(response, "GPU")
        self.assertContains(response, "MTG")
        self.assertContains(response, f"?tag={self.gpu_tag.pk}")


class TagManagementTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tag = Tag.objects.create(name="GPU", color="#3498db")

    def test_tag_list_page(self):
        response = self.client.get(reverse("view_tags"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "GPU")
        self.assertContains(response, "Add Tag")

    def test_create_tag(self):
        response = self.client.post(
            reverse("add_tag"),
            {"name": "MTG", "color": "#9b59b6"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Tag.objects.filter(name="MTG").exists())

    def test_edit_tag(self):
        response = self.client.post(
            reverse("edit_tag", args=[self.tag.pk]),
            {"name": "Graphics", "color": "#e74c3c"},
        )
        self.assertEqual(response.status_code, 302)
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.name, "Graphics")

    def test_delete_tag(self):
        response = self.client.post(reverse("delete_tag", args=[self.tag.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Tag.objects.filter(pk=self.tag.pk).exists())


class CCSearchParserFixtureTests(SimpleTestCase):
    """When Canada Computers changes search result HTML structure, update fixtures
    and CCSearchParser selectors together."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.conf import settings

        cls.fixtures = settings.BASE_DIR / "tracking" / "fixtures" / "html" / "cc"

    def _parse_fixture(self, filename, term="RTX 5070"):
        from tracking.parsers import CCSearchParser

        html = (self.fixtures / filename).read_text()
        parser = CCSearchParser(term=term)
        parser._init_vars()
        parser.feed(html)
        return parser

    def test_parses_products_from_minimal_fixture(self):
        parser = self._parse_fixture("search_results_minimal.html")
        self.assertEqual(len(parser.results), 2)

    def test_parses_price_and_title(self):
        parser = self._parse_fixture("search_results_minimal.html")
        first = parser.results[0]
        self.assertEqual(first["title"], "Test GPU RTX 5070")
        self.assertAlmostEqual(first["price"], 799.99)
        self.assertTrue(first["instock"])

    def test_out_of_stock_product(self):
        parser = self._parse_fixture("search_results_minimal.html")
        oos = parser.results[1]
        self.assertFalse(oos["instock"])


@override_settings(TIME_ZONE="America/Halifax", USE_TZ=True)
class TimezoneDisplayTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.source, _ = Source.objects.update_or_create(
            key="cc",
            defaults={
                "name": "Test Source",
                "base_search_url": "https://example.com/search?s={term}",
            },
        )
        self.item = SearchableItem.objects.create(text="test item", active=True)
        ItemSource.objects.create(item=self.item, source=self.source)

    def test_settings_time_zone(self):
        self.assertEqual(settings.TIME_ZONE, "America/Halifax")

    def test_localtime_used_in_sparkline_json(self):
        utc_dt = datetime(2026, 1, 16, 3, 0, 0, tzinfo=ZoneInfo("UTC"))
        update = WebUpdate.objects.create()
        WebUpdate.objects.filter(pk=update.pk).update(timestamp=utc_dt)
        update.refresh_from_db()
        SearchResult.objects.create(
            title="Test Product",
            search_term=self.item.text,
            price=19.99,
            item=self.item,
            update=update,
            source=self.source,
        )

        request = RequestFactory().get(reverse("view_terms"))
        view = SearchableListView()
        view.setup(request)
        view.object_list = view.get_queryset()
        context = view.get_context_data()
        items = json.loads(context["items_json"])
        item_data = next(i for i in items if i["id"] == self.item.pk)
        self.assertEqual(item_data["price_history"][0]["date"], "15/01/26")

    def test_webupdate_list_shows_atlantic_time(self):
        utc_dt = datetime(2026, 1, 15, 18, 0, 0, tzinfo=ZoneInfo("UTC"))
        update = WebUpdate.objects.create()
        WebUpdate.objects.filter(pk=update.pk).update(timestamp=utc_dt)
        update.refresh_from_db()

        response = self.client.get(reverse("view_updates"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2026-01-15 14:00")
        self.assertNotContains(response, "2026-01-15 18:00")


class SearchTermAndSummaryQueryTests(TestCase):
    def setUp(self):
        self.source, _ = Source.objects.update_or_create(
            key="cc",
            defaults={
                "name": "Test Source",
                "base_search_url": "https://example.com/search?s={term}",
            },
        )
        self.item = SearchableItem.objects.create(text="test item", active=True)
        ItemSource.objects.create(item=self.item, source=self.source)
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

        self.assertEqual(annotated_item.latest_minprice, 100.0)
        self.assertEqual(annotated_item.latest_minprice_title, "In Stock")


class FetchJobTests(TestCase):
    def setUp(self):
        self.source, _ = Source.objects.update_or_create(
            key="cc",
            defaults={
                "name": "Test Source",
                "base_search_url": "https://example.com/search?s={term}",
            },
        )
        self.item = SearchableItem.objects.create(text="test item", active=True)
        ItemSource.objects.create(item=self.item, source=self.source)
        self.fetcher = MagicMock()

    def test_fetch_job_on_http_failure(self):
        self.fetcher.get.return_value = MagicMock(status_code=403, text="Forbidden")
        with patch.dict(
            "tracking.parsers.sources",
            {"cc": MagicMock(return_value=MagicMock(results=[]))},
        ):
            run_web_update(fetcher=self.fetcher)

        job = FetchJob.objects.get()
        self.assertEqual(job.status, FetchJob.Status.HTTP_ERROR)
        self.assertEqual(job.http_status, 403)
        self.assertEqual(WebUpdate.objects.count(), 1)

    @patch("tracking.scrape._run_parser_search")
    def test_fetch_job_on_success(self, mock_run_parser):
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
            ok=True, http_status=200, error_message="", result_count=2
        )

        with patch.dict(
            "tracking.parsers.sources",
            {"cc": MagicMock(return_value=mock_parser)},
        ):
            run_web_update(fetcher=self.fetcher)

        job = FetchJob.objects.get()
        self.assertEqual(job.status, FetchJob.Status.SUCCESS)
        self.assertEqual(job.result_count, 2)
        self.assertEqual(job.http_status, 200)

    def test_fetch_job_on_unknown_parser(self):
        ItemSource.objects.all().delete()
        bad_source = Source.objects.create(
            name="Bad Source",
            key="bad",
            base_search_url="https://example.com/search?s={term}",
        )
        ItemSource.objects.create(item=self.item, source=bad_source)

        run_web_update(fetcher=self.fetcher)

        job = FetchJob.objects.get()
        self.assertEqual(job.status, FetchJob.Status.CONFIG_ERROR)
        self.fetcher.get.assert_not_called()

    def test_webupdate_created_even_if_all_fail(self):
        ItemSource.objects.all().delete()
        bad_source = Source.objects.create(
            name="Bad Source",
            key="bad",
            base_search_url="https://example.com/search?s={term}",
        )
        ItemSource.objects.create(item=self.item, source=bad_source)

        stats = run_web_update(fetcher=self.fetcher)

        self.assertEqual(WebUpdate.objects.count(), 1)
        self.assertEqual(stats.error_count, 1)
        self.assertEqual(FetchJob.objects.count(), 1)


class ItemSourcePatternFieldsTests(TestCase):
    def setUp(self):
        self.source, _ = Source.objects.update_or_create(
            key="cc",
            defaults={
                "name": "Test Source",
                "base_search_url": CC_DEFAULT_SEARCH_URL,
            },
        )
        self.item = SearchableItem.objects.create(text="rtx 5070")

    def test_item_source_pattern_fields_default_empty(self):
        item_source = ItemSource.objects.create(item=self.item, source=self.source)
        self.assertEqual(item_source.title_include_patterns, [])
        self.assertEqual(item_source.title_exclude_patterns, [])


class CCSearchParserPatternTests(SimpleTestCase):
    def test_cc_parser_no_gpu_patterns(self):
        from tracking.parsers import CCSearchParser

        term = "RTX 5070"
        parser = CCSearchParser(term=term)
        parser._init_vars()
        self.assertEqual(parser.title_patterns, [term.lower() + "$"])


class TitleMatchesRulesTests(SimpleTestCase):
    def test_title_matches_rules_stub(self):
        from tracking.matching import title_matches_rules

        title = "MSI RTX 5070 Gaming X Trio"

        self.assertTrue(title_matches_rules(title, [], []))
        self.assertTrue(title_matches_rules(title, ["MSI.*5070"], []))
        self.assertFalse(title_matches_rules(title, ["ASUS.*5070"], []))
        self.assertFalse(title_matches_rules(title, [], ["MSI.*"]))
        self.assertTrue(title_matches_rules(title, ["MSI.*5070"], ["Gigabyte.*"]))
