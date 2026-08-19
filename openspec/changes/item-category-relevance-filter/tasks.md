## 1. Prerequisites

- [x] 1.1 Confirm `search-term-relevance-filter` has been implemented and merged (this change's `add_result` wiring assumes its gate already exists)

## 2. Data model

- [x] 2.1 Add `expected_product_line` plain-text field to `SearchableItem` (`tracking/models.py`), blank by default
- [x] 2.2 Add `expected_category` plain-text field to `SearchableItem` (`tracking/models.py`), blank by default, independent of `expected_product_line`
- [x] 2.3 Add `product_line` column to `SearchResult` (`tracking/models.py`), populated at storage time from the parser's product-line signal
- [x] 2.4 Add a new `ObservedCategoryValue` model (`tracking/models.py`): `source` (FK to `Source`), `field_name` (choice: `"category"` | `"product_line"`), `value` (raw string as observed), `last_seen` (updated on every repeat observation); unique together on `(source, field_name, value)`
- [x] 2.5 Generate and apply the Django migration for all new fields/columns/table (2.1–2.4)
- [x] 2.6 Add a one-time data migration backfilling `ObservedCategoryValue(field_name="category")` from distinct historical `SearchResult.category` values, grouped by `source` — restores immediate suggestion coverage for `category` on deploy; `product_line` has no equivalent backfill source and starts cold regardless (see design.md Decision 9)
- [x] 2.7 Expose `expected_product_line` and `expected_category` in the relevant item create/edit form(s) (`tracking/forms.py`) and templates, labeled in user-facing plain-text terms (no mention of regex)

## 3. Base-class filtering

- [x] 3.1 Add a normalized-substring-match helper to `JSONSearchParser` (`tracking/parsers.py`) that case-folds and whitespace-collapses both an expected value and a candidate row's signal, `re.escape()`s the expected value, and reports whether it appears as a substring
- [x] 3.2 Widen `JSONSearchParser.add_result` to `add_result(title, price, instock, category="", product_line="")` and wire the helper in for both `expected_product_line` (checked against `product_line`) and `expected_category` (checked against `category`), so a row is only appended to `self.results` when it passes the term-relevance check and both new checks
- [x] 3.3 Skip each check independently (pass all rows on that axis) when its corresponding expected value is blank — `expected_product_line` being blank must not affect the `expected_category` check, and vice versa
- [x] 3.4 Add a DEBUG-level log line when a row is rejected for product-line or category mismatch, including the title, the row's signal(s), and the item's expected value(s), without raising or affecting `FetchJob` status
- [x] 3.5 Add an **unconditional** `ObservedCategoryValue` upsert in `add_result` for every row processed — both the `category` and `product_line` raw signals, whenever non-blank — running *before* the term-relevance/`expected_product_line`/`expected_category` checks decide whether the row is kept. Unlike 3.4's log line, this must NOT be gated on rejection: it fires for accepted and rejected rows alike (this is what lets the value-discovery UI in Section 5 see collisions that filtering would otherwise hide)
- [x] 3.6 Update the storage code path that persists `SearchResult` rows to also store the new `product_line` value

## 4. Vendor parser wiring

- [x] 4.1 Update `WtFiltersParser.parse_data` to supply `row["category"]` as the `product_line` signal (new extraction — the existing `category` signal via `subcategory`/`category` is unchanged)
- [x] 4.2 Update `ShopifyParser.parse_data` to supply `src["General_Game_Type"]` (or `src["Game Type"]`) as the `product_line` signal (new extraction — the existing `category` signal via `MTG_Set_Name`/`Set` is unchanged)
- [x] 4.3 Update `StorepassParser.parse_data` to supply `product["vendor"]` as the `product_line` signal (new extraction — the existing `category` signal via `productLineData["set"]` is unchanged)
- [x] 4.4 Confirm no display-behavior change to existing `category` output for `wt`/`f2f`/`hfx` — `category`'s meaning and column are untouched by this change; `product_line` is purely additive

## 5. Value-discovery UI

- [x] 5.1 Add a query/view helper returning distinct `ObservedCategoryValue` values (not `SearchResult`) for `field_name="product_line"` and `field_name="category"`, grouped by `Source.key` and ordered by `-last_seen`, scoped to the `Source`s used by a given item's configured `ItemSource`s
- [x] 5.2 Wire the helper into the `SearchableItem` create/edit form as a `datalist` (or equivalent) attached to the `expected_product_line` and `expected_category` inputs
- [x] 5.3 Confirm the form degrades gracefully (plain free-text input, no error) when an item has no configured sources or no `ObservedCategoryValue` rows yet

## 6. Fixtures

- [x] 6.1 Add a same-title, different-product-line row to `tracking/fixtures/html/wt/search_results_sample.json` (or a supplementary fixture) exercising the `expected_product_line` reject path
- [x] 6.2 Add an equivalent same-title, different-product-line row to the `f2f` fixture
- [x] 6.3 Add an equivalent same-title, different-product-line row to the `hfx` fixture
- [x] 6.4 Add a same-product-line, different-category (set) row to at least one fixture exercising the `expected_category` reject path independently of `expected_product_line`

## 7. Test coverage

- [x] 7.1 Add `JSONSearchParser`/base-class unit tests exercising, for both `expected_product_line` and `expected_category` independently: match (included), mismatch (excluded), blank (pass-through), case-insensitivity, incidental whitespace, and a value containing regex metacharacters matched literally
- [x] 7.2 Add a unit test exercising both fields set simultaneously: pass-both (included), pass-one-fail-other (excluded)
- [x] 7.3 Extend `tracking/tests/test_wtfilters_parser.py` using the fixtures from 6.1 and 6.4, asserting off-product-line and off-category rows are excluded while matching rows are retained
- [x] 7.4 Extend the `ShopifyParser` fixture tests (`tracking/tests/test_parsers.py`) using the fixture from 6.2
- [x] 7.5 Extend the `StorepassParser` fixture tests (`tracking/tests/test_parsers.py`) using the fixture from 6.3
- [x] 7.6 Add/extend a `SearchableItem` model or form test confirming both new fields default to blank and round-trip correctly, independently of each other
- [x] 7.7 Add a test confirming `SearchResult.product_line` is populated and exported/displayed correctly
- [x] 7.8 Add a test confirming `ObservedCategoryValue` is upserted (created, then `last_seen` updated on repeat) for an **accepted** row's category and product-line signals
- [x] 7.9 Add a test confirming `ObservedCategoryValue` is still upserted for a row **rejected** by the term-relevance, `expected_product_line`, or `expected_category` check — the key behavior this design closes the gap on
- [x] 7.10 Add a test for the value-discovery query helper: scoping to an item's configured vendors only, sourced from `ObservedCategoryValue` (not `SearchResult`), and empty-list behavior when no observations exist
- [x] 7.11 Add a test for the `category` backfill data migration (2.6): distinct historical `SearchResult.category` values, grouped by `source`, land correctly in `ObservedCategoryValue`
- [x] 7.12 Run the full `tracking` test suite and confirm no existing assertions (result counts, fixture-derived rows, category display values) regress

## 8. Documentation

- [x] 8.1 Update `tracking/docs/wt_investigation.md`, `f2f_investigation.md`, and `hfx_investigation.md` to note which raw field now feeds `expected_product_line` vs. `expected_category`
- [x] 8.2 Add an explicit callout in `tracking/docs/hfx_investigation.md` distinguishing the existing `product_line` Storepass **query parameter** (request-side, per-`ItemSource`) from this change's `product_line` **field/column** (response-side filtering) — same vocabulary, different layer, per `design.md` Decision 6
- [x] 8.3 Add a short note to each vendor's fixture `README.md` that future fixture refreshes should ideally include at least one off-product-line row and one off-category row for regression coverage, mirroring the note added by `search-term-relevance-filter` for off-term rows
