## MODIFIED Requirements

### Requirement: Latest price is the minimum of each source's own latest known price
For each `SearchableItem` shown on the `view_terms` list, the system SHALL compute the displayed "Latest price" as the minimum, across the item's linked sources, of each source's own most recent in-stock `SearchResult` price **that currently matches that item's and item-source's relevance criteria** (search term, expected product line, expected category, title include/exclude patterns — see `retroactive-result-matching`). The comparison SHALL NOT be restricted to results that share the same `WebUpdate` — a source's most recent in-stock price remains its current price even when other sources belonging to the same item are checked or updated more recently. When a source's own most recent `WebUpdate` stored more than one in-stock `SearchResult` for the item (tied on timestamp), the source's "latest known price" SHALL deterministically resolve to the cheapest of those tied results, not an arbitrary one. If multiple tied results additionally share the same price, the result SHALL deterministically resolve to the alphabetically-first title among them, not an arbitrary one. A source whose recent in-stock results are all currently excluded by relevance criteria SHALL NOT contribute a price to the cross-source minimum.

#### Scenario: A source not re-checked in the latest run still wins if cheaper
- **WHEN** an item has two sources, Source A and Source B, both last stored on an earlier `WebUpdate` at $9.99 and $5.25 respectively, and a later `WebUpdate` stores a new $7.99 result only for Source A (Source B's price is unchanged and therefore not re-stored)
- **THEN** the item's Latest price is $5.25, attributed to Source B

#### Scenario: All sources checked and stored together
- **WHEN** an item's sources are all checked and stored as part of the same `WebUpdate`
- **THEN** the Latest price is the minimum in-stock price among those results, same as before this change

#### Scenario: No source has ever stored an in-stock price
- **WHEN** an item has no `SearchResult` rows with `instock=1`, or has no linked sources at all
- **THEN** `latest_known_minprice`, `latest_known_minprice_title`, and `latest_known_minprice_source` are all `None`

#### Scenario: A source's own latest scrape stores multiple tied in-stock results
- **WHEN** a source's single most recent `WebUpdate` stores multiple in-stock `SearchResult` rows for the same item (e.g. distinct product variants matched by that source in one scrape), at different prices
- **THEN** that source's contribution to the cross-source minimum is the cheapest of those tied rows, regardless of the order the rows were stored in

#### Scenario: Tied results also tie on price
- **WHEN** a source's single most recent `WebUpdate` stores multiple in-stock `SearchResult` rows for the same item at the same price
- **THEN** that source's contribution to the cross-source minimum is attributed to the alphabetically-first title among those rows, regardless of the order the rows were stored in

#### Scenario: A source's newest row is excluded, an older row still matches
- **WHEN** a source's single most recent in-stock `SearchResult` no longer matches current relevance criteria, but an earlier in-stock `SearchResult` for the same source still does
- **THEN** the source's contribution to the cross-source minimum is that earlier, still-matching result's price — not the excluded newer row, and not `None`

#### Scenario: A source's entire recent history is excluded
- **WHEN** none of a source's recent in-stock `SearchResult` rows for an item currently match relevance criteria
- **THEN** that source contributes no price, and the item's Latest price is the minimum among its other sources' still-matching contributions (or `None` if no source has one)
