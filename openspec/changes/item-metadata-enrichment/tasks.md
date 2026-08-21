## 1. Data model & migration

- [x] 1.1 Add `metadata_provider_key` (blank-default `CharField`) to `SearchableItem` in `tracking/models.py`
- [x] 1.2 Add `ItemMetadata` model (one-to-one with `SearchableItem`): `status` (`unfetched`/`pending`/`matched`/`needs_review`/`no_match`/`error`), `external_id`, `pinned_external_id`, `payload` (`JSONField`), `fetched_at`
- [x] 1.3 Add `MetadataFetchRequest` model (item FK, `requested_at`, `status`) backing the drain queue
- [x] 1.4 Generate and review the migration for the above (no backfill needed — new fields/tables only)

## 2. Provider registry & Scryfall provider

- [x] 2.1 Create `tracking/metadata_providers.py` with a provider base contract: `resolve(item) -> ResolutionResult` (`MATCHED`/`NEEDS_REVIEW`/`NO_MATCH`) and `to_display(payload) -> {thumbnail_url, description, external_url}`
- [x] 2.2 Define the `Candidate` shape used by `NEEDS_REVIEW` results (`external_id`, plus the same three display fields)
- [x] 2.3 Implement `ScryfallProvider`: `resolve()` queries Scryfall using the item's `text`, mapping Scryfall's response to `MATCHED`/`NEEDS_REVIEW`/`NO_MATCH`; `to_display()` maps a stored Scryfall payload to `thumbnail_url`/`description`/`external_url`
- [x] 2.4 Register `ScryfallProvider` in a `PROVIDERS = {"scryfall": ScryfallProvider}` module-level dict (no DB model)
- [x] 2.5 Send a descriptive `User-Agent` on outbound Scryfall requests per their API guidelines

## 3. Shared refresh entrypoint & staleness triggers

- [x] 3.1 Implement `request_metadata_refresh(item)` (or equivalent single entrypoint) that: creates `ItemMetadata` if absent, enqueues a `MetadataFetchRequest`, and is the *only* code path allowed to enqueue a refresh
- [x] 3.2 Implement the provider-change reset: when `metadata_provider_key` changes to a different value or is cleared, clear `ItemMetadata.payload`/`external_id`/`pinned_external_id` and reset `status` to `unfetched` before any new refresh is requested
- [x] 3.3 Wire `SearchableCreateView`/`SearchableItemCreateForm` to call the entrypoint when a provider is set on creation
- [x] 3.4 Wire `BulkAddItemsView`/`BulkAddItemsForm` to call the entrypoint for every item created in a batch with a non-blank provider
- [x] 3.5 Wire `SearchableUpdateView`/`SearchableItemForm` to call the entrypoint when `metadata_provider_key` changes, or when `text` changes and `pinned_external_id` is not set (skip when pinned)
- [x] 3.6 Add the manual "Retry metadata fetch" action (view + URL) that calls the same entrypoint

## 4. Refresh queue & periodic drain task

- [x] 4.1 Implement the Huey fetch task that: calls the item's provider `resolve()`, updates `ItemMetadata` (`matched`/`needs_review`/`no_match`/`error`) and `fetched_at`, and marks the `MetadataFetchRequest` done — on failure, sets `status=error` with no automatic retry
- [x] 4.2 Implement a `@periodic_task(crontab(...))` ticker (mirroring `dispatch_scheduled_updates` in `tracking/tasks.py`) that wakes on a fixed cadence (~once/minute) and dispatches fetch tasks for up to a bounded number of pending `MetadataFetchRequest` rows per wake
- [x] 4.3 Confirm dev/test settings run this immediately in-process like existing Huey tasks (consistent with `HUEY["immediate"]`), so tests don't need a live worker

## 5. Forms

