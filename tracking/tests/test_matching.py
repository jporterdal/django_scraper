from django.test import SimpleTestCase, TestCase

from tracking.models import ItemSource
from tracking.tests.factories import make_cc_source, make_item


class TitleMatchesRulesTests(SimpleTestCase):
    def test_title_matches_rules_stub(self):
        from tracking.matching import title_matches_rules

        title = "MSI RTX 5070 Gaming X Trio"

        self.assertTrue(title_matches_rules(title, [], []))
        self.assertTrue(title_matches_rules(title, ["MSI.*5070"], []))
        self.assertFalse(title_matches_rules(title, ["ASUS.*5070"], []))
        self.assertFalse(title_matches_rules(title, [], ["MSI.*"]))
        self.assertTrue(title_matches_rules(title, ["MSI.*5070"], ["Gigabyte.*"]))


class TermMatchesTests(SimpleTestCase):
    def test_blank_term_always_matches(self):
        from tracking.matching import term_matches

        self.assertTrue(term_matches("Lightning Bolt (NM)", ""))

    def test_contiguous_phrase_matches(self):
        from tracking.matching import term_matches

        self.assertTrue(term_matches("Lightning Bolt (NM)", "Lightning Bolt"))

    def test_non_contiguous_words_do_not_match(self):
        from tracking.matching import term_matches

        self.assertFalse(term_matches("Bolt of Lightning (NM)", "Lightning Bolt"))

    def test_diacritic_folded_and_case_insensitive(self):
        from tracking.matching import term_matches

        self.assertTrue(term_matches("Café Racer", "cafe racer"))


class ValueMatchesAnyTests(SimpleTestCase):
    def test_empty_expected_values_always_matches(self):
        from tracking.matching import value_matches_any

        self.assertTrue(value_matches_any("Magic", []))

    def test_substring_match(self):
        from tracking.matching import value_matches_any

        self.assertTrue(value_matches_any("Magic the Gathering", ["Magic"]))

    def test_no_matching_expected_value(self):
        from tracking.matching import value_matches_any

        self.assertFalse(value_matches_any("Disney Lorcana", ["Magic"]))

    def test_regex_metacharacters_in_expected_value_are_literal(self):
        from tracking.matching import value_matches_any

        self.assertFalse(value_matches_any("Magic the Gathering", ["Magic+"]))
        self.assertTrue(value_matches_any("Magic+ Edition", ["Magic+"]))


class ResultMatchesItemSourceAggregateTests(TestCase):
    """result_matches_item_source() — the read-time aggregate of all four checks."""

    @classmethod
    def setUpTestData(cls):
        cls.source = make_cc_source(name="Test Source")

    def _item_source(self, item_text="lightning bolt", expected_product_line=None,
                      expected_category=None, include=None, exclude=None):
        item = make_item(
            text=item_text,
            expected_product_line=expected_product_line or [],
            expected_category=expected_category or [],
        )
        return ItemSource(
            item=item,
            source=self.source,
            title_include_patterns=include or [],
            title_exclude_patterns=exclude or [],
        )

    def test_all_checks_pass(self):
        from tracking.matching import result_matches_item_source

        item_source = self._item_source(
            expected_product_line=[{"value": "Magic", "source": None}],
            expected_category=[{"value": "Strixhaven", "source": None}],
            include=[r"\(NM\)"],
        )
        self.assertTrue(
            result_matches_item_source(
                "Lightning Bolt (NM)", "Strixhaven - Mystical Archive", "Magic", item_source
            )
        )

    def test_off_term_fails(self):
        from tracking.matching import result_matches_item_source

        item_source = self._item_source(item_text="lightning bolt")
        self.assertFalse(
            result_matches_item_source("Giant Growth (NM)", "Cards", "Magic", item_source)
        )

    def test_off_product_line_fails(self):
        from tracking.matching import result_matches_item_source

        item_source = self._item_source(
            expected_product_line=[{"value": "Magic", "source": None}]
        )
        self.assertFalse(
            result_matches_item_source(
                "Lightning Bolt (NM)", "Cards", "Disney Lorcana", item_source
            )
        )

    def test_off_category_fails(self):
        from tracking.matching import result_matches_item_source

        item_source = self._item_source(
            expected_category=[{"value": "Strixhaven", "source": None}]
        )
        self.assertFalse(
            result_matches_item_source(
                "Lightning Bolt (NM)", "Modern Masters 2015", "Magic", item_source
            )
        )

    def test_pattern_excluded_fails(self):
        from tracking.matching import result_matches_item_source

        item_source = self._item_source(exclude=["Foil"])
        self.assertFalse(
            result_matches_item_source(
                "Lightning Bolt Foil (NM)", "Cards", "Magic", item_source
            )
        )
