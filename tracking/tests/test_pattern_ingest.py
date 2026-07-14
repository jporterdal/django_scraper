"""Step 1 — pattern-aware ingest: include/exclude at store time."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from tracking.models import FetchJob, SearchResult, WebUpdate
from tracking.scrape import FetchOutcome, run_web_update
from tracking.tests.factories import make_linked_item


def _ok_outcome(result_count=0):
    return FetchOutcome(
        ok=True, http_status=200, error_message="", result_count=result_count
    )


def _mock_parser(results):
    parser = MagicMock()
    parser.results = results
    return parser


class PatternAwareIngestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source, cls.item, cls.item_source = make_linked_item(item_text="Lightning Bolt")

    def setUp(self):
        self.fetcher = MagicMock()

    def _run(self, results):
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

    def test_include_pattern_stores_only_matching_titles(self):
        self.item_source.title_include_patterns = [r"\(NM\)"]
        self.item_source.save(update_fields=["title_include_patterns"])
        results = [
            {
                "title": "Lightning Bolt (NM)",
                "price": 1.50,
                "category": "Cards",
                "instock": 1,
            },
            {
                "title": "Lightning Bolt (LP)",
                "price": 1.00,
                "category": "Cards",
                "instock": 1,
            },
        ]

        stats = self._run(results)
        job = FetchJob.objects.get()

        self.assertEqual(job.status, FetchJob.Status.SUCCESS)
        self.assertEqual(job.result_count, 1)
        self.assertEqual(job.stored_count, 1)
        self.assertEqual(stats.result_count, 1)
        self.assertEqual(SearchResult.objects.count(), 1)
        self.assertEqual(SearchResult.objects.get().title, "Lightning Bolt (NM)")

    def test_exclude_pattern_skips_matching_titles(self):
        self.item_source.title_exclude_patterns = ["Foil"]
        self.item_source.save(update_fields=["title_exclude_patterns"])
        results = [
            {
                "title": "Lightning Bolt (NM)",
                "price": 1.50,
                "category": "Cards",
                "instock": 1,
            },
            {
                "title": "Lightning Bolt Foil (NM)",
                "price": 5.00,
                "category": "Cards",
                "instock": 1,
            },
        ]

        stats = self._run(results)
        job = FetchJob.objects.get()

        self.assertEqual(job.result_count, 1)
        self.assertEqual(job.stored_count, 1)
        self.assertEqual(stats.result_count, 1)
        self.assertEqual(SearchResult.objects.count(), 1)
        self.assertEqual(SearchResult.objects.get().title, "Lightning Bolt (NM)")

    def test_empty_patterns_store_all_titles(self):
        results = [
            {
                "title": "Lightning Bolt (NM)",
                "price": 1.50,
                "category": "Cards",
                "instock": 1,
            },
            {
                "title": "Lightning Bolt (LP)",
                "price": 1.00,
                "category": "Cards",
                "instock": 0,
            },
        ]

        stats = self._run(results)
        job = FetchJob.objects.get()

        self.assertEqual(job.result_count, 2)
        self.assertEqual(job.stored_count, 2)
        self.assertEqual(stats.result_count, 2)
        self.assertEqual(SearchResult.objects.count(), 2)

    def test_all_filtered_out_is_success_with_zero_counts(self):
        self.item_source.title_include_patterns = [r"\(NM\)"]
        self.item_source.save(update_fields=["title_include_patterns"])
        results = [
            {
                "title": "Lightning Bolt (LP)",
                "price": 1.00,
                "category": "Cards",
                "instock": 1,
            },
            {
                "title": "Lightning Bolt (MP)",
                "price": 0.75,
                "category": "Cards",
                "instock": 1,
            },
        ]

        stats = self._run(results)
        job = FetchJob.objects.get()
        webupdate = WebUpdate.objects.get()

        self.assertEqual(job.status, FetchJob.Status.SUCCESS)
        self.assertNotEqual(job.status, FetchJob.Status.EMPTY)
        self.assertEqual(job.result_count, 0)
        self.assertEqual(job.stored_count, 0)
        self.assertEqual(stats.result_count, 0)
        self.assertEqual(SearchResult.objects.count(), 0)
        self.assertEqual(webupdate.skipped_duplicate_count, 0)
        # Unchanged badge uses result_count > 0 and stored_count == 0;
        # all-filtered-out must not look like an unchanged confirm.
        self.assertFalse(job.result_count > 0 and job.stored_count == 0)

    def test_dedup_still_applies_to_pattern_matching_titles(self):
        self.item_source.title_include_patterns = [r"\(NM\)"]
        self.item_source.save(update_fields=["title_include_patterns"])
        results = [
            {
                "title": "Lightning Bolt (NM)",
                "price": 1.50,
                "category": "Cards",
                "instock": 1,
            },
            {
                "title": "Lightning Bolt (LP)",
                "price": 1.00,
                "category": "Cards",
                "instock": 1,
            },
        ]

        self._run(results)
        self.assertEqual(SearchResult.objects.count(), 1)

        stats = self._run(results)
        webupdate = WebUpdate.objects.order_by("-timestamp").first()
        job = FetchJob.objects.filter(webupdate=webupdate).get()

        self.assertEqual(SearchResult.objects.count(), 1)
        self.assertEqual(job.result_count, 1)
        self.assertEqual(job.stored_count, 0)
        self.assertEqual(stats.result_count, 0)
        self.assertEqual(webupdate.skipped_duplicate_count, 1)
