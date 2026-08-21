## Why

Operators want to see reference metadata for tracked items — e.g. a Magic: the Gathering card's image and description pulled from Scryfall — without turning `SearchableItem` or its templates into an MTG-specific data model. The project already tracks non-card items (coffee, computer parts) alongside MTG singles, so any metadata mechanism must generalize across domains from day one, not retrofit generality later.

## What Changes

- Add an optional `metadata_provider_key` field to `SearchableItem` (single-select, blank by default) resolved against a pure code registry of metadata providers — no DB-backed provider model, mirroring the existing `parser_key`/`tracking/parsers.py` registry pattern.
- Add a new `ItemMetadata` model (one-to-one with `SearchableItem`) holding fetch/resolution state: `status`, `external_id`, `pinned_external_id` (sticky manual override), opaque `payload` JSON, `fetched_at`.
- Define a generic three-slot display contract (`thumbnail_url`, `description`, `external_url`) that every provider maps its raw `payload` into via a pure `to_display()` function, computed at render time — never denormalized. The same contract renders both a confirmed match and each disambiguation candidate.
- Define a generic provider resolution contract (`resolve(item)` → matched / needs-review-with-candidates / no-match) and a generic candidate-selection UI that sets `pinned_external_id` on operator choice, plus manual external-ID entry as a fallback.
- Ship exactly one registered provider: Scryfall.
- Add a lightweight fetch-request queue plus a periodic Huey task (mirroring the existing `UpdateSchedule`/`dispatch_scheduled_updates` cadence pattern) that drains queued requests at a bounded rate — explicitly not built on the vendor rate-limit/budget subsystem (`tracking/ratelimit/`), which solves a different problem (learning a live vendor-reported quota).
- Wire a single shared enqueue entrypoint into the three existing item create/update code paths (`SearchableCreateView`, `BulkAddItemsView`, `SearchableUpdateView`) so provider selection or text edits correctly (re)request a fetch, with `pinned_external_id` exempting an item from text-change invalidation and a provider-key change resetting stale state.
- Add UI: single-select provider field on the create/edit/bulk-add forms; matched/candidate/status display plus a manual "Retry metadata fetch" action on the item detail page; an inline thumbnail column on the `view_terms` list page (eager-loaded to avoid N+1).
- On fetch failure, mark `status=error` with no automatic retry — the manual retry action is the only recovery path in this change.

Explicitly out of scope for this change (documented as future directions): bulk-editing metadata (or any other field) across multiple existing items at once; a periodic sweep re-fetching metadata for all items; bulk "refresh all"/"refresh errored" actions; a second registered provider.

## Capabilities

### New Capabilities
- `item-metadata-enrichment`: Optional, domain-agnostic external metadata enrichment for `SearchableItem` — provider selection, resolution/disambiguation, the generic three-slot display contract, staleness/re-fetch triggers, the bounded-rate refresh queue, and the associated UI surfaces (forms, detail page, list thumbnail).

### Modified Capabilities
(none — this change is purely additive and does not alter `item-category-relevance`, `search-term-relevance`, `api-rate-limiting`, or `item-list-latest-price` requirements)

## Impact

- **Models** (`tracking/models.py`): new `metadata_provider_key` field on `SearchableItem`; new `ItemMetadata` and metadata-fetch-queue models; migration.
- **New module**: a metadata-providers registry (mirrors `tracking/parsers.py`) plus a `ScryfallProvider` implementation.
- **Forms** (`tracking/forms.py`): `SearchableItemCreateForm`, `SearchableItemForm`, `BulkAddItemsForm` gain the provider select field.
- **Views** (`tracking/views.py`): `SearchableCreateView`, `BulkAddItemsView`, `SearchableUpdateView` route through a new shared enqueue entrypoint; `SearchableItemDetailView` gains resolution/candidate display and a retry action; `SearchableListView.get_queryset` gains eager-loaded metadata for the list thumbnail.
- **Tasks** (`tracking/tasks.py`): new periodic task draining the metadata-fetch queue.
- **Templates**: `searchableitem_form.html`, `bulk_add_form.html`, `searchableitem_detail.html`, `searchableitem_list.html`.
- **No changes** to `tracking/ratelimit/`, `tracking/scrape.py`, `Source`/`ItemSource`/`SearchResult`/`WebUpdate`/`FetchJob`, or any relevance-filtering logic.
