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


class ResponseTooLargeError(Exception):
    """Raised when a fetched response exceeds the configured size cap.

    Carries the offending ``url`` and the observed ``size`` (bytes) alongside the
    ``limit`` that was exceeded so callers can log/record it distinctly.
    """

    def __init__(self, url, size, limit):
        self.url = url
        self.size = size
        self.limit = limit
        super().__init__(
            f"Response from {url} is too large: {size} bytes exceeds cap of "
            f"{limit} bytes"
        )


class Fetcher:
    """HTTP client for vendor search pages with retries and rate limiting."""

    def __init__(
        self,
        delay_seconds=1.0,
        jitter_seconds=0.42,
        timeout=30,
        user_agent=DEFAULT_USER_AGENT,
        max_response_bytes=None,
    ):
        self.delay_seconds = delay_seconds
        self.jitter_seconds = jitter_seconds
        self.timeout = timeout
        # 0 or None disables the size cap (unlimited).
        self.max_response_bytes = max_response_bytes
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent

        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 503],
            allowed_methods=["GET", "POST"],
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
            max_response_bytes=getattr(settings, "SCRAPE_MAX_RESPONSE_BYTES", 8_000_000),
        )

    def wait(self):
        if self.delay_seconds <= 0:
            return
        jitter = random.uniform(0, self.jitter_seconds) if self.jitter_seconds > 0 else 0
        pause = self.delay_seconds + jitter
        logger.debug("Rate limit pause %.2fs before next request", pause)
        time.sleep(pause)

    def request(self, method, url, json=None, headers=None):
        method_upper = method.upper()
        logger.info("%s %s", method_upper, url)
        if method_upper == "GET":
            response = self._session.get(url, timeout=self.timeout, headers=headers)
        elif method_upper == "POST":
            response = self._session.post(
                url, json=json, timeout=self.timeout, headers=headers
            )
        else:
            raise ValueError(f"Unsupported HTTP method: {method!r}")

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
        self._enforce_size_cap(response, url)
        return response

    def get(self, url, headers=None):
        return self.request("GET", url, headers=headers)

    def post(self, url, json=None, headers=None):
        return self.request("POST", url, json=json, headers=headers)

    def _enforce_size_cap(self, response, url):
        """Reject responses larger than ``max_response_bytes``.

        Uses the ``Content-Length`` header as a fast path when present, then falls
        back to the actual downloaded body size. Raises ``ResponseTooLargeError``
        when the cap is exceeded. A cap of 0 or None means unlimited.
        """
        limit = self.max_response_bytes
        if not limit:
            return

        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                declared_size = int(declared)
            except (TypeError, ValueError):
                declared_size = None
            if declared_size is not None and declared_size > limit:
                logger.warning(
                    "Response from %s declares %s bytes, exceeding cap of %s bytes",
                    url,
                    declared_size,
                    limit,
                )
                raise ResponseTooLargeError(url, declared_size, limit)

        body_size = len(response.content)
        if body_size > limit:
            logger.warning(
                "Response from %s is %s bytes, exceeding cap of %s bytes",
                url,
                body_size,
                limit,
            )
            raise ResponseTooLargeError(url, body_size, limit)
