## ADDED Requirements

### Requirement: Latest price is the minimum of each source's own latest known price
For each `SearchableItem` shown on the `view_terms` list, the system SHALL compute the displayed "Latest price" as the minimum, across the item's linked sources, of each source's own most recent in-stock `SearchResult` price. The comparison SHALL NOT be restricted to results that share the same `WebUpdate` — a source's most recent in-stock price remains its current price even when other sources belonging to the same item are checked or updated more recently.

#### Scenario: A source not re-checked in the latest run still wins if cheaper
- **WHEN** an item has two sources, Source A and Source B, both last stored on an earlier `WebUpdate` at $9.99 and $5.25 respectively, and a later `WebUpdate` stores a new $7.99 result only for Source A (Source B's price is unchanged and therefore not re-stored)
- **THEN** the item's Latest price is $5.25, attributed to Source B

#### Scenario: All sources checked and stored together
- **WHEN** an item's sources are all checked and stored as part of the same `WebUpdate`
- **THEN** the Latest price is the minimum in-stock price among those results, same as before this change

#### Scenario: No source has ever stored an in-stock price
- **WHEN** an item has no `SearchResult` rows with `instock=1`, or has no linked sources at all
- **THEN** `latest_known_minprice`, `latest_known_minprice_title`, and `latest_known_minprice_source` are all `None`

### Requirement: Latest price, title, and source annotations describe the same result
The `latest_known_minprice`, `latest_known_minprice_title`, and `latest_known_minprice_source` values displayed together SHALL always originate from the same winning `(item, source)` result — never independently resolved values that could describe different sources or different points in time.

#### Scenario: Title and source match the winning price
- **WHEN** the Latest price for an item resolves to a given source's most recent in-stock result
- **THEN** the displayed title and source key are that same result's title and source key, not another source's
