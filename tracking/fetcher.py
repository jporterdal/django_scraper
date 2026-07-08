import logging
import random
import time

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:138.0) "
    "Gecko/20100101 Firefox/138.0"
)


class Fetcher:
    """HTTP client for vendor search pages with retries and rate limiting."""

    def __init__(
        self,
        delay_seconds=3.0,
        jitter_seconds=1.0,
        timeout=30,
        user_agent=DEFAULT_USER_AGENT,
    ):
        self.delay_seconds = delay_seconds
        self.jitter_seconds = jitter_seconds
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent

        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 503],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    @classmethod
    def from_settings(cls):
        return cls(
            delay_seconds=getattr(settings, "SCRAPE_REQUEST_DELAY_SECONDS", 3.0),
            jitter_seconds=getattr(settings, "SCRAPE_REQUEST_DELAY_JITTER_SECONDS", 1.0),
            timeout=getattr(settings, "SCRAPE_REQUEST_TIMEOUT_SECONDS", 30),
        )

    def wait(self):
        if self.delay_seconds <= 0:
            return
        jitter = random.uniform(0, self.jitter_seconds) if self.jitter_seconds > 0 else 0
        pause = self.delay_seconds + jitter
        logger.debug("Rate limit pause %.2fs before next request", pause)
        time.sleep(pause)

    def get(self, url, headers=None):
        logger.info("GET %s", url)
        response = self._session.get(url, timeout=self.timeout, headers=headers)
        if response.status_code in (403, 429):
            logger.warning(
                "HTTP %s from %s — possible bot detection or rate limiting",
                response.status_code,
                url,
            )
        elif response.status_code >= 400:
            logger.warning("HTTP %s from %s", response.status_code, url)
        else:
            logger.debug("HTTP %s from %s", response.status_code, url)
        return response
