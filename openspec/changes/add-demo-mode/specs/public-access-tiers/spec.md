## Purpose

Defines who may do what when the site is exposed publicly: a fail-closed classification of every page into public-read, public-write, or login-only, the create-only rules for anonymous visitors, the inputs anonymous visitors may not control (because the server acts on them), and the connection details hidden from them. It keeps the private deployment private by default while letting a demo deployment show most of the app.

## ADDED Requirements

### Requirement: Every application page belongs to exactly one access tier

Each page of the application SHALL be classified as exactly one of: public-read, public-write, or login-only. A page not classified SHALL be treated as login-only. When demo mode is off, tiers SHALL have no effect and every application page SHALL require login. An automated check SHALL fail whenever an application page exists that is not classified, so that adding a page forces an explicit access decision. Pages supplied by the framework for authentication and administration are governed by their own rules and are outside the classification.

#### Scenario: Unclassified page in demo mode

- **WHEN** demo mode is on and an anonymous visitor requests a page that has no classification
- **THEN** the visitor is redirected to the login page

#### Scenario: Classification check catches a new page

- **WHEN** a page is added to the application without being classified
- **THEN** the automated classification check fails

#### Scenario: Public-read page with demo mode off

- **WHEN** demo mode is off and an anonymous visitor requests a page that would be public-read in demo mode
- **THEN** the visitor is redirected to the login page

### Requirement: Public-read pages are viewable by anonymous visitors in demo mode

In demo mode, anonymous visitors SHALL be able to view: the item list, the item detail page, the item's price-history exports, the scrape history and its per-run job listing, the scrape progress view, the tag list, the source list, each item's source list, and the schedule list (read-only, with no create, edit, or delete controls).

#### Scenario: Anonymous visitor views the item list

- **WHEN** demo mode is on and an anonymous visitor requests the item list
- **THEN** the page is returned without a login redirect

#### Scenario: Schedule list is read-only for anonymous visitors

- **WHEN** an anonymous visitor views the schedule list in demo mode
- **THEN** the list is shown
- **AND** no control to create, edit, or delete a schedule is shown

### Requirement: Public-write actions are create-only and bounded

In demo mode, anonymous visitors SHALL be permitted these actions only: creating an item (single or bulk), linking an item to an existing source, requesting a scrape of a single item, retrying a metadata lookup, and choosing among offered metadata candidates. Each SHALL remain subject to the usage limits. No anonymous action SHALL modify or delete an existing item, item-source, tag, source, or schedule, and no anonymous action SHALL toggle an item's active state.

#### Scenario: Anonymous visitor adds an item

- **WHEN** demo mode is on and an anonymous visitor submits a valid new item
- **THEN** the item is created

#### Scenario: Anonymous visitor tries to edit an item

- **WHEN** demo mode is on and an anonymous visitor requests the item edit page or submits an item edit
- **THEN** the visitor is redirected to the login page
- **AND** the item is unchanged

#### Scenario: Anonymous visitor tries to change a source

- **WHEN** demo mode is on and an anonymous visitor requests any page to create, edit, or delete a source
- **THEN** the visitor is redirected to the login page

### Requirement: Login-only surface

The following SHALL require login in every mode: creating, editing, and deleting sources; creating, editing, and deleting schedules; creating, editing, and deleting tags; editing items individually or in bulk; editing or deleting an item's source links; requesting a scrape of all active items or of all items with a tag; entering a metadata external identifier by hand; and the administration site. A logged-in visitor SHALL have full access to these.

#### Scenario: Operator manages a source

- **WHEN** demo mode is on and a logged-in operator requests the source creation page
- **THEN** the page is shown

#### Scenario: Anonymous visitor requests a scrape of all items

- **WHEN** demo mode is on and an anonymous visitor submits a scrape request for all active items or for a tag
- **THEN** no scrape is started
- **AND** the visitor is told that this action requires the operator login

#### Scenario: Administration site requires login

- **WHEN** demo mode is on and an anonymous visitor requests the administration site index
- **THEN** the visitor is redirected to the login page

### Requirement: Anonymous scrape requests cover exactly one item

