## Purpose

Defines hard, configurable ceilings on how much a deployment can track and how much it can fetch: total item count, rows kept per fetch, how soon an item may be fetched again, and how many fetches may happen per day. These bound hosting cost and outbound traffic regardless of who is using the site, and each limit is inert when unset.

## ADDED Requirements

### Requirement: Item cap

When an item cap is configured, the total number of items (active and inactive) SHALL never exceed it. This SHALL hold for every way of creating items, including bulk add, and under concurrent creation. An attempt to create an item at the cap SHALL be rejected with a message stating the cap, and SHALL create nothing. The cap SHALL apply equally to anonymous visitors and operators. Deleting items (through the administration site) frees slots.

#### Scenario: Single add at the cap

- **WHEN** the item cap is 25, 25 items exist, and a visitor submits a new item
- **THEN** no item is created
- **AND** the visitor is told the item limit has been reached

#### Scenario: Bulk add exceeding remaining capacity

- **WHEN** the item cap leaves 3 slots and a visitor submits a bulk add of 10 terms
- **THEN** no items are created
- **AND** the message states that 3 slots remain

#### Scenario: Bulk add within remaining capacity

- **WHEN** the item cap leaves 10 slots and a visitor submits a bulk add of 4 terms
- **THEN** all 4 items are created

#### Scenario: Concurrent creation at one slot remaining

- **WHEN** the item cap leaves exactly 1 slot and two visitors submit new items at the same moment
- **THEN** exactly one item is created
- **AND** the total never exceeds the cap

#### Scenario: No cap configured

- **WHEN** no item cap is configured
- **THEN** item creation is not limited by count

### Requirement: Per-fetch row cap

When a per-fetch row cap is configured, each (item, source) fetch SHALL make at most that many result rows eligible for storage. The cap SHALL apply after relevance filtering and before unchanged-row suppression, and SHALL keep the first rows in the order the source returned them and discard the rest. The fetch's job record SHALL show how many rows were discarded by the cap. Repeating a fetch that returns the same rows in the same order SHALL select the same eligible rows, so that a capped result set does not churn between runs.

#### Scenario: More rows than the cap

- **WHEN** the per-fetch cap is 100 and a fetch yields 190 relevant rows
- **THEN** at most 100 rows are eligible for storage
- **AND** the job record shows 90 rows discarded by the cap

#### Scenario: Fewer rows than the cap

- **WHEN** the per-fetch cap is 100 and a fetch yields 40 relevant rows
- **THEN** all 40 are eligible
- **AND** the job record shows 0 rows discarded by the cap

#### Scenario: Repeat fetch is stable

- **WHEN** an identical response is fetched twice under a 100-row cap
- **THEN** the second fetch stores no new rows

#### Scenario: No cap configured

- **WHEN** no per-fetch cap is configured
- **THEN** no rows are discarded by a cap

### Requirement: Per-item fetch cooldown

When a fetch cooldown is configured, an item SHALL NOT be fetched again until the cooldown has elapsed since its most recent fetch that actually sent a request. This SHALL apply to manual and scheduled runs alike. An item skipped for cooldown SHALL have each of its fetch units recorded as a terminal throttled outcome visible in the scrape history, SHALL send no request, and SHALL NOT count against the daily budget. A visitor who requested a manual run SHALL be told how many items were skipped for cooldown.

#### Scenario: Item fetched recently

- **WHEN** the cooldown is 60 minutes and an item was fetched 10 minutes ago
- **THEN** a new run skips that item
- **AND** the skipped units appear in the scrape history as throttled
- **AND** the visitor is told one item was skipped for cooldown

#### Scenario: Cooldown elapsed

- **WHEN** the cooldown is 60 minutes and an item was last fetched 61 minutes ago
- **THEN** a new run fetches that item

#### Scenario: Throttled units do not restart the cooldown

- **WHEN** an item is skipped for cooldown
- **THEN** the item's cooldown expiry is unchanged

### Requirement: Daily fetch budget

When a daily fetch budget is configured, the number of fetches that sent a request in the trailing 24 hours SHALL NOT exceed it. When a run would exceed the remaining budget, only as many units as the budget allows SHALL proceed, and the rest SHALL be recorded as throttled outcomes without sending requests. The budget SHALL be checked when a run is planned and again immediately before each unit sends its request, so that queued work does not exceed the budget after other runs have used it. Throttled units SHALL NOT count against the budget.

#### Scenario: Run larger than the remaining budget

- **WHEN** 5 fetches remain in the budget and a run plans 8 units
- **THEN** 5 units proceed
- **AND** 3 units are recorded as throttled with no request sent

#### Scenario: Budget spent while work is queued

- **WHEN** a unit was planned while budget remained but the budget is exhausted by the time it would run
- **THEN** the unit is recorded as throttled and sends no request

#### Scenario: Budget frees as time passes

- **WHEN** fetches from more than 24 hours ago exist
- **THEN** they do not count against the budget

### Requirement: Limit outcomes are visible and every run still completes

Throttled units and units that could not store results because of the stored-row ceiling SHALL appear in the scrape history with a distinct, human-readable outcome. A run in which some or all units were throttled SHALL still finish, so that progress views stop polling and the run is not left pending.

#### Scenario: Fully throttled run

- **WHEN** every unit in a manual run is throttled
- **THEN** the run finishes
- **AND** the visitor sees why nothing was fetched

#### Scenario: Throttled units in history

- **WHEN** the scrape history lists a run with throttled units
- **THEN** each throttled unit shows a throttled outcome distinct from success and error outcomes
