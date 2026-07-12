from django.test import TestCase
from unittest.mock import MagicMock, patch
from tracking.fetcher import Fetcher

class FetcherTests(TestCase):
    @patch("tracking.fetcher.time.sleep")
    def test_wait_sleeps_for_configured_delay(self, mock_sleep):
        Fetcher(delay_seconds=2.0, jitter_seconds=0.0).wait()
        mock_sleep.assert_called_once_with(2.0)

    @patch("tracking.fetcher.time.sleep")
    def test_wait_skipped_when_delay_is_zero(self, mock_sleep):
        Fetcher(delay_seconds=0).wait()
        mock_sleep.assert_not_called()
