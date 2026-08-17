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

    def test_default_session_retries_429_and_503(self):
        fetcher = Fetcher()
        adapter = fetcher._session.get_adapter("https://example.com")
        self.assertEqual(set(adapter.max_retries.status_forcelist), {429, 503})

    def test_api_session_retries_503_only_not_429(self):
        """Profiled (API) sends must not have urllib3 silently retry a 429 —
        the pacer needs to see it and apply Retry-After / Defer (D6)."""
        fetcher = Fetcher()
        adapter = fetcher._api_session.get_adapter("https://example.com")
        self.assertEqual(set(adapter.max_retries.status_forcelist), {503})

    def test_get_profiled_routes_to_api_session(self):
        fetcher = Fetcher()
        fake_response = MagicMock(status_code=200, headers={})
        fetcher._api_session = MagicMock()
        fetcher._api_session.get.return_value = fake_response
        fetcher._session = MagicMock()
        fetcher.get("https://example.com", profiled=True)
        fetcher._api_session.get.assert_called_once()
        fetcher._session.get.assert_not_called()

    def test_get_unprofiled_routes_to_default_session(self):
        fetcher = Fetcher()
        fake_response = MagicMock(status_code=200, headers={})
        fetcher._session = MagicMock()
        fetcher._session.get.return_value = fake_response
        fetcher._api_session = MagicMock()
        fetcher.get("https://example.com")
        fetcher._session.get.assert_called_once()
        fetcher._api_session.get.assert_not_called()
