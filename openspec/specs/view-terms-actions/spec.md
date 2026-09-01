# view-terms-actions

## Purpose

The contract for which item-management actions are exposed directly on the `view_terms` page versus reachable only through the persistent navigation, how the navigation itself groups related destinations, and the navigation's display of the site logo. Before this capability, `view_terms`'s card header and the persistent nav each exposed the same destinations (bulk add, tag/source/schedule management, scrape history) with no defined boundary between "page-specific action" and "nav-reachable destination" — this capability defines that boundary.

## Requirements

### Requirement: view_terms card header exposes only page-specific actions
The `view_terms` page's "All Items" card header SHALL contain only actions that are specific to that page's item table: creating a single new item, and running an update. It SHALL NOT contain links that merely duplicate a persistent-navigation destination (bulk add, tag management, source management, schedule management, or scrape history).

#### Scenario: Card header omits nav-duplicating links
- **WHEN** the `view_terms` page is rendered
- **THEN** the "All Items" card header contains a control to add a new item and a control to run an update, and does not contain a "Bulk Add", "Manage Tags", "Manage Sources", "Schedules", or "Scrape History" control

#### Scenario: Card header still offers the item-creation entry point
- **WHEN** the `view_terms` page is rendered
- **THEN** the "All Items" card header contains a control that links to the add-item page (`add_term`), independent of any equivalent entry point elsewhere on the page

### Requirement: Update actions are exposed as a single grouped control with two scopes
The "All Items" card header SHALL expose the "update selected items" and "update all active items" actions as one grouped control (e.g. a split button or dropdown), rather than as two separately laid out buttons. Both scopes SHALL continue to submit the existing update form unchanged (`POST` to the `update` view, `mode` values `selected` and `all`).

#### Scenario: Both update scopes remain available and submit the existing form
- **WHEN** the `view_terms` page is rendered
- **THEN** a control to update selected items and a control to update all active items are both present, both are part of the same grouped control, and both submit to the `update` URL with their respective existing `mode` value

### Requirement: Persistent navigation groups creation and management destinations
The persistent navigation (present on every page via the base template) SHALL group the two item-creation destinations (add a single item, bulk add) under one navigation menu, and SHALL group the three management/configuration destinations (tags, sources, schedules) under a separate navigation menu. Scrape history and the admin site SHALL remain direct, ungrouped navigation links.

#### Scenario: Creation destinations are reachable via one grouped menu
- **WHEN** any page extending the base template is rendered
- **THEN** the persistent navigation contains one menu that, when opened, exposes links to add a single item and to bulk add items

#### Scenario: Management destinations are reachable via one grouped menu
- **WHEN** any page extending the base template is rendered
- **THEN** the persistent navigation contains one menu that, when opened, exposes links to the tags, sources, and schedules pages

#### Scenario: Scrape history remains a direct link
- **WHEN** any page extending the base template is rendered
- **THEN** the persistent navigation contains a direct link to the scrape history page that is not nested inside a menu

### Requirement: Persistent navigation displays the site logo
The persistent navigation's brand link SHALL display the site's logo image alongside the "Pricing Tracker" text, and SHALL remain a link to the `view_terms` page. The logo image SHALL be served as a Django static asset (referenced via the `static` template tag), not loaded from an external or ad-hoc path, and SHALL render cleanly against both the dark and light theme's navbar background.

#### Scenario: Logo renders as part of the brand link
- **WHEN** any page extending the base template is rendered
- **THEN** the navbar brand link to `view_terms` includes an `<img>` referencing a static-file logo asset, with non-empty `alt` text, in addition to the existing "Pricing Tracker" text

#### Scenario: Logo remains visually clean in both themes
- **WHEN** the theme toggle switches between dark and light mode
- **THEN** the logo image renders cleanly against the navbar background in both themes

### Requirement: Tag-filter row retains a contextual tag-management link
When the `view_terms` page displays its tag-filter row (i.e. tags exist), that row SHALL continue to include a link to the tag-management page positioned alongside the tag filter options, independent of the tag-management entry point in the persistent navigation.

#### Scenario: Contextual tag-management link is present alongside tag filters
- **WHEN** the `view_terms` page is rendered and at least one tag exists
- **THEN** the tag-filter row contains a link to the tag-management page in addition to the per-tag filter links
