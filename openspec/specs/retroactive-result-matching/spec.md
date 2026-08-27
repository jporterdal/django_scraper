# retroactive-result-matching

## Purpose
Defines the requirement that a stored `SearchResult` row is only used in price/chart calculations while it still matches the owning item's and item-source's *current* relevance criteria (search term, expected product line, expected category, title include/exclude patterns) — re-evaluated at read time, not assumed valid forever from ingest.

## Requirements

### Requirement: Stored results are re-validated against current relevance criteria before use
A stored `SearchResult` row SHALL only be considered by a "Latest price" calculation or the detail-page price-history chart while it currently matches the owning `SearchableItem`'s and `ItemSource`'s relevance criteria — the search term (`SearchableItem.text`), `expected_product_line`, `expected_category` (both per-vendor), and the `ItemSource`'s `title_include_patterns`/`title_exclude_patterns`. A row is evaluated against the *current* value of each field, not the value in effect when the row was fetched and stored.

#### Scenario: A pattern added after storage excludes an already-stored row
- **WHEN** a `SearchResult` row was stored before an `ItemSource` exclude pattern existed, and the pattern is later added and now matches that row's title
- **THEN** the row is no longer considered when computing that source's contribution to Latest price or the chart, even though it was accepted at fetch time

#### Scenario: A search-term edit excludes an already-stored row
- **WHEN** `SearchableItem.text` is edited such that an already-stored row's title no longer contains the new term as a contiguous phrase
- **THEN** the row is no longer considered when computing Latest price or the chart, even though it matched the term in effect when it was fetched

#### Scenario: An expected-value edit excludes an already-stored row
- **WHEN** `expected_product_line` or `expected_category` is edited such that an already-stored row's `product_line`/`category` no longer matches any currently-applicable value for that row's vendor
- **THEN** the row is no longer considered when computing Latest price or the chart

#### Scenario: A source with no currently-matching recent result contributes nothing
- **WHEN** none of a source's recent in-stock `SearchResult` rows for an item currently match that item's/item-source's criteria (e.g. its only recent activity was later excluded, and no new fetch has produced a currently-matching row)
- **THEN** that source contributes no price to the item's Latest price calculation — it is omitted from the cross-source comparison, not shown with a stale price

#### Scenario: A still-matching row continues to be used normally
- **WHEN** a `SearchResult` row currently matches all applicable relevance criteria
- **THEN** it is eligible for Latest price and chart calculations exactly as before this change, with no regression for the common case

### Requirement: Retroactive matching does not alter stored history
Re-evaluating a `SearchResult` row against current relevance criteria SHALL NOT delete, mutate, or hide the row from the historical record. Excluding a row from a calculation is a read-time/display decision, not a data-retention one.

#### Scenario: Excluded row remains in the detail page's full results table
- **WHEN** a `SearchResult` row no longer matches current relevance criteria
- **THEN** the row still appears in the detail page's full results table (see `r.matches` display treatment) and remains available via CSV/JSON export, unchanged from before this change

#### Scenario: A later-corrected pattern restores a row's eligibility
- **WHEN** an `ItemSource` exclude pattern that previously excluded a stored row is subsequently removed or edited such that the row matches again
- **THEN** the row becomes eligible again for Latest price and chart calculations, with no data loss having occurred while it was excluded
