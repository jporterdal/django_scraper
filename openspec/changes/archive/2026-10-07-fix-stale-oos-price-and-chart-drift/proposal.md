## Why

In production, an item that goes out of stock at a vendor keeps showing its last in-stock price as "Latest price", and the detail-page price-history chart sometimes disagrees with that reported price. Both come from how the views read the per-title change log that scrape-time dedup leaves behind. The scraper records the data correctly: an out-of-stock transition is stored as an `instock=0` row, and an unchanged title is deliberately not re-stored.

1. **Stale out-of-stock price:** `_resolve_latest_known_prices` filters to `instock=1` *before* picking each title thread's newest row. A thread's out-of-stock row is never seen, so its older in-stock price wins. The chart's carry-forward has the same blind spot: on later unchanged fetches it draws "confirmed, unchanged" points at the stale price for a title that is out of stock.
2. **Chart / Latest price drift:** each chart point is the minimum over only the rows *stored in that update*. Dedup stores only titles that changed, so a cheaper, unchanged sibling title is missing from that minimum. Example from `bugdata.csv`: on 2026-08-21 hfx stored only the Extended Art row ($3.00), so the chart plots $3.00. The real lowest was still $1.25 (HOB-137, unchanged since 08-19). The list sparkline also skips relevance re-validation, which is a third way it can drift from the Latest price it sits next to.

## What Changes

- A title thread's current state comes from its newest currently-matching row, *including* out-of-stock rows. A thread whose current state is out of stock contributes no price. When it comes back in stock, a new in-stock row is stored and the thread contributes again.
- Latest price, the detail-page chart, and the list sparkline all use one shared per-source, chronological replay of title-thread state, so each source's final chart point and its Latest-price contribution are computed the same way and cannot drift.
- Each chart point is the lowest price across all of that source's threads that are in stock and currently matching, *as of that update*. It is no longer the minimum over only the rows stored in that update.
- If a source was checked and none of its threads are in stock, the chart shows a gap (a `null` point) instead of carrying the stale price forward.
- Solid and hollow chart points now reflect whether the source's *lowest price* changed at that update, which is what the "(price changed)" and "(confirmed, unchanged)" tooltips already claim.
- The list sparkline now applies the same retroactive relevance matching as Latest price and the detail chart.
- Not changed: a title the vendor stops returning entirely (rather than returning it flagged as out of stock) keeps its last known state. This is documented in design.md as a possible future change. It is not expected to happen with current vendors, which all return out-of-stock rows with explicit flags.

## Capabilities

### New Capabilities
- `price-history-chart`: how the detail-page per-source price-history chart and the `view_terms` list sparkline compute their points (state per thread as of each update, out-of-stock gaps, solid/hollow meaning) and the requirement that they agree with Latest price.

### Modified Capabilities
- `item-list-latest-price`: a thread's latest known price is taken from its newest currently-matching row whether in or out of stock. An out-of-stock current state removes that thread's contribution instead of falling back to an older in-stock price.

## Impact

- **Code:** `tracking/views.py`: `_resolve_latest_known_prices`, `_source_price_points`, `_build_source_chart_series`, and the sparkline block in `SearchableListView.get_context_data`. These are replaced by, or rebuilt on, one shared replay helper.
- **Templates:** `searchableitem_detail.html` and `searchableitem_list.html` must render `null` prices as gaps (this is Chart.js's default with `spanGaps` off) and give gap points a sensible tooltip and point style.
- **Tests:** new regression tests built from the `bugdata.csv` sequence (hfx 08-19 → 08-23, and wt 08-22 in stock then 08-23 out of stock). Existing tests in `test_sparkline.py`, `test_dedup.py`, `test_retroactive_matching.py`, and `test_item_detail.py` must keep passing, or be updated only where they encoded the buggy behavior.
- **Data / migrations:** none. Stored history, dedup, and scraping are unchanged; this is a read-time fix.
- **Performance:** the list view already loads every in-stock row for the visible items. It will now also load the out-of-stock rows, which keeps it within the same bounded number of queries.
