"""demo-replay-data: local catalogues, renderers, drift, replay fetcher, egress (tasks 2.1-2.6, 2.9, 2.10)."""

import json
import socket
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from tracking import parsers
from tracking.demo import dataset, replay
from tracking.demo.egress import (
    DemoEgressBlocked,
    egress_guard_installed,
    install_egress_guard,
    uninstall_egress_guard,
)
from tracking.demo.renderers import RENDERERS
from tracking.demo.replay import DemoEgressError, ReplayFetcher, drifted_price
from tracking.fetcher import Fetcher
from tracking.models import FetchJob, ItemMetadata, SearchResult, WebUpdate

from .demo_base import DemoTestCase


class DatasetLoaderTests(SimpleTestCase):
    def test_every_file_loads(self):
        self.assertEqual(len(dataset.sources()), 3)
        for source in dataset.sources():
            self.assertIn(source["parser_key"], RENDERERS)
            self.assertIn(source["parser_key"], parsers.sources)
            self.assertTrue(dataset.catalogue(source["key"]))
        seed = dataset.seed()
        self.assertGreaterEqual(len(seed["items"]), 4)
        self.assertLessEqual(len(seed["items"]), 5)
        self.assertTrue(dataset.metadata_entries())
        self.assertTrue(dataset.preset_patterns())

    def test_every_seed_item_has_two_sources_and_some_use_presets(self):
        items = dataset.seed()["items"]
        for item in items:
            self.assertGreaterEqual(len(item["sources"]), 2, item["text"])
        self.assertTrue(
            any(s["include"] or s["exclude"] for item in items for s in item["sources"])
        )

    def test_malformed_manifest_fails_loudly(self):
        dataset.seed.cache_clear()
        try:
            with patch.object(dataset, "_load", return_value={"user": {}, "tags": []}):
                with self.assertRaises(dataset.DemoDataError):
                    dataset.seed()
        finally:
            dataset.seed.cache_clear()

    def test_non_preset_seed_pattern_fails(self):
        real = dataset._load("seed.json")
        bad = json.loads(json.dumps(real))
        bad["items"][0]["sources"][0]["exclude"] = ["(a+)+$"]
        dataset.seed.cache_clear()
        try:
            with patch.object(dataset, "_load", side_effect=lambda p: bad if p == "seed.json" else real):
                with self.assertRaises(dataset.DemoDataError):
                    dataset.seed()
        finally:
            dataset.seed.cache_clear()

    def test_every_thumbnail_resolves_as_a_static_file(self):
        for entry in dataset.metadata_entries():
            self.assertIsNotNone(finders.find(entry["thumbnail"]), entry["thumbnail"])

    def test_preset_patterns_compile(self):
        import re

        for pattern, _label in dataset.preset_patterns():
            re.compile(pattern)


def _flatten(products):
    """Expected parser rows for neutral products (shopify/storepass title suffixing)."""
    rows = []
    for product in products:
        for variant in product["variants"]:
            title = product["title"]
            if variant["condition"]:
                title = f"{title} ({variant['condition']})"
            rows.append((title, float(variant["price"]), 1 if variant["instock"] else 0,
                         product["category"], product["product_line"]))
    return rows


class RendererRoundTripTests(SimpleTestCase):
    def _parse(self, parser_key, body):
        parser = parsers.sources[parser_key](term="")
        parser.parse_response(SimpleNamespace(json=lambda: body))
        return [
            (r["title"], r["price"], r["instock"], r["category"], r["product_line"])
            for r in parser.results
        ]

    def test_each_renderer_round_trips_through_its_real_parser(self):
        for source in dataset.sources():
            products = list(dataset.catalogue(source["key"]))
            parsed = self._parse(source["parser_key"], RENDERERS[source["parser_key"]](products))
            if source["parser_key"] == "wtfilters":
                expected = [
                    (p["title"], float(p["variants"][0]["price"]),
                     1 if p["variants"][0]["instock"] else 0, p["category"], p["product_line"])
                    for p in products
                ]
            else:
                expected = _flatten(products)
            self.assertEqual(parsed, expected, source["key"])


class PriceDriftTests(SimpleTestCase):
    def test_drift_stays_within_five_percent(self):
        start = timezone.now()
        for base in (0.29, 0.35, 1.10, 4.50, 24.99, 61.50):
            for step in range(0, 60 * 24 * 60, 37):
                price = drifted_price(base, "product-x", start + timedelta(minutes=step))
                self.assertGreaterEqual(price, round(base * 0.95, 2) - 0.0001)
                self.assertLessEqual(price, base * 1.05 + 0.0001)

    def test_runs_a_minute_apart_get_different_prices(self):
        when = timezone.now()
        a = [drifted_price(24.99, f"p{i}", when) for i in range(10)]
        b = [drifted_price(24.99, f"p{i}", when + timedelta(minutes=1)) for i in range(10)]
        self.assertNotEqual(a, b)

    def test_deterministic_for_the_same_moment(self):
        when = timezone.now()
        self.assertEqual(drifted_price(9.5, "p", when), drifted_price(9.5, "p", when))


