from django.test import SimpleTestCase, TestCase
from django.conf import settings
import json
from unittest.mock import MagicMock, patch

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

class CCSearchParserPatternTests(SimpleTestCase):
    def test_cc_parser_no_gpu_patterns(self):
        from tracking.parsers import CCSearchParser

        term = "RTX 5070"
        parser = CCSearchParser(term=term)
        parser._init_vars()
        self.assertEqual(parser.title_patterns, [term.lower() + "$"])

class ParserContractTests(SimpleTestCase):
    """Phase 2 Step 2 — uniform parse_response contract for JSON and HTML parsers."""

    def test_json_parser_parse_response_populates_results(self):
        from tracking.parsers import JSONSearchParser

        class TinyParser(JSONSearchParser):
            def parse_data(self, data):
                for entry in data["items"]:
                    self.add_result(
                        title=entry["name"],
                        price=entry["cost"],
                        instock=entry["available"],
                        category=entry["set"],
                    )

        response = MagicMock(json=lambda: {
            "items": [
                {"name": "Widget", "cost": "5.5", "available": True, "set": "Alpha"},
            ]
        })
        parser = TinyParser(term="widget")
        parser.parse_response(response)

        self.assertEqual(len(parser.results), 1)
        row = parser.results[0]
        self.assertEqual(set(row.keys()), {"title", "price", "category", "instock"})
        self.assertIsInstance(row["price"], float)

    def test_add_result_coerces_types(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser()
        parser.add_result(title=1, price="3.5", instock=True)

        self.assertEqual(
            parser.results[0],
            {"title": "1", "price": 3.5, "instock": 1, "category": ""},
        )

    def test_cc_parser_has_parse_response(self):
        from tracking.parsers import CCSearchParser

        fixtures = settings.BASE_DIR / "tracking" / "fixtures" / "html" / "cc"
        html = (fixtures / "search_results_minimal.html").read_text()
        parser = CCSearchParser(term="RTX 5070")
        parser.parse_response(MagicMock(text=html))

        self.assertEqual(len(parser.results), 2)

class ShopifyParserFixtureTests(SimpleTestCase):
    """Phase 2 Step 3 — ShopifyParser (F2F prod-indexer) against the real fixture."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        fixtures = settings.BASE_DIR / "tracking" / "fixtures" / "html" / "f2f"
        cls.data = json.loads((fixtures / "search_results_sample.json").read_text())

    def _parse(self):
        from tracking.parsers import ShopifyParser

        parser = ShopifyParser(term="Lightning Bolt")
        parser.parse_data(self.data)
        return parser

    def test_parses_at_least_one_variant_row(self):
        parser = self._parse()
        self.assertGreaterEqual(len(parser.results), 1)

    def test_row_shape(self):
        parser = self._parse()
        row = parser.results[0]
        self.assertIsInstance(row["price"], float)
        self.assertTrue(row["title"])
        self.assertIn("Lightning Bolt", row["title"])
        self.assertIn(row["instock"], (0, 1))

    def test_condition_in_title(self):
        parser = self._parse()
        self.assertTrue(
            any("(NM)" in row["title"] for row in parser.results),
            "expected at least one row title with a condition tag like (NM)",
        )

    def test_instock_derived_from_inventory(self):
        parser = self._parse()
        instock_rows = [row for row in parser.results if row["instock"] == 1]
        self.assertTrue(
            instock_rows,
            "expected at least one in-stock row from a variant with inventory > 0",
        )

class StorepassParserFixtureTests(SimpleTestCase):
    """Phase 2 Step 4 — StorepassParser (HFX Storepass) against the real fixture."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        fixtures = settings.BASE_DIR / "tracking" / "fixtures" / "html" / "hfx"
        cls.data = json.loads((fixtures / "search_results_sample.json").read_text())

    def _parse(self):
        from tracking.parsers import StorepassParser

        parser = StorepassParser(term="Lightning Bolt")
        parser.parse_data(self.data)
        return parser

    def test_parses_at_least_one_variant_row(self):
        parser = self._parse()
        self.assertGreaterEqual(len(parser.results), 1)

    def test_price_is_float(self):
        parser = self._parse()
        self.assertTrue(parser.results)
        for row in parser.results:
            self.assertIsInstance(row["price"], float)

    def test_condition_in_title(self):
        parser = self._parse()
        self.assertTrue(
            any("(Near Mint)" in row["title"] for row in parser.results),
            "expected at least one row title with a condition tag like (Near Mint)",
        )

    def test_out_of_stock_variant(self):
        parser = self._parse()
        oos_rows = [row for row in parser.results if row["instock"] == 0]
        self.assertTrue(
            oos_rows,
            "expected at least one out-of-stock row from a variant with inventory_quantity == 0",
        )
