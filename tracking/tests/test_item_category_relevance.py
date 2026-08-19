"""item-category-relevance-filter — model, form, and value-discovery coverage.

Parser-level filtering tests live in test_parsers.py (base-class checks) and
the per-vendor fixture test files (test_wtfilters_parser.py, test_parsers.py's
ShopifyParser/StorepassParser fixture classes). This file covers the pieces
that sit above the parser: SearchableItem/SearchResult fields,
ObservedCategoryValue upserts, the value-discovery query helper, and the
category backfill data migration.
"""

import json
from importlib import import_module

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tracking.forms import SearchableItemForm
from tracking.models import ObservedCategoryValue, SearchResult, observed_values_for_item
from tracking.parsers import WtFiltersParser
from tracking.tests.base import AuthedClientTestCase
from tracking.tests.factories import (
    make_cc_source,
    make_item,
    make_item_source,
    make_source,
    make_web_update,
)


class SearchableItemExpectedFieldsTests(TestCase):
    def test_fields_blank_by_default(self):
        item = make_item()
        self.assertEqual(item.expected_product_line, "")
        self.assertEqual(item.expected_category, "")

    def test_fields_round_trip_independently(self):
        item = make_item()
        item.expected_product_line = "Magic"
        item.save()
        item.refresh_from_db()
        self.assertEqual(item.expected_product_line, "Magic")
        self.assertEqual(item.expected_category, "")

        item.expected_category = "Strixhaven"
        item.save()
        item.refresh_from_db()
        self.assertEqual(item.expected_product_line, "Magic")
        self.assertEqual(item.expected_category, "Strixhaven")


class SearchableItemFormExpectedFieldsTests(AuthedClientTestCase):
    def test_form_includes_both_fields(self):
        form = SearchableItemForm()
        self.assertIn("expected_product_line", form.fields)
        self.assertIn("expected_category", form.fields)

    def test_edit_view_round_trips_expected_values(self):
        item = make_item()
        response = self.client.post(
            reverse("edit_term", args=[item.pk]),
            {
                "text": item.text,
                "priority": item.priority,
                "expected_product_line": "Magic",
                "expected_category": "Strixhaven",
                "tags": [],
            },
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.expected_product_line, "Magic")
        self.assertEqual(item.expected_category, "Strixhaven")


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
            expected_product_line="Magic",
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
