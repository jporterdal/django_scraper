## Context

`SearchableItem` today only knows about vendor price search (`Source`/`ItemSource`/`SearchResult`/`FetchJob`/`WebUpdate`), plus two generic free-text disambiguation fields (`expected_product_line`, `expected_category`, see `item-category-relevance`). Operators want to attach external reference metadata — a Scryfall card's image and text for MTG items — without the project's core models or templates becoming MTG-shaped. The project already tracks non-card items (coffee, computer parts) in the same tables, so "generalizes past MTG" is a real, present constraint, not speculative future-proofing.

This design was worked out interactively in `/opsx:explore`; the decisions below restate that conversation's conclusions with rationale, grounded in existing code.

## Goals / Non-Goals

**Goals:**
- Let an operator attach one external metadata provider to an item and see a small, fixed set of display fields (image, description, external link) sourced from that provider.
- Keep the core app (models, generic templates, queueing) completely ignorant of any provider's domain-specific data shape.
- Reuse existing architectural idioms (registry-by-key, pinned-override, periodic-drain task) rather than inventing new ones.
- Support the case where automatic matching is ambiguous or fails, with a generic (not Scryfall-specific) resolution UI.

**Non-Goals:**
- Feeding fetched metadata into search-relevance filtering (`item-category-relevance` stays untouched).
- A second real provider implementation — only the registry seam needs to exist.
- Bulk-editing metadata (or anything else) across multiple existing items — future change.
- A periodic re-sweep of already-matched items, or bulk "refresh all"/"refresh errored" actions — future directions, documented but not built.
- Automatic bounded-retry/backoff on fetch failure — a manual retry action is sufficient for this low-stakes, display-only data.

## Decisions

### 1. Single provider per item, as a plain model field — not a join table

A join table (`ItemMetadataLink`, many-to-many-shaped like `ItemSource`) was considered during exploration, but the requirement is strictly one provider per item or none. `SearchableItem.metadata_provider_key` (blank-allowed `CharField`) is therefore an ordinary field, like `priority`/`active`, not a relation. This also removes any need for the "transient suggestion/manual field merged in `save()`" idiom used for `expected_product_line` (`tracking/forms.py:196-255`) — a single-select is just a normal bound field.

The *fetched state* still gets its own model, `ItemMetadata` (one-to-one with `SearchableItem`), for the same reason `SearchResult`/`WebUpdate` live apart from `Source`: operator configuration and system-fetched state are different concerns with different writers.

### 2. Pure code registry, no `MetadataProvider` DB model

`Source.parser_key` resolves against `tracking/parsers.py`'s `sources = {...}` dict (line 324); `Source.rate_limit_profile` resolves the same way against `tracking/ratelimit/profiles.py`'s `PROFILES` dict. Both precedents are pure code registries *because the thing they select is not itself operator-configurable data* — parsing/pacing logic ships with the code. `Source` itself is a DB model because vendor URLs/headers vary per deployment.

A metadata provider is the same shape as a parser: Scryfall needs no API key, no per-deployment base URL, no operator-editable config. So `tracking/metadata_providers.py` holds a plain `PROVIDERS = {"scryfall": ScryfallProvider}` dict, and `metadata_provider_key`'s form choices are populated live from its keys (mirroring `tracking/forms.py:346`'s `parser_registry`-driven dropdown). No new DB table for providers.

**Alternative considered**: a `MetadataProvider` DB model mirroring `Source`. Rejected for v1 — nothing needs per-deployment configuration yet. If a future provider needs an API key or alternate base URL, this decision should be revisited then, not pre-built now.

### 3. Generic three-slot display contract, computed at render time

The core agnosticism mechanism: every provider implements `to_display(payload) -> {thumbnail_url, description, external_url}`, a pure function over the stored `payload`. Generic templates render only these three fields — never provider-specific keys like `mana_cost` or `rarity`, which stay inert inside `payload`. Computing this at render time (rather than storing denormalized columns) means a `to_display` bugfix or reshaping takes effect immediately for already-fetched items, with `payload` as the single source of truth.

