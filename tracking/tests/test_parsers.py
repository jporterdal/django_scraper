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
        self.assertEqual(set(row.keys()), {"title", "price", "category", "product_line", "instock"})
        self.assertIsInstance(row["price"], float)

    def test_add_result_coerces_types(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser()
        parser.add_result(title=1, price="3.5", instock=True)

        self.assertEqual(
            parser.results[0],
            {"title": "1", "price": 3.5, "instock": 1, "category": "", "product_line": ""},
        )

    def test_cc_parser_has_parse_response(self):
        from tracking.parsers import CCSearchParser

        fixtures = settings.BASE_DIR / "tracking" / "fixtures" / "html" / "cc"
        html = (fixtures / "search_results_minimal.html").read_text()
        parser = CCSearchParser(term="RTX 5070")
        parser.parse_response(MagicMock(text=html))

        self.assertEqual(len(parser.results), 2)

class JSONSearchParserRelevanceTests(SimpleTestCase):
    """search-term-relevance-filter — JSONSearchParser.add_result rejects rows whose
    title doesn't contain the search term as a contiguous phrase."""

    def test_full_phrase_match_is_included(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(term="Fire Dragon")
        parser.add_result(title="Fire Dragon (POR)", price=1, instock=True)

        self.assertEqual(len(parser.results), 1)

    def test_single_word_match_is_excluded(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(term="Lightning Bolt")
        parser.add_result(title="Lightning Greaves (Foil)", price=1, instock=True)

        self.assertEqual(parser.results, [])

    def test_zero_word_match_is_excluded(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(term="Lightning Bolt")
        parser.add_result(title="Counterspell (Masters 25)", price=1, instock=True)

        self.assertEqual(parser.results, [])

    def test_reordered_words_are_excluded(self):
        """Fire Dragon / Dragon Fire — a different card (Lorcana), words present
        but not contiguous, confirmed via a live wt vendor search."""
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(term="Fire Dragon")
        parser.add_result(title="Dragon Fire (0130)", price=1, instock=True)

        self.assertEqual(parser.results, [])

    def test_matching_is_case_insensitive(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(term="lightning bolt")
        parser.add_result(title="LIGHTNING BOLT (Revised Edition)", price=1, instock=True)

        self.assertEqual(len(parser.results), 1)

    def test_incidental_whitespace_in_term_is_tolerated(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(term="The Unbeatable Squirrel Girl ")
        parser.add_result(title="The Unbeatable Squirrel Girl (MSH) - Foil", price=1, instock=True)

        self.assertEqual(len(parser.results), 1)

    def test_ascii_term_matches_accented_title(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(term="Kili the Resourceful")
        parser.add_result(title="Kíli the Resourceful", price=1, instock=True)

        self.assertEqual(len(parser.results), 1)

    def test_accented_term_matches_ascii_title(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(term="Kíli the Resourceful")
        parser.add_result(title="Kili the Resourceful", price=1, instock=True)

        self.assertEqual(len(parser.results), 1)

    def test_non_decomposing_special_character_is_not_folded(self):
        """ß does not decompose into a base letter + combining mark under NFKD,
        so it is intentionally out of scope for diacritic folding."""
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(term="Straße")
        parser.add_result(title="Strasse", price=1, instock=True)

        self.assertEqual(parser.results, [])

    def test_blank_term_disables_the_check(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(term="")
        parser.add_result(title="Anything At All", price=1, instock=True)

        self.assertEqual(len(parser.results), 1)


class JSONSearchParserProductLineCategoryTests(SimpleTestCase):
    """item-category-relevance-filter — expected_product_line/expected_category
    checks on JSONSearchParser.add_result, independent of term-relevance.

    Both fields are list-valued; a row passes a field's check if *any* listed
    value matches (OR within the field)."""

    def test_product_line_match_is_included(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(expected_product_line=["Magic"])
        parser.add_result(
            title="Energy Retrieval", price=1, instock=True,
            product_line="Magic the Gathering Singles",
        )
        self.assertEqual(len(parser.results), 1)

    def test_product_line_matches_second_listed_value(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(expected_product_line=["Magic", "MTG"])
        parser.add_result(
            title="Energy Retrieval", price=1, instock=True,
            product_line="MTG Singles",
        )
        self.assertEqual(len(parser.results), 1)

    def test_product_line_mismatch_is_excluded(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(expected_product_line=["Magic"])
        parser.add_result(
            title="Energy Retrieval", price=1, instock=True,
            product_line="Pokémon Trading Card Game",
        )
        self.assertEqual(parser.results, [])

    def test_product_line_matching_none_of_several_values_is_excluded(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(expected_product_line=["Magic", "MTG"])
        parser.add_result(
            title="Energy Retrieval", price=1, instock=True,
            product_line="Pokémon Trading Card Game",
        )
        self.assertEqual(parser.results, [])

    def test_empty_expected_product_line_list_disables_the_check(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(expected_product_line=[])
        parser.add_result(
            title="Energy Retrieval", price=1, instock=True,
            product_line="Pokémon Trading Card Game",
        )
        self.assertEqual(len(parser.results), 1)

    def test_product_line_matching_is_case_insensitive_and_whitespace_tolerant(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(expected_product_line=[" magic "])
        parser.add_result(
            title="Lightning Bolt", price=1, instock=True,
            product_line="MAGIC: THE GATHERING",
        )
        self.assertEqual(len(parser.results), 1)

    def test_product_line_with_regex_metacharacters_matches_literally(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(expected_product_line=["Magic (Core Set)"])
        parser.add_result(
            title="Lightning Bolt", price=1, instock=True,
            product_line="Magic (Core Set) Singles",
        )
        self.assertEqual(len(parser.results), 1)

    def test_category_match_is_included(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(expected_category=["Strixhaven"])
        parser.add_result(
            title="Lightning Bolt", price=1, instock=True,
            category="Strixhaven - Mystical Archive",
        )
        self.assertEqual(len(parser.results), 1)

    def test_category_matches_second_listed_value(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(expected_category=["Strixhaven", "Kaldheim"])
        parser.add_result(
            title="Lightning Bolt", price=1, instock=True, category="Kaldheim",
        )
        self.assertEqual(len(parser.results), 1)

    def test_category_mismatch_is_excluded(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(expected_category=["Strixhaven"])
        parser.add_result(
            title="Lightning Bolt", price=1, instock=True, category="Kaldheim",
        )
        self.assertEqual(parser.results, [])

    def test_empty_expected_category_list_disables_the_check(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(expected_category=[])
        parser.add_result(
            title="Lightning Bolt", price=1, instock=True, category="Kaldheim",
        )
        self.assertEqual(len(parser.results), 1)

    def test_category_matching_is_case_insensitive_and_whitespace_tolerant(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(expected_category=[" strixhaven "])
        parser.add_result(
            title="Lightning Bolt", price=1, instock=True,
            category="STRIXHAVEN - MYSTICAL ARCHIVE",
        )
        self.assertEqual(len(parser.results), 1)

    def test_category_with_regex_metacharacters_matches_literally(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(expected_category=["30th Anniversary (Retro)"])
        parser.add_result(
            title="Lightning Bolt", price=1, instock=True,
            category="30th Anniversary (Retro) Frame",
        )
        self.assertEqual(len(parser.results), 1)

    def test_both_fields_set_pass_both_is_included(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(
            expected_product_line=["Magic"], expected_category=["Strixhaven"],
        )
        parser.add_result(
            title="Lightning Bolt", price=1, instock=True,
            product_line="Magic the Gathering Singles",
            category="Strixhaven - Mystical Archive",
        )
        self.assertEqual(len(parser.results), 1)

    def test_both_fields_set_pass_via_non_first_list_entry(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(
            expected_product_line=["Magic", "MTG"], expected_category=["Strixhaven"],
        )
        parser.add_result(
            title="Lightning Bolt", price=1, instock=True,
            product_line="MTG Singles",
            category="Strixhaven - Mystical Archive",
        )
        self.assertEqual(len(parser.results), 1)

    def test_both_fields_set_pass_one_fail_other_is_excluded(self):
        from tracking.parsers import JSONSearchParser

        parser = JSONSearchParser(
            expected_product_line=["Magic"], expected_category=["Strixhaven"],
        )
        parser.add_result(
            title="Lightning Bolt", price=1, instock=True,
            product_line="Magic the Gathering Singles",
            category="Kaldheim",
        )
        self.assertEqual(parser.results, [])


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

class ShopifyParserRelevanceTests(SimpleTestCase):
    """search-term-relevance-filter — synthetic reordered-title case for the f2f
    payload shape (illustrative; not live-smoke-tested the way wt was)."""

    def test_reordered_title_variant_is_excluded(self):
        from tracking.parsers import ShopifyParser

        data = {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "title": "Fire Dragon",
                            "MTG_Set_Name": "Portal",
                            "variants": [
                                {
                                    "price": 9.0,
                                    "inventoryQuantity": 3,
                                    "selectedOptions": [{"name": "Condition", "value": "NM"}],
                                },
                            ],
                        },
                    },
                    {
                        "_source": {
                            "title": "Dragon Fire",
                            "MTG_Set_Name": "Adventures in the Forgotten Realms",
                            "variants": [
                                {
                                    "price": 1.0,
                                    "inventoryQuantity": 5,
                                    "selectedOptions": [{"name": "Condition", "value": "NM"}],
                                },
                            ],
                        },
                    },
                ]
            }
        }
        parser = ShopifyParser(term="Fire Dragon")
        parser.parse_data(data)

        self.assertEqual(len(parser.results), 1)
        self.assertTrue(parser.results[0]["title"].startswith("Fire Dragon"))


class ShopifyParserProductLineCategoryTests(SimpleTestCase):
    """item-category-relevance-filter — real f2f-shaped payload with a
    same-titled off-product-line row and a same-product-line off-category row."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        fixtures = settings.BASE_DIR / "tracking" / "fixtures" / "html" / "f2f"
        cls.data = json.loads(
            (fixtures / "search_results_product_line_mismatch.json").read_text()
        )

    def _parse(self, **kwargs):
        from tracking.parsers import ShopifyParser

        parser = ShopifyParser(term="Lightning Bolt", **kwargs)
        parser.parse_data(self.data)
        return parser

    def test_off_product_line_row_is_excluded(self):
        parser = self._parse(expected_product_line=["Magic"])
        self.assertEqual(len(parser.results), 2)
        self.assertTrue(all("Lorcana" not in r["product_line"] for r in parser.results))

    def test_off_category_row_is_excluded_independent_of_product_line(self):
        parser = self._parse(expected_category=["Strixhaven"])
        self.assertEqual(len(parser.results), 1)
        self.assertEqual(parser.results[0]["category"], "Strixhaven: Mystical Archive")

    def test_matching_rows_are_retained(self):
        parser = self._parse(
            expected_product_line=["Magic"], expected_category=["Strixhaven"]
        )
        self.assertEqual(len(parser.results), 1)
        self.assertIn("Magic", parser.results[0]["product_line"])


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


class StorepassParserRelevanceTests(SimpleTestCase):
    """search-term-relevance-filter — synthetic reordered-title case for the hfx
    payload shape (illustrative; not live-smoke-tested the way wt was)."""

    def test_reordered_title_product_is_excluded(self):
        from tracking.parsers import StorepassParser

        data = {
            "products": [
                {
                    "display_name": "Fire Dragon [Portal]",
                    "name": "Fire Dragon Portal",
                    "productLineData": {"set": "Portal"},
                    "variantInfo": [
                        {"price": 9.0, "inventory_quantity": 2, "title": "Near Mint"},
                    ],
                },
                {
                    "display_name": "Dragon Fire [Forgotten Realms]",
                    "name": "Dragon Fire Forgotten Realms",
                    "productLineData": {"set": "Forgotten Realms"},
                    "variantInfo": [
                        {"price": 1.0, "inventory_quantity": 5, "title": "Near Mint"},
                    ],
                },
            ]
        }
        parser = StorepassParser(term="Fire Dragon")
        parser.parse_data(data)

        self.assertEqual(len(parser.results), 1)
        self.assertTrue(parser.results[0]["title"].startswith("Fire Dragon"))


class StorepassParserProductLineCategoryTests(SimpleTestCase):
    """item-category-relevance-filter — real hfx-shaped payload with a
    same-titled off-product-line row and a same-product-line off-category row."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        fixtures = settings.BASE_DIR / "tracking" / "fixtures" / "html" / "hfx"
        cls.data = json.loads(
            (fixtures / "search_results_product_line_mismatch.json").read_text()
        )

    def _parse(self, **kwargs):
        from tracking.parsers import StorepassParser

        parser = StorepassParser(term="Lightning Bolt", **kwargs)
        parser.parse_data(self.data)
        return parser

    def test_off_product_line_row_is_excluded(self):
        parser = self._parse(expected_product_line=["Magic"])
        self.assertEqual(len(parser.results), 2)
        self.assertTrue(all("Lorcana" not in r["product_line"] for r in parser.results))

    def test_off_category_row_is_excluded_independent_of_product_line(self):
        parser = self._parse(expected_category=["Strixhaven"])
        self.assertEqual(len(parser.results), 1)
        self.assertEqual(parser.results[0]["category"], "Strixhaven: Mystical Archive")

    def test_matching_rows_are_retained(self):
        parser = self._parse(
            expected_product_line=["Magic"], expected_category=["Strixhaven"]
        )
        self.assertEqual(len(parser.results), 1)
        self.assertIn("Magic", parser.results[0]["product_line"])
