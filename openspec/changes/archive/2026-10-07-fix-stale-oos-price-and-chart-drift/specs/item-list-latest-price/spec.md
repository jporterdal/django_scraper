## MODIFIED Requirements

### Requirement: Latest price is the minimum of each source's own latest known price
For each `SearchableItem` shown on the `view_terms` list, the system SHALL compute the displayed "Latest price" as the minimum, across the item's linked sources, of each source's own "latest known price." A source's results are independently deduplicated per distinct title (a source can carry several concurrently-valid result "threads" at once, e.g. distinct matched variants — see `retroactive-result-matching`), so a source's latest known price SHALL be resolved in two steps: first, per title-thread, the thread's **current state** — its most recent `SearchResult` row, **in stock or out of stock**, **that currently matches that item's and item-source's relevance criteria** (search term, expected product line, expected category, title include/exclude patterns); second, the minimum across that source's threads whose current state is in stock with a price. A thread whose current state is out of stock SHALL contribute no price — the system SHALL NOT fall back to an older in-stock row of that thread. A thread that the vendor simply stops returning (no new row stored, in or out of stock) SHALL keep its last known state. A thread's own timestamp SHALL NOT be compared against, or allowed to shadow, another thread's row when determining either thread's latest price — resolving "most recent" happens strictly within a thread, never across threads. The comparison SHALL NOT be restricted to results that share the same `WebUpdate` — a source's most recent in-stock price remains its current price even when other sources (or other threads of the same source) belonging to the same item are checked or updated more recently. When a thread's most recent `WebUpdate` stored more than one currently-matching row for that thread, an in-stock row SHALL take precedence over an out-of-stock row, and among in-stock rows the cheapest SHALL win. When a source's own most recent `WebUpdate` stored more than one in-stock `SearchResult` for the item (tied on timestamp), the source's "latest known price" SHALL deterministically resolve to the cheapest of those tied results, not an arbitrary one. If multiple tied results additionally share the same price, the result SHALL deterministically resolve to the alphabetically-first title among them, not an arbitrary one. A source whose threads are all currently excluded by relevance criteria, or all currently out of stock, SHALL NOT contribute a price to the cross-source minimum.

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
- **WHEN** none of a source's recent in-stock `SearchResult` rows for an item, across any of its threads, currently match relevance criteria
- **THEN** that source contributes no price, and the item's Latest price is the minimum among its other sources' still-matching contributions (or `None` if no source has one)

#### Scenario: A cheaper thread's price is not masked by a costlier thread's more recent timestamp
- **WHEN** a source has two independently-deduplicated title threads for an item (e.g. two matched variants) — thread A's price has been unchanged, and therefore not re-stored by dedup, since an earlier `WebUpdate`, while thread B's price changes on a later `WebUpdate` and gets a fresh `SearchResult` row
- **THEN** the source's contribution to the cross-source minimum is resolved per thread and then compared — thread A's still-current, cheaper price wins — even though thread B's row carries the more recent timestamp

#### Scenario: A thread with no currently-matching row does not block other threads on the same source
- **WHEN** one of a source's title threads has no in-stock `SearchResult` row that currently matches relevance criteria, but another thread on the same source does
- **THEN** the non-matching thread contributes nothing, and the source's latest known price is drawn from its other, currently-matching thread(s) — not `None`

#### Scenario: A thread that goes out of stock stops contributing its old price
- **WHEN** a source's only thread for an item stored an in-stock row at $1.25 on one `WebUpdate`, and a later `WebUpdate` stored an out-of-stock row for the same title
- **THEN** that source contributes no price — the item's Latest price is the minimum among its other sources' contributions (or `None` if none), not the stale $1.25

#### Scenario: Out-of-stock thread does not block an in-stock sibling thread
- **WHEN** a source has two threads for an item, thread A's current state is out of stock and thread B's current state is in stock at $2.00
- **THEN** that source's latest known price is $2.00 from thread B

#### Scenario: A thread that comes back in stock contributes again
- **WHEN** a thread's history is in stock at $1.25, then out of stock, then in stock again at $1.50 on a later `WebUpdate`
- **THEN** that thread contributes $1.50

#### Scenario: A title the vendor stops returning keeps its last known state
- **WHEN** a thread's newest stored row is in stock at $3.00, and later fetches for that source succeed without that title appearing at all (no row stored for it, in or out of stock)
- **THEN** that thread still contributes $3.00

#### Scenario: Same-update in-stock row takes precedence over an out-of-stock row for the same title
- **WHEN** a single `WebUpdate` stores two currently-matching rows with the same title for a source — one out of stock, one in stock at $4.00 — and they are that thread's most recent rows
- **THEN** that thread's current state is in stock at $4.00
