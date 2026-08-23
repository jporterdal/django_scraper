## ADDED Requirements

### Requirement: Item selection on the list page includes inactive items
The `view_terms` item list SHALL render a selection checkbox for every row, regardless of the item's `active` state. Selecting inactive items SHALL be possible so bulk activation/deactivation can be performed.

#### Scenario: Inactive item can be selected
- **WHEN** an operator views `view_terms` and an item's `active` is `False`
- **THEN** that item's row still renders a selection checkbox

#### Scenario: Existing price-update selection behavior is unaffected
- **WHEN** an operator checks a mix of active and inactive items and submits the existing "Update Selected" price-update action
- **THEN** only the active items among those checked are included in the price-update dispatch, exactly as before this change

### Requirement: A bulk-edit workspace is entered from a selection of items
`view_terms` SHALL offer a "Bulk Edit Selected" action that, given one or more checked item ids, opens a bulk-edit workspace scoped to exactly that set of items.

#### Scenario: Entering the workspace with a selection
- **WHEN** an operator checks two or more items on `view_terms` and submits "Bulk Edit Selected"
- **THEN** the bulk-edit workspace renders, scoped to exactly the checked items

#### Scenario: No items selected
- **WHEN** an operator submits "Bulk Edit Selected" with no items checked
- **THEN** no workspace is entered and a warning is shown, consistent with the existing "no items selected" handling for price-update selection

### Requirement: The bulk-edit workspace persists the working selection across multiple edit rounds
The working set of item ids SHALL be carried forward across every apply action within the workspace, without requiring the operator to re-select items from `view_terms`. The workspace SHALL only be exited, returning to `view_terms`, via an explicit "Done" action.

#### Scenario: Selection survives one apply round
- **WHEN** an operator applies a field change in the workspace
- **THEN** the workspace re-renders with the same working item selection (minus any items explicitly removed, see below), ready for another edit round

#### Scenario: Multiple sequential edits to the same selection
- **WHEN** an operator applies a priority change, then in a second round applies a tag change, to the same working selection without leaving the workspace
- **THEN** both changes are applied to the appropriate items and the working selection remains intact between rounds

#### Scenario: Done returns to the item list
- **WHEN** an operator clicks "Done" in the workspace
- **THEN** they are returned to `view_terms`

### Requirement: An operator can remove an individual item from the working selection
The workspace SHALL allow removing a single item from the current working selection without leaving the workspace or affecting the rest of the selection.

#### Scenario: Removing one item from the selection
- **WHEN** an operator is in the workspace with N items selected and removes one item via its row action
- **THEN** the working selection contains N-1 items, and subsequent apply rounds in this workspace only affect those N-1 items

### Requirement: Every bulk-editable field defaults to leaving the item's existing value unchanged
Each field offered in the bulk-edit form SHALL default to a "leave unchanged" state distinct from that field's normal blank, false, or empty value, so that a partial edit round only affects the fields the operator explicitly set.

#### Scenario: Applying one field leaves others untouched
- **WHEN** an operator sets only the `priority` field in an apply round, leaving `active`, `tags`, `metadata_provider_key`, `expected_product_line`, and `expected_category` at their "leave unchanged" defaults
- **THEN** only `priority` is modified on the affected items; every other field on every affected item retains its prior value

### Requirement: Priority is bulk-editable as a plain overwrite
The workspace SHALL offer a `priority` field that, when set away from "leave unchanged," overwrites `priority` on every item in the working selection.

#### Scenario: Bulk priority change
- **WHEN** an operator sets the bulk `priority` field to `A` and applies, with 10 items in the working selection
- **THEN** all 10 items have `priority` set to `A`

### Requirement: Active state is bulk-editable as a tri-state
The workspace SHALL offer an `active` control with three states: leave unchanged, activate, deactivate. This SHALL NOT be a plain checkbox, since a checkbox cannot represent "leave unchanged" independent of `active`'s own true/false values.

#### Scenario: Bulk deactivate
- **WHEN** an operator sets the bulk `active` control to "deactivate" and applies
- **THEN** every item in the working selection has `active` set to `False`, regardless of each item's prior state

#### Scenario: Bulk reactivate
- **WHEN** an operator sets the bulk `active` control to "activate" and applies
- **THEN** every item in the working selection has `active` set to `True`, regardless of each item's prior state

#### Scenario: Leaving active unchanged
- **WHEN** an operator leaves the bulk `active` control at its default and applies changes to other fields
- **THEN** no item's `active` value is modified

