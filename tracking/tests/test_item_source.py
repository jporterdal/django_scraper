from django.test import TestCase
from django.urls import reverse
from django.db import IntegrityError, transaction
import json

from tracking.models import ItemSource
from tracking.tests.base import AuthedClientTestCase
from tracking.tests.factories import make_cc_source, make_item, make_item_source, make_source


class ItemSourcePatternFieldsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source = make_cc_source(name="Test Source")
        cls.item = make_item(text="rtx 5070")

    def test_item_source_pattern_fields_default_empty(self):
        item_source = ItemSource.objects.create(item=self.item, source=self.source)
        self.assertEqual(item_source.title_include_patterns, [])
        self.assertEqual(item_source.title_exclude_patterns, [])

class ItemSourceFormTests(AuthedClientTestCase):
    """Phase 2 Step 5 — ItemSourceForm regex validation + line<->JSON conversion."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.source = make_cc_source(name="Test Source")
        cls.item = make_item(text="rtx 5070")

    def _base_data(self, **overrides):
        data = {
            "source": self.source.pk,
            "url_suffix": "",
            "title_include_patterns": "",
            "title_exclude_patterns": "",
        }
        data.update(overrides)
        return data

    def test_form_rejects_invalid_regex(self):
        from tracking.forms import ItemSourceForm

        form = ItemSourceForm(data=self._base_data(title_include_patterns="[["))
        self.assertFalse(form.is_valid())
        self.assertIn("title_include_patterns", form.errors)

    def test_form_converts_lines_to_json_list(self):
        from tracking.forms import ItemSourceForm

        form = ItemSourceForm(
            data=self._base_data(title_include_patterns="a\nb"),
            instance=ItemSource(item=self.item),
        )
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save()
        obj.refresh_from_db()
        self.assertEqual(obj.title_include_patterns, ["a", "b"])

    def test_form_blank_patterns_save_empty_list(self):
        from tracking.forms import ItemSourceForm

        form = ItemSourceForm(
            data=self._base_data(),
            instance=ItemSource(item=self.item),
        )
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save()
        obj.refresh_from_db()
        self.assertEqual(obj.title_include_patterns, [])
        self.assertEqual(obj.title_exclude_patterns, [])

    def test_form_drops_blank_lines(self):
        from tracking.forms import ItemSourceForm

        form = ItemSourceForm(
            data=self._base_data(title_exclude_patterns="Foil\n\n\\bTi\\b\n"),
            instance=ItemSource(item=self.item),
        )
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save()
        obj.refresh_from_db()
        self.assertEqual(obj.title_exclude_patterns, ["Foil", "\\bTi\\b"])

    def test_form_initial_list_rendered_as_lines(self):
        from tracking.forms import ItemSourceForm

        item_source = ItemSource.objects.create(
            item=self.item,
            source=self.source,
            title_include_patterns=["RTX 5070"],
            title_exclude_patterns=["\\bTi\\b", "SUPER"],
        )
        form = ItemSourceForm(instance=item_source)
        self.assertEqual(form.initial["title_include_patterns"], "RTX 5070")
        self.assertEqual(
            form.initial["title_exclude_patterns"], "\\bTi\\b\nSUPER"
        )

    def test_edit_route_updates_patterns(self):
        item_source = ItemSource.objects.create(item=self.item, source=self.source)
        response = self.client.post(
            reverse("edit_item_source", args=[item_source.pk]),
            self._base_data(
                title_include_patterns="RTX 5070",
                title_exclude_patterns="\\bTi\\b\nSUPER",
            ),
        )
        self.assertEqual(response.status_code, 302)
        item_source.refresh_from_db()
        self.assertEqual(item_source.title_include_patterns, ["RTX 5070"])
        self.assertEqual(item_source.title_exclude_patterns, ["\\bTi\\b", "SUPER"])

class ResultMatchesItemSourceTests(TestCase):
    """Phase 2 Step 5 — result_matches_item_source() pattern-aware matching."""

    @classmethod
    def setUpTestData(cls):
        cls.source = make_cc_source(name="Test Source")
        cls.item = make_item(text="rtx 5070")

    def _item_source(self, include=None, exclude=None):
        return ItemSource(
            item=self.item,
            source=self.source,
            title_include_patterns=include or [],
            title_exclude_patterns=exclude or [],
        )

    def test_result_matches_item_source_include(self):
        from tracking.matching import result_matches_item_source

        item_source = self._item_source(include=["Lightning Bolt"])
        self.assertTrue(
            result_matches_item_source("Lightning Bolt (NM)", item_source)
        )

    def test_result_matches_item_source_exclude(self):
        from tracking.matching import result_matches_item_source

        item_source = self._item_source(exclude=["Foil"])
        self.assertFalse(
            result_matches_item_source("Lightning Bolt [Foil]", item_source)
        )

    def test_empty_patterns_pass_all(self):
        from tracking.matching import result_matches_item_source

        item_source = self._item_source()
        self.assertTrue(
            result_matches_item_source("Anything At All", item_source)
        )

    def test_rtx_5070_excludes_ti_word_boundary(self):
        from tracking.matching import result_matches_item_source

        item_source = self._item_source(include=["RTX 5070"], exclude=["\\bTi\\b"])
        self.assertTrue(
            result_matches_item_source(
                "MSI GeForce RTX 5070 Gaming Trio OC 16GB", item_source
            )
        )
        self.assertFalse(
            result_matches_item_source(
                "MSI GeForce RTX 5070 Ti Gaming Trio OC 16GB", item_source
            )
        )

class ItemSourceManagementTests(AuthedClientTestCase):
    """Phase 2 Step 7 — item-centric ItemSource management UI."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.source = make_cc_source()
        cls.other_source = make_source(
            key="f2f",
            name="Face to Face Games",
            parser_key="shopify",
            base_search_url="https://example.com/search/keyword/{term}",
        )
        cls.item = make_item(text="rtx 5070")

    def test_item_sources_list_page(self):
        ItemSource.objects.create(item=self.item, source=self.source)
        response = self.client.get(reverse("item_sources", args=[self.item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cc")

    def test_add_item_source(self):
        response = self.client.post(
            reverse("add_item_source", args=[self.item.pk]),
            {
                "source": self.source.pk,
                "url_suffix": "",
                "title_include_patterns": "",
                "title_exclude_patterns": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ItemSource.objects.filter(item=self.item, source=self.source).exists()
        )

    def test_add_item_source_excludes_linked_sources(self):
        ItemSource.objects.create(item=self.item, source=self.source)
        response = self.client.get(reverse("add_item_source", args=[self.item.pk]))
        self.assertEqual(response.status_code, 200)
        source_qs = response.context["form"].fields["source"].queryset
        self.assertNotIn(self.source, source_qs)
        self.assertIn(self.other_source, source_qs)

    def test_duplicate_item_source_blocked(self):
        from django.db import IntegrityError, transaction

        ItemSource.objects.create(item=self.item, source=self.source)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ItemSource.objects.create(item=self.item, source=self.source)

    def test_delete_item_source(self):
        item_source = ItemSource.objects.create(item=self.item, source=self.source)
        response = self.client.post(
            reverse("delete_item_source", args=[item_source.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ItemSource.objects.filter(pk=item_source.pk).exists())
