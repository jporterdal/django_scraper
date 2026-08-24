## Why

The `view_terms` "Latest price" for an item can display a higher price than a vendor actually offers, because picking "a source's own latest result" has no deterministic tiebreak when that source's single scrape stores multiple `SearchResult` rows sharing the same `WebUpdate` timestamp — which happens routinely, since one `SearchableItem` intentionally matches multiple product variants (e.g. regular/foil/extended-art printings) per source. Confirmed by local repro against exported prod data: with both sources' `ItemSource` links live and every row in stock, only reordering how one source's same-timestamp rows were inserted flipped the computed Latest price from the correct $1.25 to an incorrect $1.75 — the "latest row" pick is arbitrary, not price-based.

## What Changes

- `source_latest`, the per-source "this vendor's own latest in-stock result" subquery in `SearchableListView.get_queryset()` (`tracking/views.py`), gains a deterministic secondary sort by price (ascending) after `-update__timestamp`, so that among a source's own rows tied on timestamp, the cheapest one is always the one selected — plus a tertiary sort by title (ascending) so that if two of those tied rows also share the same price, the pick is still deterministic rather than falling back to arbitrary database row order.
- No change to the existing per-source-then-cross-source-minimum structure (fixed in a prior change, `item-list-latest-price`) — sources are still compared independently and the item's Latest price is still the minimum across sources' own latest picks.
- No change to result-matching breadth (title include/exclude patterns) — multiple product variants matching one item per source is intended behavior, out of scope here.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `item-list-latest-price`: adds a requirement that when a source's own most recent `WebUpdate` stored multiple in-stock `SearchResult` rows for an item (tied on timestamp), the source's "latest known price" deterministically resolves to the cheapest of those tied rows, not an arbitrary one.

## Impact

- `tracking/views.py` — `SearchableListView.get_queryset()`, the `source_latest` subquery (~line 590-594).
- `tracking/tests/test_sparkline.py` and/or `tracking/tests/test_scrape.py` — gain a regression case with multiple same-`WebUpdate` `SearchResult` rows for one source, asserting the cheapest is selected regardless of insertion order.
- No migrations, no template/UI changes, no changes to the cross-source minimum logic.
