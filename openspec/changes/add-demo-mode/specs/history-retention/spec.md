## Purpose

Defines how long stored price history is kept and how much of it may exist at once. Price history is stored change-only, so a price that has not changed for months exists as a single old row; retention therefore has to protect each listing's current and boundary rows instead of simply deleting everything older than a date.

## ADDED Requirements

### Requirement: Retention window

When a history retention window (in days) is configured, the system SHALL run a background job at least once every 24 hours that removes stored price history older than the window, subject to the anchor rule below. The job SHALL decide by age alone, not by whether a row still matches current relevance criteria. It SHALL be safe to run repeatedly with the same result, and a failure SHALL be logged without stopping other background work. When no window is configured, the job SHALL remove nothing.

#### Scenario: Job removes old history

- **WHEN** the window is 14 days and rows older than 14 days exist that are not protected by the anchor rule
- **THEN** the next daily run removes them

#### Scenario: Job is repeatable

- **WHEN** the job runs twice in a row with no new data
- **THEN** the second run removes nothing

#### Scenario: No window configured

- **WHEN** no retention window is configured
- **THEN** the job removes no rows

### Requirement: Anchor rule protects current and boundary prices

For each listing (an item, source, and title combination), retention SHALL keep every row inside the window and SHALL also keep the newest row older than the window (the anchor) when one exists. Rows older than the anchor SHALL be removed. A listing whose only row is older than the window SHALL keep that row. Retention SHALL NOT change the "latest price" shown for any item, nor the current price of any listing.

#### Scenario: Old rows around the boundary

- **WHEN** the window is 14 days and a listing has rows dated 40, 30, 20, and 5 days ago
- **THEN** the 20-day row and the 5-day row are kept
- **AND** the 40-day and 30-day rows are removed

#### Scenario: Stable price with a single old row

- **WHEN** a listing's only row is 60 days old because its price never changed
- **THEN** that row is kept

#### Scenario: Latest price unchanged by retention

- **WHEN** retention runs
- **THEN** the latest price and its source and title shown for every item on the item list are the same before and after

#### Scenario: Chart continuity

- **WHEN** a listing's anchor row is older than the window
- **THEN** the anchor row is still available to the item's price-history chart as that listing's earliest point

### Requirement: Run history is pruned without destroying kept results

Scrape runs and their fetch jobs older than the window SHALL be removed only when no kept result row still belongs to that run. A run that an anchor row belongs to SHALL be kept together with its jobs. Metadata fetch requests in a terminal state older than the window SHALL be removed, and pending requests SHALL never be removed by retention. Removing old run history SHALL NOT remove any result row that retention or the ceiling rule would keep.

#### Scenario: Run referenced by an anchor

- **WHEN** an old scrape run is the source of a kept anchor row
- **THEN** the run and its jobs are kept

#### Scenario: Unreferenced old run

- **WHEN** an old scrape run has no kept result rows
- **THEN** the run and its jobs are removed

#### Scenario: Pending metadata request

- **WHEN** a pending metadata fetch request is older than the window
- **THEN** it is not removed

### Requirement: Stored-row ceiling

When a stored-row ceiling is configured, the total number of stored price rows SHALL NOT exceed it. When storing a fetch's rows would exceed the ceiling, the system SHALL first free space by removing the oldest rows that are neither the newest row of their listing nor a listing's anchor. If the ceiling would still be exceeded, the fetch's rows SHALL NOT be stored, existing rows SHALL be left untouched, and the fetch's job SHALL end with a storage-full outcome visible in the scrape history. A listing's newest row and its anchor SHALL never be removed by the ceiling rule.

#### Scenario: Space freed by evicting superseded rows

- **WHEN** storing a fetch would exceed the ceiling and superseded rows exist
- **THEN** the oldest superseded rows are removed until the fetch fits
- **AND** the fetch's rows are stored

#### Scenario: Only protected rows remain

- **WHEN** storing a fetch would exceed the ceiling and every remaining row is a newest row or an anchor
- **THEN** the fetch's rows are not stored
- **AND** the fetch's job shows a storage-full outcome
- **AND** no existing row is removed

#### Scenario: Newest row is never evicted

- **WHEN** the ceiling rule frees space
- **THEN** every listing still has its newest row

#### Scenario: No ceiling configured

- **WHEN** no ceiling is configured
- **THEN** stored rows are not limited by count
