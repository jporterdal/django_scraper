# demo-sandbox-reset

## Purpose

Guarantees that every demo visitor finds a populated, unvandalized set of seed items, and returns the shared sandbox to that seed state automatically once the demo has been idle. A visitor who is actively using the demo never has the reset happen underneath them.

## Requirements

### Requirement: Seed data is always present
While demo mode is enabled, the demo SHALL always contain the seed data:
- 4–5 seed items with fixed identifiers, each linked to at least two preset demo Sources
- seed tags
- seed metadata
- preset patterns on at least one seed item-source
- price history spanning several weeks that ends at the time of the most recent reset

A visitor SHALL never see an item list that lacks any seed item.

#### Scenario: First visit after deploy
- **WHEN** the first visitor arrives after a fresh deploy
- **THEN** the item list shows every seed item, each with price history and sparklines

#### Scenario: Seed history looks current
- **WHEN** a visitor views a seed item's detail page shortly after a reset
- **THEN** its most recent price point is from around the time of that reset, not from when the demo data was authored

### Requirement: Seed data is protected from all edits
While demo mode is enabled, the system SHALL refuse every create, edit or delete that targets seed data:
- the seed item's own fields
- its tags assignments
- its item-sources and their patterns
- its metadata actions
- seed tags

Refused actions SHALL save nothing and SHALL show a message saying the record is a protected demo item. Edit and delete controls for seed data SHALL NOT be shown. Running a price update that includes seed items SHALL be allowed and SHALL NOT count as an edit. Seed items SHALL still be selectable for updates and for the bulk-edit workspace.

#### Scenario: Editing a seed item is refused
- **WHEN** a request submits an edit to a seed item's text, priority, active flag, tags or expected values
- **THEN** the seed item is unchanged and a "protected demo item" message is shown

#### Scenario: Deleting a seed tag is refused
- **WHEN** a request submits deletion of a seed tag
- **THEN** the tag still exists and is still assigned to its seed items

#### Scenario: Bulk edit skips seed items
- **WHEN** a visitor applies a bulk edit to a selection containing both seed items and visitor items
- **THEN** the visitor items are changed, the seed items are unchanged, and each seed item's outcome is reported as skipped because it is protected

#### Scenario: Updating seed items is allowed
- **WHEN** a visitor runs "Update Selected" on seed items
- **THEN** new price results are stored for those seed items

### Requirement: Reset restores the seed state
A demo reset SHALL remove all visitor-created data, including items, item-sources, tags, metadata and their results, update runs and fetch jobs. It SHALL then restore the seed data to its defined state, with newly generated price history ending at the time of the reset. The reset SHALL be atomic: concurrent requests SHALL see either the full pre-reset state or the full post-reset state, never an empty or partial one. Item identifiers SHALL NOT be reused for visitor items after a reset: a stale link to a removed visitor item SHALL return not-found rather than show a different item.

#### Scenario: Visitor data removed on reset
- **WHEN** a reset runs after visitors created items and tags and ran updates
- **THEN** only the seed data and its regenerated history remain

#### Scenario: Concurrent request during reset
- **WHEN** a request reads the item list while a reset is in progress
- **THEN** the response shows either the complete pre-reset data or the complete post-reset data

#### Scenario: Stale visitor-item link
- **WHEN** a visitor opens a bookmarked link to a visitor-created item that a reset removed, after another visitor has created a new item
- **THEN** the system responds not-found

#### Scenario: Seed links survive reset
- **WHEN** a visitor opens a link to a seed item made before a reset
- **THEN** the same seed item is shown

### Requirement: Reset on boot
While demo mode is enabled, the deployment's start-up SHALL perform a reset before the web server accepts requests. A fresh or restarted deployment therefore always starts from the seed state.

#### Scenario: Restart restores seed state
- **WHEN** the demo deployment is restarted after visitors modified data
- **THEN** the first request after restart sees only the seed data

### Requirement: Idle-gated automatic reset
While demo mode is enabled, the system SHALL perform a reset at the start of the first request that arrives when both of the following are true:
- no request has been handled for at least `DEMO_RESET_IDLE_SECONDS`
- at least `DEMO_RESET_MIN_INTERVAL_SECONDS` have passed since the last reset

That request SHALL be served from the freshly reset data. `DEMO_RESET_IDLE_SECONDS` and `DEMO_RESET_MIN_INTERVAL_SECONDS` SHALL be demo-specific Django settings. Each SHALL default to 3600 seconds and be overridable by an environment variable of the same name, and neither SHALL have any effect when demo mode is off. When several requests arrive at once while a reset is due, exactly one reset SHALL be performed.

Activity SHALL be recorded at least once per `DEMO_ACTIVITY_WRITE_INTERVAL_SECONDS` (a demo-specific setting, default 60), so that the server doesn't write on every request. A reset SHALL therefore become eligible no later than that interval after the idle threshold is reached. A reset SHALL never occur while any request has been handled within the last `DEMO_RESET_IDLE_SECONDS`.

#### Scenario: Reset after an idle hour
- **WHEN** the last request was handled more than `DEMO_RESET_IDLE_SECONDS` plus `DEMO_ACTIVITY_WRITE_INTERVAL_SECONDS` ago, the last reset was more than `DEMO_RESET_MIN_INTERVAL_SECONDS` ago, and a visitor arrives
- **THEN** that visitor's first page is served from freshly reset seed data

#### Scenario: Active visitor is not reset
- **WHEN** a visitor has made a request within the last `DEMO_RESET_IDLE_SECONDS`
- **THEN** no reset occurs, even if the last reset was more than `DEMO_RESET_MIN_INTERVAL_SECONDS` ago

#### Scenario: Recent reset is not repeated
- **WHEN** the demo has been idle longer than `DEMO_RESET_IDLE_SECONDS` but the last reset was less than `DEMO_RESET_MIN_INTERVAL_SECONDS` ago
- **THEN** no reset occurs

#### Scenario: Concurrent arrivals reset once
- **WHEN** several requests arrive simultaneously after a qualifying idle period
- **THEN** exactly one reset is performed and every request is served from the post-reset data

#### Scenario: Settings override defaults
- **WHEN** the deployment sets `DEMO_RESET_IDLE_SECONDS=600` and `DEMO_RESET_MIN_INTERVAL_SECONDS=1800`
- **THEN** reset eligibility uses a 10-minute idle threshold (plus the activity write interval) and a 30-minute minimum interval
