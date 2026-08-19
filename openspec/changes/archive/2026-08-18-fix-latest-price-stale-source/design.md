## Context

`SearchableListView.get_queryset()` (`tracking/views.py`) annotates each `SearchableItem` with `latest_known_minprice`/`_title`/`_source` for the `view_terms` list. The prior implementation:

1. Found `_latest_storing_update_id`: the most recent `WebUpdate` (by timestamp) with at least one in-stock `SearchResult` for the item, across *any* source.
2. Computed the min price only among `SearchResult` rows whose `update_id` equals that single update.

This breaks whenever an item's sources are checked/stored on different `WebUpdate`s and only some of them see a price change. The scraper (`tracking/scrape.py`) intentionally skips storing a new `SearchResult` row when a source's price is unchanged from its last stored row (dedup/carry-forward) — so a stable source has no row on a `WebUpdate` where only a different source's price moved. Once any other source stores a fresher row, step 1 above jumps `_latest_storing_update_id` to that update, and step 2's per-update filter silently excludes the stable source from the comparison, even though its last-stored price is still its current price.

`SearchableItemDetailView` and `_source_price_points`/`_build_source_chart_series` already treat each source's price history independently with correct carry-forward, so the per-source-latest-price approach is an established pattern elsewhere in this view module — the list view's min-price annotation is the outlier.

## Goals / Non-Goals

**Goals:**
- `latest_known_minprice` (and its paired `_title`/`_source` annotations) must reflect the true minimum of each linked source's own most recent in-stock price, regardless of which `WebUpdate` last touched which source.
- Keep the fix scoped to the query in `get_queryset()`; no schema, scraper, or template changes.
- Preserve existing query-count characteristics (list view stays annotation-based, no N+1 introduced per item).

**Non-Goals:**
- Changing scraper dedup/carry-forward semantics (`tracking/scrape.py`) — that behavior is correct and is the reason this bug exists (stable prices legitimately don't get new rows).
- Changing `SearchableItemDetailView`'s chart/history logic, which already computes per-source series correctly.
- Backfilling or correcting any previously-displayed (already-viewed) incorrect prices — this is a display-computation fix only, no stored data is wrong.

## Decisions

**Decision: compute per-source latest price via a correlated subquery on `ItemSource`, then take min across those, instead of scoping to one shared `WebUpdate`.**

New shape in `get_queryset()`:
- `source_latest`: `SearchResult` filtered to `item=OuterRef("item_id"), source=OuterRef("source_id"), instock=1`, ordered by `-update__timestamp` — i.e., each source's own latest in-stock row, evaluated independently per source.
- `cheapest_item_source`: `ItemSource.objects.filter(item=OuterRef("pk"))` annotated with `_latest_price`/`_latest_title` from `source_latest`, excluding sources with no known price, ordered by `(_latest_price, source_id)`.
- The three outer annotations (`latest_known_minprice`, `latest_known_minprice_title`, `latest_known_minprice_source`) all pull from the same `cheapest_item_source` ordering via `.values(...)[:1]`, guaranteeing they describe the same winning `(item, source)` row rather than being independently (and potentially inconsistently) resolved.

Alternatives considered:
- **Window functions (`RowNumber` partitioned by source, ordered by timestamp)**: would let one query compute "latest per source" directly, but Django doesn't allow filtering on a window-function annotation within the same queryset — it would need an extra wrapping subquery/materialization, adding complexity without a clear win over the nested-`Subquery` approach for this data volume (few sources per item).
- **Application-level computation** (pull all `SearchResult`/`ItemSource` rows into Python and reduce there, similar to `_source_price_points`): rejected to keep the list view's per-item price annotation queryable/sortable at the DB level and consistent with how `last_checked_at` and other list annotations already work; would also require restructuring `get_context_data()`'s existing per-item-source loop.
- **Keep single shared "latest update" but broaden its scope to "latest update per source, unioned"**: functionally converges on the same result as the chosen fix but is a more convoluted way to express "each source's own latest," so the direct per-`ItemSource` correlated subquery was preferred for clarity.

## Risks / Trade-offs

- **[Risk]** Nested correlated subqueries (3 levels: outer `SearchableItem` → `ItemSource` → `SearchResult`) could be slower than the previous 2-level version at large scale. → **Mitigation**: per-item source counts are small (a handful of sources per item in practice); existing test suite (349 tests) and the list view's typical item counts do not show a measurable regression. Revisit with an `EXPLAIN` if the item catalog grows by orders of magnitude.
- **[Risk]** A source that has *never* stored an in-stock result is correctly excluded (`exclude(_latest_price__isnull=True)`), matching prior behavior when an item has no priced sources at all. → No mitigation needed; this preserves the existing `None`/empty-state behavior covered by `test_price_history_empty_when_no_latest_price`.

## Migration Plan

Pure code change, no data migration. Deploy as a normal code release; no rollback complexity beyond reverting the `get_queryset()` diff if needed.

## Open Questions

None.
