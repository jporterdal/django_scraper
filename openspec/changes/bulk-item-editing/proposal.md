## Why

Operators managing dozens or hundreds of tracked items currently have no way to change a field — priority, active state, tags, metadata provider, or expected product-line/category — across more than one item at a time; every edit goes through the single-item form. The archived `item-metadata-enrichment` change explicitly deferred this ("bulk-editing of any field across multiple existing items") and pointed at the `item_ids` checkbox / `mode=selected` plumbing already on `view_terms` (`UpdateFromWebView`) as reusable groundwork. This change builds that feature on top of that groundwork.

## What Changes

- Widen the item-row checkbox on `view_terms` (`searchableitem_list.html`) to render for inactive rows too, not just active ones, so bulk reactivate/deactivate is possible. `UpdateFromWebView`'s own `mode=selected` handling is unaffected — it already re-filters to `active=True` server-side.
- Add a "Bulk Edit Selected" action that POSTs the checked `item_ids` into a new, persistent bulk-edit workspace (`BulkEditItemsView`) rather than a one-shot form — the workspace survives multiple sequential partial edits to the same selection, since re-selecting items is the most tedious part of the flow and must not be discarded after a single edit.
- The workspace's working `item_ids` set is carried forward as hidden POST fields across every Apply (not a URL/session token), with a scrollable item roster (text, active badge, tags, priority) supporting a per-row "remove from this selection" action, and an explicit "Done" action as the only way back to `view_terms`.
- A single combined edit form where every field defaults to "leave unchanged" (a real sentinel, not each field's normal blank/false value), covering:
  - `priority` — overwrite
  - `active` — tri-state (leave / activate / deactivate)
  - `tags` — one add/remove/leave-unchanged control per tag (never a full replace); each row annotated with how many of the current selection already have that tag, when at least one does
  - `metadata_provider_key` — leave / set / clear, routed per item through the existing `request_metadata_refresh` entrypoint from `item-metadata-enrichment` so the provider-change reset behavior is reused, not reimplemented
  - `expected_product_line` / `expected_category` — one add/remove/leave-unchanged control per row, grouped by every vendor present on at least one selected item (each group labeled with its subset count, e.g. "8 of 20") plus a "Manual entry" group for `source: null` values already present in the selection; add applies only to items with the row's vendor configured (or every item, for a manual row), remove strips the exact stored `(value, source)` pair from items that currently have it; each row annotated with how many of the current selection already have that exact value, when at least one does. **Note (`/opsx:explore`, 2026-08-23):** initially shipped additive-only, drawing vendor suggestions from `ObservedCategoryValue` only; reworked to the tri-state control described here once `expected-value-vendor-provenance`'s `(value, source)`-pair storage landed and made a well-defined remove action possible.
- Each Apply is best-effort per item: every item in the working selection is attempted regardless of whether another item in the same round failed, with errors reported per item rather than aborting the whole batch.
- Document, but do not solve, the multi-user/concurrent-edit case (an item mutated or deleted by someone else mid-session) as an explicit known limitation for future revisit — mirroring how `item-metadata-enrichment` documented its own deferred scope.
- Explicitly out of scope: bulk-editing `text`; **creating brand-new** manual `expected_product_line`/`expected_category` values via free text in bulk mode (existing manual values are in scope — see above); any audit/undo log of bulk changes; a periodic/scheduled bulk operation.

## Capabilities

### New Capabilities
- `bulk-item-editing`: Operator-triggered bulk editing of existing `SearchableItem`s across a persistent, multi-round selection workspace — selection widening, the leave-unchanged sentinel form pattern, per-field apply semantics (overwrite/tri-state/add-remove/vendor-scoped-additive), per-row selection editing, per-item best-effort error handling, and the documented concurrency non-goal.

### Modified Capabilities
(none — this change references `item-metadata-enrichment`'s `request_metadata_refresh` entrypoint and `item-category-relevance`'s `ObservedCategoryValue` model as dependencies, but does not change either capability's existing requirements)

## Impact

- **Views** (`tracking/views.py`): new `BulkEditItemsView` (workspace GET/POST handling, per-round apply, per-row selection removal, Done); `searchableitem_list.html` checkbox widened to all rows; new "Bulk Edit Selected" button wired alongside the existing selection actions.
- **Forms** (`tracking/forms.py`): new bulk-edit form(s) implementing the leave-unchanged sentinel per field; a new vendor-scoped suggestion-choice helper (sibling to `observed_values_for_item`, scoped to the union of vendors across a selection rather than one item's sources); dynamically-built per-row tri-state fields for tags and `expected_*` rows (design.md Decision 9); a `_remove_expected_entries` helper (sibling to `_merge_expected_entries`) for the new remove action.
- **Models** (`tracking/models.py`): a new helper for vendor-scoped `ObservedCategoryValue` lookup with per-vendor item-count annotation across a selection; a new per-tag activity-count helper and a new expected-value activity/manual-entry helper (design.md Decision 9); no schema/migration changes.
- **New template**: a bulk-edit workspace template, reusing the existing scrollable-panel and Bootstrap form-class patterns already used by the single-item suggestion checkboxes.
- **Docs**: `README.md` gains a section analogous to the metadata-provider one, including the documented multi-user/concurrency limitation.
- **No changes** to `tracking/tasks.py`, `tracking/ratelimit/`, `tracking/scrape.py`, or any vendor-fetch/parsing code; no changes to the `item-metadata-enrichment` or `item-category-relevance` specs.
