## 1. Consolidate matching logic in `matching.py`

- [ ] 1.1 Add `term_matches(title, term) -> bool` to `matching.py` (contiguous-phrase, diacritic-folded check, moved from `parsers.py`'s inline logic — reuse `_normalize_for_match` behavior)
- [ ] 1.2 Add `value_matches_any(value, expected_values) -> bool` to `matching.py`, generalizing the `expected_product_line`/`expected_category` substring check (empty list = pass)
- [ ] 1.3 Keep `title_matches_rules(title, include_patterns, exclude_patterns)` as-is
- [ ] 1.4 Add aggregate `result_matches_item_source(title, category, product_line, item_source) -> bool` that runs all four checks (term via `item_source.item.text`, product_line/category via `item_source.item.expected_values_for_source(...)`, title patterns via `item_source.title_include_patterns`/`title_exclude_patterns`) and returns a single bool
- [ ] 1.5 Update/add unit tests in `test_matching.py` for `term_matches`, `value_matches_any`, and the new `result_matches_item_source` aggregate (one test per check causing a fail, one all-pass case)

## 2. Move ingest-time filtering into `parsers.py`, call `matching.py` functions

- [ ] 2.1 Add `include_patterns`/`exclude_patterns` params to `JSONSearchParser.__init__`, populated from `item_source.title_include_patterns`/`title_exclude_patterns` at construction (scrape.py, alongside existing `expected_product_line`/`expected_category` resolution)
- [ ] 2.2 Rewrite `add_result` to call `matching.term_matches`, `matching.value_matches_any` (x2), and `matching.title_matches_rules` individually, each with its own `logger.debug` rejection message (preserve today's four distinct log messages: off-term, off-product-line, off-category, pattern-excluded)
- [ ] 2.3 Remove the now-redundant inline term/product_line/category checks and `_normalize_for_match` duplication from `parsers.py` if fully superseded by `matching.py` calls (keep `_normalize_for_match` itself in whichever module owns it — verify no circular import between `parsers.py` and `matching.py`)
- [ ] 2.4 Update `test_pattern_ingest.py` and parser-level tests (`test_wtfilters_parser.py` or equivalent) to confirm title-pattern rejection now happens inside `add_result`/parsing, not a separate post-parse step

## 3. Simplify `scrape.py` ingest to one filtering pass

- [ ] 3.1 Remove the separate `filter_results_for_item_source(parser.results, item_source)` call in `fetch_one_unit` (scrape.py) — `parser.results` is already fully filtered once task 2 lands
- [ ] 3.2 Verify `FetchJob.result_count`/`stored_count` semantics are unchanged (still reflect the fully-filtered candidate count) — add/adjust a regression test if the accounting shifts
- [ ] 3.3 Run full `test_pattern_ingest.py` suite to confirm ingest behavior (what gets stored) is unchanged by the restructuring — this task is refactor-only, no behavior change at ingest

## 4. Retroactive re-validation for "Latest price" (list view)

- [ ] 4.1 Add a window-size setting (e.g. `RETROACTIVE_MATCH_WINDOW = 20`) to Django settings, per design.md Decision 6
- [ ] 4.2 Replace `SearchableListView.get_queryset`'s `source_latest`/`cheapest_item_source` `Subquery` construction with: (a) a windowed query using `Window(RowNumber(), partition_by=[item_id, source_id], order_by=[-update__timestamp, price, title])` scoped to `item__in=<page items>, instock=1`, capped at the window-size setting, in one query
- [ ] 4.3 Fetch `ItemSource` rows (with `select_related("item", "source")`) for the page's items, needed to resolve each row's current criteria
- [ ] 4.4 In Python: group windowed rows by `(item_id, source_id)`, preserving query order; for each `ItemSource`, walk its group and take the first row where `matching.result_matches_item_source(...)` is true; log when a group is exhausted with zero matches (per design.md risk mitigation)
- [ ] 4.5 Compute `latest_known_minprice`/`_title`/`_source` per item from the resolved per-source winners (minimum across sources), preserving the existing tiebreak order (cheapest, then alphabetical title) and the "same winning result" consistency guarantee
- [ ] 4.6 Confirm `SearchableListView.get_context_data`'s sparkline/`item_source_pairs` logic (which reads `latest_known_minprice_source`) needs no changes — verify by running `test_list_price_source.py`

## 5. Retroactive re-validation for detail-page chart

- [ ] 5.1 In `SearchableItemDetailView.get_context_data`, replace the existing `r.matches = result_matches_item_source(r.title, isrc)` call with the new `matching.result_matches_item_source(r.title, r.category, r.product_line, isrc)` signature (now covering all five fields, not just title patterns)
- [ ] 5.2 Wire `_build_source_chart_series` to skip rows where `matches` is false when building `stored_by_source_update` (chart points), instead of building the series from all stored rows unconditionally
- [ ] 5.3 Confirm the full raw results table still lists every stored row (matching and non-matching), using `r.matches` only for the existing gray-out display treatment — no rows removed from that table

## 6. Test coverage for retroactive behavior

- [ ] 6.1 Add `test_sparkline.py` cases: exclude pattern added after storage removes that source's contribution to Latest price; source's entire recent window excluded falls out of the cross-source minimum entirely; an older still-matching row is used when the newest row is excluded
- [ ] 6.2 Add a `test_sparkline.py` (or new file) case for `SearchableItem.text` edited after storage, changing which stored rows currently match
- [ ] 6.3 Add a case for `expected_product_line`/`expected_category` edited after storage, changing which stored rows currently match
- [ ] 6.4 Add a detail-page test confirming the chart excludes a now-non-matching row's price point while the raw results table still lists it
- [ ] 6.5 Add a regression case confirming a re-added/loosened pattern restores a previously-excluded row's eligibility (no data was lost while excluded)

## 7. Spec sync and cleanup

- [ ] 7.1 Run `openspec validate --change "fix-latest-price-retroactive-filtering" --strict` and resolve any issues
- [ ] 7.2 Manually verify list-view page-load latency against a production-scale dataset before merging (per design.md Migration Plan)
- [ ] 7.3 After merge, sync `item-list-latest-price` spec deltas and archive this change per the standard OpenSpec workflow
