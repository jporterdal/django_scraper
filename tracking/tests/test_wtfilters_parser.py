"""Phase 4 Step 2 — WtFiltersParser against the saved POST-JSON fixture."""

import json
from unittest.mock import MagicMock

from django.conf import settings
from django.test import SimpleTestCase

from tracking.parsers import WtFiltersParser, sources


def _json_response(payload, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


class WtFiltersParserTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        fixture_path = (
            settings.BASE_DIR
            / "tracking"
            / "fixtures"
            / "html"
            / "wt"
            / "search_results_sample.json"
        )
        cls.fixture = json.loads(fixture_path.read_text())

    def _parse(self):
        parser = WtFiltersParser(term="Lightning Bolt")
        parser.parse_response(_json_response(self.fixture))
        return parser

    def test_registered_in_sources(self):
        self.assertIs(sources["wtfilters"], WtFiltersParser)

    def test_parses_at_least_one_result(self):
        parser = self._parse()
        self.assertGreaterEqual(len(parser.results), 1)

    def test_result_contract_shape(self):
        parser = self._parse()
        for row in parser.results:
            self.assertEqual(set(row.keys()), {"title", "price", "instock", "category"})
            self.assertIsInstance(row["title"], str)
            self.assertIsInstance(row["price"], float)
            self.assertIsInstance(row["category"], str)
            self.assertIn(row["instock"], (0, 1))

    def test_first_fixture_row_fields(self):
        parser = self._parse()
        first = parser.results[0]
        self.assertEqual(first["title"], "Lightning Bolt (042) (STA) - Foil")
        self.assertEqual(first["price"], 10.0)
        self.assertEqual(first["instock"], 1)
        self.assertEqual(first["category"], "Strixhaven - Mystical Archive")

    def test_out_of_stock_row(self):
        parser = self._parse()
        row = next(r for r in parser.results if r["title"] == "Lightning Bolt (MM2)")
        self.assertEqual(row["price"], 1.75)
        self.assertEqual(row["instock"], 0)
        self.assertEqual(row["category"], "Modern Masters 2015")
