## 1. Selection UI on view_terms

- [ ] 1.1 Widen the item-row checkbox in `searchableitem_list.html` to render for inactive rows too (remove the `{% if item.active %}` gate around the checkbox specifically; row styling for inactive items is unaffected)
- [ ] 1.2 Confirm `UpdateFromWebView`'s `mode=selected` handling (`tracking/views.py:912-923`) is unaffected by the wider selection (it already filters to `active=True` server-side)
- [ ] 1.3 Add a "Bulk Edit Selected" button alongside the existing selection actions, submitting checked `item_ids` via POST to the new bulk-edit workspace URL
- [ ] 1.4 Handle the empty-selection case (warning message, no workspace entered), consistent with the existing "no items selected" messaging for price updates

## 2. Bulk-edit workspace view & routing

- [ ] 2.1 Add `BulkEditItemsView` and its URL (e.g. `bulk_edit_items`)
- [ ] 2.2 Implement working-selection handling: initial POST from `view_terms` carries `item_ids`; every subsequent Apply/remove/Done action on the workspace carries the current working `item_ids` forward as hidden POST fields
- [ ] 2.3 Implement the per-row "remove from selection" action (removes one id from the working set, re-renders the workspace with the rest intact)
- [ ] 2.4 Implement "Done" (returns to `view_terms`, no further processing)
- [ ] 2.5 Handle an item id in the working selection that no longer resolves to an existing `SearchableItem` by dropping it from the working set silently (not a specially-handled concurrency feature — see design.md's documented non-goal; this is just normal missing-row handling, not conflict detection)

## 3. Bulk-edit form (leave-unchanged sentinel)

- [ ] 3.1 Implement the combined bulk-edit form with every field defaulting to "leave unchanged": `priority` (leave/overwrite), `active` (leave/activate/deactivate), `tags_add` and `tags_remove` (independent multi-selects), `metadata_provider_key` (leave/set/clear)
- [ ] 3.2 Populate `metadata_provider_key` choices live from the provider registry, mirroring `BulkAddItemsForm`'s pattern
- [ ] 3.3 Populate `tags_add`/`tags_remove` choices from all `Tag`s (mirroring existing tag selection patterns)

## 4. Vendor-scoped expected_product_line / expected_category suggestions

- [ ] 4.1 Implement a vendor-scoped suggestion helper (sibling to `observed_values_for_item`) that, given a set of item ids, returns each vendor (`Source`) present via any of those items' `ItemSource`s, annotated with how many of the given items have that vendor configured
- [ ] 4.2 For each such vendor, build suggestion choices from `ObservedCategoryValue` scoped to that vendor, separately for `field_name="product_line"` and `field_name="category"`, with each choice value carrying enough information (vendor + raw value) to resolve the correct item subset at apply time
- [ ] 4.3 Render the vendor-grouped suggestion checkboxes in the workspace form, each group labeled with its subset count (e.g. "8 of 20 selected items")
- [ ] 4.4 Ensure the vendor/count computation and per-vendor suggestion lookups are batched (bounded query count for the whole workspace render), not looped per item

## 5. Per-field apply logic

- [ ] 5.1 Implement `priority` bulk apply: overwrite on every item in the working selection when set away from leave-unchanged
- [ ] 5.2 Implement `active` bulk apply: set `True`/`False` on every item in the working selection when set away from leave-unchanged
- [ ] 5.3 Implement tag add/remove bulk apply: add `tags_add` members to and remove `tags_remove` members from each item's existing `tags`, independently
- [ ] 5.4 Implement `metadata_provider_key` bulk apply: for each item whose value actually changes, call the existing shared refresh entrypoint (`request_metadata_refresh` / `sync_metadata_after_save`, per `item-metadata-enrichment`) exactly as `SearchableItemForm.save()` does today, so the provider-change reset behavior is reused
- [ ] 5.5 Implement `expected_product_line`/`expected_category` bulk apply: for each checked vendor-scoped suggestion, add its value (merged/deduped by exact string equality) only to items in the working selection that have that suggestion's vendor configured via `ItemSource`

## 6. Best-effort per-item apply and error reporting

- [ ] 6.1 Implement the apply loop so each item in the working selection is attempted independently for the round's changes; one item's exception/validation failure does not stop the loop from attempting the rest
- [ ] 6.2 Collect and render per-item results after an apply round: which items succeeded, which failed and why
- [ ] 6.3 Reset the form to all-fields-leave-unchanged after a successful (or partially successful) apply round, ready for the next round

## 7. Templates

- [ ] 7.1 Add the bulk-edit workspace template: item roster (scrollable panel, reusing the existing suggestion-panel CSS/markup pattern from `searchableitem_form.html`) with text/active-badge/tags/priority and per-row remove action; the combined edit form; the last-round result summary; the Done action
- [ ] 7.2 Apply Bootstrap form classes consistently with `_apply_bootstrap_form_classes`, matching existing form styling

## 8. Tests

- [ ] 8.1 Selection UI tests: inactive items are selectable; existing `mode=selected` price-update behavior is unchanged by the wider checkbox
- [ ] 8.2 Workspace session tests: selection persists across multiple sequential apply rounds; per-row removal shrinks the working set without affecting the rest; Done returns to `view_terms`
- [ ] 8.3 Leave-unchanged tests: an apply round touching only one field leaves every other field on every affected item untouched
- [ ] 8.4 Per-field apply tests: priority overwrite; active tri-state (activate/deactivate/leave); tag add and remove independently preserve unrelated tags; metadata-provider set/clear routes through the shared entrypoint and triggers the existing provider-change reset behavior
- [ ] 8.5 Vendor-scoped suggestion tests: a vendor present on only one selected item still gets its own group with an accurate count; applying a vendor-scoped suggestion affects only the matching subset of the selection; existing per-item `expected_*` values are preserved (additive, deduplicated) across repeated applies
- [ ] 8.6 Best-effort apply tests: a failure on one item in an apply round does not prevent the round from applying to the remaining items; failures are reported per item
- [ ] 8.7 Query-count test for the vendor-scoped suggestion computation across a multi-item selection (bounded, not linear in selection size)

## 9. Docs

- [ ] 9.1 Add a `README.md` section documenting the bulk-edit workspace: what fields are editable, the additive semantics for tags/expected_*, and how the metadata-provider field routes through the existing refresh entrypoint
- [ ] 9.2 Document the multi-user/concurrent-modification limitation explicitly as a deferred future direction (an item changed or deleted by another user/process mid-session is not detected or specially handled), mirroring how `item-metadata-enrichment` documented its own deferred scope
- [ ] 9.3 Note other deferred future directions: manual free-text entry for `expected_product_line`/`expected_category` in bulk mode, and any audit/undo log of bulk changes