The same three-slot shape is reused for disambiguation candidates (`resolve()`'s `NEEDS_REVIEW` results) — one rendering path serves both "here is your confirmed match" and "here are your candidates, pick one," rather than a second provider-owned template mechanism.

**Alternative considered**: letting each provider ship its own template partial for full-fidelity rendering. Rejected — it reopens the door to domain-specific display logic leaking into the generic item pages, which is exactly what this design exists to prevent. A provider that outgrows three slots can still store anything it wants in `payload`; a future change can widen the contract deliberately if a real second provider demands it.

### 4. Resolution/disambiguation contract mirrors `ItemSource.pinned_url`

`ItemSource.pinned_url` ("fetch this directly instead of running a search," `tracking/models.py:256`) is the existing precedent for "auto behavior failed or is untrustworthy, let the operator pin an explicit override." `ItemMetadata.pinned_external_id` is the direct analog: once set (by picking a disambiguation candidate or entering an ID manually), it is authoritative and immune to the staleness triggers below.

`provider.resolve(item)` returns one of `MATCHED` (single confident match), `NEEDS_REVIEW` (0..N candidates), or `NO_MATCH`. The plumbing must not assume matching is usually unambiguous — Scryfall's search-term-based matching is expected to be single-card-confident most of the time (multiple printings collapse to one card identity), but a future provider may have a much higher `NEEDS_REVIEW` rate, and the generic UI/state machine must handle that as the normal case, not an edge case.

### 5. Staleness triggers, funneled through one entrypoint

Three existing, separate view/form code paths can all touch `metadata_provider_key`: `SearchableCreateView` (single create), `BulkAddItemsView` (bulk create), `SearchableUpdateView` (single edit). A single function — e.g. `tracking/metadata.py::request_metadata_refresh(item)` — is the only thing allowed to enqueue a fetch request, called from all three `save()` paths (and the manual retry action). This avoids three slightly-diverging reimplementations of the same trigger logic.

Trigger conditions:
- `metadata_provider_key` is non-blank, **and**
- either `metadata_provider_key` was just set/changed, **or** `text` changed and `pinned_external_id` is not set.

Rationale for "text change re-fetches unless pinned": an unpinned match is an inference from `text` and should be re-validated whenever that input changes (cheap to do, and stale card art silently surviving a rename is a worse failure mode than an extra fetch). A pinned match is an explicit operator claim, independent of `text`, exactly like `pinned_url` is independent of search-term/pattern edits.

**Edge case — provider changed**: switching (or blanking) `metadata_provider_key` must reset the existing `ItemMetadata` row (clear `payload`, `external_id`, `pinned_external_id`, `status` back to `unfetched`) before re-enqueuing. A stale `payload` is shaped for the old provider and would render garbage through the new provider's `to_display`; a stale `pinned_external_id` is a claim about the old provider's ID space, not the new one.

### 6. Refresh queue: periodic drain task, not the rate-limit subsystem

`tracking/ratelimit/` (`profiles.py`, `pacer.py`, `budget.py`, `extractors.py`) exists to *learn a vendor-reported live quota from response headers* (IETF/x-ratelimit/GraphQL-cost) and pace dynamically against it. That solves a different problem than "don't dispatch more than roughly N metadata fetches per minute regardless of how many items were just created." Reusing it would mean dragging in budget-learning machinery to solve a flat cap.

Instead: a lightweight queue (`MetadataFetchRequest`: item FK, requested_at, status) populated by the shared entrypoint, drained by a new `@periodic_task(crontab(...))` — the same shape as `UpdateSchedule`/`dispatch_scheduled_updates` (`tracking/tasks.py:318`), which already wakes on a cadence and dispatches due work. Cadence starts at roughly once per minute, draining up to a small fixed batch size per tick; both are tunable constants, explicitly expected to be revisited once real usage patterns are known.

No periodic re-sweep of already-`matched` items ships in this change — only creation/update/manual-retry populate the queue.

### 7. Failure handling: mark and stop, manual retry only

Unlike `FetchJob`/`fetch_one`'s bounded retry-with-backoff for price fetches, a failed metadata fetch simply sets `status=error` and stops. This is proportionate to low-stakes, non-time-critical display data, and avoids building a second retry/backoff mechanism alongside the one that already exists for price fetches. Recovery is the explicit "Retry metadata fetch" action on the item detail page, which re-enters the same shared enqueue entrypoint.

### 8. List-page thumbnail must not introduce N+1 queries

`SearchableListView.get_queryset` already eager-loads related data via `prefetch_related("tags")` and multiple `Subquery` annotations specifically to keep the item table to a bounded query count regardless of row count. The new thumbnail column must follow the same discipline — `select_related` on the one-to-one `ItemMetadata` — not a per-row lookup.

## Risks / Trade-offs

- **[Risk]** Scryfall's search-term-based auto-match is a heuristic; a same-named-but-different-card mismatch could silently display wrong art. → Mitigation: `pinned_external_id` gives the operator a permanent, explicit correction path once noticed; this is display-only, so the blast radius of a wrong match is cosmetic, not data-corrupting.
- **[Risk]** No automatic retry means a transient Scryfall outage leaves items stuck at `status=error` until an operator notices and clicks retry. → Mitigation: acceptable for v1 given low stakes; the documented future direction (bulk "refresh errored" action) is the natural fix once this friction is actually felt.
- **[Risk]** A fixed three-slot display contract may feel restrictive if Scryfall's rich data (mana cost, set symbol, price) turns out to be wanted later. → Mitigation: deliberate — `payload` retains everything; widening the contract is a future, additive change, not a rewrite, since nothing downstream depends on the contract being exactly three fields forever.
- **[Trade-off]** Computing `to_display()` at render time instead of storing denormalized columns adds a small per-render cost (negligible: pure function over already-fetched JSON, no extra queries) in exchange for never having stale display data after a mapping-logic change.

## Migration Plan

1. Migration adding `SearchableItem.metadata_provider_key` (blank default — no data backfill needed, existing items simply have no provider).
2. Migration adding `ItemMetadata` and `MetadataFetchRequest` models (new tables, no backfill).
3. Ship with zero items having a `metadata_provider_key` set; enrichment is fully opt-in per item going forward via the updated forms.
4. No rollback complexity beyond standard Django migration reversal — no existing behavior is altered, only additive.

## Open Questions

- Exact periodic-task cadence and per-tick batch size are placeholders (~once/minute, small batch) pending real usage — tune once live.
- Whether a second provider ever gets built, and whether it stays comfortably within the three-slot contract, remains unknown; this design commits only to the registry seam existing, not to a second implementation.
