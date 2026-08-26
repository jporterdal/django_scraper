## Why

`ItemSource.title_include_patterns`/`title_exclude_patterns`, `SearchableItem.text` (the search term), and `SearchableItem.expected_product_line`/`expected_category` are all user-editable fields that gate which parsed rows become `SearchResult` rows — but that gate only fires once, at fetch time (`tracking/parsers.py`'s `add_result`, and `tracking/matching.py`'s `filter_results_for_item_source`). Once a row is stored, nothing ever re-checks it. If a user edits any of these fields after results already exist — e.g. adding an exclude pattern to prune a spurious vendor listing they've observed — already-stored rows that would now fail the new criteria stay in the table and keep feeding the "Latest price" calculation and the detail-page price-history chart indefinitely, until a coincidentally-differently-priced new fetch happens to supersede them. If the excluded row was a source's only recent activity, the stale price is shown forever with no way for the user to know why.

## What Changes

- Add a read-time re-validation step to every place a stored `SearchResult` feeds a calculation: instead of trusting a source's single most-recent in-stock row, walk that source's recent in-stock rows newest-first and use the first one that still passes the item's *current* relevance criteria (search term, `expected_product_line`, `expected_category`, title include/exclude patterns). A source with no currently-matching row in its recent history contributes nothing to the calculation — it is not "stuck" on a stale price, nor is its history deleted.
- Consolidate the five relevance checks into `tracking/matching.py`: individual functions (`term_matches`, `value_matches_any`, `title_matches_rules`) callable separately so `tracking/parsers.py`'s `add_result` can keep emitting a distinct `logger.debug` reason per rejected row, plus one aggregate entrypoint (`result_matches_item_source`) that runs all five and returns a single pass/fail, used by the new read-time re-check.
- Move title-pattern enforcement (`title_include_patterns`/`title_exclude_patterns`) into the same per-row ingest path as the other three checks (`add_result`), eliminating the separate post-parse `filter_results_for_item_source` pass in `tracking/scrape.py` — ingest becomes one filtering pass instead of two.
- Restructure `SearchableListView.get_queryset`'s "Latest price" computation from a single cross-item correlated SQL subquery (which can only express "give me the single newest row," not "walk until one still matches" — regex has no portable sqlite/Postgres equivalent) into one windowed query (`RowNumber` partitioned by item+source, same ordering as today) that fetches a bounded recent window of in-stock rows in one round trip, resolved in Python against the current criteria.
- Wire the same re-validation into the detail page's price-history chart (`_build_source_chart_series`), which today builds its series from every stored row unconditionally — replacing the existing `r.matches` flag (currently computed but only used to gray out a row in the results table, never consulted by the chart or any calculation) with the same check actually driving both.
- No change to what gets stored or deleted — `SearchResult` rows and fetch history are untouched by this change. This is prospective-only: it changes what counts as "currently valid" for display/calculation, not the historical record.

## Capabilities

### New Capabilities
- `retroactive-result-matching`: defines the requirement that a stored `SearchResult` row is only used in price/chart calculations while it still matches the owning item's and item-source's *current* relevance criteria (search term, expected product line, expected category, title include/exclude patterns) — re-evaluated at read time, not assumed valid forever from ingest.

### Modified Capabilities
- `item-list-latest-price`: the definition of a source's "latest known price" changes from "its single most recent in-stock `SearchResult`" to "the most recent in-stock `SearchResult` that still matches current relevance criteria" — a source whose recent rows are all now-excluded contributes no price, rather than surfacing a stale one.

## Impact

- `tracking/matching.py`: new granular functions (`term_matches`, `value_matches_any`), existing `title_matches_rules` retained, new aggregate `result_matches_item_source` covering all five fields.
- `tracking/parsers.py`: `JSONSearchParser.add_result` calls the granular `matching.py` functions instead of inline checks; constructor gains `include_patterns`/`exclude_patterns`.
- `tracking/scrape.py`: drops the separate `filter_results_for_item_source` call (`fetch_one_unit`) now that ingest filtering is fully handled inside `add_result`.
- `tracking/views.py`: `SearchableListView.get_queryset` (windowed query + Python resolution replacing the `source_latest`/`cheapest_item_source` subqueries), `SearchableItemDetailView`/`_build_source_chart_series` (chart now excludes non-matching rows; `r.matches` reused rather than duplicated).
- `tracking/tests/`: `test_pattern_ingest.py`, `test_matching.py`, `test_item_source.py`, `test_sparkline.py` need new/updated cases for retroactive exclusion, term changes, and expected-value changes affecting already-stored rows.
- `openspec/specs/item-list-latest-price/spec.md`: requirement text and scenarios updated for the new "currently matches" condition.