### Requirement: Tags are bulk-editable via one add/remove/unchanged control per tag, never a full replace
The workspace SHALL offer one control per `Tag` in the system, each presenting exactly three mutually exclusive states — add, remove, leave unchanged — defaulting to leave unchanged, so at most one of add/remove is ever active for a given tag at once. On apply, a tag set to add SHALL be added to every item in the working selection; a tag set to remove SHALL be removed from every item in the working selection; a tag left unchanged SHALL NOT be modified on any item. The workspace SHALL NOT offer a mechanism that replaces an item's entire tag set from one shared value.

> **Note:** this requirement originally specified two independent multi-select fields (tags to add, tags to remove). `/opsx:explore` (2026-08-23, design.md Decision 9) replaced that mechanism with one tri-state control per tag — the underlying add/remove/never-a-full-replace semantics are unchanged, only the control's shape is.

#### Scenario: Adding a tag preserves existing unrelated tags
- **WHEN** an item in the working selection already carries a tag not related to the bulk edit, and the operator sets a different tag's control to "add"
- **THEN** the item ends up with both its pre-existing tag and the newly added tag

#### Scenario: Removing a tag only removes the specified tag
- **WHEN** an item in the working selection carries two tags, and the operator sets one of them to "remove"
- **THEN** only the specified tag is removed from that item; the other tag remains

#### Scenario: Add and remove are idempotent
- **WHEN** a tag's control is set to "add" and applied against an item that already has that tag, or set to "remove" and applied against an item that never had that tag
- **THEN** that item's tags are unaffected — no error, no duplicate, no-op

### Requirement: Each per-row tri-state control shows how many eligible selected items currently hold that value
For every row offered by a tri-state control — a tag, a vendor-scoped expected value, a manual expected value, or a Source-scoped include/exclude search pattern — the workspace SHALL display how many items in the current working selection already have that value against how many selected items could possibly have it, worded "Active on X / Y items," whenever X is at least 1. When X is 0, no such annotation SHALL be shown for that row.

The denominator Y is the row's own eligible scope, not always the whole selection: for a row scoped to a vendor or Source (a vendor-scoped expected-value row, or a Source-scoped search-pattern row), Y is the count of selected items configured for that vendor/Source — the same count already shown as that row's group heading. For a row with no such scoping (a tag row, or a "Manual entry" expected-value row), Y is the size of the whole working selection, since any selected item could hold that value.

> **Note:** this requirement originally worded the annotation "Active on N items in current selection" with no denominator. `/opsx:apply` (2026-08-23) changed it to "Active on X / Y items" per user request, adding the eligible-scope denominator; the underlying counting logic for X (and its 0-count omission rule) is unchanged.

#### Scenario: Tag annotation reflects the current selection, not every item in the system
- **WHEN** a tag is applied to 6 items total in the system, 4 of which are in the current working selection of 10
- **THEN** that tag's row shows "Active on 4 / 10 items"

#### Scenario: Expected-value annotation reflects items holding that exact value, scoped to the vendor's own item count
- **WHEN** 8 of 20 selected items have vendor `wt` configured, but only 3 of those 8 currently have `{"value": "Gadgets", "source": "wt"}` stored
- **THEN** the `wt`/`Gadgets` row's annotation reads "Active on 3 / 8 items", distinct from the `wt` group's own "8 of 20 selected items have this vendor configured" heading

#### Scenario: Manual-entry and tag rows use the whole selection as their denominator
- **WHEN** a "Manual entry" expected-value row or a tag row is evaluated against a working selection of 10 items, with no vendor/Source scoping narrowing which items are eligible
- **THEN** that row's denominator is 10, the full selection size

#### Scenario: Search-pattern annotation is scoped to the Source's own item count
- **WHEN** a Source-scoped Include/Exclude pattern row is evaluated against a working selection where 8 of 20 items have that Source's `ItemSource` configured, and 5 of those 8 currently have the pattern
- **THEN** that row's annotation reads "Active on 5 / 8 items"

#### Scenario: Annotation omitted when no selected item currently has the value
- **WHEN** a tag or a vendor-scoped expected value is not currently present on any item in the working selection
- **THEN** that row shows no "Active on..." annotation

