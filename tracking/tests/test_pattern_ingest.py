"""Step 1 — pattern-aware ingest: include/exclude at store time.

Exercises the real ``WtFiltersParser``/``JSONSearchParser.add_result`` path
(only the HTTP fetch is stubbed) rather than a bare mock with pre-populated
``.results``, since title-pattern filtering now happens inline in
``add_result`` instead of a separate post-parse pass — a mock parser would
bypass it entirely and prove nothing.
"""

from unittest.mock import MagicMock

from django.test import TestCase

from tracking.models import FetchJob, SearchResult, WebUpdate
from tracking.scrape import run_web_update
from tracking.tests.factories import make_linked_item, make_source


def _wt_response(rows):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"data": {"results": rows}}
    return response


class PatternAwareIngestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        source = make_source(parser_key="wtfilters")
        cls.source, cls.item, cls.item_source = make_linked_item(
            source=source, item_text="Lightning Bolt"
        )

    def setUp(self):
        self.fetcher = MagicMock()

    def _run(self, rows):
        self.fetcher.get.return_value = _wt_response(rows)
        return run_web_update(fetcher=self.fetcher)

    def test_include_pattern_stores_only_matching_titles(self):
        self.item_source.title_include_patterns = [r"\(NM\)"]
        self.item_source.save(update_fields=["title_include_patterns"])
        rows = [
            {
                "title": "Lightning Bolt (NM)",
                "price": 1.50,
                "category": "Cards",
                "in_stock": True,
            },
            {
                "title": "Lightning Bolt (LP)",
                "price": 1.00,
                "category": "Cards",
                "in_stock": True,
            },
        ]

        stats = self._run(rows)
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
        rows = [
            {
                "title": "Lightning Bolt (NM)",
                "price": 1.50,
                "category": "Cards",
                "in_stock": True,
            },
            {
                "title": "Lightning Bolt Foil (NM)",
                "price": 5.00,
                "category": "Cards",
                "in_stock": True,
            },
        ]

        stats = self._run(rows)
        job = FetchJob.objects.get()

        self.assertEqual(job.result_count, 1)
        self.assertEqual(job.stored_count, 1)
        self.assertEqual(stats.result_count, 1)
        self.assertEqual(SearchResult.objects.count(), 1)
        self.assertEqual(SearchResult.objects.get().title, "Lightning Bolt (NM)")

    def test_empty_patterns_store_all_titles(self):
        rows = [
            {
                "title": "Lightning Bolt (NM)",
                "price": 1.50,
                "category": "Cards",
                "in_stock": True,
            },
            {
                "title": "Lightning Bolt (LP)",
                "price": 1.00,
                "category": "Cards",
                "in_stock": False,
            },
        ]

        stats = self._run(rows)
        job = FetchJob.objects.get()

        self.assertEqual(job.result_count, 2)
        self.assertEqual(job.stored_count, 2)
        self.assertEqual(stats.result_count, 2)
        self.assertEqual(SearchResult.objects.count(), 2)

    def test_all_filtered_out_is_success_with_zero_counts(self):
        self.item_source.title_include_patterns = [r"\(NM\)"]
        self.item_source.save(update_fields=["title_include_patterns"])
        rows = [
            {
                "title": "Lightning Bolt (LP)",
                "price": 1.00,
                "category": "Cards",
                "in_stock": True,
            },
            {
                "title": "Lightning Bolt (MP)",
                "price": 0.75,
                "category": "Cards",
                "in_stock": True,
            },
        ]

        stats = self._run(rows)
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

    def test_vendor_returns_zero_rows_is_empty(self):
        """True vendor-side "no results" (raw_count == 0) is still EMPTY,
        distinct from "vendor returned rows but our patterns rejected them all"."""
        stats = self._run([])
        job = FetchJob.objects.get()

        self.assertEqual(job.status, FetchJob.Status.EMPTY)
        self.assertEqual(stats.result_count, 0)
        self.assertEqual(SearchResult.objects.count(), 0)

    def test_dedup_still_applies_to_pattern_matching_titles(self):
        self.item_source.title_include_patterns = [r"\(NM\)"]
        self.item_source.save(update_fields=["title_include_patterns"])
        rows = [
            {
                "title": "Lightning Bolt (NM)",
                "price": 1.50,
                "category": "Cards",
                "in_stock": True,
            },
            {
                "title": "Lightning Bolt (LP)",
                "price": 1.00,
                "category": "Cards",
                "in_stock": True,
            },
        ]

        self._run(rows)
        self.assertEqual(SearchResult.objects.count(), 1)

        stats = self._run(rows)
        webupdate = WebUpdate.objects.order_by("-timestamp").first()
        job = FetchJob.objects.filter(webupdate=webupdate).get()

        self.assertEqual(SearchResult.objects.count(), 1)
        self.assertEqual(job.result_count, 1)
        self.assertEqual(job.stored_count, 0)
        self.assertEqual(stats.result_count, 0)
        self.assertEqual(webupdate.skipped_duplicate_count, 1)
