"""item-category-relevance-filter — model, form, and value-discovery coverage.

Parser-level filtering tests live in test_parsers.py (base-class checks) and
the per-vendor fixture test files (test_wtfilters_parser.py, test_parsers.py's
ShopifyParser/StorepassParser fixture classes). This file covers the pieces
that sit above the parser: SearchableItem/SearchResult fields,
ObservedCategoryValue upserts, the value-discovery query helper, and the
category backfill data migration.
"""

import json
import re
from importlib import import_module

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracking.forms import SearchableItemForm, _suggestion_choice_value
from tracking.models import ObservedCategoryValue, SearchResult, observed_values_for_item
from tracking.parsers import JSONSearchParser, WtFiltersParser
from tracking.tests.base import AuthedClientTestCase
from tracking.tests.factories import (
    make_cc_source,
    make_item,
    make_item_source,
    make_source,
    make_web_update,
)


class SearchableItemExpectedFieldsTests(TestCase):
    def test_fields_empty_list_by_default(self):
        item = make_item()
        self.assertEqual(item.expected_product_line, [])
        self.assertEqual(item.expected_category, [])

    def test_fields_round_trip_independently(self):
        item = make_item()
        item.expected_product_line = [
            {"value": "Magic", "source": None},
            {"value": "MTG", "source": "wt"},
        ]
        item.save()
        item.refresh_from_db()
        self.assertEqual(
            item.expected_product_line,
            [{"value": "Magic", "source": None}, {"value": "MTG", "source": "wt"}],
        )
        self.assertEqual(item.expected_category, [])

        item.expected_category = [{"value": "Strixhaven", "source": None}]
        item.save()
        item.refresh_from_db()
        self.assertEqual(
            item.expected_product_line,
            [{"value": "Magic", "source": None}, {"value": "MTG", "source": "wt"}],
        )
        self.assertEqual(item.expected_category, [{"value": "Strixhaven", "source": None}])

    def test_expected_values_for_source_prunes_per_vendor(self):
        item = make_item()
        item.expected_product_line = [
            {"value": "MTG", "source": "wt"},
            {"value": "Magic", "source": None},
        ]
        item.save()
        self.assertEqual(
            item.expected_values_for_source("product_line", "wt"),
            ["MTG", "Magic"],
        )
        self.assertEqual(
            item.expected_values_for_source("product_line", "f2f"),
            ["Magic"],
        )