### Requirement: The metadata provider is bulk-editable and routes through the existing refresh entrypoint
The workspace SHALL offer a `metadata_provider_key` control with three states: leave unchanged, set to a specific registry key, or clear. For every item whose `metadata_provider_key` actually changes as a result of an apply round, the change SHALL be applied through the same shared refresh entrypoint used by individual item creation, bulk item creation, and individual item editing (see the `item-metadata-enrichment` capability's single-entrypoint requirement), so that the existing provider-change reset behavior applies uniformly.

#### Scenario: Bulk-setting a provider enqueues refreshes through the shared entrypoint
- **WHEN** an operator sets the bulk `metadata_provider_key` control to a registered provider key and applies, across 5 items that previously had no provider set
- **THEN** each of the 5 items has `metadata_provider_key` updated and a metadata refresh is requested for each through the shared entrypoint, exactly as an equivalent single-item edit would

#### Scenario: Bulk-clearing a provider resets fetched state
- **WHEN** an operator sets the bulk `metadata_provider_key` control to "clear" and applies, across items that had a provider and existing fetched `ItemMetadata`
- **THEN** each affected item's `metadata_provider_key` is cleared and its `ItemMetadata` is reset (payload/external_id/pinned_external_id cleared, status back to unfetched) via the shared entrypoint's existing reset behavior, with no refresh enqueued for the cleared items

#### Scenario: Leaving the provider unchanged does not touch metadata state
- **WHEN** an operator leaves the bulk `metadata_provider_key` control at its default
- **THEN** no item's `metadata_provider_key` or `ItemMetadata` is modified, and no refresh is requested

### Requirement: Expected product-line and category rows are grouped by vendor, plus a manual-entry group, across the selection
For the working selection, the workspace SHALL compute the set of vendors present via any selected item's configured `ItemSource`s, with no minimum-shared-item threshold — a vendor present on even one selected item SHALL get its own group. Each vendor's group SHALL be labeled with how many of the working selection's items have that vendor configured, and SHALL offer one row per value sourced from `ObservedCategoryValue` (the `item-category-relevance` capability's data source) scoped to that vendor, separately for `expected_product_line` and `expected_category`. In addition, the workspace SHALL offer a "Manual entry" group — one row per distinct value stored with `source: null` on at least one item in the working selection, sourced from the selected items' own stored `expected_product_line`/`expected_category`, not from `ObservedCategoryValue`.

> **Note:** the "Manual entry" group is new as of `/opsx:explore` (2026-08-23, design.md Decision 9); the vendor-grouping behavior below is unchanged from this capability's original implementation.

#### Scenario: A vendor used by one item still gets a group
- **WHEN** the working selection has 20 items and exactly 1 of them has `ItemSource` configured against vendor `coolstuff`
- **THEN** a suggestion group for `coolstuff` is shown, labeled to indicate it applies to 1 of the 20 selected items

#### Scenario: Suggestion values are sourced per vendor, not per item
- **WHEN** vendor `wt` has `ObservedCategoryValue` rows for `field_name="product_line"` with values `"Magic"` and `"Pokemon"`
- **THEN** the `wt` group's `expected_product_line` suggestions include both values, regardless of which specific selected items' own prior fetches produced them

#### Scenario: A manual value present on any selected item gets its own Manual entry row
- **WHEN** at least one item in the working selection has `{"value": "Foil", "source": null}` in its `expected_category`
- **THEN** a "Manual entry" group row for `"Foil"` is shown for `expected_category`

#### Scenario: A manual value absent from every selected item is not offered
- **WHEN** no item in the working selection has any `source: null` entry with the value `"Reprint"` in `expected_category`
- **THEN** no "Manual entry" row for `"Reprint"` is shown, even if some other, non-selected item in the system has it

### Requirement: Expected product-line/category rows apply as add/remove via a tri-state control
Each row offered under a vendor group or the "Manual entry" group SHALL present the same three mutually exclusive states as tag rows — add, remove, leave unchanged — defaulting to leave unchanged.

On apply: a vendor-scoped row set to add SHALL have its value added, as a `{"value", "source"}` entry with `source` set to that row's vendor key, only to items in the working selection that have that vendor's `ItemSource` configured; a manual row set to add SHALL have its value added, as a `{"value", "source": null}` entry, to every item in the working selection. A vendor-scoped row set to remove SHALL have that exact `(value, source)` pair removed from every item in the working selection that currently has it and has that vendor's `ItemSource` configured; a manual row set to remove SHALL have its `{"value", "source": null}` entry removed from every item in the working selection that currently has it. A row left unchanged SHALL NOT modify that value on any item. In every case the value SHALL be merged into or removed from each affected item's existing list — an item's other `expected_product_line`/`expected_category` entries SHALL never be replaced or discarded as a side effect.