class ReplayFetcherTests(SimpleTestCase):
    def test_matching_term_returns_parser_shaped_json(self):
        response = ReplayFetcher().get("demo://dragonhoard/search?q=Emberwake+Phoenix")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "application/json")
        titles = [hit["_source"]["title"] for hit in response.json()["hits"]["hits"]]
        self.assertIn("Emberwake Phoenix", titles)
        # Loose vendor-style search: near-misses come back too.
        self.assertIn("Phoenix of the Emberwake", titles)

    def test_no_match_term_returns_empty_parser_shaped_body(self):
        response = ReplayFetcher().get("demo://tidewater/search?q=Nothing+Like+It")
        self.assertEqual(response.json(), {"data": {"total": 0, "results": []}})

    def test_non_demo_url_raises(self):
        for url in (
            "https://api.scryfall.com/cards/search?q=x",
            "http://127.0.0.1/",
            "demo://unknown-source/search?q=x",
        ):
            with self.assertRaises(DemoEgressError, msg=url):
                ReplayFetcher().get(url)

    def test_post_is_served_the_same_way(self):
        response = ReplayFetcher().post("demo://manavault/search?q=Gloomtide", json={"x": 1})
        self.assertTrue(response.json()["products"])


class FetcherWiringTests(SimpleTestCase):
    @override_settings(DEMO_MODE=True)
    def test_from_settings_returns_replay_fetcher_in_demo_mode(self):
        self.assertIsInstance(Fetcher.from_settings(), ReplayFetcher)

    @override_settings(DEMO_MODE=True)
    def test_constructing_real_fetcher_fails_in_demo_mode(self):
        with self.assertRaises(RuntimeError):
            Fetcher()

    def test_real_fetcher_unchanged_outside_demo_mode(self):
        self.assertIsInstance(Fetcher.from_settings(), Fetcher)


class EgressGuardTests(SimpleTestCase):
    def tearDown(self):
        uninstall_egress_guard()
        super().tearDown()

    def test_non_loopback_connect_blocked(self):
        install_egress_guard()
        self.assertTrue(egress_guard_installed())
        with self.assertRaises(DemoEgressBlocked):
            socket.create_connection(("192.0.2.1", 443), timeout=1)

    def test_loopback_allowed(self):
        install_egress_guard()
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        try:
            client = socket.create_connection(server.getsockname(), timeout=1)
            client.close()
        finally:
            server.close()

    def test_uninstalled_guard_does_not_interfere(self):
        install_egress_guard()
        uninstall_egress_guard()
        self.assertFalse(egress_guard_installed())
        self.assertNotEqual(socket.socket.connect.__name__, "_guarded_connect")


class NoEgressEndToEndTests(DemoTestCase):
    """A full demo update plus every metadata action, with sockets made to fail."""

    def test_update_and_metadata_actions_make_no_connections(self):
        attempts = []

        def refuse(sock, address):
            attempts.append(address)
            raise OSError("network disabled in this test")

        visitor = self.visitor_item(text="Vexing Starfall", sources=("dragonhoard", "tidewater"))
        visitor.metadata_provider_key = "demo"
        visitor.save()
        unknown = self.visitor_item(text="Nothing Like It", sources=("manavault",))

        with patch.object(socket.socket, "connect", refuse), \
                patch.object(socket.socket, "connect_ex", refuse):
            before = SearchResult.objects.count()
            response = self.client.post("/update/", {"mode": "all"})
            self.assertEqual(response.status_code, 302)
            run = WebUpdate.objects.latest("pk")
            self.assertEqual(run.status, WebUpdate.Status.DONE)
            self.assertGreater(SearchResult.objects.count(), before)
            statuses = set(FetchJob.objects.filter(webupdate=run).values_list("status", flat=True))
            self.assertEqual(statuses, {FetchJob.Status.SUCCESS, FetchJob.Status.EMPTY})
            self.assertEqual(
                FetchJob.objects.get(webupdate=run, item=unknown).status, FetchJob.Status.EMPTY
            )

            self.client.post(f"/item/{visitor.pk}/metadata/retry/")
            metadata = ItemMetadata.objects.get(item=visitor)
            self.assertEqual(metadata.status, ItemMetadata.Status.MATCHED)
            self.client.post(
                f"/item/{visitor.pk}/metadata/set_external_id/",
                {"external_id": "demo-hollowbrook-seer"},
            )
            metadata.refresh_from_db()
            self.assertEqual(metadata.external_id, "demo-hollowbrook-seer")

        self.assertEqual(attempts, [])


class LooseSearchShowsFilteringTests(TestCase):
    def test_catalogue_search_is_word_based(self):
        found = {p["title"] for p in replay.search_catalogue("dragonhoard", "Emberwake Phoenix")}
        self.assertIn("Emberwake Phoenix Playmat", found)
        self.assertEqual(replay.search_catalogue("dragonhoard", "of the"), [])
