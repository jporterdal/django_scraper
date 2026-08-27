## 1. Consolidate matching logic in `matching.py`

- [x] 1.1 Add `term_matches(title, term) -> bool` to `matching.py` (contiguous-phrase, diacritic-folded check, moved from `parsers.py`'s inline logic — reuse `_normalize_for_match` behavior)
- [x] 1.2 Add `value_matches_any(value, expected_values) -> bool` to `matching.py`, generalizing the `expected_product_line`/`expected_category` substring check (empty list = pass)
- [x] 1.3 Keep `title_matches_rules(title, include_patterns, exclude_patterns)` as-is
- [x] 1.4 Add aggregate `result_matches_item_source(title, category, product_line, item_source) -> bool` that runs all four checks (term via `item_source.item.text`, product_line/category via `item_source.item.expected_values_for_source(...)`, title patterns via `item_source.title_include_patterns`/`title_exclude_patterns`) and returns a single bool
- [x] 1.5 Update/add unit tests in `test_matching.py` for `term_matches`, `value_matches_any`, and the new `result_matches_item_source` aggregate (one test per check causing a fail, one all-pass case)

## 2. Move ingest-time filtering into `parsers.py`, call `matching.py` functions

- [x] 2.1 Add `include_patterns`/`exclude_patterns` params to `JSONSearchParser.__init__`, populated from `item_source.title_include_patterns`/`title_exclude_patterns` at construction (scrape.py, alongside existing `expected_product_line`/`expected_category` resolution)
- [x] 2.2 Rewrite `add_result` to call `matching.term_matches`, `matching.value_matches_any` (x2), and `matching.title_matches_rules` individually, each with its own `logger.debug` rejection message (preserve today's four distinct log messages: off-term, off-product-line, off-category, pattern-excluded)
- [x] 2.3 Remove the now-redundant inline term/product_line/category checks and `_normalize_for_match` duplication from `parsers.py` if fully superseded by `matching.py` calls (keep `_normalize_for_match` itself in whichever module owns it — verify no circular import between `parsers.py` and `matching.py`)
- [x] 2.4 Update `test_pattern_ingest.py` and parser-level tests (`test_wtfilters_parser.py` or equivalent) to confirm title-pattern rejection now happens inside `add_result`/parsing, not a separate post-parse step

## 3. Simplify `scrape.py` ingest to one filtering pass

- [x] 3.1 Remove the separate `filter_results_for_item_source(parser.results, item_source)` call in `fetch_one_unit` (scrape.py) — `parser.results` is already fully filtered once task 2 lands
- [x] 3.2 Verify `FetchJob.result_count`/`stored_count` semantics are unchanged (still reflect the fully-filtered candidate count) — add/adjust a regression test if the accounting shifts
- [x] 3.3 Run full `test_pattern_ingest.py` suite to confirm ingest behavior (what gets stored) is unchanged by the restructuring — this task is refactor-only, no behavior change at ingest

## 4. Retroactive re-validation for "Latest price" (list view)

- [x] 4.1 ~~Add a window-size setting (e.g. `RETROACTIVE_MATCH_WINDOW = 20`) to Django settings, per design.md Decision 6~~ — **superseded by 4b.1**: Decision 6 now removes this setting instead of tuning it; the work this task describes is being undone, not shipped as-is
- [x] 4.2 ~~Replace `SearchableListView.get_queryset`'s `source_latest`/`cheapest_item_source` `Subquery` construction with: (a) a windowed query using `Window(RowNumber(), partition_by=[item_id, source_id], order_by=[-update__timestamp, price, title])` scoped to `item__in=<page items>, instock=1`, capped at the window-size setting, in one query~~ — **superseded by 4b.2**: this partition key/cap is the dedup-timestamp-masking bug described in 4b's preface; do not treat this as the shipped query shape
- [x] 4.3 Fetch `ItemSource` rows (with `select_related("item", "source")`) for the page's items, needed to resolve each row's current criteria — *(still accurate; unaffected by 4b)*
- [x] 4.4 ~~In Python: group windowed rows by `(item_id, source_id)`, preserving query order; for each `ItemSource`, walk its group and take the first row where `matching.result_matches_item_source(...)` is true; log when a group is exhausted with zero matches (per design.md risk mitigation)~~ — **superseded by 4b.3**: grouping by `(item_id, source_id)` without `title` is precisely what lets one thread's timestamp shadow another's; this grouping must not ship
- [x] 4.5 Compute `latest_known_minprice`/`_title`/`_source` per item from the resolved per-source winners (minimum across sources), preserving the existing tiebreak order (cheapest, then alphabetical title) and the "same winning result" consistency guarantee — *(per-item logic still accurate, but its input — "per-source winners" — now comes from 4b.3's two-level resolution, not 4.4; re-verify per 4b.4)*
- [x] 4.6 Confirm `SearchableListView.get_context_data`'s sparkline/`item_source_pairs` logic (which reads `latest_known_minprice_source`) needs no changes — verify by running `test_list_price_source.py` — *(re-verify per 4b.4 once 4b lands, but no change to this logic itself is expected)*

### 4b. Correction: dedup-timestamp-masking bug found while implementing task 4

While building the walk in 4.4, we found that partitioning solely by `(item_id, source_id)` reproduces a bug already present in the `source_latest`/`cheapest_item_source` subqueries this task removed (present since `8a7e0af`/`4b758ff`, predating this change): dedup is keyed `(item, source, title)` (`scrape.py`'s `_dedupe_unit_candidates`), so one source can carry several concurrently-valid, independently-timestamped title "threads." Ranking a source's rows for "latest price" without partitioning by title lets a thread whose price just changed (fresh timestamp) shadow a sibling thread whose price is unchanged and therefore un-restored by dedup (stale timestamp, still-current price) — the item's reported Latest price silently jumps to the changed thread's price even when a cheaper, still-valid thread exists. This was inherited into 4.2's first-draft window because it deliberately kept "same ordering as today"; discovered only once task 4 was implemented and re-examined against the dedup model in `scrape.py`. Folding the fix into this change (design.md Decision 3) rather than deferring it, since it's the same function/query already being restructured.

- [x] 4b.1 Remove the `RETROACTIVE_MATCH_WINDOW` setting added in 4.1 (superseded by design.md Decision 6 — per-thread partitioning replaces the need for a shared window size)
- [x] 4b.2 Change the windowed query's `partition_by` from `[item_id, source_id]` to `[item_id, source_id, title]`, keeping `order_by=[-update__timestamp, price, title]`; drop the row cap entirely (every row in a thread is a candidate — see design.md Decision 3 and Risks)
- [x] 4b.3 Rework the Python resolution into two levels: (a) per thread `(item_id, source_id, title)`, walk newest-first for the first row where `matching.result_matches_item_source(...)` is true; (b) per `ItemSource`, take the minimum price across that source's thread winners (reusing the existing cheapest-then-alphabetical-title tiebreak from 4.5 across threads, not just across same-timestamp rows); log when a thread is exhausted with zero matches
- [x] 4b.4 Re-verify 4.5's per-item cross-source minimum and 4.6's sparkline/`item_source_pairs` logic still hold unchanged on top of the new per-thread-then-per-source resolution

## 5. Retroactive re-validation for detail-page chart

- [x] 5.1 In `SearchableItemDetailView.get_context_data`, replace the existing `r.matches = result_matches_item_source(r.title, isrc)` call with the new `matching.result_matches_item_source(r.title, r.category, r.product_line, isrc)` signature (now covering all five fields, not just title patterns)
- [x] 5.2 Wire `_build_source_chart_series` to skip rows where `matches` is false when building `stored_by_source_update` (chart points), instead of building the series from all stored rows unconditionally
- [x] 5.3 Confirm the full raw results table still lists every stored row (matching and non-matching), using `r.matches` only for the existing gray-out display treatment — no rows removed from that table

## 6. Test coverage for retroactive behavior

- [x] 6.1 Add `test_sparkline.py` cases: exclude pattern added after storage removes that source's contribution to Latest price; source's entire recent window excluded falls out of the cross-source minimum entirely; an older still-matching row is used when the newest row is excluded
- [x] 6.2 Add a `test_sparkline.py` (or new file) case for `SearchableItem.text` edited after storage, changing which stored rows currently match
- [x] 6.3 Add a case for `expected_product_line`/`expected_category` edited after storage, changing which stored rows currently match
- [x] 6.4 Add a detail-page test confirming the chart excludes a now-non-matching row's price point while the raw results table still lists it
- [x] 6.5 Add a regression case confirming a re-added/loosened pattern restores a previously-excluded row's eligibility (no data was lost while excluded)
- [x] 6.6 Add a `test_sparkline.py` regression case for the dedup-timestamp-masking bug (task 4b): a source with two title threads, thread A stored once and never re-stored (dedup skips it as unchanged), thread B stored again later at a higher price — assert Latest price is still thread A's lower, older-timestamped price, not thread B's newer one
- [x] 6.7 Add a case confirming a thread with no currently-matching row doesn't block a sibling thread on the same source from contributing (item-list-latest-price spec scenario "A thread with no currently-matching row does not block other threads on the same source")

## 7. Spec sync and cleanup

- [x] 7.1 Re-run `openspec validate --change "fix-latest-price-retroactive-filtering" --strict` and resolve any issues (previously passed against the pre-4b spec/task text; re-validate now that the requirement and scenarios changed for per-thread resolution)
- [x] 7.2 Re-verify list-view page-load latency against a production-scale dataset after 4b lands (per design.md Migration Plan and the new uncapped-per-thread-scan Risk) — the prior 300-item/4-source/15-update-per-pair sanity check (~0.3s on SQLite) predates the removal of the row cap and no longer reflects the query this change ships. Re-checked: same 300-item/4-source/2-thread/15-row-per-thread scale, worst case (every row but the oldest excluded, forcing a full per-thread scan) is ~0.55s on SQLite; a 4x deeper history (60 rows/thread) scales roughly linearly to ~2.3s, confirming the documented unbounded-scan-cost risk but staying acceptable at today's data volume.
- [x] 7.3 After merge, sync `item-list-latest-price` spec deltas and archive this change per the standard OpenSpec workflow
