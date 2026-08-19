## ADDED Requirements

### Requirement: Scrape history list paginates 25 rows per page, newest first
The scrape-history list view (`WebUpdateListView`, `view_updates`) SHALL display `WebUpdate` runs ordered by `timestamp` descending, 25 rows per page. Each page SHALL contain only the rows belonging to that page — no row that appears on one page SHALL also appear on another page of the same result set.

#### Scenario: More than one page of runs exists
- **WHEN** more than 25 `WebUpdate` rows exist and the list view is requested
- **THEN** the response indicates more than one page is available (e.g. a "Next" control and "Page 1 of N" indicator), and the first page contains exactly the 25 most recent rows by `timestamp`

#### Scenario: Last page contains only its own remainder rows
- **WHEN** the list view's second page is requested and 26 total `WebUpdate` rows exist
- **THEN** the second page contains exactly the single oldest remaining row, and none of the rows already shown on page 1 (including the newest row) appear on page 2
