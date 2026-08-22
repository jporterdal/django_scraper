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

### Requirement: Tags are bulk-editable as independent add and remove sets, never a full replace
The workspace SHALL offer two independent tag selections — tags to add and tags to remove — applied respectively as additions to and removals from each affected item's existing `tags`. The workspace SHALL NOT offer a mechanism that replaces an item's entire tag set from one shared value.

#### Scenario: Adding a tag preserves existing unrelated tags
- **WHEN** an item in the working selection already carries a tag not related to the bulk edit, and the operator adds a different tag via the bulk "tags to add" selection
- **THEN** the item ends up with both its pre-existing tag and the newly added tag

#### Scenario: Removing a tag only removes the specified tag
- **WHEN** an item in the working selection carries two tags, and the operator selects one of them in "tags to remove"
- **THEN** only the specified tag is removed from that item; the other tag remains

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

### Requirement: Expected product-line and category suggestions are grouped by vendor across the selection
For the working selection, the workspace SHALL compute the set of vendors present via any selected item's configured `ItemSource`s, with no minimum-shared-item threshold — a vendor present on even one selected item SHALL get its own suggestion group. Each vendor's group SHALL be labeled with how many of the working selection's items have that vendor configured, and SHALL offer suggestion checkboxes sourced from `ObservedCategoryValue` (the `item-category-relevance` capability's data source) scoped to that vendor, separately for `expected_product_line` and `expected_category`.

#### Scenario: A vendor used by one item still gets a group
- **WHEN** the working selection has 20 items and exactly 1 of them has `ItemSource` configured against vendor `coolstuff`
- **THEN** a suggestion group for `coolstuff` is shown, labeled to indicate it applies to 1 of the 20 selected items

#### Scenario: Suggestion values are sourced per vendor, not per item
- **WHEN** vendor `wt` has `ObservedCategoryValue` rows for `field_name="product_line"` with values `"Magic"` and `"Pokemon"`
- **THEN** the `wt` group's `expected_product_line` suggestions include both values, regardless of which specific selected items' own prior fetches produced them

### Requirement: Applying an expected product-line or category suggestion affects only the matching vendor subset
When an operator checks a vendor-scoped `expected_product_line` or `expected_category` suggestion and applies, the value SHALL be added only to the items in the working selection that have that suggestion's vendor configured via `ItemSource`; other items in the selection SHALL be unaffected by that checkbox. The value SHALL be merged into each affected item's existing list and deduplicated by exact string equality, never replacing the item's existing list.

#### Scenario: Suggestion applies only to items with the matching vendor
- **WHEN** the working selection has 20 items, 8 of which have vendor `wt` configured, and the operator checks a `wt`-scoped `expected_product_line` suggestion and applies
- **THEN** exactly those 8 items have the value added to `expected_product_line`; the other 12 items are unmodified by this checkbox

#### Scenario: Applying a suggestion preserves an item's existing expected values
- **WHEN** an item already has `expected_product_line` containing a manually-entered value unrelated to any vendor suggestion, and a vendor-scoped suggestion is applied to that item
- **THEN** the item's `expected_product_line` contains both the pre-existing manual value and the newly added suggestion value

#### Scenario: Checking the same suggestion twice across rounds does not duplicate it
- **WHEN** an item already has a given value in `expected_category` from an earlier apply round, and the same vendor-scoped suggestion is checked and applied again in a later round
- **THEN** the item's `expected_category` still contains that value exactly once

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
