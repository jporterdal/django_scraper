## ADDED Requirements

### Requirement: SearchableItem may specify a single metadata provider
`SearchableItem` SHALL have an optional `metadata_provider_key` field, blank by default, whose non-blank values are restricted to keys registered in the metadata-provider code registry. At most one provider SHALL be associated with an item at a time; the field SHALL be presented as a single-select, never as a multi-select or set of checkboxes.

#### Scenario: Item has no provider by default
- **WHEN** a `SearchableItem` is created without specifying a metadata provider
- **THEN** `metadata_provider_key` is blank and no metadata is fetched for the item

#### Scenario: Operator selects a registered provider
- **WHEN** an operator sets an item's `metadata_provider_key` to a key registered in the provider registry (e.g. `"scryfall"`)
- **THEN** the value is stored on the item and a metadata refresh is requested (see "A single entrypoint enqueues metadata refresh requests")

#### Scenario: Provider selection is single-valued
- **WHEN** an operator edits an item's metadata provider
- **THEN** the UI offers a single-select control, and the stored value is at most one provider key, never a list

### Requirement: Metadata providers are resolved from a pure code registry
Metadata providers SHALL be registered in a code-only registry (a key-to-implementation mapping shipped with the application code), not a database-backed model. Provider selection choices offered in the UI SHALL be populated live from this registry's keys.

#### Scenario: Registry provides form choices
- **WHEN** an operator opens an item create or edit form
- **THEN** the metadata-provider select field's choices are exactly the keys currently registered in the provider registry

#### Scenario: No database table exists for provider configuration
- **WHEN** a metadata provider is added or changed
- **THEN** it is done by registering or editing a code-level entry, not by creating or editing a database row

