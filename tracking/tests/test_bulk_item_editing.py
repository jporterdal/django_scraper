"""bulk-item-editing — selection UI, workspace session, and per-field apply coverage."""

from unittest.mock import patch

from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from tracking.forms import (
    BULK_EDIT_CLEAR,
    BULK_EDIT_LEAVE,
    BulkEditItemsForm,
    apply_bulk_edit,
)
from tracking.models import (
    ItemMetadata,
    MetadataFetchRequest,
    ObservedCategoryValue,
    SearchableItem,
    Tag,
    vendor_scoped_suggestions_for_items,
)
from tracking.tests.base import AuthedClientTestCase, LinkedSourceTestCase
from tracking.tests.factories import make_item, make_item_source, make_source


def _bulk_edit_post_data(item_ids, **overrides):
    data = {
        "in_workspace": "1",
        "item_ids": [str(pk) for pk in item_ids],
        "priority": BULK_EDIT_LEAVE,
        "active": BULK_EDIT_LEAVE,
        "tags_add": [],
        "tags_remove": [],
        "metadata_provider_key": BULK_EDIT_LEAVE,
        "expected_product_line_suggestions": [],
        "expected_category_suggestions": [],
    }
    data.update(overrides)
    return data


class SelectionUITests(LinkedSourceTestCase):
    """Task 8.1 — inactive items are selectable; mode=selected is unaffected."""

    def test_inactive_item_row_has_selection_checkbox(self):
        inactive_item = make_item(text="Inactive Item", active=False)
        response = self.client.get(reverse("view_terms"))
        self.assertContains(
            response,
            f'name="item_ids" value="{inactive_item.pk}"',
        )

    def test_mode_selected_price_update_still_filters_to_active_items(self):
        inactive_item = make_item(text="Inactive Item", active=False)
        with patch("tracking.tasks.fetch_one"):
            response = self.client.post(
                reverse("update"),
                {
                    "mode": "selected",
                    "item_ids": [str(self.item.pk), str(inactive_item.pk)],
                },
            )
        # Only the active item (with a configured source) is dispatched;
        # behavior matches pre-change semantics exactly.
        self.assertEqual(response.status_code, 302)


