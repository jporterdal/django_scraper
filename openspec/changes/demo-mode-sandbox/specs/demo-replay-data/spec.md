## Purpose

Makes demo-mode price updates and metadata enrichment use only bundled local data while still running the real parsing, relevance-filtering and dedup pipeline. Visitors see realistic scrape behavior and the server never makes an outbound network request.

## ADDED Requirements

### Requirement: No outbound HTTP in demo mode
While demo mode is enabled, the system SHALL NOT make any outbound network request on behalf of a price update, metadata refresh or any other request. An attempt to create the real HTTP fetcher, or to request any URL that does not address bundled demo data, SHALL fail with an error that is recorded on the affected fetch job or metadata record. It SHALL NOT fall back to a network request.

#### Scenario: Full price update makes no network calls
- **WHEN** a visitor runs "Update All Active" in demo mode with outbound networking blocked at the process level
- **THEN** the run completes, results are stored, and no outbound connection is attempted

#### Scenario: Non-demo URL fails closed
- **WHEN** a fetch in demo mode is given a URL that does not address bundled demo data
- **THEN** the fetch job is recorded with an error status and no network request is made

#### Scenario: Real metadata provider is unavailable
- **WHEN** demo mode is enabled
- **THEN** the only metadata provider offered or used is the demo provider, and no request is sent to an external metadata service

### Requirement: Simulated vendor search over a local catalogue
While demo mode is enabled, each preset demo Source SHALL answer searches from a bundled catalogue of products for that Source. Each product has a title, price, stock status, category and product line. A search SHALL return the catalogue products whose titles share at least one word with the search term, so loosely related products are returned the way real vendor searches return them. Results SHALL be delivered in that Source's real vendor response format and SHALL pass through the same parser, relevance filters, pattern filters and dedup used outside demo mode.

#### Scenario: Seed term returns relevant and irrelevant results
- **WHEN** a seed item's term is searched against a demo Source whose catalogue contains both the matching product and loosely related products
- **THEN** the matching product is stored as a result and the loosely related products are rejected by the existing relevance filters

#### Scenario: Visitor term with no catalogue match
- **WHEN** a visitor-created item's term shares no words with any catalogue title for a Source
- **THEN** that Source's fetch job is recorded as "No results" and the run completes normally

#### Scenario: Visitor term with catalogue matches
- **WHEN** a visitor creates an item whose term matches catalogue titles and runs an update
- **THEN** results for the matching products are stored and appear on the item list and detail pages

### Requirement: Simulated prices change between runs
While demo mode is enabled, each search SHALL return catalogue prices with a small bounded random drift applied (at most 5% from the catalogue price). Repeated update runs SHALL therefore produce new price points rather than being skipped as unchanged duplicates.

#### Scenario: Second run records new prices
- **WHEN** a visitor runs an update twice for the same item
- **THEN** the second run stores new price points for at least some results, and every stored price is within 5% of its catalogue price

### Requirement: Demo metadata from local data
While demo mode is enabled, metadata enrichment SHALL resolve items against a bundled set of metadata entries. The thumbnail, description and external link SHALL use the same display contract as other providers. Every thumbnail SHALL be served by the demo deployment itself. Resolution SHALL produce matched, needs-review or no-match outcomes just as a real provider would, and entering an identifier manually SHALL only resolve identifiers that exist in the bundled entries.

#### Scenario: Ambiguous term needs review
- **WHEN** a visitor item's term matches more than one bundled metadata entry
- **THEN** the item's metadata status is needs-review and the candidates are offered for selection

#### Scenario: Unknown manual identifier
- **WHEN** a visitor enters an external identifier that is not among the bundled entries
- **THEN** the item's metadata status is no-match and no network request is made

#### Scenario: Thumbnails are self-hosted
- **WHEN** any page renders a demo metadata thumbnail
- **THEN** the image URL points at the demo deployment's own static files
