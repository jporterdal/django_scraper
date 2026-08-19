## Why

The "Latest price" shown per item on `view_terms` (`SearchableListView`) is meant to be the cheapest currently-known in-stock price across an item's sources. Instead, `SearchableListView.get_queryset()` scoped the min-price comparison to whichever single `WebUpdate` most recently stored *any* row for the item, rather than to each source's own latest known price. Because the scraper's dedup logic (`tracking/scrape.py`) skips re-storing a `SearchResult` when a source's price hasn't changed, a source that's been stable for days has no row on a newer `WebUpdate` triggered by a different source's price change — so it silently dropped out of the comparison entirely, and a stale-but-still-cheaper source lost to a newer, more expensive one. Confirmed live: an item with source B unchanged at $5.25 since Aug 17 and source A newly dropped to $7.99 on Aug 18 displayed "$7.99" instead of the correct $5.25.

## What Changes

- `SearchableListView.get_queryset()` now computes each source's own most recent in-stock price independently (via a correlated subquery per `ItemSource`), then takes the min across those per-source values — instead of restricting the comparison to rows sharing one shared "latest" `WebUpdate`.
- `latest_known_minprice`, `latest_known_minprice_title`, and `latest_known_minprice_source` annotations are now sourced consistently from the same per-source-latest ordering, so all three always describe the same winning `(item, source)` pair.
- No changes to scraper dedup/carry-forward behavior, storage schema, or other views (e.g. `SearchableItemDetailView`'s per-source chart logic already computed per-source history correctly and is unaffected).

## Capabilities

### New Capabilities

- `item-list-latest-price`: the `view_terms` list view's "Latest price" contract — for each item, the displayed price/source/title is the minimum of each linked source's own most recent in-stock price, independent of whether other sources were re-checked or restored in the same scrape run. Not previously documented in `openspec/specs/`; codifying it now since this change fixes a real defect in that contract.

### Modified Capabilities

None — no existing spec documents this behavior yet.

## Impact

- `tracking/views.py` — `SearchableListView.get_queryset()` (the fix).
- `tracking/tests/test_sparkline.py` — added `test_latest_price_keeps_stale_cheaper_source` regression test covering the divergent-source-update-cadence scenario.
- No migrations, no API/URL changes, no changes to the scraper (`tracking/scrape.py`) or detail-view chart logic.
