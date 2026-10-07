## Purpose

Defines the `DEMO_MODE` deployment flag: what it switches on, how it supplies default usage limits without owning them, the safety guard that keeps it off `DEBUG` deployments, the banner that tells visitors what they are looking at, and the operator documentation that ships with it. The flag turns a private single-tenant deployment into one that is safe to expose publicly as a portfolio demo.

## ADDED Requirements

### Requirement: Demo mode is an environment flag that defaults to off

The system SHALL read a boolean `DEMO_MODE` setting from the environment, defaulting to off when unset. With demo mode off, the system SHALL behave exactly as it does without this capability: every page requires login, and no usage limit is enforced unless its own setting is explicitly configured.

#### Scenario: Flag unset

- **WHEN** the application starts with no `DEMO_MODE` value in the environment
- **THEN** demo mode is off
- **AND** an anonymous request to any application page is redirected to the login page

#### Scenario: Flag enabled

- **WHEN** the application starts with `DEMO_MODE` set to a true value
- **THEN** demo mode is on
- **AND** the public-access rules defined by the `public-access-tiers` capability apply

### Requirement: Demo mode supplies defaults for usage limits, not the limits themselves

Each usage limit (item cap, history retention window, per-fetch row cap, per-item fetch cooldown, daily fetch budget, stored-row ceiling, and response-size cap) SHALL be individually configurable through its own environment variable and SHALL be enforced whether or not demo mode is on. Demo mode SHALL only change the default used when a limit's variable is unset. An unset limit outside demo mode SHALL mean unlimited (for the response-size cap, the existing default). Setting a limit's variable to `0` SHALL disable that limit, including in demo mode. The demo defaults SHALL include a 14-day history retention window and a 100-row per-fetch cap.

#### Scenario: Demo defaults apply when a limit is unset

- **WHEN** demo mode is on and no limit variables are set
- **THEN** the history retention window is 14 days
- **AND** the per-fetch row cap is 100 rows
- **AND** every other usage limit has a finite demo default

#### Scenario: Environment overrides a demo default

- **WHEN** demo mode is on and the item-cap variable is set to a different positive number
- **THEN** that number is the item cap

#### Scenario: Zero disables a limit even in demo mode

- **WHEN** demo mode is on and the daily fetch budget variable is set to `0`
- **THEN** no daily fetch budget is enforced

#### Scenario: Limits work without demo mode

- **WHEN** demo mode is off and the item-cap variable is set to a positive number
- **THEN** that item cap is enforced

### Requirement: Demo mode refuses to start alongside debug mode

The system SHALL refuse to start, with an error message naming both settings, when `DEMO_MODE` and `DEBUG` are both on. This SHALL hold regardless of any other setting, because `DEBUG` exposes stack traces and settings to anonymous visitors.

#### Scenario: Both flags on

- **WHEN** the application starts with `DEMO_MODE` and `DEBUG` both true
- **THEN** startup fails with an error that names both settings
- **AND** no request is served

#### Scenario: Demo mode on with debug off

- **WHEN** the application starts with `DEMO_MODE` true and `DEBUG` false
- **THEN** startup succeeds

### Requirement: The automated test suite runs with demo mode off

The project's dedicated test settings SHALL force demo mode off, independent of the environment, so that suite results do not depend on a developer's local configuration. Tests that need demo behavior SHALL enable it explicitly per test.

#### Scenario: Environment has demo mode on

- **WHEN** the test suite runs in an environment where `DEMO_MODE` is true
- **THEN** demo mode is off for tests that do not enable it themselves

### Requirement: Demo banner

When demo mode is on, every page SHALL display a banner stating that the site is a demo and showing: the number of item slots remaining out of the item cap, the number of fetches remaining in the current 24-hour budget, and the history retention window in days. A limit that is disabled SHALL be omitted from the banner. For anonymous visitors the banner SHALL include a link to the operator login. When demo mode is off, no banner SHALL be shown.

#### Scenario: Banner shows remaining quota

- **WHEN** demo mode is on, the item cap is 25, and 10 items exist
- **THEN** every page shows that 15 item slots remain

#### Scenario: Disabled limit omitted

- **WHEN** demo mode is on and the daily fetch budget is disabled
- **THEN** the banner shows no fetch-budget figure

#### Scenario: Anonymous visitor sees the operator login link

- **WHEN** an anonymous visitor views any page in demo mode
- **THEN** the banner links to the login page

#### Scenario: Demo mode off

- **WHEN** demo mode is off
- **THEN** no demo banner appears on any page

### Requirement: Demo deployment documentation

The project SHALL include operator documentation for demo mode that covers: every demo-related environment variable with its default; the recommended deployment shape (a separate environment with its own database and queue that never shares data with a private deployment, the same environment variables on the web and worker services, and a platform-level spending limit as a backstop); an operator runbook (provisioning sources through the login-only screens, creating sample items, and manual cleanup of items through the admin site, since items never expire); and a section listing documented future options that are not implemented, including cookie-based visitor ownership of their own items, login throttling, and closing the second admin login form.

#### Scenario: Documentation covers the required topics

- **WHEN** the demo documentation is reviewed
- **THEN** it lists every demo-related environment variable and its demo default
- **AND** it describes the operator runbook and the deployment shape
- **AND** it lists the unimplemented future options, including cookie-based visitor ownership
