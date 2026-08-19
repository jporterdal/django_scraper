## 1. Base-class filtering

- [ ] 1.1 Add a phrase-containment helper to `JSONSearchParser` (`tracking/parsers.py`) that normalizes `self.term` and a candidate title (lowercase, collapse/strip whitespace) and reports whether the normalized term appears as a contiguous substring of the normalized title
- [ ] 1.2 Wire the helper into `JSONSearchParser.add_result` so a row is only appended to `self.results` when the title passes the check
- [ ] 1.3 Skip the check entirely (pass all rows) when `self.term` is blank/empty after normalization
- [ ] 1.4 Add a DEBUG-level log line when a row is rejected, including the title and term, without raising or affecting `FetchJob` status

## 2. Vendor parser verification

- [ ] 2.1 Confirm `WtFiltersParser.parse_data` requires no changes (routes through `add_result` already) and passes existing tests
- [ ] 2.2 Confirm `ShopifyParser.parse_data` requires no changes and passes existing tests
- [ ] 2.3 Confirm `StorepassParser.parse_data` requires no changes and passes existing tests
- [ ] 2.4 Capture a live `wt` fixture for `term="Fire Dragon"` (mirroring the smoke test run during design) as a permanent regression fixture, since it's the concrete case motivating the phrase-containment policy over token-AND

## 3. Test coverage

- [ ] 3.1 Add a `JSONSearchParser`/base-class unit test exercising: full-phrase match (included), one-word-only match (excluded), zero-word match (excluded), all-words-but-reordered match (excluded — the `"Fire Dragon"`/`"Dragon Fire"` case), case-insensitivity, incidental whitespace in the term, and blank-term pass-through
- [ ] 3.2 Extend `tracking/tests/test_wtfilters_parser.py` using the captured `"Fire Dragon"` fixture (task 2.4), asserting `parser.results` reduces to exactly the genuine `"Fire Dragon (POR)"` row
- [ ] 3.3 Extend the `ShopifyParser` fixture tests in `tracking/tests/test_parsers.py` with an equivalent reordered/false-positive case for the f2f payload shape
- [ ] 3.4 Extend the `StorepassParser` fixture tests in `tracking/tests/test_parsers.py` with an equivalent reordered/false-positive case for the hfx payload shape
- [ ] 3.5 Run the full `tracking` test suite and confirm no existing assertions (result counts, fixture-derived rows) regress

## 4. Documentation

- [ ] 4.1 Update `tracking/docs/wt_investigation.md`'s risk table entry for large/generic result sets to reference the new baseline filter instead of only "use pattern filters"
- [ ] 4.2 Add a short note to `tracking/fixtures/html/wt/README.md`, `.../f2f/README.md`, and `.../hfx/README.md` that the shared `JSONSearchParser` term-relevance check now runs on top of these fixtures, so future fixture refreshes should ideally include at least one off-term row for regression coverage