class SearchableItemFormExpectedFieldsTests(AuthedClientTestCase):
    def test_form_includes_suggestion_and_manual_fields(self):
        form = SearchableItemForm()
        self.assertIn("expected_product_line_suggestions", form.fields)
        self.assertIn("expected_product_line_manual", form.fields)
        self.assertIn("expected_category_suggestions", form.fields)
        self.assertIn("expected_category_manual", form.fields)

    def test_edit_view_stores_manually_entered_values(self):
        item = make_item()
        response = self.client.post(
            reverse("edit_term", args=[item.pk]),
            {
                "text": item.text,
                "priority": item.priority,
                "expected_product_line_suggestions": [],
                "expected_product_line_manual": "Magic",
                "expected_category_suggestions": [],
                "expected_category_manual": "Strixhaven",
                "tags": [],
            },
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.expected_product_line, [{"value": "Magic", "source": None}])
        self.assertEqual(item.expected_category, [{"value": "Strixhaven", "source": None}])

    def test_checking_suggestions_from_two_vendors_stores_two_independent_entries(self):
        """expected-value-vendor-provenance — task 5.1 (was: dedupes to one
        stored value; that collapsing behavior was the bug this change fixes)."""
        wt = make_source(key="testwt-dedupe", parser_key="wtfilters")
        f2f = make_source(key="testf2f-dedupe", parser_key="shopify")
        item = make_item()
        make_item_source(item, wt)
        make_item_source(item, f2f)
        now = timezone.now()
        ObservedCategoryValue.objects.create(
            source=wt, field_name="product_line", value="Magic: The Gathering", last_seen=now
        )
        ObservedCategoryValue.objects.create(
            source=f2f, field_name="product_line", value="Magic: The Gathering", last_seen=now
        )

        response = self.client.post(
            reverse("edit_term", args=[item.pk]),
            {
                "text": item.text,
                "priority": item.priority,
                "expected_product_line_suggestions": [
                    _suggestion_choice_value("testwt-dedupe", "Magic: The Gathering"),
                    _suggestion_choice_value("testf2f-dedupe", "Magic: The Gathering"),
                ],
                "expected_product_line_manual": "MTG",
                "expected_category_suggestions": [],
                "expected_category_manual": "",
                "tags": [],
            },
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(
            item.expected_product_line,
            [
                {"value": "Magic: The Gathering", "source": "testwt-dedupe"},
                {"value": "Magic: The Gathering", "source": "testf2f-dedupe"},
                {"value": "MTG", "source": None},
            ],
        )

    def test_stored_value_prechecks_only_its_own_vendor_checkbox(self):
        """expected-value-vendor-provenance — task 5.2 (was: prechecks all
        matching checkboxes; that shared pre-check was the bug this change fixes)."""
        wt = make_source(key="testwt-precheck", parser_key="wtfilters")
        f2f = make_source(key="testf2f-precheck", parser_key="shopify")
        item = make_item()
        make_item_source(item, wt)
        make_item_source(item, f2f)
        item.expected_product_line = [
            {"value": "Magic: The Gathering", "source": "testwt-precheck"}
        ]
        item.save()
        now = timezone.now()
        ObservedCategoryValue.objects.create(
            source=wt, field_name="product_line", value="Magic: The Gathering", last_seen=now
        )
        ObservedCategoryValue.objects.create(
            source=f2f, field_name="product_line", value="Magic: The Gathering", last_seen=now
        )

        response = self.client.get(reverse("edit_term", args=[item.pk]))
        body = response.content.decode()
        checkboxes = re.findall(
            r'<input[^>]*name="expected_product_line_suggestions"[^>]*>', body
        )
        self.assertEqual(len(checkboxes), 2)
        checked = [cb for cb in checkboxes if "checked" in cb]
        self.assertEqual(len(checked), 1)
        self.assertIn("testwt-precheck", checked[0])

    def test_unchecking_one_vendor_checkbox_removes_only_that_vendors_entry(self):
        """expected-value-vendor-provenance — task 5.3."""
        wt = make_source(key="testwt-uncheck", parser_key="wtfilters")
        f2f = make_source(key="testf2f-uncheck", parser_key="shopify")
        item = make_item()
        make_item_source(item, wt)
        make_item_source(item, f2f)
        item.expected_product_line = [
            {"value": "MTG", "source": "testwt-uncheck"},
            {"value": "MTG", "source": "testf2f-uncheck"},
        ]
        item.save()

        response = self.client.post(
            reverse("edit_term", args=[item.pk]),
            {
                "text": item.text,
                "priority": item.priority,
                "expected_product_line_suggestions": [
                    _suggestion_choice_value("testf2f-uncheck", "MTG"),
                ],
                "expected_product_line_manual": "",
                "expected_category_suggestions": [],
                "expected_category_manual": "",
                "tags": [],
            },
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(
            item.expected_product_line, [{"value": "MTG", "source": "testf2f-uncheck"}]
        )

    def test_manual_entry_never_reclassified_as_vendor_suggestion(self):
        """expected-value-vendor-provenance — task 5.4."""
        wt = make_source(key="testwt-manual", parser_key="wtfilters")
        item = make_item()
        make_item_source(item, wt)
        item.expected_product_line = [{"value": "MTG", "source": None}]
        item.save()
        now = timezone.now()
        ObservedCategoryValue.objects.create(
            source=wt, field_name="product_line", value="MTG", last_seen=now
        )

        form = SearchableItemForm(instance=item)
        checked = form.initial["expected_product_line_suggestions"]
        manual_choice = _suggestion_choice_value(None, "MTG")
        vendor_choice = _suggestion_choice_value("testwt-manual", "MTG")
        self.assertEqual(checked, [manual_choice])
        choice_values = [
            value for value, _ in form.fields["expected_product_line_suggestions"].choices
        ]
        self.assertIn(vendor_choice, choice_values)
        self.assertNotIn(vendor_choice, checked)

    def test_stale_vendor_entry_still_renders_and_is_removable(self):
        """expected-value-vendor-provenance — task 5.5."""
        wt = make_source(key="testwt-stale", parser_key="wtfilters")
        item = make_item()
        make_item_source(item, wt)
        item.expected_product_line = [{"value": "Some Old Value", "source": "testwt-stale"}]
        item.save()

        form = SearchableItemForm(instance=item)
        encoded = _suggestion_choice_value("testwt-stale", "Some Old Value")
        choice_values = [
            value for value, _ in form.fields["expected_product_line_suggestions"].choices
        ]
        self.assertIn(encoded, choice_values)
        self.assertEqual(form.initial["expected_product_line_suggestions"], [encoded])

        response = self.client.post(
            reverse("edit_term", args=[item.pk]),
            {
                "text": item.text,
                "priority": item.priority,
                "expected_product_line_suggestions": [],
                "expected_product_line_manual": "",
                "expected_category_suggestions": [],
                "expected_category_manual": "",
                "tags": [],
            },
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.expected_product_line, [])


class SearchResultProductLineTests(AuthedClientTestCase):
    """Product-line signal is persisted and displayed alongside category."""

    def test_product_line_populated_and_displayed(self):
        source = make_cc_source()
        item = make_item(text="Lightning Bolt")
        update = make_web_update()
        SearchResult.objects.create(
            title="Lightning Bolt",
            search_term=item.text,
            price=9.99,
            category="Strixhaven - Mystical Archive",
            product_line="Magic the Gathering Singles",
            item=item,
            instock=1,
            source=source,
            update=update,
        )
        response = self.client.get(reverse("item_detail", args=[item.pk]))
        self.assertContains(response, "Magic the Gathering Singles")

    def test_product_line_in_export(self):
        source = make_cc_source()
        item = make_item(text="Lightning Bolt")
        update = make_web_update()
        SearchResult.objects.create(
            title="Lightning Bolt",
            search_term=item.text,
            price=9.99,
            category="Strixhaven - Mystical Archive",
            product_line="Magic the Gathering Singles",
            item=item,
            instock=1,
            source=source,
            update=update,
        )
        response = self.client.get(reverse("export_item_json", args=[item.pk]))
        rows = json.loads(response.content)
        self.assertEqual(rows[0]["product_line"], "Magic the Gathering Singles")


def _json_response(payload):
    class _Response:
        def json(self_inner):
            return payload

    return _Response()


class ObservedCategoryValueUpsertTests(TestCase):
    """Every row's category/product-line signals are recorded, regardless of
    whether the row is ultimately accepted or rejected by filtering."""

    def setUp(self):
        self.source = make_source(key="testwt", parser_key="wtfilters")

    def test_accepted_row_is_recorded(self):
        parser = WtFiltersParser(term="Lightning Bolt", source=self.source)
        parser.parse_response(_json_response({
            "data": {"results": [{
                "title": "Lightning Bolt",
                "price": 1,
                "in_stock": True,
                "category": "Magic the Gathering Singles",
                "subcategory": "Strixhaven - Mystical Archive",
            }]}
        }))
        self.assertEqual(len(parser.results), 1)
        self.assertTrue(
            ObservedCategoryValue.objects.filter(
                source=self.source,
                field_name="product_line",
                value="Magic the Gathering Singles",
            ).exists()
        )
        self.assertTrue(
            ObservedCategoryValue.objects.filter(
                source=self.source,
                field_name="category",
                value="Strixhaven - Mystical Archive",
            ).exists()
        )

    def test_rejected_row_is_still_recorded(self):
        parser = WtFiltersParser(
            term="Lightning Bolt",
            expected_product_line=["Magic"],
            source=self.source,
        )
        parser.parse_response(_json_response({
            "data": {"results": [{
                "title": "Lightning Bolt",
                "price": 1,
                "in_stock": True,
                "category": "Disney Lorcana Singles",
                "subcategory": "Into the Inklands",
            }]}
        }))
        self.assertEqual(parser.results, [])
        self.assertTrue(
            ObservedCategoryValue.objects.filter(
                source=self.source,
                field_name="product_line",
                value="Disney Lorcana Singles",
            ).exists()
        )

    def test_repeat_observation_updates_last_seen_not_duplicate(self):
        parser = WtFiltersParser(term="Lightning Bolt", source=self.source)
        row = {
            "title": "Lightning Bolt",
            "price": 1,
            "in_stock": True,
            "category": "Magic the Gathering Singles",
            "subcategory": "Strixhaven - Mystical Archive",
        }
        parser.parse_response(_json_response({"data": {"results": [row]}}))
        first = ObservedCategoryValue.objects.get(
            source=self.source, field_name="product_line", value="Magic the Gathering Singles"
        )
        first_seen = first.last_seen

        parser.parse_response(_json_response({"data": {"results": [row]}}))
        self.assertEqual(
            ObservedCategoryValue.objects.filter(
                source=self.source, field_name="product_line", value="Magic the Gathering Singles"
            ).count(),
            1,
        )
        second = ObservedCategoryValue.objects.get(
            source=self.source, field_name="product_line", value="Magic the Gathering Singles"
        )
        self.assertGreaterEqual(second.last_seen, first_seen)


class ObservedValuesForItemTests(TestCase):
    def test_scoped_to_items_configured_sources(self):
        wt = make_source(key="testwt2", parser_key="wtfilters")
        other = make_source(key="testother", parser_key="wtfilters")
        item = make_item()
        make_item_source(item, wt)

        now = timezone.now()
        ObservedCategoryValue.objects.create(
            source=wt, field_name="product_line", value="Magic the Gathering Singles", last_seen=now
        )
        ObservedCategoryValue.objects.create(
            source=other, field_name="product_line", value="Unrelated Vendor Value", last_seen=now
        )

        values = [v for _, v in observed_values_for_item(item, "product_line")]
        self.assertIn("Magic the Gathering Singles", values)
        self.assertNotIn("Unrelated Vendor Value", values)

    def test_empty_when_no_observations(self):
        item = make_item()
        self.assertEqual(observed_values_for_item(item, "product_line"), [])


class BackfillObservedCategoryValuesMigrationTests(TestCase):
    """The 0019 data migration backfills ObservedCategoryValue(field_name="category")
    from distinct historical SearchResult.category values, grouped by source."""

    def test_backfill_creates_observed_values_from_search_results(self):
        source = make_cc_source()
        item = make_item()
        update = make_web_update()
        SearchResult.objects.create(
            title="Widget",
            search_term=item.text,
            price=9.99,
            category="Hardware",
            item=item,
            instock=1,
            source=source,
            update=update,
        )
        ObservedCategoryValue.objects.filter(source=source, field_name="category").delete()

        migration = import_module(
            "tracking.migrations.0019_backfill_observed_category_values"
        )

        class _FakeApps:
            def get_model(self_inner, app_label, model_name):
                from django.apps import apps as real_apps

                return real_apps.get_model(app_label, model_name)

        migration.backfill_observed_category_values(_FakeApps(), None)

        self.assertTrue(
            ObservedCategoryValue.objects.filter(
                source=source, field_name="category", value="Hardware"
            ).exists()
        )


class ExpectedValuePerVendorPruningTests(TestCase):
    """expected-value-vendor-provenance — tasks 5.6/5.7: per-vendor pruning,
    end-to-end from ``SearchableItem.expected_values_for_source`` through
    ``JSONSearchParser.add_result``."""

    def test_vendor_tagged_entry_filters_only_that_vendor(self):
        wt = make_source(key="testwt-pv", parser_key="wtfilters")
        f2f = make_source(key="testf2f-pv", parser_key="shopify")
        item = make_item(text="Energy Retrieval")
        make_item_source(item, wt)
        make_item_source(item, f2f)
        item.expected_product_line = [{"value": "MTG", "source": "testwt-pv"}]
        item.save()

        wt_parser = JSONSearchParser(
            term=item.text,
            expected_product_line=item.expected_values_for_source("product_line", "testwt-pv"),
        )
        wt_parser.add_result(
            title="Energy Retrieval", price=1.0, instock=True, product_line="Pokemon TCG"
        )
        self.assertEqual(wt_parser.results, [])

        f2f_parser = JSONSearchParser(
            term=item.text,
            expected_product_line=item.expected_values_for_source("product_line", "testf2f-pv"),
        )
        f2f_parser.add_result(
            title="Energy Retrieval", price=1.0, instock=True, product_line="Pokemon TCG"
        )
        self.assertEqual(len(f2f_parser.results), 1)

    def test_manual_entry_filters_every_configured_vendor(self):
        wt = make_source(key="testwt-pv2", parser_key="wtfilters")
        f2f = make_source(key="testf2f-pv2", parser_key="shopify")
        item = make_item(text="Lightning Bolt")
        make_item_source(item, wt)
        make_item_source(item, f2f)
        item.expected_product_line = [{"value": "Magic", "source": None}]
        item.save()

        for source_key in ("testwt-pv2", "testf2f-pv2"):
            parser = JSONSearchParser(
                term=item.text,
                expected_product_line=item.expected_values_for_source(
                    "product_line", source_key
                ),
            )
            parser.add_result(
                title="Lightning Bolt",
                price=1.0,
                instock=True,
                product_line="Magic the Gathering",
            )
            self.assertEqual(len(parser.results), 1, source_key)


class ExpectedValueVendorProvenanceMigrationTests(TestCase):
    """expected-value-vendor-provenance — task 5.8: 0022 migration heuristic."""

    def _migration(self):
        return import_module(
            "tracking.migrations.0022_expected_value_vendor_provenance"
        )

    def _fake_apps(self):
        class _FakeApps:
            def get_model(self_inner, app_label, model_name):
                from django.apps import apps as real_apps

                return real_apps.get_model(app_label, model_name)

        return _FakeApps()

    def test_value_matching_exactly_one_vendor_is_attributed(self):
        wt = make_source(key="testwt-mig1", parser_key="wtfilters")
        f2f = make_source(key="testf2f-mig1", parser_key="shopify")
        item = make_item()
        make_item_source(item, wt)
        make_item_source(item, f2f)
        item.expected_product_line = ["MTG"]
        item.save()
        now = timezone.now()
        ObservedCategoryValue.objects.create(
            source=wt, field_name="product_line", value="MTG", last_seen=now
        )

        self._migration().migrate_expected_values_to_vendor_provenance(
            self._fake_apps(), None
        )

        item.refresh_from_db()
        self.assertEqual(
            item.expected_product_line, [{"value": "MTG", "source": "testwt-mig1"}]
        )

    def test_value_matching_zero_or_multiple_vendors_degrades_to_null(self):
        wt = make_source(key="testwt-mig2", parser_key="wtfilters")
        f2f = make_source(key="testf2f-mig2", parser_key="shopify")
        item = make_item()
        make_item_source(item, wt)
        make_item_source(item, f2f)
        item.expected_product_line = ["Magic", "Unmatched"]
        item.save()
        now = timezone.now()
        ObservedCategoryValue.objects.create(
            source=wt, field_name="product_line", value="Magic", last_seen=now
        )
        ObservedCategoryValue.objects.create(
            source=f2f, field_name="product_line", value="Magic", last_seen=now
        )

        self._migration().migrate_expected_values_to_vendor_provenance(
            self._fake_apps(), None
        )

        item.refresh_from_db()
        self.assertEqual(
            item.expected_product_line,
            [
                {"value": "Magic", "source": None},
                {"value": "Unmatched", "source": None},
            ],
        )

    def test_reverse_migration_drops_source_losslessly(self):
        item = make_item()
        item.expected_product_line = [
            {"value": "Magic", "source": "wt"},
            {"value": "MTG", "source": None},
        ]
        item.save()

        self._migration().reverse_expected_values_to_plain_strings(
            self._fake_apps(), None
        )

        item.refresh_from_db()
        self.assertEqual(item.expected_product_line, ["Magic", "MTG"])
