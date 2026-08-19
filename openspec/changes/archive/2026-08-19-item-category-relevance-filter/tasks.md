## 1. Prerequisites

- [x] 1.1 Confirm `search-term-relevance-filter` has been implemented and merged (this change's `add_result` wiring assumes its gate already exists)

## 2. Data model

- [x] 2.1 Change `expected_product_line` on `SearchableItem` (`tracking/models.py`) from a plain-text `CharField` to a list-valued `JSONField` (mirroring `ItemSource.title_include_patterns`), empty list by default
- [x] 2.2 Change `expected_category` on `SearchableItem` (`tracking/models.py`) from a plain-text `CharField` to a list-valued `JSONField`, empty list by default, independent of `expected_product_line`
- [x] 2.3 Add `product_line` column to `SearchResult` (`tracking/models.py`), populated at storage time from the parser's product-line signal — unaffected by the list-vs-single-value change on `SearchableItem`, still a single per-row value
- [x] 2.4 Add a new `ObservedCategoryValue` model (`tracking/models.py`): `source` (FK to `Source`), `field_name` (choice: `"category"` | `"product_line"`), `value` (raw string as observed), `last_seen` (updated on every repeat observation); unique together on `(source, field_name, value)`
- [x] 2.5 Generate and apply a new Django migration for the `expected_product_line`/`expected_category` field-type change (2.1–2.2); confirm it correctly migrates any already-stored string values into single-item lists rather than dropping data
- [x] 2.6 Add a one-time data migration backfilling `ObservedCategoryValue(field_name="category")` from distinct historical `SearchResult.category` values, grouped by `source` — restores immediate suggestion coverage for `category` on deploy; `product_line` has no equivalent backfill source and starts cold regardless (see design.md Decision 9)
- [x] 2.7 Expose `expected_product_line` and `expected_category` in the relevant item create/edit form(s) (`tracking/forms.py`) and templates via the checkbox + manual-entry mechanism built in Section 5 (not a plain text input), labeled in user-facing plain-text terms (no mention of regex)

## 3. Base-class filtering

- [x] 3.1 Keep the normalized-substring-match helper on `JSONSearchParser` (`tracking/parsers.py`) that case-folds and whitespace-collapses both a single expected value and a candidate row's signal, `re.escape()`s the expected value, and reports whether it appears as a substring — reused per listed value by 3.2
- [x] 3.2 Update `JSONSearchParser.add_result` (signature unchanged: `add_result(title, price, instock, category="", product_line="")` — the per-row signals stay single values) to check the row's `product_line` signal against **every** value in `self.expected_product_line` (OR — any match passes) and the row's `category` signal against every value in `self.expected_category` (OR), so a row is only appended to `self.results` when it passes the term-relevance check and both list-valued checks
- [x] 3.3 Skip each check independently (pass all rows on that axis) when its corresponding expected-value **list is empty** — `expected_product_line` being empty must not affect the `expected_category` check, and vice versa
- [x] 3.4 Update the DEBUG-level log line for a rejected row to include the title, the row's signal(s), and the item's full expected-value **list(s)** (not a single value), without raising or affecting `FetchJob` status
- [x] 3.5 Keep the **unconditional** `ObservedCategoryValue` upsert in `add_result` for every row processed — both the `category` and `product_line` raw signals, whenever non-blank — running *before* the term-relevance/`expected_product_line`/`expected_category` checks decide whether the row is kept. Unaffected by the list-vs-single-value change: this upsert only ever touches the row's own raw signals, never `self.expected_product_line`/`self.expected_category`
- [x] 3.6 Keep the storage code path that persists `SearchResult` rows storing the `product_line` value — unaffected, still a single per-row value

## 4. Vendor parser wiring

- [x] 4.1 `WtFiltersParser.parse_data` supplies `row["category"]` as the `product_line` signal — unaffected by this revision (row-level signal extraction, unrelated to the item-level list-vs-single-value change)
- [x] 4.2 `ShopifyParser.parse_data` supplies `src["General_Game_Type"]` (or `src["Game Type"]`) as the `product_line` signal — unaffected
- [x] 4.3 `StorepassParser.parse_data` supplies `product["vendor"]` as the `product_line` signal — unaffected
- [x] 4.4 No display-behavior change to existing `category` output for `wt`/`f2f`/`hfx` — unaffected

## 5. Value-discovery UI

- [x] 5.1 Keep the query/view helper (`observed_values_for_item`) returning distinct `ObservedCategoryValue` `(source_key, value)` pairs (not `SearchResult`, not deduplicated across sources) for `field_name="product_line"` and `field_name="category"`, ordered by `-last_seen`, scoped to the `Source`s used by a given item's configured `ItemSource`s — already returns the right shape for the checkbox UI below, no query change needed
- [x] 5.2 Replace the `datalist` wiring on the `SearchableItem` create/edit form with: (a) a `MultipleChoiceField` rendered as `CheckboxSelectMultiple`, choices built per-instance from 5.1 with each choice's value = the raw vendor string and label = `"{value} ({source_key})"`, choices **not** deduplicated across sources; and (b) a separate plain-text `Textarea` field ("Other value(s), one per line") for manual entries — one pair of fields for `expected_product_line`, one pair for `expected_category`
- [x] 5.3 On form save, merge each field's checked suggestion values with its manual-textarea lines and deduplicate by exact string equality (`list(dict.fromkeys(...))`, preserving order) before writing to the model's list field
- [x] 5.4 On form load (edit), pre-check every suggestion checkbox (across all matching sources) whose value is in the item's currently stored list; populate the manual textarea with any stored values that don't match any current suggestion choice, so nothing already configured on the item is silently dropped from the form
- [x] 5.5 Confirm the form degrades gracefully (checkbox group absent, manual textarea present, no error) when an item has no configured sources or no `ObservedCategoryValue` rows yet
- [x] 5.6 Polish the checkbox suggestion UI: wrap each field's checkbox group in a scrollable container (bounded `max-height`, `overflow-y: auto`) so a long suggestion list doesn't push the rest of the form off-screen; sort/group choices alphabetically by vendor and then alphabetically by value within each vendor (display-only ordering, independent of the underlying query's `-last_seen` order); exclude `CheckboxSelectMultiple`/`RadioSelect` from the shared Bootstrap-class helper, since Django applies a widget's `attrs["class"]` to both the outer options container and each individual `<input>`, and forcing `form-check-input` there would shrink the container to checkbox dimensions; visually indent the "Other expected …" manual-entry subsections under their parent checkbox section

## 6. Fixtures

- [x] 6.1 Same-title, different-product-line row in `tracking/fixtures/html/wt/search_results_product_line_mismatch.json` — unaffected by this revision (vendor payload shape, unrelated to item-level list-vs-single-value change)
- [x] 6.2 Equivalent row in the `f2f` fixture — unaffected
- [x] 6.3 Equivalent row in the `hfx` fixture — unaffected
- [x] 6.4 Same-product-line, different-category (set) row exercising the `expected_category` reject path — unaffected

## 7. Test coverage

- [x] 7.1 Update `JSONSearchParser`/base-class unit tests for `expected_product_line`/`expected_category` to pass **lists** instead of single strings, and add coverage for OR-within-a-field semantics: a row matching only the *second* listed value is still included; a row matching none of several listed values is excluded — alongside the existing match/mismatch/blank-list/case-insensitivity/whitespace/regex-metacharacter cases
- [x] 7.2 Update the "both fields set simultaneously" unit test (pass-both included, pass-one-fail-other excluded) to use list-valued fields, including a case where the passing match comes from a non-first list entry
- [x] 7.3 Update `tracking/tests/test_wtfilters_parser.py`'s product-line/category tests to pass `expected_product_line`/`expected_category` as lists (e.g. `["Magic"]`) instead of strings
- [x] 7.4 Update the `ShopifyParser` product-line/category tests (`tracking/tests/test_parsers.py`) to pass list-valued expected fields
- [x] 7.5 Update the `StorepassParser` product-line/category tests (`tracking/tests/test_parsers.py`) to pass list-valued expected fields
- [x] 7.6 Update the `SearchableItem` model/form tests confirming both new fields default to an **empty list** and round-trip a list of values correctly, independently of each other
- [x] 7.7 `SearchResult.product_line` populated/exported/displayed test — unaffected, still a single per-row value
- [x] 7.8 `ObservedCategoryValue` upserted for an **accepted** row's category/product-line signals — unaffected, this test doesn't touch `expected_product_line`/`expected_category` at all
- [x] 7.9 Update the **rejected**-row `ObservedCategoryValue` upsert test to pass `expected_product_line` as a list (e.g. `["Magic"]`) instead of a string — underlying behavior (upsert happens regardless of rejection) is unchanged, only the constructor kwarg's type changes
- [x] 7.10 Value-discovery query helper test (`observed_values_for_item` scoping + empty-list behavior) — unaffected, doesn't touch `expected_product_line`/`expected_category`
- [x] 7.11 `category` backfill data migration test — unaffected
- [x] 7.12 Add a test confirming the merge/dedupe-on-save logic: checking two different-vendor checkboxes that share the same raw value stores that value exactly once; a manually entered value not among suggestions is added alongside checked suggestions
- [x] 7.13 Add a test confirming edit-time pre-population: a stored value matching suggestion checkboxes from multiple vendors pre-checks all of them; a stored value matching no current suggestion appears in the manual textarea instead
- [x] 7.14 Run the full `tracking` test suite and confirm no existing assertions (result counts, fixture-derived rows, category display values) regress

## 8. Documentation

- [x] 8.1 Vendor investigation docs note which raw field feeds `expected_product_line` vs. `expected_category` — unaffected by this revision (describes per-row raw signal mapping, not the item-level field's cardinality)
- [x] 8.2 Explicit callout in `tracking/docs/hfx_investigation.md` distinguishing the two `product_line` concepts — unaffected
- [x] 8.3 Fixture `README.md` notes about off-product-line/off-category regression rows — unaffected