In demo mode, an anonymous scrape request SHALL be accepted only when it names exactly one item. A request naming zero or several items SHALL be rejected without starting a scrape.

#### Scenario: Single item scrape

- **WHEN** an anonymous visitor requests a scrape of one active item
- **THEN** a scrape run is started for that item alone

#### Scenario: Multi-item scrape

- **WHEN** an anonymous visitor requests a scrape of two selected items
- **THEN** no scrape is started
- **AND** the visitor is told that anonymous scrapes are limited to one item at a time

### Requirement: Anonymous item-source links cannot steer server-side fetches

When an anonymous visitor links an item to a source (individually or as part of bulk add), the system SHALL require an existing source to be chosen; SHALL NOT offer or accept a search-URL suffix or title include/exclude patterns; and SHALL accept a pinned URL only when it is an `http` or `https` URL whose host equals the host of the chosen source's search URL (compared case-insensitively). Any other pinned URL SHALL be rejected with a validation message. A logged-in operator SHALL keep the existing behavior for all of these fields.

#### Scenario: Pinned URL on the source's own host

- **WHEN** an anonymous visitor links an item to a source and pins a URL on that source's host
- **THEN** the link is created

#### Scenario: Pinned URL on a different host

- **WHEN** an anonymous visitor pins a URL whose host differs from the chosen source's host
- **THEN** the link is rejected with a validation message
- **AND** no link is created

#### Scenario: Risky fields are not available to anonymous visitors

- **WHEN** an anonymous visitor opens the form to link an item to a source
- **THEN** the search-URL suffix and the include and exclude pattern fields are not present
- **AND** submitting values for them has no effect

#### Scenario: Operator keeps full control

- **WHEN** a logged-in operator links an item to a source with a suffix and patterns
- **THEN** those values are accepted as before

### Requirement: Anonymous search terms use a conservative character set

In demo mode, search terms submitted by anonymous visitors (single or bulk) SHALL be limited to letters, digits, spaces, and a small set of punctuation (`' - . , : & ! ( ) / +`), SHALL NOT contain control characters or angle brackets, and SHALL be no longer than the existing term length limit. Terms violating this SHALL be rejected with a validation message. Terms submitted by logged-in operators SHALL keep the existing rules.

#### Scenario: Ordinary term accepted

- **WHEN** an anonymous visitor submits the term `Lightning Bolt`
- **THEN** the term is accepted

#### Scenario: Term with markup rejected

- **WHEN** an anonymous visitor submits a term containing `<` or a control character
- **THEN** the term is rejected with a validation message

### Requirement: Connection details are hidden from anonymous visitors

Anonymous views SHALL NOT display a source's search URL template, request headers, or request body template, nor a fetch job's request URL or raw error text. Anonymous views of sources SHALL show only each source's name, short key, and parser. Fetch job listings SHALL still show each job's status, result counts, and duration to anonymous visitors. Logged-in operators SHALL see all of these.

#### Scenario: Anonymous source list

- **WHEN** an anonymous visitor views the source list in demo mode
- **THEN** each source shows its name, key, and parser
- **AND** no search URL, header, or request body content appears anywhere in the page

#### Scenario: Anonymous job listing

- **WHEN** an anonymous visitor views the job listing for a scrape run
- **THEN** each job's status and counts are shown
- **AND** no request URL or raw error message text is shown

#### Scenario: Operator sees details

- **WHEN** a logged-in operator views the same source list and job listing
- **THEN** the search URL, headers, request URL, and error text are shown as before

### Requirement: Navigation and controls reflect the visitor's role

In demo mode, navigation and page controls SHALL be role-aware. Anonymous visitors SHALL see no link to the administration site, no logout control, no edit, delete, or manage controls for items, tags, sources, schedules, or item-source links, and no scrape-all or scrape-by-tag controls. They SHALL see a link to the operator login. Logged-in visitors SHALL see logout, the management controls, and (when they are staff) the administration link.

#### Scenario: Anonymous navigation

- **WHEN** an anonymous visitor views any page in demo mode
- **THEN** no administration link or logout control appears
- **AND** an operator login link appears

#### Scenario: Staff operator navigation

- **WHEN** a logged-in staff user views any page
- **THEN** the administration link and logout control appear
