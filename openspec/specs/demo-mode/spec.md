# demo-mode

## Purpose

Lets one environment variable turn a deployment into a public, no-login demo. Anonymous visitors can try the app's features, but only through a closed allowlist of bounded inputs that cannot make outbound requests or exhaust server resources.

## Requirements

### Requirement: Single variable enables demo mode
The system SHALL enable demo mode when the `DEMO_MODE` environment variable is true. It SHALL behave exactly as it does today when `DEMO_MODE` is unset or false. While demo mode is enabled, the system SHALL apply these settings regardless of any other variable:
- debug mode off
- background tasks run in-process with no Redis
- a local SQLite database, ignoring `DATABASE_URL`
- zero scrape request delay and jitter
- no debug log file

The standard deployment variables (`SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `SECURE_DEPLOYMENT`) SHALL remain required and SHALL keep their existing meaning.

#### Scenario: Demo mode forces debug off
- **WHEN** the app starts with `DEMO_MODE=True` and `DEBUG` unset or `DEBUG=True`
- **THEN** debug mode is off and error pages show no tracebacks

#### Scenario: Demo mode needs no Redis or Postgres
- **WHEN** the app starts with `DEMO_MODE=True` and `REDIS_URL` and `DATABASE_URL` set to unreachable values
- **THEN** the app serves pages and completes price updates using only the local SQLite database and in-process tasks

#### Scenario: Demo mode off is unchanged
- **WHEN** `DEMO_MODE` is unset
- **THEN** login is required, Sources and Schedules are editable, free-text patterns are accepted, and no demo banner is shown

### Requirement: Visitors use the app without logging in
While demo mode is enabled, every request SHALL be handled as a single fixed demo user. That user SHALL NOT be staff or a superuser. No login SHALL be required, and the login, logout and admin pages SHALL NOT be reachable. Visiting the app SHALL NOT create any server-side session record.

#### Scenario: Anonymous visitor reaches the item list
- **WHEN** a visitor with no cookies requests the item list
- **THEN** the item list is shown directly, with no redirect to a login page

#### Scenario: Admin is unavailable
- **WHEN** a visitor requests `/admin/` or the login page
- **THEN** the system responds with not-found (or a redirect to the item list) and no admin or login form is shown

#### Scenario: Browsing creates no session rows
- **WHEN** many visitors browse the app and submit allowed actions
- **THEN** the number of stored session records does not grow

### Requirement: Writes are denied unless explicitly allowlisted
While demo mode is enabled, the system SHALL reject every request with an unsafe HTTP method (anything other than GET, HEAD or OPTIONS) unless it targets an action on the demo allowlist. The allowlist SHALL contain only:
- creating, editing and deleting visitor-created items and their item-sources
- bulk add
- bulk edit
- creating, editing and deleting visitor-created tags
- the metadata actions on visitor-created items
- triggering a price update

Any action not on the allowlist, including actions added to the app in future, SHALL be rejected by default with a forbidden response, and no data SHALL change.

#### Scenario: Unlisted write is rejected
- **WHEN** a visitor submits the form to create, edit or delete a Source, or to create, edit or delete a Schedule
- **THEN** the system returns forbidden and no Source or Schedule is changed

#### Scenario: Allowlisted write succeeds
- **WHEN** a visitor submits a valid form to create a new item
- **THEN** the item is created

### Requirement: Sources and schedules are preset and read-only
While demo mode is enabled, the Sources and Schedules pages SHALL show the preset demo rows, with no add, edit or delete controls. Visitors SHALL be able to link their own items only to preset demo Sources.

#### Scenario: Source list has no edit controls
- **WHEN** a visitor views the Sources page
- **THEN** the preset demo Sources are listed with no add, edit or delete buttons

### Requirement: Item-source URL overrides are unavailable
While demo mode is enabled, the system SHALL NOT allow visitors to set an item-source's pinned URL or URL suffix. Those inputs SHALL NOT be shown, and any submitted value SHALL be ignored and stored as blank.

#### Scenario: Submitted pinned URL is discarded
- **WHEN** a visitor submits an item-source form with a pinned URL or URL suffix value included in the request
- **THEN** the saved item-source has a blank pinned URL and a blank URL suffix

### Requirement: Title patterns are restricted to a preset list
While demo mode is enabled, title include and exclude patterns SHALL only be chosen from a preset list of demo patterns. The system SHALL NOT offer free-text pattern entry anywhere, including the single-item forms and the bulk-edit workspace. Any submitted pattern that is not in the preset list SHALL be rejected with a validation error, and the submission SHALL save nothing.

#### Scenario: Visitor picks a preset pattern
- **WHEN** a visitor adds a preset exclude pattern to an item-source of their own item
- **THEN** the pattern is saved and later results matching it are excluded

#### Scenario: Arbitrary regex is rejected
- **WHEN** a request submits a pattern that is not in the preset list (for example `(a+)+$`) for any pattern field
- **THEN** the form reports a validation error and no pattern is saved

#### Scenario: Bulk edit has no free-text pattern entry
- **WHEN** a visitor opens the bulk-edit workspace
- **THEN** each Source's pattern section offers add, remove or leave-unchanged controls for preset patterns only, with no free-text pattern box

### Requirement: Visitor input volume is bounded
While demo mode is enabled, the system SHALL enforce these limits, each configurable as a demo-only setting:
- a maximum total number of visitor-created items
- a maximum total number of visitor-created tags
- a bulk-add limit per submission lower than the normal limit
- a minimum interval between price update runs across the whole demo
- a maximum total number of stored search results, at which point new update runs are refused
- a maximum request body size and field count that is lower than the default and applies to every request

A request that exceeds a limit SHALL be refused with a visible message, and no partial write SHALL be made for that request.

#### Scenario: Item cap reached
- **WHEN** visitors have already created the maximum number of items and a visitor submits a new item
- **THEN** the item is not created and a message explains that the demo item limit has been reached

#### Scenario: Update requested too soon
- **WHEN** a price update run started less than the minimum update interval ago and a visitor requests another update
- **THEN** no new run starts and a message says when updates will be available again

#### Scenario: Oversized request rejected
- **WHEN** a request body exceeds the demo request size limit
- **THEN** the request is rejected and nothing is saved

### Requirement: Demo framing is visible
While demo mode is enabled, every page SHALL show a demo banner. The banner SHALL say that the data is a shared sandbox that resets automatically, and SHALL show how long ago the last reset happened. The navigation SHALL NOT show the Admin link or a logout control.

#### Scenario: Banner shows last reset
- **WHEN** a visitor views any page 10 minutes after a reset
- **THEN** the page shows the demo banner stating the data was last reset about 10 minutes ago

### Requirement: Metadata enrichment completes inline in demo mode
While demo mode is enabled, a metadata refresh requested by creating or editing a visitor item, or by a metadata action, SHALL be processed within the same request. The item detail page SHALL then show the result straight away instead of a queued state, and SHALL NOT warn that the refresh queue is not being drained.

#### Scenario: New item shows metadata immediately
- **WHEN** a visitor creates an item with the demo metadata provider selected and a term the demo metadata data knows about
- **THEN** the item detail page shows a matched thumbnail, description and link from the demo metadata data, with no "queue not draining" warning
