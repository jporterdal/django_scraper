## 1. Design fork resolution (blocking — do first)

- [ ] 1.1 Decide Option A vs. Option B from `design.md`'s Open Questions: does the per-row category signal reuse the existing `category` kwarg/field on `add_result`/`SearchResult` (Option A), or does a new, distinct field get threaded through (Option B)?
- [ ] 1.2 If Option B: pick the new kwarg/field name (e.g. `product_category`) and decide whether it is persisted on `SearchResult`/displayed in the UI, or consumed transiently during filtering only
- [ ] 1.3 Confirm `search-term-relevance-filter` has been implemented and merged (this change's `add_result` wiring assumes its gate already exists)

## 2. Data model

- [ ] 2.1 Add the new plain-text expected-category field to `SearchableItem` (`tracking/models.py`), blank by default
- [ ] 2.2 Generate and apply the Django migration for the new field
- [ ] 2.3 Expose the field in the relevant item create/edit form(s) (`tracking/forms.py`) and templates, labeled in user-facing plain-text terms (no mention of regex)

## 3. Base-class filtering

- [ ] 3.1 Add a normalized-substring-match helper to `JSONSearchParser` (`tracking/parsers.py`) that case-folds and whitespace-collapses both the item's expected category value and a candidate row's category signal, `re.escape()`s the expected value, and reports whether it appears as a substring
- [ ] 3.2 Wire the helper into `JSONSearchParser.add_result` (per the Task 1 decision) so a row is only appended to `self.results` when it passes both the existing term-relevance check and this new category check
- [ ] 3.3 Skip the category check entirely (pass all rows) when the item's expected category value is blank
- [ ] 3.4 Add a DEBUG-level log line when a row is rejected for category mismatch, including the title, the row's category signal, and the item's expected value, without raising or affecting `FetchJob` status

## 4. Vendor parser wiring

- [ ] 4.1 Update `WtFiltersParser.parse_data` to supply its category/product-line signal (`row["category"]`, per design.md) into the check, per the Task 1 decision
- [ ] 4.2 Update `ShopifyParser.parse_data` to supply its category/product-line signal (`src["General_Game_Type"]` or `src["Game Type"]`, per design.md) into the check, per the Task 1 decision
- [ ] 4.3 Update `StorepassParser.parse_data` to supply its category/product-line signal (`product["vendor"]`, per design.md) into the check, per the Task 1 decision
- [ ] 4.4 If Option A was chosen in Task 1, confirm and document the resulting display-behavior change for existing `category` output (`wt`/`f2f`/`hfx` now show product line instead of set name) in `searchableitem_detail.html` and CSV/JSON export

## 5. Fixtures

- [ ] 5.1 Add a same-title, different-category row to `tracking/fixtures/html/wt/search_results_sample.json` (or a supplementary fixture) exercising the reject path
- [ ] 5.2 Add an equivalent same-title, different-category row to the `f2f` fixture
- [ ] 5.3 Add an equivalent same-title, different-category row to the `hfx` fixture

## 6. Test coverage

- [ ] 6.1 Add a `JSONSearchParser`/base-class unit test exercising: category match (included), category mismatch (excluded), blank expected category (pass-through), case-insensitivity, incidental whitespace, and a value containing regex metacharacters matched literally
- [ ] 6.2 Extend `tracking/tests/test_wtfilters_parser.py` using the fixture from 5.1, asserting the off-category row is excluded while the matching row is retained
- [ ] 6.3 Extend the `ShopifyParser` fixture tests (`tracking/tests/test_parsers.py`) using the fixture from 5.2
- [ ] 6.4 Extend the `StorepassParser` fixture tests (`tracking/tests/test_parsers.py`) using the fixture from 5.3
- [ ] 6.5 Add/extend a `SearchableItem` model or form test confirming the new field defaults to blank and round-trips correctly
- [ ] 6.6 Run the full `tracking` test suite and confirm no existing assertions (result counts, fixture-derived rows, category display values) regress

## 7. Documentation

- [ ] 7.1 Update `tracking/docs/wt_investigation.md`, `f2f_investigation.md`, and `hfx_investigation.md` to note which raw field now feeds the category-relevance check
- [ ] 7.2 Add a short note to each vendor's fixture `README.md` that future fixture refreshes should ideally include at least one off-category row for regression coverage, mirroring the note added by `search-term-relevance-filter` for off-term rows
