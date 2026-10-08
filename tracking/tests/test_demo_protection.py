"""demo-sandbox-reset: seed data is protected from all edits (tasks 4.1-4.5)."""

from django.db import transaction
from django.test import SimpleTestCase

from tracking.demo.protection import (
    ProtectedDemoDataError,
    allow_seed_writes,
    is_protected,
)
from tracking.models import (
    DemoState,
    ItemMetadata,
    ItemSource,
    MetadataFetchRequest,
    SearchableItem,
    SearchResult,
    Tag,
)

from .demo_base import SEED_ITEM_PKS, SEED_TAG_PKS, DemoTestCase


class IsProtectedTests(DemoTestCase):
    def test_seed_and_visitor_rows(self):
        seed_item = SearchableItem.objects.get(pk=SEED_ITEM_PKS[0])
        visitor = self.visitor_item()
        self.assertTrue(is_protected(seed_item))
        self.assertFalse(is_protected(visitor))
        self.assertTrue(is_protected(Tag.objects.get(pk=SEED_TAG_PKS[0])))
        self.assertFalse(is_protected(Tag.objects.create(name="Mine")))
        self.assertTrue(is_protected(ItemSource.objects.filter(item=seed_item).first()))
        self.assertFalse(is_protected(ItemSource.objects.filter(item=visitor).first()))
        self.assertTrue(is_protected(ItemMetadata.objects.get(item=seed_item)))
        self.assertFalse(is_protected(None))


class NotProtectedOutsideDemoTests(SimpleTestCase):
    def test_nothing_protected_when_demo_off(self):
        self.assertFalse(is_protected(SearchableItem(pk=SEED_ITEM_PKS[0])))
        self.assertFalse(is_protected(Tag(pk=SEED_TAG_PKS[0])))


class SeedEndpointRefusalTests(DemoTestCase):
    def setUp(self):
        super().setUp()
        self.item = SearchableItem.objects.get(pk=SEED_ITEM_PKS[0])
        self.item_source = ItemSource.objects.filter(item=self.item).first()
        self.tag = Tag.objects.get(pk=SEED_TAG_PKS[0])

    def _snapshot(self):
        item = SearchableItem.objects.get(pk=self.item.pk)
        metadata = ItemMetadata.objects.get(item=item)
        return (
            item.text, item.priority, item.active, item.expected_product_line,
            item.expected_category, sorted(item.tags.values_list("pk", flat=True)),
            list(ItemSource.objects.filter(item=item).order_by("pk").values_list(
                "pk", "source_id", "title_include_patterns", "title_exclude_patterns")),
            metadata.status, metadata.external_id, metadata.pinned_external_id,
            MetadataFetchRequest.objects.count(),
            list(Tag.objects.order_by("pk").values_list("pk", "name", "color")),
        )

    def _assert_refused(self, path, data):
        before = self._snapshot()
        response = self.client.post(path, data, follow=True)
        self.assertEqual(self._snapshot(), before, path)
        self.assertContains(response, "protected demo item", msg_prefix=path)

    def test_every_seed_write_endpoint_refuses(self):
        pk, isrc, tag = self.item.pk, self.item_source.pk, self.tag.pk
        self._assert_refused(f"/edit_term/{pk}/", {
            "text": "Vandalized", "priority": "3", "metadata_provider_key": "",
        })
        self._assert_refused(f"/item/{pk}/sources/add/", {"source": "manavault"})
        self._assert_refused(f"/item_source/{isrc}/edit/", {
            "source": self.item_source.source_id, "title_exclude_patterns": ["Token"],
        })
        self._assert_refused(f"/item_source/{isrc}/delete/", {})
        self._assert_refused(f"/item/{pk}/metadata/retry/", {})
        self._assert_refused(f"/item/{pk}/metadata/select_candidate/", {"external_id": "x"})
        self._assert_refused(f"/item/{pk}/metadata/set_external_id/", {"external_id": "x"})
        self._assert_refused(f"/tags/{tag}/edit/", {"name": "Vandalized", "color": ""})
        self._assert_refused(f"/tags/{tag}/delete/", {})

    def test_get_of_seed_edit_page_redirects(self):
        response = self.client.get(f"/edit_term/{self.item.pk}/")
        self.assertEqual(response.status_code, 302)

    def test_deleting_seed_tag_keeps_assignments(self):
        assigned = set(self.tag.items.values_list("pk", flat=True))
        self.client.post(f"/tags/{self.tag.pk}/delete/")
        self.assertTrue(Tag.objects.filter(pk=self.tag.pk).exists())
        self.assertEqual(set(self.tag.items.values_list("pk", flat=True)), assigned)