> **Note:** this requirement originally specified an additive-only checkbox with deduplication by exact string equality (pre-`expected-value-vendor-provenance`), then deduplication by exact `(value, source)` pair equality with add as the only action (post-`expected-value-vendor-provenance`, `tasks.md` task 5.6). `/opsx:explore` (2026-08-23, design.md Decision 9) adds the remove action and the manual-entry case, now that `(value, source)`-pair storage makes both well-defined.

#### Scenario: Add applies only to items with the matching vendor
- **WHEN** the working selection has 20 items, 8 of which have vendor `wt` configured, and the operator sets a `wt`-scoped `expected_product_line` row to "add" and applies
- **THEN** exactly those 8 items have the value added to `expected_product_line`; the other 12 items are unmodified by this row

#### Scenario: Remove strips only the exact (value, source) pair from items that currently have it
- **WHEN** the working selection has 20 items, 8 of which have vendor `wt` configured and currently store `{"value": "Gadgets", "source": "wt"}`, and the operator sets that row to "remove" and applies
- **THEN** those 8 items no longer have that entry in `expected_product_line`; every other entry on those items, and every other item in the selection, is unaffected

#### Scenario: Manual-entry add/remove affects every item in the selection, regardless of vendor configuration
- **WHEN** the operator sets a "Manual entry" row to "add" and applies, across a selection where the selected items have varying (or no) `ItemSource` configuration
- **THEN** every item in the working selection has that value added as a `{"value", "source": null}` entry, unaffected by which vendors, if any, each item has configured

#### Scenario: Applying a row preserves an item's existing expected values
- **WHEN** an item already has `expected_product_line` containing a manually-entered value unrelated to the row being applied, and a vendor-scoped row is set to "add" and applied to that item
- **THEN** the item's `expected_product_line` contains both the pre-existing manual value and the newly added value

#### Scenario: Re-applying the same add across rounds does not duplicate it
- **WHEN** an item already has a given value in `expected_category` from an earlier apply round, and the same row is set to "add" and applied again in a later round
- **THEN** the item's `expected_category` still contains that value exactly once

#### Scenario: Add and remove are idempotent
- **WHEN** a row is set to "add" and applied against an item that already has that exact `(value, source)` entry, or set to "remove" and applied against an item that never had it
- **THEN** that item's `expected_product_line`/`expected_category` is unaffected — no error, no duplicate, no-op

### Requirement: Bulk apply attempts every selected item independently
For a given apply round, the workspace SHALL attempt to apply the round's field changes to every item in the working selection, even if applying to one item fails. A failure on one item SHALL NOT prevent the remaining items in the same round from being attempted. Each item's outcome SHALL be reported individually.

#### Scenario: One item's failure does not block the rest of the batch
- **WHEN** an apply round targets 10 items and applying the round's changes to item 3 fails (e.g. a validation error)
- **THEN** items 1, 2, and 4 through 10 still have the round's changes applied, and the failure for item 3 is reported alongside the successes

#### Scenario: Per-item failures are individually reported
- **WHEN** an apply round results in failures on more than one item
- **THEN** each failed item's identity and failure reason are shown, distinct from the items that succeeded

### Requirement: Concurrent modification of a selected item during a bulk-edit session is not detected or handled
This capability SHALL NOT detect or specially handle the case where an item in the working selection is deleted or otherwise modified by another user or process between being selected and a later apply round in the same workspace session. Multi-user interaction is out of scope for this capability.

#### Scenario: An apply round proceeds without concurrency detection
- **WHEN** an item in the working selection has been modified by another process since it was selected, and an apply round runs against the working selection including that item
- **THEN** the apply round proceeds using the current per-item application logic with no concurrency check specific to this capability; the resulting behavior is whatever the ordinary per-item save path produces, not a specially-detected conflict

### Requirement: Include/exclude search patterns are grouped by Source across the selection, by union
For the working selection, the workspace SHALL compute the set of `Source`s present via any selected item's configured `ItemSource`, with no minimum-shared-item threshold — a `Source` present on even one selected item SHALL get its own group. Each group SHALL be labeled with the `Source`'s `name` and how many of the working selection's items have that `Source` configured. Each group SHALL offer two subsections, Include and Exclude, each listing one row per distinct pattern present in that field (`title_include_patterns`/`title_exclude_patterns`) on any selected item's `ItemSource` for that `Source`. A subsection with no such patterns SHALL display an explicit "No patterns defined" note rather than rendering empty.

