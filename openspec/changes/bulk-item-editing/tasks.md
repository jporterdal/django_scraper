## 1. Selection UI on view_terms

- [x] 1.1 Widen the item-row checkbox in `searchableitem_list.html` to render for inactive rows too (remove the `{% if item.active %}` gate around the checkbox specifically; row styling for inactive items is unaffected)
- [x] 1.2 Confirm `UpdateFromWebView`'s `mode=selected` handling (`tracking/views.py:912-923`) is unaffected by the wider selection (it already filters to `active=True` server-side)
- [x] 1.3 Add a "Bulk Edit Selected" button alongside the existing selection actions, submitting checked `item_ids` via POST to the new bulk-edit workspace URL
- [x] 1.4 Handle the empty-selection case (warning message, no workspace entered), consistent with the existing "no items selected" messaging for price updates

## 2. Bulk-edit workspace view & routing

- [x] 2.1 Add `BulkEditItemsView` and its URL (e.g. `bulk_edit_items`)
- [x] 2.2 Implement working-selection handling: initial POST from `view_terms` carries `item_ids`; every subsequent Apply/remove/Done action on the workspace carries the current working `item_ids` forward as hidden POST fields
- [x] 2.3 Implement the per-row "remove from selection" action (removes one id from the working set, re-renders the workspace with the rest intact)
- [x] 2.4 Implement "Done" (returns to `view_terms`, no further processing)
- [x] 2.5 Handle an item id in the working selection that no longer resolves to an existing `SearchableItem` by dropping it from the working set silently (not a specially-handled concurrency feature — see design.md's documented non-goal; this is just normal missing-row handling, not conflict detection)

## 3. Bulk-edit form (leave-unchanged sentinel)

- [x] 3.1 Implement the combined bulk-edit form with every field defaulting to "leave unchanged": `priority` (leave/overwrite), `active` (leave/activate/deactivate), `tags_add` and `tags_remove` (independent multi-selects), `metadata_provider_key` (leave/set/clear)
- [x] 3.2 Populate `metadata_provider_key` choices live from the provider registry, mirroring `BulkAddItemsForm`'s pattern
- [x] 3.3 Populate `tags_add`/`tags_remove` choices from all `Tag`s (mirroring existing tag selection patterns)

## 4. Vendor-scoped expected_product_line / expected_category suggestions

> **Follow-up landed** (see design.md's "Known Follow-up", UPDATE 2026-08-22): the anticipated storage-model change — `expected-value-vendor-provenance` — has merged from `dev` and archived, moving `expected_product_line`/`expected_category` to `(value, source)`-pair storage. Tasks 4.1–4.4 below (building vendor-scoped suggestion choices) remain correct: `ObservedCategoryValue.value` stays a raw string regardless of the storage-model change, and the choice encoding already carries `(vendor, value)`. Apply task 5.5's *consumption* of those choices does not — see task 5.6.

- [x] 4.1 Implement a vendor-scoped suggestion helper (sibling to `observed_values_for_item`) that, given a set of item ids, returns each vendor (`Source`) present via any of those items' `ItemSource`s, annotated with how many of the given items have that vendor configured
- [x] 4.2 For each such vendor, build suggestion choices from `ObservedCategoryValue` scoped to that vendor, separately for `field_name="product_line"` and `field_name="category"`, with each choice value carrying enough information (vendor + raw value) to resolve the correct item subset at apply time
- [x] 4.3 Render the vendor-grouped suggestion checkboxes in the workspace form, each group labeled with its subset count (e.g. "8 of 20 selected items")
- [x] 4.4 Ensure the vendor/count computation and per-vendor suggestion lookups are batched (bounded query count for the whole workspace render), not looped per item

## 5. Per-field apply logic

- [x] 5.1 Implement `priority` bulk apply: overwrite on every item in the working selection when set away from leave-unchanged
- [x] 5.2 Implement `active` bulk apply: set `True`/`False` on every item in the working selection when set away from leave-unchanged
- [x] 5.3 Implement tag add/remove bulk apply: add `tags_add` members to and remove `tags_remove` members from each item's existing `tags`, independently
- [x] 5.4 Implement `metadata_provider_key` bulk apply: for each item whose value actually changes, call the existing shared refresh entrypoint (`request_metadata_refresh` / `sync_metadata_after_save`, per `item-metadata-enrichment`) exactly as `SearchableItemForm.save()` does today, so the provider-change reset behavior is reused
- [x] 5.5 Implement `expected_product_line`/`expected_category` bulk apply: for each checked vendor-scoped suggestion, add its value (merged/deduped by exact string equality) only to items in the working selection that have that suggestion's vendor configured via `ItemSource` — implemented and correct against the flat-`list[str]` model in place at the time; superseded by 5.6 now that the storage model has changed
- [x] 5.6 Rework `_resolve_suggestion_subsets`/`_apply_bulk_edit_to_item` (`tracking/forms.py`) for the landed `{"value","source"}`-pair storage model: carry `(source_key, value)` through to apply time instead of discarding the vendor, and merge into each item's existing list as `{"value","source"}` dicts deduplicated by exact `(value, source)` pair — mirroring `_merge_checked_and_manual` in the single-item form. Fixes two confirmed failure modes: `TypeError: unhashable type: 'dict'` when a selected item already has stored entries (current code mixes dicts and strings in `dict.fromkeys`), and silent corruption to a plain-string list when it doesn't, which then breaks the next scrape for that item at `tracking/scrape.py:633` (`item.expected_values_for_source` does `entry["source"]` on each entry)

## 6. Best-effort per-item apply and error reporting

- [x] 6.1 Implement the apply loop so each item in the working selection is attempted independently for the round's changes; one item's exception/validation failure does not stop the loop from attempting the rest
- [x] 6.2 Collect and render per-item results after an apply round: which items succeeded, which failed and why
- [x] 6.3 Reset the form to all-fields-leave-unchanged after a successful (or partially successful) apply round, ready for the next round

## 7. Templates

- [x] 7.1 Add the bulk-edit workspace template: item roster (scrollable panel, reusing the existing suggestion-panel CSS/markup pattern from `searchableitem_form.html`) with text/active-badge/tags/priority and per-row remove action; the combined edit form; the last-round result summary; the Done action
- [x] 7.2 Apply Bootstrap form classes consistently with `_apply_bootstrap_form_classes`, matching existing form styling

## 8. Tests

- [x] 8.1 Selection UI tests: inactive items are selectable; existing `mode=selected` price-update behavior is unchanged by the wider checkbox
- [x] 8.2 Workspace session tests: selection persists across multiple sequential apply rounds; per-row removal shrinks the working set without affecting the rest; Done returns to `view_terms`
- [x] 8.3 Leave-unchanged tests: an apply round touching only one field leaves every other field on every affected item untouched
- [x] 8.4 Per-field apply tests: priority overwrite; active tri-state (activate/deactivate/leave); tag add and remove independently preserve unrelated tags; metadata-provider set/clear routes through the shared entrypoint and triggers the existing provider-change reset behavior
- [x] 8.5 Vendor-scoped suggestion tests: a vendor present on only one selected item still gets its own group with an accurate count; applying a vendor-scoped suggestion affects only the matching subset of the selection; existing per-item `expected_*` values are preserved (additive, deduplicated) across repeated applies
- [x] 8.6 Best-effort apply tests: a failure on one item in an apply round does not prevent the round from applying to the remaining items; failures are reported per item
- [x] 8.7 Query-count test for the vendor-scoped suggestion computation across a multi-item selection (bounded, not linear in selection size)
- [x] 8.8 Update `VendorScopedSuggestionTests` (and any other `expected_product_line`/`expected_category` assertions in `test_bulk_item_editing.py`) to assert against `{"value","source"}` dict entries instead of flat strings, and add a case covering an item that already has stored entries before the apply round — current assertions (e.g. `assertEqual(item.expected_product_line, ["Gadgets"])`) pass only because the test items involved start with empty `expected_*` lists, which masks the bug fixed in 5.6
- [ ] 8.9 Tag tri-state tests: the tag-activity helper (task 10.1) returns accurate per-tag counts scoped to the working selection, via a bounded (not per-tag) query; add/remove/unchanged apply correctly per tag; add-when-already-present and remove-when-already-absent are no-ops
- [ ] 8.10 Expected-value tri-state tests: the expected-value-activity/manual-entry helper (task 10.2) returns accurate per-`(value,source)` counts and the correct "Manual entry" group via a bounded (not per-row) query; add applies only to the matching vendor subset (or every item, for manual rows); remove strips only the exact `(value, source)` pair from items that have it and leaves every other entry untouched; add-when-already-present and remove-when-already-absent are no-ops
- [ ] 8.11 Query-count test for the new tag-activity and expected-value-activity/manual-entry helpers across a multi-item selection (bounded, not linear in selection size), extending task 8.7's discipline to the new helpers

## 9. Docs

- [x] 9.1 Add a `README.md` section documenting the bulk-edit workspace: what fields are editable, the additive semantics for tags/expected_*, and how the metadata-provider field routes through the existing refresh entrypoint
- [x] 9.2 Document the multi-user/concurrent-modification limitation explicitly as a deferred future direction (an item changed or deleted by another user/process mid-session is not detected or specially handled), mirroring how `item-metadata-enrichment` documented its own deferred scope
- [x] 9.3 Note other deferred future directions: manual free-text entry for `expected_product_line`/`expected_category` in bulk mode, and any audit/undo log of bulk changes
- [ ] 9.4 Update the `README.md` bulk-edit section for the tri-state rework: describe the per-row add/remove/unchanged control (tags and expected_*), the "Active on N items in current selection" annotation, and that manual `expected_*` values already present in the selection are now offered alongside vendor suggestions — while bulk-authoring brand-new manual values via free text remains deferred

## 10. Tri-state add/remove/unchanged controls for tags and expected_* rows

> Reworks Section 3's `tags_add`/`tags_remove` and Sections 4–5's `expected_*` suggestion mechanism per design.md Decision 9 (`/opsx:explore`, 2026-08-23): a per-row three-state control (add/remove/unchanged) replaces the independent multi-select/checkbox mechanism, `expected_*` gains a "Manual entry" group and, for the first time, a remove action. Tasks 3.1/3.3/4.1–4.4/5.3/5.5/5.6 remain historically accurate for what they built; this section supersedes their UI and top-level apply mechanism, not the underlying data-sourcing helpers (`vendor_scoped_suggestions_for_items`, `_merge_expected_entries`), which are extended rather than replaced.

- [ ] 10.1 Add a per-selected-items tag-activity helper (sibling to `vendor_scoped_suggestions_for_items`) returning, for every `Tag`, how many items in the working selection currently have it — one batched `Count`-over-selection query, not looped per tag
- [ ] 10.2 Add an expected-value-activity helper: for a given selection and `field_name`, fetch each selected item's stored `expected_product_line`/`expected_category` once and tally in Python how many items currently hold each distinct `(value, source)` pair — one query, bounded by selection size, not one per row; this same pass also yields the full "Manual entry" group (distinct `source: null` values present on ≥1 selected item, with their per-value counts)
- [ ] 10.3 Replace `tags_add`/`tags_remove` (two `ModelMultipleChoiceField`s) with one dynamically-built per-tag tri-state field per `Tag` in `Tag.objects.all()`, each a 3-way choice (add/remove/unchanged) defaulting to unchanged, name-spaced `tag_state:<tag.pk>`
- [ ] 10.4 Replace `expected_product_line_suggestions`/`expected_category_suggestions` (additive-only `MultipleChoiceField`s) with one dynamically-built per-row tri-state field per vendor-scoped suggestion row and per manual-entry row, name-spaced `expected_product_line_state:<encoded (source, value)>` / `expected_category_state:<encoded (source, value)>`, reusing the existing `json.dumps([source_key, value])` encoding (`source_key = None` for manual rows, round-tripping as JSON `null`)
- [ ] 10.5 Render each tri-state control as three Bootstrap toggle buttons (`.btn-check` + `.btn-outline-*` radios sharing one row's name) labeled `+`/`−`/`o`, with the active state visually highlighted via `:checked` styling — no custom JS required for mutual exclusivity or highlighting
- [ ] 10.6 Render the "(Active on N items in current selection)" annotation beneath each row from tasks 10.1/10.2, omitted entirely when N is 0
- [ ] 10.7 Add the "Manual entry" group to the workspace template (product-line and category each), alongside the existing vendor groups, using the same tri-state row rendering as 10.5–10.6
- [ ] 10.8 Rework `apply_bulk_edit`'s tag handling to parse the per-tag tri-state fields into add/remove sets (preserving today's `item.tags.add()`/`.remove()` apply semantics) instead of reading `tags_add`/`tags_remove` directly
- [ ] 10.9 Rework `apply_bulk_edit`'s expected-value handling to support remove: for a row set to "remove," strip the exact `(value, source)` entry from every selected item that has it (vendor-scoped: only items with that vendor's `ItemSource`; manual: every item in the selection); add a `_remove_expected_entries` helper (sibling to `_merge_expected_entries`) filtering by `(value, source)` inequality
- [ ] 10.10 Confirm idempotence: applying "add" to an item that already has the value, or "remove" to an item that doesn't, is a no-op (already true for tags via M2M add/remove semantics; true for expected_* via `_merge_expected_entries`'s pair-dedup and the new remove helper's pair-equality filter) — no new invariant to add, just verify by test (see 8.9/8.10)
