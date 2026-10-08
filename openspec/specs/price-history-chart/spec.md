# price-history-chart

## Purpose

Defines how per-source price-history points are computed for the item detail-page chart and the `view_terms` list sparkline, including out-of-stock gaps and solid/hollow point meaning. It also requires those points to agree with the item's reported Latest price.

## Requirements


### Requirement: Each chart point is the source's lowest current price as of that update
For each linked source of an item, the system SHALL build a chronological series with one entry per evaluated `WebUpdate` for that source. An evaluated `WebUpdate` is one where the source either stored at least one `SearchResult` for the item, or had a successful fetch that parsed one or more results. The entry's price SHALL be the minimum price across all of the source's title threads whose state *as of that update* is in stock with a non-null price. A thread's state as of an update is resolved with the same rules as Latest price (see `item-list-latest-price`): its most recent currently-matching row stored at or before that update, in stock or out of stock. Rows not stored in that update (e.g. siblings skipped by dedup because they were unchanged) SHALL still count toward the minimum through their thread's carried-forward state. A source fetch that failed, or that succeeded with zero parsed results, SHALL NOT produce an entry. Rows that no longer match current relevance criteria SHALL be ignored, as `retroactive-result-matching` requires.

#### Scenario: An update that only re-stores a costlier sibling does not spike the chart
- **WHEN** a source's history for an item is: on 08-19, rows "HOB-137" $1.25 and "HOB-137 Foil" $1.25 are stored; on 08-21, only "Extended Art" $3.00 is stored (the other two titles are unchanged, so not re-stored)
- **THEN** the 08-21 chart entry for that source is $1.25, not $3.00

#### Scenario: Chart follows the bugdata.csv hfx sequence
- **WHEN** an hfx source stores 08-19 {HOB-137 $1.25, Foil $1.25}, 08-21 {Extended Art $3.00}, 08-22 {HOB-137 $1.50, Foil $2.50, Extended Art $3.50}, 08-23 {HOB-137 $1.75, Foil $2.25, Extended Art $3.25}
- **THEN** that source's chart entries are $1.25, $1.25, $1.50, $1.75 for 08-19, 08-21, 08-22, 08-23

#### Scenario: Unchanged fetch carries all threads forward
- **WHEN** a source fetch succeeds, parses results, and stores none (everything unchanged)
- **THEN** the entry for that update has the same price as the source's previous entry

#### Scenario: Failed or empty fetch produces no entry
- **WHEN** a source fetch for an update fails, or succeeds with zero parsed results
- **THEN** no chart entry is produced for that source at that update

### Requirement: Out-of-stock periods are shown as gaps
When an evaluated update leaves a source with no thread that is in stock and currently matching, the system SHALL emit that entry with a `null` price, so the chart line breaks instead of continuing at a stale price. The gap entry SHALL be labeled with its date, and its tooltip SHALL say it is out of stock instead of showing a price. Gap entries before the source's first priced entry SHALL be omitted. The system SHALL NOT emit a "confirmed, unchanged" entry carrying a price from a thread whose state is out of stock.

#### Scenario: Source goes out of stock
- **WHEN** a wt source stores in-stock rows on 08-22 (lowest $1.25) and, on 08-23, stores out-of-stock rows for all of those titles
- **THEN** the 08-22 entry is $1.25 and the 08-23 entry is a gap (`null` price), not $1.25

#### Scenario: Still out of stock on later unchanged fetches
- **WHEN** after that, a later fetch succeeds with results but stores nothing because the titles are still out of stock and unchanged
- **THEN** the entry for that later update is also a gap, not a hollow "confirmed, unchanged" $1.25 point

#### Scenario: Back in stock resumes the line
- **WHEN** a later update stores an in-stock row for one of that source's threads at $1.50
- **THEN** that update's entry is $1.50 and the line resumes after the gap

### Requirement: Solid and hollow points reflect whether the source's lowest price changed
On the detail-page chart, each priced entry SHALL be drawn solid ("price changed") when it is the source's first priced entry, the first priced entry after a gap, or its price differs from the source's previous priced entry. Otherwise it SHALL be drawn hollow ("confirmed, unchanged").

#### Scenario: Costlier sibling change does not mark the lowest price as changed
- **WHEN** an update stores a new row only for a costlier sibling thread, and the source's lowest price is the same as its previous entry
- **THEN** that entry is hollow ("confirmed, unchanged")

#### Scenario: Lowest price changes
- **WHEN** an update changes the source's lowest in-stock price compared with its previous priced entry
- **THEN** that entry is solid ("price changed")

### Requirement: Chart and sparkline agree with Latest price
For each source, the price state reached at the end of that source's series SHALL equal that source's contribution to the item's Latest price (see `item-list-latest-price`), and SHALL be absent exactly when the source contributes no price. The `view_terms` list sparkline SHALL use the same per-source series as the detail chart for the Latest price's winning source, including relevance re-validation and out-of-stock gaps. Its final priced entry SHALL therefore equal the displayed Latest price.

#### Scenario: Detail chart's last point equals Latest price
- **WHEN** an item's Latest price is $1.75 from source hfx
- **THEN** the most recent priced entry in hfx's detail-page chart series is $1.75

#### Scenario: Sparkline's last point equals Latest price
- **WHEN** an item's Latest price resolves to a source and that source has a price history
- **THEN** the last priced entry of the item's list sparkline equals the displayed Latest price

#### Scenario: Sparkline ignores rows excluded by current relevance criteria
- **WHEN** a row on the Latest price's source no longer matches the item's current relevance criteria
- **THEN** the row does not contribute to any sparkline entry, just as it does not contribute to Latest price or the detail chart