#### Scenario: A Source used by one item still gets a group
- **WHEN** the working selection has 20 items and exactly 1 of them has an `ItemSource` configured against Source `amazon`
- **THEN** a Search-patterns group for `amazon` is shown, labeled with its name and indicating it applies to 1 of the 20 selected items

#### Scenario: Pattern rows are sourced from the selection's own ItemSource rows, not a separate taxonomy
- **WHEN** two different items in the working selection each have an `ItemSource` for Source `cc`, one with `title_include_patterns` containing `"Foil"` and the other with `"Booster"`
- **THEN** the `cc` group's Include subsection offers rows for both `"Foil"` and `"Booster"`

#### Scenario: An empty subsection is announced explicitly
- **WHEN** a Source group's Exclude subsection has no patterns configured on any selected item's `ItemSource` for that Source
- **THEN** that subsection displays "No patterns defined" instead of rendering with no rows and no message

### Requirement: Include/exclude search pattern rows apply as add/remove via a tri-state control, scoped to items with a matching Source
Each row offered under a Source group's Include or Exclude subsection SHALL present the same three mutually exclusive states as tag and expected-value rows — add, remove, leave unchanged — defaulting to leave unchanged. On apply, a row set to add or remove SHALL only affect items in the working selection that have that Source's `ItemSource` configured; items without it SHALL be unaffected by that row and SHALL NOT have an `ItemSource` created as a side effect. Add SHALL append the pattern to the affected `ItemSource`'s corresponding field if not already present; remove SHALL strip it if present. An item's other patterns in that field, and its patterns in the other field, SHALL never be replaced or discarded as a side effect.

#### Scenario: Add applies only to items with the matching Source
- **WHEN** the working selection has 20 items, 8 of which have Source `wt` configured, and the operator sets a `wt` Include-pattern row to "add" and applies
- **THEN** exactly those 8 items' `ItemSource` rows for `wt` have the pattern added to `title_include_patterns`; the other 12 items are unaffected by this row

#### Scenario: Remove strips only the specified pattern from items that have it
- **WHEN** 8 selected items have Source `wt` configured with `"Used"` in `title_exclude_patterns`, and the operator sets that row to "remove" and applies
- **THEN** those 8 items' `wt` `ItemSource` rows no longer have `"Used"` in `title_exclude_patterns`; every other pattern on those rows, and every other item, is unaffected

#### Scenario: Applying a row preserves an item's other existing patterns
- **WHEN** an item's `ItemSource` for a Source already has an unrelated pattern in `title_include_patterns`, and a different Include-pattern row for that Source is set to "add" and applied to that item
- **THEN** the item's `title_include_patterns` contains both the pre-existing pattern and the newly added one

#### Scenario: Add and remove are idempotent
- **WHEN** a row is set to "add" and applied against an `ItemSource` that already has that exact pattern, or set to "remove" and applied against one that never had it
- **THEN** that `ItemSource`'s patterns are unaffected — no error, no duplicate, no-op

### Requirement: New include/exclude search patterns can be authored via free text, scoped to items with a matching Source
Each Source group's Include and Exclude subsection SHALL offer a free-text control (one pattern per line) for adding patterns not already present on any selected item, in addition to the tri-state rows over existing patterns. Each line SHALL be validated as a regular expression; an invalid pattern SHALL be reported back to the operator without being applied. On apply, every valid line SHALL be added to the corresponding field of every item in the working selection that has that Source's `ItemSource` configured, using the same add semantics (append-if-absent) as the tri-state add action. This control is intentionally offered for include/exclude search patterns despite the equivalent being out of scope for `expected_product_line`/`expected_category` (see that capability's free-text Non-Goal) — search patterns have no system-observed suggestion source to fall back on.

#### Scenario: A newly typed pattern is added only to items with the matching Source
- **WHEN** the working selection has 20 items, 5 of which have Source `cc` configured, and the operator types a new pattern into `cc`'s Include free-text field and applies
- **THEN** exactly those 5 items' `cc` `ItemSource` rows have the new pattern added to `title_include_patterns`; the other 15 items are unaffected

#### Scenario: An invalid regex is rejected without being applied
- **WHEN** an operator types a syntactically invalid regular expression into a Source's free-text pattern field and applies
- **THEN** no item's patterns are modified as a result of that field, and the invalid pattern is reported back to the operator

#### Scenario: Free-text add is idempotent with existing patterns
- **WHEN** an operator types a pattern into a Source's free-text field that some selected items' matching `ItemSource` already has
- **THEN** those items' patterns are unaffected by the free-text add — no duplicate is created