class SeedControlsHiddenTests(DemoTestCase):
    def test_edit_controls_absent_for_seed_rows_present_for_visitor_rows(self):
        visitor = self.visitor_item()
        seed_pk = SEED_ITEM_PKS[0]
        page = self.client.get("/view_terms/")
        self.assertNotContains(page, f'href="/edit_term/{seed_pk}/"')
        self.assertContains(page, f'href="/edit_term/{visitor.pk}/"')

        detail = self.client.get(f"/item/{seed_pk}/")
        self.assertNotContains(detail, f"/edit_term/{seed_pk}/")
        self.assertNotContains(detail, "/metadata/")

        sources = self.client.get(f"/item/{seed_pk}/sources/")
        self.assertNotContains(sources, f"/item/{seed_pk}/sources/add/")
        self.assertNotContains(sources, "/item_source/")

        tags = self.client.get("/tags/")
        self.assertNotContains(tags, f"/tags/{SEED_TAG_PKS[0]}/edit/")
        self.assertContains(tags, "Protected demo tag")


class BulkEditSkipsSeedTests(DemoTestCase):
    def test_mixed_selection(self):
        visitor = self.visitor_item()
        seed = SearchableItem.objects.get(pk=SEED_ITEM_PKS[0])
        seed_priority = seed.priority
        response = self.client.post("/bulk_edit/", {
            "in_workspace": "1",
            "item_ids": [seed.pk, visitor.pk],
            "priority": "3", "active": "__leave__", "metadata_provider_key": "__leave__",
        })
        self.assertEqual(response.status_code, 200)
        seed.refresh_from_db()
        visitor.refresh_from_db()
        self.assertEqual(seed.priority, seed_priority)
        self.assertEqual(visitor.priority, 3)
        results = {r["item"].pk: r for r in response.context["results"]}
        self.assertTrue(results[visitor.pk]["success"])
        self.assertTrue(results[seed.pk]["skipped"])
        self.assertContains(response, "Skipped: protected demo item")


class SignalBackstopTests(DemoTestCase):
    def test_direct_orm_writes_to_seed_rows_raise(self):
        item = SearchableItem.objects.get(pk=SEED_ITEM_PKS[0])
        tag = Tag.objects.get(pk=SEED_TAG_PKS[0])
        visitor_tag = Tag.objects.create(name="Mine")
        item_source = ItemSource.objects.filter(item=item).first()

        def rename():
            item.text = "Vandalized"
            item.save()

        writes = [
            rename,
            item.delete,
            lambda: item.tags.add(visitor_tag),
            lambda: visitor_tag.items.add(item),
            tag.items.clear,
            item_source.delete,
            tag.delete,
        ]
        for write in writes:
            # Each in its own savepoint: Django's delete() runs in
            # atomic(savepoint=False), so a refusal would otherwise poison
            # the test's wrapping transaction.
            with self.assertRaises(ProtectedDemoDataError), transaction.atomic():
                write()
        self.assertEqual(SearchableItem.objects.get(pk=item.pk).text, "Emberwake Phoenix")

    def test_visitor_rows_unaffected(self):
        visitor = self.visitor_item()
        tag = Tag.objects.create(name="Mine")
        visitor.text = "Renamed"
        visitor.save()
        visitor.tags.add(tag)
        Tag.objects.get(pk=SEED_TAG_PKS[0]).items.add(visitor)
        visitor.delete()

    def test_allowed_inside_reset_context(self):
        item = SearchableItem.objects.get(pk=SEED_ITEM_PKS[0])
        with allow_seed_writes():
            item.text = "Changed by reset"
            item.save()
            item.tags.clear()
        item.refresh_from_db()
        self.assertEqual(item.text, "Changed by reset")


class SeedUpdatesAllowedTests(DemoTestCase):
    def test_update_selected_on_seed_items_stores_results(self):
        DemoState.objects.filter(pk=1).update(last_update_started_at=None)
        before = SearchResult.objects.filter(item_id__in=SEED_ITEM_PKS).count()
        response = self.client.post("/update/", {"mode": "selected", "item_ids": SEED_ITEM_PKS})
        self.assertEqual(response.status_code, 302)
        self.assertGreater(SearchResult.objects.filter(item_id__in=SEED_ITEM_PKS).count(), before)
