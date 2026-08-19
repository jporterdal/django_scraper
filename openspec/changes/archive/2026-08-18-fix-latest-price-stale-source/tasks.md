## 1. Diagnose

- [x] 1.1 Trace how "Latest price" is computed for `view_terms` (`SearchableListView.get_queryset()` in `tracking/views.py`)
- [x] 1.2 Write a reproduction test seeding two sources on an earlier shared `WebUpdate`, then a new `WebUpdate` that only re-stores a price change for one source, and confirm the annotated `latest_known_minprice` wrongly reflects only the re-checked source
- [x] 1.3 Confirm root cause: the min-price query scoped its comparison to a single `_latest_storing_update_id` (the most recent `WebUpdate` with any stored row for the item), which excludes sources whose price is unchanged and therefore not re-stored on that update (scraper dedup/carry-forward in `tracking/scrape.py`)

## 2. Fix

- [x] 2.1 In `tracking/views.py::SearchableListView.get_queryset()`, replace the single-shared-`WebUpdate` min-price query with a per-`ItemSource` correlated subquery (`source_latest`) that finds each source's own most recent in-stock `SearchResult`
- [x] 2.2 Annotate `ItemSource` rows for the item with each source's latest price/title, exclude sources with no known price, and order by `(price, source_id)` to pick the cheapest (`cheapest_item_source`)
- [x] 2.3 Derive `latest_known_minprice`, `latest_known_minprice_title`, and `latest_known_minprice_source` from the same `cheapest_item_source` ordering so all three describe the same winning source
- [x] 2.4 Remove the now-unused `_latest_storing_update_id` annotation and its comment

## 3. Verify

- [x] 3.1 Confirm the reproduction test now passes (`latest_known_minprice == 5.25`, `latest_known_minprice_source == "amz"`)
- [x] 3.2 Fold the reproduction scenario into a permanent regression test, `test_latest_price_keeps_stale_cheaper_source`, in `tracking/tests/test_sparkline.py`
- [x] 3.3 Run the full `tracking` test suite (`python manage.py test tracking`) and confirm all 349 tests pass, including existing sparkline/scrape tests covering carry-forward and multi-source history
- [x] 3.4 Delete the throwaway reproduction test file once its scenario is covered by the permanent regression test

## 4. Wrap up

- [x] 4.1 Review the diff to confirm only `tracking/views.py` (fix) and `tracking/tests/test_sparkline.py` (regression test) changed
