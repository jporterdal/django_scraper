"""Replay fetcher: serves demo searches from local catalogues (design D7).

Drop-in for ``tracking.fetcher.Fetcher`` in demo mode. It contains no network
code at all: it only understands ``demo://<source_key>/search?q=<term>`` URLs
and refuses anything else, so a misconfigured URL fails closed as an errored
fetch job instead of falling back to a real request.
"""

import hashlib
import json
import math
import re
from urllib.parse import parse_qs, urlsplit

import requests
from django.utils import timezone

from ..matching import _normalize_for_match
from . import dataset
from .renderers import RENDERERS

# Results per search, after word matching (keeps every demo response small).
MAX_RESULTS = 24
# Price drift never moves a price more than this fraction from its catalogue value.
MAX_DRIFT = 0.05

_STOPWORDS = frozenset({"a", "an", "and", "of", "the"})


class DemoEgressError(RuntimeError):
    """A demo fetch was asked for something other than bundled demo data."""


def _words(text):
    return {
        word
        for word in re.findall(r"[a-z0-9]+", _normalize_for_match(text))
        if word not in _STOPWORDS
    }


def search_catalogue(source_key, term):
    """Products sharing at least one word with ``term``, like a loose vendor search.

    Deliberately looser than the app's own relevance filter, so near-misses
    come back and are then visibly rejected by the real filtering pipeline.
    """
    term_words = _words(term)
    if not term_words:
        return []
    matches = [
        product
        for product in dataset.catalogue(source_key)
        if _words(product["title"]) & term_words
    ]
    return matches[:MAX_RESULTS]


def _unit(*parts):
    """Deterministic pseudo-random float in [0, 1) from ``parts``."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(digest[:12], 16) / float(1 << 48)


def drift_factor(product_id, when):
    """Bounded, smooth, deterministic multiplicative drift for one product at ``when``.

    Two slow waves (weeks/days) give charts a believable shape; a small
    per-minute noise term means two update runs a minute apart still record
    new prices rather than being deduplicated as unchanged.
    """
    days = when.timestamp() / 86400.0
    phase_a = _unit(product_id, "a") * 2 * math.pi
    phase_b = _unit(product_id, "b") * 2 * math.pi
    noise = (_unit(product_id, int(when.timestamp() // 60)) - 0.5) * 0.02
    value = (
        0.03 * math.sin(2 * math.pi * days / 17.0 + phase_a)
        + 0.012 * math.sin(2 * math.pi * days / 4.3 + phase_b)
        + noise
    )
    return max(-MAX_DRIFT, min(MAX_DRIFT, value))


def drifted_price(base_price, product_id, when):
    """``base_price`` with drift applied, rounded to cents and kept within ±MAX_DRIFT."""
    price = round(base_price * (1 + drift_factor(product_id, when)), 2)
    low = math.ceil(base_price * (1 - MAX_DRIFT) * 100) / 100
    high = math.floor(base_price * (1 + MAX_DRIFT) * 100) / 100
    return min(max(price, low), high)


def _with_drift(product, when):
    return {
        **product,
        "variants": [
            {
                **variant,
                "price": drifted_price(
                    variant["price"], f"{product['id']}|{variant['condition']}", when
                ),
            }
            for variant in product["variants"]
        ],
    }


def _json_response(url, body):
    response = requests.Response()
    response.status_code = 200
    response.url = url
    response.encoding = "utf-8"
    response.headers["Content-Type"] = "application/json"
    response._content = json.dumps(body).encode("utf-8")
    return response


class ReplayFetcher:
    """``Fetcher``-compatible client over the bundled demo catalogues.

    ``clock`` (a zero-argument callable returning an aware datetime) sets the
    moment prices are drifted for; seed-history generation passes simulated
    past timestamps, live demo requests use the real current time.
    """

    def __init__(self, clock=None):
        self.clock = clock or timezone.now

    def wait(self):
        """No pacing: nothing leaves the process."""

    def request(self, method, url, json=None, headers=None, profiled=False):
        parts = urlsplit(url)
        if parts.scheme != dataset.DEMO_URL_SCHEME:
            raise DemoEgressError(f"Demo mode only serves demo:// URLs, refused {url!r}")
        source = dataset.sources_by_key().get(parts.netloc)
        if source is None:
            raise DemoEgressError(f"No demo source {parts.netloc!r} for {url!r}")
        renderer = RENDERERS.get(source["parser_key"])
        if renderer is None:
            raise DemoEgressError(f"No demo renderer for parser {source['parser_key']!r}")

        term = parse_qs(parts.query).get("q", [""])[0]
        when = self.clock()
        products = [_with_drift(p, when) for p in search_catalogue(source["key"], term)]
        return _json_response(url, renderer(products))

    def get(self, url, headers=None, profiled=False):
        return self.request("GET", url, headers=headers, profiled=profiled)

    def post(self, url, json=None, headers=None, profiled=False):
        return self.request("POST", url, json=json, headers=headers, profiled=profiled)