class BulkEditEntryTests(AuthedClientTestCase):
    """Tasks 1.3/1.4 and 2.1 — entering the workspace from view_terms."""

    def test_no_items_selected_shows_warning_and_no_workspace(self):
        response = self.client.post(reverse("bulk_edit_items"), {})
        self.assertRedirects(response, reverse("view_terms"))

    def test_get_redirects_to_view_terms(self):
        response = self.client.get(reverse("bulk_edit_items"))
        self.assertRedirects(response, reverse("view_terms"))

    def test_selection_enters_workspace_scoped_to_checked_items(self):
        item_a = make_item(text="Workspace Item A")
        item_b = make_item(text="Workspace Item B")
        other = make_item(text="Not Selected")
        response = self.client.post(
            reverse("bulk_edit_items"),
            {"item_ids": [str(item_a.pk), str(item_b.pk)]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Workspace Item A")
        self.assertContains(response, "Workspace Item B")
        self.assertNotContains(response, "Not Selected")


class WorkspaceSessionTests(AuthedClientTestCase):
    """Task 8.2 — selection persistence, per-row removal, Done."""

    def test_selection_persists_across_sequential_apply_rounds(self):
        item_a = make_item(text="Round Item A", priority=SearchableItem.Priority.C)
        item_b = make_item(text="Round Item B", priority=SearchableItem.Priority.C)
        item_ids = [item_a.pk, item_b.pk]

        response = self.client.post(
            reverse("bulk_edit_items"),
            _bulk_edit_post_data(item_ids, priority=str(SearchableItem.Priority.A)),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Round Item A")
        self.assertContains(response, "Round Item B")

        response2 = self.client.post(
            reverse("bulk_edit_items"),
            _bulk_edit_post_data(
                item_ids, tags_add=[]
            ),
        )
        self.assertEqual(response2.status_code, 200)
        self.assertContains(response2, "Round Item A")
        self.assertContains(response2, "Round Item B")

        item_a.refresh_from_db()
        item_b.refresh_from_db()
        self.assertEqual(item_a.priority, SearchableItem.Priority.A)
        self.assertEqual(item_b.priority, SearchableItem.Priority.A)

    def test_per_row_removal_shrinks_working_set(self):
        item_a = make_item(text="Keep Item")
        item_b = make_item(text="Remove Item")
        response = self.client.post(
            reverse("bulk_edit_items"),
            {
                "in_workspace": "1",
                "item_ids": [str(item_a.pk), str(item_b.pk)],
                "remove_item_id": str(item_b.pk),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Keep Item")
        self.assertNotContains(response, "Remove Item")

    def test_done_returns_to_view_terms(self):
        item = make_item(text="Done Item")
        response = self.client.post(
            reverse("bulk_edit_items"),
            {
                "in_workspace": "1",
                "item_ids": [str(item.pk)],
                "done": "1",
            },
        )
        self.assertRedirects(response, reverse("view_terms"))

    def test_missing_item_dropped_silently(self):
        item = make_item(text="Still Here")
        missing_pk = item.pk + 100000
        response = self.client.post(
            reverse("bulk_edit_items"),
            {"item_ids": [str(item.pk), str(missing_pk)]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Still Here")
        self.assertNotContains(response, str(missing_pk))


class LeaveUnchangedTests(TestCase):
    """Task 8.3 — a partial apply round leaves untouched fields alone."""

    def test_priority_only_round_leaves_other_fields_untouched(self):
        tag = Tag.objects.create(name="Kept Tag")
        item = make_item(
            text="Partial Item",
            priority=SearchableItem.Priority.C,
            active=True,
        )
        item.tags.add(tag)
        item.expected_product_line = ["Existing"]
        item.save()

        form = BulkEditItemsForm(
            data=_bulk_edit_post_data([item.pk], priority=str(SearchableItem.Priority.S)),
            item_ids=[item.pk],
        )
        self.assertTrue(form.is_valid(), form.errors)
        results = apply_bulk_edit([item.pk], form.cleaned_data)
        self.assertTrue(results[0]["success"])

        item.refresh_from_db()
        self.assertEqual(item.priority, SearchableItem.Priority.S)
        self.assertTrue(item.active)
        self.assertEqual(list(item.tags.all()), [tag])
        self.assertEqual(item.expected_product_line, ["Existing"])
        self.assertEqual(item.metadata_provider_key, "")


class PerFieldApplyTests(TestCase):
    """Task 8.4 — priority/active/tags/metadata-provider apply semantics."""

    def _apply(self, item_ids, **overrides):
        form = BulkEditItemsForm(
            data=_bulk_edit_post_data(item_ids, **overrides), item_ids=item_ids
        )
        self.assertTrue(form.is_valid(), form.errors)
        return apply_bulk_edit(item_ids, form.cleaned_data)

    def test_priority_overwrite(self):
        items = [make_item(text=f"P{i}", priority=SearchableItem.Priority.C) for i in range(3)]
        item_ids = [i.pk for i in items]
        self._apply(item_ids, priority=str(SearchableItem.Priority.S))
        for item in items:
            item.refresh_from_db()
            self.assertEqual(item.priority, SearchableItem.Priority.S)

    def test_active_tristate_deactivate(self):
        items = [make_item(text=f"A{i}", active=True) for i in range(2)]
        item_ids = [i.pk for i in items]
        self._apply(item_ids, active="deactivate")
        for item in items:
            item.refresh_from_db()
            self.assertFalse(item.active)

    def test_active_tristate_activate(self):
        items = [make_item(text=f"B{i}", active=False) for i in range(2)]
        item_ids = [i.pk for i in items]
        self._apply(item_ids, active="activate")
        for item in items:
            item.refresh_from_db()
            self.assertTrue(item.active)

    def test_active_leave_unchanged_does_not_modify(self):
        item = make_item(text="LeaveActive", active=True)
        self._apply([item.pk], active=BULK_EDIT_LEAVE)
        item.refresh_from_db()
        self.assertTrue(item.active)

    def test_tag_add_and_remove_independently_preserve_unrelated_tags(self):
        keep_tag = Tag.objects.create(name="Keep")
        remove_tag = Tag.objects.create(name="Remove")
        add_tag = Tag.objects.create(name="Add")
        item = make_item(text="TagItem")
        item.tags.add(keep_tag, remove_tag)

        self._apply(
            [item.pk],
            tags_add=[str(add_tag.pk)],
            tags_remove=[str(remove_tag.pk)],
        )
        item.refresh_from_db()
        tag_names = set(item.tags.values_list("name", flat=True))
        self.assertEqual(tag_names, {"Keep", "Add"})

    def test_metadata_provider_set_routes_through_shared_entrypoint(self):
        items = [make_item(text=f"M{i}") for i in range(5)]
        item_ids = [i.pk for i in items]
        self._apply(item_ids, metadata_provider_key="scryfall")
        for item in items:
            item.refresh_from_db()
            self.assertEqual(item.metadata_provider_key, "scryfall")
            self.assertEqual(
                MetadataFetchRequest.objects.filter(item=item).count(), 1
            )

    def test_metadata_provider_clear_resets_fetched_state_no_refresh(self):
        item = make_item(text="ClearMe", metadata_provider_key="scryfall")
        ItemMetadata.objects.create(
            item=item,
            status=ItemMetadata.Status.MATCHED,
            external_id="ext-1",
            payload={"a": 1},
        )
        self._apply([item.pk], metadata_provider_key=BULK_EDIT_CLEAR)
        item.refresh_from_db()
        self.assertEqual(item.metadata_provider_key, "")
        item.metadata.refresh_from_db()
        self.assertEqual(item.metadata.status, ItemMetadata.Status.UNFETCHED)
        self.assertEqual(item.metadata.external_id, "")
        self.assertEqual(item.metadata.payload, {})
        self.assertEqual(MetadataFetchRequest.objects.filter(item=item).count(), 0)

    def test_metadata_provider_leave_unchanged_does_not_touch_state(self):
        item = make_item(text="LeaveProvider", metadata_provider_key="scryfall")
        ItemMetadata.objects.create(item=item, status=ItemMetadata.Status.MATCHED)
        self._apply([item.pk], metadata_provider_key=BULK_EDIT_LEAVE)
        item.refresh_from_db()
        self.assertEqual(item.metadata_provider_key, "scryfall")
        item.metadata.refresh_from_db()
        self.assertEqual(item.metadata.status, ItemMetadata.Status.MATCHED)
        self.assertEqual(MetadataFetchRequest.objects.filter(item=item).count(), 0)


class VendorScopedSuggestionTests(TestCase):
    """Task 8.5 — vendor grouping, subset apply, additive dedup."""

    def test_vendor_used_by_one_item_still_gets_own_group_with_accurate_count(self):
        vendor = make_source(key="onlyone", parser_key="cc")
        items = [make_item(text=f"V{i}") for i in range(20)]
        make_item_source(items[0], vendor)
        ObservedCategoryValue.objects.create(
            source=vendor, field_name="product_line", value="Widgets", last_seen=timezone.now()
        )

        groups = vendor_scoped_suggestions_for_items(
            [i.pk for i in items], "product_line"
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["source"], vendor)
        self.assertEqual(groups[0]["item_count"], 1)
        self.assertEqual(groups[0]["values"], ["Widgets"])

    def test_applying_suggestion_affects_only_matching_vendor_subset(self):
        vendor = make_source(key="subset", parser_key="cc")
        with_vendor = [make_item(text=f"W{i}") for i in range(8)]
        without_vendor = [make_item(text=f"X{i}") for i in range(12)]
        for item in with_vendor:
            make_item_source(item, vendor)
        ObservedCategoryValue.objects.create(
            source=vendor, field_name="product_line", value="Gadgets", last_seen=timezone.now()
        )

        all_items = with_vendor + without_vendor
        item_ids = [i.pk for i in all_items]
        form = BulkEditItemsForm(
            data=_bulk_edit_post_data(
                item_ids,
                expected_product_line_suggestions=[
                    '["subset", "Gadgets"]',
                ],
            ),
            item_ids=item_ids,
        )
        self.assertTrue(form.is_valid(), form.errors)
        apply_bulk_edit(item_ids, form.cleaned_data)

        for item in with_vendor:
            item.refresh_from_db()
            self.assertEqual(
                item.expected_product_line, [{"value": "Gadgets", "source": "subset"}]
            )
        for item in without_vendor:
            item.refresh_from_db()
            self.assertEqual(item.expected_product_line, [])

    def test_repeated_apply_is_additive_and_deduplicated(self):
        """Task 5.6 — existing entries are stored as {"value","source"} dicts

        (post-migration shape; see the expected-value-vendor-provenance
        capability), and a suggestion applied twice does not duplicate.
        """
        vendor = make_source(key="dedupe", parser_key="cc")
        item = make_item(text="DedupeItem")
        make_item_source(item, vendor)
        item.expected_category = [{"value": "Manual Value", "source": None}]
        item.save()
        ObservedCategoryValue.objects.create(
            source=vendor, field_name="category", value="Vendor Value", last_seen=timezone.now()
        )

        for _ in range(2):
            form = BulkEditItemsForm(
                data=_bulk_edit_post_data(
                    [item.pk],
                    expected_category_suggestions=['["dedupe", "Vendor Value"]'],
                ),
                item_ids=[item.pk],
            )
            self.assertTrue(form.is_valid(), form.errors)
            apply_bulk_edit([item.pk], form.cleaned_data)

        item.refresh_from_db()
        self.assertEqual(
            item.expected_category,
            [
                {"value": "Manual Value", "source": None},
                {"value": "Vendor Value", "source": "dedupe"},
            ],
        )

    def test_applying_suggestion_preserves_existing_vendor_tagged_entries(self):
        """Task 5.6/8.8 — an item already carrying a *vendor-tagged* entry

        (not just a manual one) does not crash the apply round and keeps
        that entry alongside the newly added suggestion. Regression test for
        the pre-5.6 bug where merging a dict-shaped existing list via
        ``dict.fromkeys`` raised ``TypeError: unhashable type: 'dict'``.
        """
        vendor = make_source(key="tagged", parser_key="cc")
        other_vendor = make_source(key="other", parser_key="cc")
        item = make_item(text="TaggedItem")
        make_item_source(item, vendor)
        item.expected_product_line = [{"value": "Existing", "source": "other"}]
        item.save()
        ObservedCategoryValue.objects.create(
            source=vendor, field_name="product_line", value="New", last_seen=timezone.now()
        )

        form = BulkEditItemsForm(
            data=_bulk_edit_post_data(
                [item.pk],
                expected_product_line_suggestions=['["tagged", "New"]'],
            ),
            item_ids=[item.pk],
        )
        self.assertTrue(form.is_valid(), form.errors)
        results = apply_bulk_edit([item.pk], form.cleaned_data)

        self.assertTrue(results[0]["success"], results[0]["error"])
        item.refresh_from_db()
        self.assertEqual(
            item.expected_product_line,
            [
                {"value": "Existing", "source": "other"},
                {"value": "New", "source": "tagged"},
            ],
        )


class BestEffortApplyTests(TestCase):
    """Task 8.6 — one item's failure does not block the rest of the round."""

    def test_one_item_failure_does_not_block_remaining_items(self):
        items = [make_item(text=f"F{i}", priority=SearchableItem.Priority.C) for i in range(4)]
        item_ids = [i.pk for i in items]
        failing_pk = items[1].pk

        original_save = SearchableItem.save

        def flaky_save(self, *args, **kwargs):
            if self.pk == failing_pk:
                raise ValueError("boom")
            return original_save(self, *args, **kwargs)

        form = BulkEditItemsForm(
            data=_bulk_edit_post_data(item_ids, priority=str(SearchableItem.Priority.S)),
            item_ids=item_ids,
        )
        self.assertTrue(form.is_valid(), form.errors)

        with patch.object(SearchableItem, "save", flaky_save):
            results = apply_bulk_edit(item_ids, form.cleaned_data)

        self.assertEqual(len(results), 4)
        by_pk = {r["item"].pk: r for r in results}
        self.assertFalse(by_pk[failing_pk]["success"])
        self.assertIn("boom", by_pk[failing_pk]["error"])
        for item in items:
            if item.pk == failing_pk:
                continue
            self.assertTrue(by_pk[item.pk]["success"])

        for item in items:
            item.refresh_from_db()
            if item.pk == failing_pk:
                self.assertEqual(item.priority, SearchableItem.Priority.C)
            else:
                self.assertEqual(item.priority, SearchableItem.Priority.S)


class QueryCountTests(TestCase):
    """Task 8.7 — vendor-scoped suggestion computation is bounded, not per-item."""

    def test_query_count_bounded_across_selection_size(self):
        vendor_a = make_source(key="qa", parser_key="cc")
        vendor_b = make_source(key="qb", parser_key="cc")
        now = timezone.now()
        ObservedCategoryValue.objects.create(
            source=vendor_a, field_name="product_line", value="A1", last_seen=now
        )
        ObservedCategoryValue.objects.create(
            source=vendor_b, field_name="product_line", value="B1", last_seen=now
        )

        small_items = [make_item(text=f"S{i}") for i in range(3)]
        for item in small_items:
            make_item_source(item, vendor_a)
        with CaptureQueriesContext(connection) as small_ctx:
            vendor_scoped_suggestions_for_items(
                [i.pk for i in small_items], "product_line"
            )

        large_items = [make_item(text=f"L{i}") for i in range(40)]
        for item in large_items:
            make_item_source(item, vendor_a if item.pk % 2 else vendor_b)
        with CaptureQueriesContext(connection) as large_ctx:
            vendor_scoped_suggestions_for_items(
                [i.pk for i in large_items], "product_line"
            )

        self.assertLessEqual(len(large_ctx.captured_queries), len(small_ctx.captured_queries) + 1)
        self.assertLess(len(large_ctx.captured_queries), 6)