### Requirement: ItemMetadata tracks per-item resolution and fetch state
Each `SearchableItem` with a non-blank `metadata_provider_key` SHALL have at most one associated `ItemMetadata` record, created no later than when the first refresh for that item is processed. `ItemMetadata` SHALL hold: a `status` (one of `unfetched`, `pending`, `matched`, `needs_review`, `no_match`, `error`), an `external_id` (the provider's resolved identifier, if any), a `pinned_external_id` (operator-set manual override, if any), an opaque `payload` (raw data as returned by the provider), and `fetched_at`.

#### Scenario: New ItemMetadata starts unfetched
- **WHEN** an item's `metadata_provider_key` is set for the first time
- **THEN** its `ItemMetadata` record (created if absent) has `status` `unfetched` until a fetch is processed

#### Scenario: Payload is opaque to the core application
- **WHEN** an `ItemMetadata.payload` is stored
- **THEN** its internal shape is defined entirely by the owning provider and is not interpreted, validated, or restricted by core application code

### Requirement: Providers resolve an item to a match, review candidates, or no match
Each registered provider SHALL implement a resolution operation that, given an item, returns exactly one of: a single confident match (external identifier plus payload), a list of zero or more review candidates when automatic resolution is not confident, or an explicit no-match outcome. The core application SHALL NOT assume resolution is usually unambiguous; the review-candidates outcome SHALL be handled as ordinary control flow, not an exceptional case.

#### Scenario: Confident match sets status matched
- **WHEN** a provider's resolution for an item returns a single confident match
- **THEN** the item's `ItemMetadata.status` becomes `matched`, `external_id` and `payload` are stored, and `fetched_at` is updated

#### Scenario: Ambiguous resolution sets status needs_review
- **WHEN** a provider's resolution for an item returns two or more candidates with no single confident match
- **THEN** the item's `ItemMetadata.status` becomes `needs_review` and the candidates are made available to the disambiguation UI

#### Scenario: No candidates found sets status no_match
- **WHEN** a provider's resolution for an item returns zero candidates
- **THEN** the item's `ItemMetadata.status` becomes `no_match`

### Requirement: A generic three-slot contract renders matched and candidate metadata
Every provider SHALL expose a mapping from its raw `payload` to exactly three display fields: `thumbnail_url`, `description`, and `external_url`. This mapping SHALL be computed at render time from the stored `payload`, not stored as separate denormalized columns. Generic templates SHALL render only these three fields for any provider and SHALL NOT contain provider-specific field names. The same three-slot mapping SHALL be used both for a confirmed match and for each individual disambiguation candidate.

#### Scenario: Matched item renders its three slots
- **WHEN** an item's `ItemMetadata.status` is `matched`
- **THEN** the item detail page renders that provider's mapped `thumbnail_url`, `description`, and `external_url` for the stored `payload`, and no other provider-specific fields

#### Scenario: Candidate list uses the same three-slot mapping
- **WHEN** an item's `ItemMetadata.status` is `needs_review` and candidates are displayed
- **THEN** each candidate is rendered using the identical three-slot mapping (its own `thumbnail_url`, `description`, `external_url`) used for a confirmed match

#### Scenario: Changing a provider's display mapping affects existing items immediately
- **WHEN** a provider's `to_display` mapping logic changes and an item already has a stored `payload` from a previous fetch
- **THEN** the item's rendered display reflects the new mapping without requiring a re-fetch, because the mapping is computed at render time

### Requirement: Operators resolve ambiguous or failed matches via a generic disambiguation UI
When an item's `ItemMetadata.status` is `needs_review`, the item detail page SHALL present the returned candidates (via the three-slot contract) and allow the operator to select one, setting it as `pinned_external_id`. The UI SHALL also allow manual entry of an external identifier as a fallback, including when `status` is `no_match`. This UI mechanism SHALL be identical regardless of which provider is configured for the item.

#### Scenario: Operator selects a candidate
- **WHEN** an item's `ItemMetadata.status` is `needs_review` and the operator selects one of the displayed candidates
- **THEN** that candidate's external identifier is stored as `pinned_external_id`, its payload is stored, and `status` becomes `matched`

#### Scenario: Operator manually enters an external identifier
- **WHEN** an item's `ItemMetadata.status` is `no_match` (or `needs_review`, if no candidate is correct) and the operator manually enters an external identifier
- **THEN** that identifier is stored as `pinned_external_id` and a fetch for that specific identifier is requested

### Requirement: A pinned external identifier is sticky and authoritative
`ItemMetadata.pinned_external_id`, once set, SHALL be treated as an explicit, permanent operator override: it SHALL NOT be overwritten by automatic re-resolution, and it SHALL exempt the item from the text-change re-fetch trigger described below. It SHALL only be cleared when the item's `metadata_provider_key` changes (see "Changing an item's metadata provider resets its fetched state").

#### Scenario: Pinned match survives a text edit
- **WHEN** an item has a `pinned_external_id` set and the operator edits the item's `text`
- **THEN** no metadata refresh is requested for that item, and the existing pinned match remains in place

#### Scenario: Pinned match is not replaced by automatic resolution
- **WHEN** an item already has a `pinned_external_id` set
- **THEN** no automated process overwrites it with a different externally-resolved match

### Requirement: A single entrypoint enqueues metadata refresh requests
All requests to (re-)fetch an item's metadata SHALL be enqueued through one shared function, called from every code path that can create or update a `SearchableItem`'s `metadata_provider_key` or `text` (individual item creation, bulk item creation, and item editing), and from the manual retry action. No code path SHALL enqueue a refresh request by any other means.

#### Scenario: Setting a provider on a newly created item enqueues a refresh
- **WHEN** an operator creates a new item (individually or as part of a bulk-add batch) with a non-blank `metadata_provider_key`
- **THEN** a metadata refresh request is enqueued for that item through the shared entrypoint

#### Scenario: Changing text on an unpinned matched item enqueues a refresh
- **WHEN** an operator edits the `text` of an item whose `metadata_provider_key` is non-blank and which has no `pinned_external_id`
- **THEN** a metadata refresh request is enqueued for that item

#### Scenario: Editing unrelated fields does not enqueue a refresh
- **WHEN** an operator edits an item's `priority`, `active`, or `tags` without changing `text` or `metadata_provider_key`
- **THEN** no metadata refresh request is enqueued

### Requirement: Changing an item's metadata provider resets its fetched state
When an item's `metadata_provider_key` changes to a different value or is cleared, its `ItemMetadata` record SHALL have `payload`, `external_id`, and `pinned_external_id` cleared and `status` reset to `unfetched` before any new refresh is requested for the new provider (if any).

#### Scenario: Switching providers clears the old payload
- **WHEN** an item's `metadata_provider_key` changes from one registered provider to another
- **THEN** the item's existing `payload`, `external_id`, and `pinned_external_id` are cleared, `status` becomes `unfetched`, and a refresh request is enqueued for the new provider

#### Scenario: Clearing the provider clears fetched state without enqueuing a refresh
- **WHEN** an item's `metadata_provider_key` is cleared to blank
- **THEN** the item's existing `payload`, `external_id`, and `pinned_external_id` are cleared, `status` becomes `unfetched`, and no refresh request is enqueued

### Requirement: Refresh requests are drained at a bounded rate by a periodic task
Enqueued metadata refresh requests SHALL be processed by a periodic background task that wakes on a fixed cadence and drains a bounded number of pending requests per wake, rather than being dispatched immediately and unboundedly at enqueue time. This mechanism SHALL be independent of the vendor rate-limit/budget system used for price-search pacing.

#### Scenario: Bulk creation does not dispatch fetches immediately
- **WHEN** a bulk-add batch creates many items with the same non-blank `metadata_provider_key` in one submission
- **THEN** refresh requests for all of them are enqueued immediately, but the corresponding fetches are dispatched only as the periodic task drains the queue over subsequent wakes, bounded per wake

#### Scenario: Periodic task processes pending requests
- **WHEN** the periodic drain task wakes and pending refresh requests exist
- **THEN** it dispatches fetches for up to its per-wake limit of pending requests and leaves any remainder queued for the next wake

### Requirement: A failed fetch is marked and does not automatically retry
When a metadata fetch fails (e.g. a network or provider error), the item's `ItemMetadata.status` SHALL be set to `error` and no automatic retry SHALL be scheduled. Recovery SHALL occur only via the manual retry action, a subsequent `text` change (if unpinned), or a `metadata_provider_key` change.

#### Scenario: Fetch failure sets error status without retrying
- **WHEN** a dispatched metadata fetch for an item fails
- **THEN** `ItemMetadata.status` becomes `error` and no further automatic fetch attempt is scheduled for that item

### Requirement: Operators can manually retry a failed or unmatched fetch
The item detail page SHALL offer an explicit action to retry a metadata fetch, available at least when `ItemMetadata.status` is `error` or `no_match`. Triggering it SHALL enqueue a refresh request through the shared entrypoint.

#### Scenario: Operator retries an errored fetch
- **WHEN** an item's `ItemMetadata.status` is `error` and the operator triggers the retry action
- **THEN** a metadata refresh request is enqueued for that item through the shared entrypoint

### Requirement: The item detail page warns when a pending fetch may not be processed
When an item's `ItemMetadata.status` is `unfetched` or `pending` and server settings do not guarantee a live background consumer is processing the refresh queue, the item detail page SHALL display a warning that the fetch may not complete without a separate background worker process running.

#### Scenario: Warning shown under default dev/test settings
- **WHEN** an item has a pending or unfetched metadata fetch and the server is running under settings equivalent to the default dev/test configuration (no live consumer guaranteed)
- **THEN** the item detail page displays a warning that metadata may not be fetched without a separate background worker process running

#### Scenario: Warning hidden when fetches are expected to be processed
- **WHEN** an item has a pending or unfetched metadata fetch and the server is running under settings that do not trigger the "may not be processed" heuristic
- **THEN** the item detail page does not display the warning

### Requirement: The item list page displays a thumbnail without introducing per-row queries
The item list page SHALL display each item's `thumbnail_url` (when available from a `matched` `ItemMetadata`) inline next to its search-term text, sized to fit reasonably within the table. Retrieving this data for the full list SHALL use eager loading (e.g. a single joined/select-related query) rather than one query per item.

#### Scenario: Matched item shows a thumbnail in the list
- **WHEN** the item list page renders a row for an item with `ItemMetadata.status` `matched` and a non-empty mapped `thumbnail_url`
- **THEN** that thumbnail image is displayed inline next to the item's search-term text, constrained to a reasonable size

#### Scenario: Item without matched metadata shows no thumbnail
- **WHEN** the item list page renders a row for an item with no configured provider, or whose `ItemMetadata.status` is not `matched`
- **THEN** no thumbnail is displayed for that row, and the row layout is otherwise unaffected

#### Scenario: Listing many items does not scale query count with row count
- **WHEN** the item list page renders a page containing many items with configured providers
- **THEN** the number of database queries used to retrieve their metadata for thumbnail display does not grow linearly with the number of items on the page

### Requirement: Metadata enrichment does not affect search-relevance filtering
Fetched metadata (`ItemMetadata` and its `payload`) SHALL NOT be read by, written to, or otherwise influence vendor search-result relevance filtering (`expected_product_line`, `expected_category`, or term-relevance checks). This capability is additive and display-only.

#### Scenario: An item with no metadata still filters results normally
- **WHEN** an item has no `metadata_provider_key` set (or its fetch has not resolved)
- **THEN** vendor search-result filtering for that item behaves exactly as it did before this capability existed

#### Scenario: Metadata match status does not gate or alter filtering
- **WHEN** an item's `ItemMetadata.status` is `matched`, `needs_review`, `no_match`, or `error`
- **THEN** none of these states change which vendor search results are accepted or rejected for that item
