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

### 9. Dev-mode visibility: warn about the consumer dependency instead of silently masking it

The periodic drain (decision 6) inherits the exact same characteristic as `UpdateSchedule`/`dispatch_scheduled_updates`: `@periodic_task` only fires inside a live `python manage.py run_huey` consumer process. `HUEY["immediate"]` only governs what happens *once* a task is dispatched (synchronous, in-process execution instead of a Redis round-trip) — it does not make Huey's scheduler thread run inside `runserver`. That scheduler thread (`huey.consumer.Scheduler.loop()`) exists only inside a `Consumer` object, which only `run_huey` constructs.

**Correction (verified by actually running `python manage.py run_huey` under dev settings, not just reading `create_storage()` in isolation):** `huey.consumer.Consumer.start()` unconditionally raises `ConfigurationError` and refuses to boot at all when `huey.immediate` is `True` — this is a hard, unconditional guard, not a soft in-memory fallback. `Huey.create_storage()`'s in-memory-broker swap only matters for code that calls `enqueue()` directly (which is how our tests, and `fetch_one`/`dispatch_fan_out` for price updates, exercise tasks under immediate mode) — it says nothing about whether the consumer *process* itself will run. So the original claim here ("Redis is not required for `run_huey` to drive this queue under the dev/test default") was wrong: `run_huey` **cannot run at all** while `HUEY_IMMEDIATE=True` (the dev/test default), regardless of Redis. Only once `HUEY_IMMEDIATE=False` does the consumer start — and at that point it *does* need a real, reachable Redis (`REDIS_URL`, or `RedisHuey`'s default `localhost:6379`), since `enqueue()` now goes through `self.storage.enqueue()` for real. The corrected guidance the README needs: a live `run_huey` process requires **both** `HUEY_IMMEDIATE=False` **and** a reachable Redis — not an either/or, and not achievable under the dev-mode default at all.

Unlike `UpdateSchedule`, this queue has no synchronous manual-trigger fallback — price updates have "Update Selected", which calls `dispatch_fan_out` directly from the view, bypassing the periodic scheduler entirely. Metadata refresh has only the periodic path: `request_metadata_refresh` (and the manual "Retry" action, which just calls it) only enqueues a `MetadataFetchRequest`; nothing drains it outside `drain_metadata_fetch_queue`. So without a signal, a pending request can sit forever with no visible indication anything is wrong — worse than schedules, which at least warn.

Mirroring `schedules_may_not_fire()` (same underlying condition: immediate mode or no configured Redis), the item detail page shows a warning when an item's fetch is `unfetched`/`pending`. This is a heuristic, not a certain diagnosis — Django's request-serving process has no way to know whether a separate `run_huey` process happens to be alive — so it can false-positive (a worker is in fact running via a real Redis). Given `run_huey` is now confirmed impossible under the dev-mode default, the warning's practical remedy in dev isn't "start `run_huey`" — it's the escape hatch in decision 10 below.

### 10. Dev-mode escape hatch: a management command wrapping the plain drain function, not a UI fallback

`drain_metadata_fetch_queue` (the `@periodic_task`) was already split from a plain, undecorated `drain_pending_metadata_fetch_requests()` specifically so tests could call it without going through Huey's scheduler (mirroring `dispatch_due_schedules` vs. `dispatch_scheduled_updates`). That same plain function is the natural basis for a dev-mode escape hatch: a management command (`python manage.py drain_metadata_queue`, or similar) that calls it directly, runnable under `HUEY_IMMEDIATE=True` with zero extra setup — no Redis, no second process. A one-line `manage.py shell -c "..."` invocation already works today with no new code; the management command exists purely to make that path discoverable and documentable rather than requiring someone to know the internal function name.

**Alternative considered and rejected (for now): a synchronous fallback wired into the UI actions**, mirroring how "Update Selected" calls `dispatch_fan_out` directly from the view for price updates — e.g. "Retry metadata fetch" (and the create/edit paths) calling the drain inline under immediate mode instead of only enqueueing. Rejected for this change: the expected amount of live, end-to-end metadata-fetch testing needed while developing on a local machine is minimal (mostly single-item spot checks during initial development, not a routine workflow), so a one-off management command invocation is proportionate. Wiring a synchronous path into the request-serving views would also reintroduce exactly the kind of foreground-blocking-request tradeoff `dispatch_fan_out`'s background fan-out was built to avoid for price updates — worth avoiding by default rather than adding quietly "just for dev." Revisit if live dev-testing turns out to be more frequent than expected, or if the management command proves too easy to forget about.

## Risks / Trade-offs

- **[Risk]** Scryfall's search-term-based auto-match is a heuristic; a same-named-but-different-card mismatch could silently display wrong art. → Mitigation: `pinned_external_id` gives the operator a permanent, explicit correction path once noticed; this is display-only, so the blast radius of a wrong match is cosmetic, not data-corrupting.
- **[Risk]** No automatic retry means a transient Scryfall outage leaves items stuck at `status=error` until an operator notices and clicks retry. → Mitigation: acceptable for v1 given low stakes; the documented future direction (bulk "refresh errored" action) is the natural fix once this friction is actually felt.
- **[Risk]** A fixed three-slot display contract may feel restrictive if Scryfall's rich data (mana cost, set symbol, price) turns out to be wanted later. → Mitigation: deliberate — `payload` retains everything; widening the contract is a future, additive change, not a rewrite, since nothing downstream depends on the contract being exactly three fields forever.
- **[Trade-off]** Computing `to_display()` at render time instead of storing denormalized columns adds a small per-render cost (negligible: pure function over already-fetched JSON, no extra queries) in exchange for never having stale display data after a mapping-logic change.
- **[Risk]** Without a live `run_huey` consumer, `MetadataFetchRequest` rows silently accumulate as `pending` forever with no error surfaced anywhere — indistinguishable from "queue is just slow." → Mitigation: a `schedules_may_not_fire()`-style warning banner on the item detail page (decision 9); `run_huey` itself cannot run at all under the dev-mode default (`HUEY_IMMEDIATE=True` — confirmed by actually running it, see decision 9's correction), so the practical dev-mode mitigation is the `drain_metadata_queue` management command (decision 10), with the README corrected to state both `HUEY_IMMEDIATE=False` and a reachable Redis as `run_huey`'s real requirements.

## Migration Plan

1. Migration adding `SearchableItem.metadata_provider_key` (blank default — no data backfill needed, existing items simply have no provider).
2. Migration adding `ItemMetadata` and `MetadataFetchRequest` models (new tables, no backfill).
3. Ship with zero items having a `metadata_provider_key` set; enrichment is fully opt-in per item going forward via the updated forms.
4. No rollback complexity beyond standard Django migration reversal — no existing behavior is altered, only additive.

## Open Questions

- Exact periodic-task cadence and per-tick batch size are placeholders (~once/minute, small batch) pending real usage — tune once live.
- Whether a second provider ever gets built, and whether it stays comfortably within the three-slot contract, remains unknown; this design commits only to the registry seam existing, not to a second implementation.
- The decision-9 warning heuristic can't distinguish "no consumer is running" from "a consumer is running just fine, but immediate mode/no-Redis happens to also be true" — it will occasionally warn when things are actually fine. Whether to generalize/reuse `schedules_may_not_fire()` as-is (same condition) or extract a differently-named shared helper is an implementation-time call, not a design one — the condition itself is settled.