- [x] 5.1 Add a `metadata_provider_key` single-select field to `SearchableItemCreateForm`, populated live from the provider registry keys (mirror `ItemSourceForm.parser_key`'s registry-driven choices)
- [x] 5.2 Add the same field to `SearchableItemForm` (edit), wired to trigger 3.5's staleness logic on save
- [x] 5.3 Add the same field to `BulkAddItemsForm`, applied to every item in the batch alongside the existing shared `tag`/`priority`/`ItemSourceFormset`

## 6. Item detail page

- [x] 6.1 Render the three-slot display (`thumbnail_url`/`description`/`external_url`) when `ItemMetadata.status == matched`
- [x] 6.2 Render the candidate-picker (same three-slot contract per candidate, from `ItemMetadata`'s stored/re-fetched candidates) when `status == needs_review`, with a selection action that sets `pinned_external_id`
- [x] 6.3 Render a manual external-ID entry fallback (available at least for `no_match` and `needs_review`) that sets `pinned_external_id` and triggers a fetch for that ID
- [x] 6.4 Render current status (`unfetched`/`pending`/`no_match`/`error`) plainly when there is nothing to display yet
- [x] 6.5 Add the "Retry metadata fetch" button (uses 3.6's action), visible at least for `error`/`no_match`

## 7. Item list page (`view_terms`) thumbnail

- [x] 7.1 Update `SearchableListView.get_queryset` to eager-load `ItemMetadata` (`select_related`) so the thumbnail column adds no per-row queries
- [x] 7.2 Add the thumbnail `<img>` next to the search-term text in `searchableitem_list.html`, CSS-constrained to a reasonable size, shown only when `status == matched` and a `thumbnail_url` is present

## 8. Tests

- [x] 8.1 Model tests: provider-change reset behavior, pinned-vs-unpinned text-change trigger behavior
- [x] 8.2 Registry tests: `ScryfallProvider` resolves matched/needs_review/no_match from representative fixture payloads; `to_display()` mapping correctness
- [x] 8.3 Entrypoint tests: single/bulk create and edit paths all enqueue via the shared function; unrelated field edits do not enqueue
- [x] 8.4 Periodic task tests: draining respects the per-wake bound and processes queued requests across multiple wakes
- [x] 8.5 View tests: candidate selection sets `pinned_external_id` and `status`; manual retry re-enqueues from `error`/`no_match`
- [x] 8.6 List view test: rendering N items with matched metadata uses a bounded, non-linear query count
- [x] 8.7 Regression test confirming `item-category-relevance` filtering behavior is unaffected by items having metadata in any status

## 9. Docs

- [x] 9.1 Document the metadata-provider registry and how to add a new provider (README or `docs/`)
- [x] 9.2 Note deferred future directions: periodic re-sweep of matched items, bulk "refresh all"/"refresh errored" actions, and the general bulk-edit-existing-items feature (pointing at the reusable `item_ids` checkbox/`mode=selected` plumbing already on `view_terms`)

## 10. Dev-mode visibility for the periodic-drain consumer dependency

- [x] 10.1 Add a "metadata fetch may not be processed" warning (reuse or generalize `schedules_may_not_fire()` — same underlying condition, see design.md decision 9) shown on the item detail page when the item's `ItemMetadata.status` is `unfetched`/`pending`
- [x] 10.2 ~~Update README's metadata-enrichment section with the dev/prod consumer-dependency matrix~~ — superseded by 10.5: the "Redis not required in dev" claim was verified wrong (`run_huey` cannot start at all under `HUEY_IMMEDIATE=True`, confirmed by actually running it — see design.md decision 9's correction)
- [ ] 10.3 Add a `drain_metadata_queue` management command wrapping the existing plain `drain_pending_metadata_fetch_requests()` (`tracking/tasks.py`) — runs under `HUEY_IMMEDIATE=True` with no Redis/second process needed
- [ ] 10.4 Test the management command (calling it processes pending requests; runs cleanly with none pending)
- [ ] 10.5 Correct README's metadata-enrichment section: `run_huey` genuinely requires both `HUEY_IMMEDIATE=False` *and* a reachable Redis (not an either/or, and not available under the dev-mode default at all); document the `drain_metadata_queue` management command as the straightforward dev-mode alternative; note that a synchronous UI fallback (mirroring "Update Selected") was considered and rejected for now — see design.md decision 10
