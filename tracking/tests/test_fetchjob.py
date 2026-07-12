from django.test import TestCase
from unittest.mock import MagicMock, patch

from tracking.models import FetchJob, ItemSource, Source, WebUpdate
from tracking.scrape import FetchOutcome, run_web_update
from tracking.tests.base import LinkedSourceTestCase


class FetchJobTests(LinkedSourceTestCase):
    def setUp(self):
        self.fetcher = MagicMock()

    def test_fetch_job_on_http_failure(self):
        self.fetcher.get.return_value = MagicMock(status_code=403, text="Forbidden")
        with patch.dict(
            "tracking.parsers.sources",
            {"cc": MagicMock(return_value=MagicMock(results=[]))},
        ):
            run_web_update(fetcher=self.fetcher)

        job = FetchJob.objects.get()
        self.assertEqual(job.status, FetchJob.Status.BLOCKED)
        self.assertEqual(job.http_status, 403)
        self.assertEqual(WebUpdate.objects.count(), 1)

    def test_fetch_job_on_http_404_stays_http_error(self):
        self.fetcher.get.return_value = MagicMock(status_code=404, text="Not Found")
        with patch.dict(
            "tracking.parsers.sources",
            {"cc": MagicMock(return_value=MagicMock(results=[]))},
        ):
            run_web_update(fetcher=self.fetcher)

        job = FetchJob.objects.get()
        self.assertEqual(job.status, FetchJob.Status.HTTP_ERROR)
        self.assertEqual(job.http_status, 404)

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
            parser_key="bad",
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
            parser_key="bad",
            base_search_url="https://example.com/search?s={term}",
        )
        ItemSource.objects.create(item=self.item, source=bad_source)

        stats = run_web_update(fetcher=self.fetcher)

        self.assertEqual(WebUpdate.objects.count(), 1)
        self.assertEqual(stats.error_count, 1)
        self.assertEqual(FetchJob.objects.count(), 1)
