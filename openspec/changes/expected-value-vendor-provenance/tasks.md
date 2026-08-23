## 1. Data model & migration

- [x] 1.1 Document the new `expected_product_line`/`expected_category` entry shape (`{"value": str, "source": str | None}`) on `SearchableItem` — no `JSONField` schema change needed, only the payload shape
- [x] 1.2 Write the data migration transforming existing `list[str]` rows to `list[{"value", "source"}]`: per item, per stored value, attempt a best-effort single-vendor match against the item's currently-configured `ItemSource`s' `ObservedCategoryValue` rows (same `field_name`, exact value equality); tag with that vendor's key if exactly one match, else `source: null`
- [x] 1.3 Provide `reverse_code` for the migration (`list[dict]` → `list[str]`, dropping `source`) so it stays reversible
- [x] 1.4 Add a small model-level helper (e.g. a method on `SearchableItem`) that, given a field name and a vendor `Source.key`, returns the flat list of applicable `.value`s — every entry tagged with that vendor's key, plus every entry with `source: null` — so the matching handoff and any other consumer don't each reimplement the per-vendor pruning logic

## 2. Matching handoff — per-vendor pruning (parsers.py itself stays untouched)

- [x] 2.1 Update `tracking/scrape.py:633-634` to build each parser instance's `expected_product_line`/`expected_category` from the task 1.4 helper, scoped to that instance's own vendor — one pruned list per `(item, ItemSource)` pair, not one flat list per item
- [x] 2.2 Add regression tests confirming the new per-vendor pruning behavior end-to-end: a vendor-tagged entry filters only that vendor's parser instance; a `source: null` entry filters every configured vendor's parser instance; a vendor with zero applicable entries has the check disabled for its own rows even when the item has entries tagged for other vendors

## 3. Single-item form — suggestion choice identity & pre-check

- [x] 3.1 Rework `_suggestion_choices` to build choices keyed by the `(source, value)` pair, reusing the existing JSON-encoding scheme already introduced for `bulk-item-editing`'s vendor-scoped suggestions (`_bulk_edit_suggestion_choice_value` in `tracking/forms.py`) rather than inventing a second encoding
- [x] 3.2 Rework `SearchableItemForm.__init__`'s pre-check logic to check exact `(source, value)` pair membership against the item's stored entries, not string membership
- [x] 3.3 Rework `SearchableItemForm.save()`/the merge logic (`_merge_and_dedupe`, `_split_stored_values`) to write back exactly the checked `(source, value)` pairs plus newly added manual entries, deduplicated by exact `(value, source)` pair equality — not by value alone

## 4. Single-item form — inline manual entries, not a textarea

- [x] 4.1 Decide and implement the "add a new value" input mechanism (single value at a time vs. multi-line paste like today's textarea) — resolves design.md's open question (decided: keep today's multi-line textarea, repurposed to add-only; consistent with the app's existing one-value-per-line convention elsewhere)
- [x] 4.2 Render `source: null` entries as inline rows labeled "Manual entry" in `searchableitem_form.html`, alongside vendor-labeled suggestion rows, replacing the old separate free-text textarea display (existing generic `_suggestions`-field rendering already surfaces the "(Manual entry)"-labeled choice rows built by `_suggestion_choices`; no template change needed)
- [x] 4.3 Ensure a stored vendor-tagged entry whose vendor no longer has a live `ObservedCategoryValue` suggestion still renders as its own row (not silently dropped from the form)
- [x] 4.4 Apply Bootstrap form-class styling consistently with the existing `_apply_bootstrap_form_classes` pattern

## 5. Tests — correct existing assertions, add new coverage

- [x] 5.1 Rewrite `test_checking_suggestions_from_two_vendors_dedupes_to_one_stored_value` to assert two independent `(value, source)` entries are stored, not one deduplicated string
- [x] 5.2 Rewrite `test_stored_value_from_multiple_vendors_prechecks_all_matching_checkboxes` to assert only the checkbox matching the stored entry's exact vendor pre-checks
- [x] 5.3 Add a test: unchecking one of two same-valued vendor checkboxes removes only that vendor's stored entry, leaving the other vendor's entry intact
- [x] 5.4 Add a test: a manual (`source: null`) entry is never reclassified as a vendor suggestion, even after a vendor later reports the identical raw string
- [x] 5.5 Add a test: a stored vendor-tagged entry whose vendor no longer offers a live suggestion still renders and remains individually removable
- [x] 5.6 Add a test: a vendor-tagged entry filters only that vendor's rows — a candidate row from a *different* configured vendor is unaffected by it, even when that other vendor has zero applicable entries of its own (the "no fallback" behavior is intentional, not a gap — see design.md Decision 2)
- [x] 5.7 Add a test: a `source: null` (manual) entry filters every vendor configured for the item
- [x] 5.8 Add migration tests: a value matching exactly one configured vendor's `ObservedCategoryValue` is attributed to that vendor (and therefore only filters that vendor post-migration); a value matching zero or more than one vendor degrades to `source: null` (and therefore filters every configured vendor post-migration, preserving near-today's coverage for the convergent-wording case)

## 6. Cleanup & verification

- [x] 6.1 Decide whether `tracking/admin.py`'s `SearchableItemAdmin` needs any adjustment for the new `list[dict]` shape, or is left as a raw-JSON-editable fallback (design.md open question) — implement if needed (decided: left as-is — no `fields`/`exclude` override, so Django's default `JSONField` widget already round-trips `list[dict]` as raw editable JSON exactly as it did `list[str]`; no code change needed)
- [x] 6.2 Spot-check a sample of migrated multi-vendor items' `expected_product_line`/`expected_category` entries against their pre-migration configuration, given the migration now has real filtering consequences, not just cosmetic ones (design.md Migration Plan step 3) — applied 0022 to the local dev DB; both multi-vendor items (`Lightning Bolt`: wt/hfx, `smoketest-item`: smokewt/smokef2f) migrated exactly as predicted from their `ObservedCategoryValue` rows (single-vendor matches attributed correctly; the ambiguous cross-vendor value degraded to `source: null`)
- [x] 6.3 Run the full test suite and confirm no regressions outside `test_item_category_relevance.py` — 464/464 passing
