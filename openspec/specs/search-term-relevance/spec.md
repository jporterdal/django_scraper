# search-term-relevance

## Purpose
Ensure that rows surfaced by `JSONSearchParser`-based vendor parsers actually reference the search term used to fetch them, instead of any row that happens to share words with the term — a baseline, always-on filter implemented once on the shared parser base class and inherited by every vendor-specific subclass, distinct from and running ahead of the existing optional per-`ItemSource` `title_include_patterns`/`title_exclude_patterns` mechanism.

## Requirements

### Requirement: Parser results must contain the search term as a contiguous phrase
`JSONSearchParser` SHALL reject a candidate row — omit it from `self.results` — unless the parser's search term, normalized (case-folded, whitespace-collapsed and stripped), appears as a contiguous substring of the row's title, normalized the same way. Requiring the term's words to be present and adjacent in order — not merely present anywhere in the title — is deliberate: it is what distinguishes a genuine item match from a title that happens to share words with the term. This check SHALL run inside the shared `add_result` method so every subclass (including future ones) inherits it without additional wiring.

#### Scenario: Row title contains the search term as a contiguous phrase
- **WHEN** a parser with `term="Fire Dragon"` adds a row titled `"Fire Dragon (POR)"`
- **THEN** the row is included in `self.results`

#### Scenario: Row title contains only one search-term word
- **WHEN** a parser with `term="Lightning Bolt"` adds a row titled `"Lightning Greaves (Foil)"` (contains "Lightning" but not "Bolt")
- **THEN** the row is excluded from `self.results`

#### Scenario: Row title contains none of the search-term words
- **WHEN** a parser with `term="Lightning Bolt"` adds a row titled `"Counterspell (Masters 25)"`
- **THEN** the row is excluded from `self.results`

#### Scenario: Row title contains every search-term word, but not as a contiguous phrase
- **WHEN** a parser with `term="Fire Dragon"` adds a row titled `"Dragon Fire (0130)"` (a real, different card from a different game — Disney Lorcana — that shares both words in reversed order; confirmed via live `wt` vendor search, which also returned `"Deck Protectors - Dragon Shield Matte Dual Fire Horse 100ct"`, an unrelated accessory, under the same reversed/split-word pattern)
- **THEN** the row is excluded from `self.results`

#### Scenario: Phrase matching is case-insensitive
- **WHEN** a parser with `term="lightning bolt"` adds a row titled `"LIGHTNING BOLT (Revised Edition)"`
- **THEN** the row is included in `self.results`

#### Scenario: Phrase matching tolerates incidental whitespace in the term
- **WHEN** a parser with `term="The Unbeatable Squirrel Girl "` (trailing space) adds a row titled `"The Unbeatable Squirrel Girl (MSH) - Foil"`
- **THEN** the row is included in `self.results`

#### Scenario: Blank search term disables the check
- **WHEN** a parser is constructed with `term=""` and adds a row with any title
- **THEN** the row is included in `self.results` (nothing to require, nothing to reject against)

### Requirement: Vendor-specific `JSONSearchParser` subclasses inherit the check with no per-parser code
`WtFiltersParser`, `ShopifyParser`, and `StorepassParser` SHALL apply this filtering by virtue of routing every row through the inherited `add_result`, without each subclass implementing its own relevance check.

#### Scenario: WtFiltersParser drops off-term rows from a real vendor response
- **WHEN** `WtFiltersParser(term="Fire Dragon")` parses the live `data.results[]` payload captured from `wt`'s search API for this term (24 rows: 1 genuine `"Fire Dragon (POR)"` plus 23 rows including four `"Dragon Fire"` listings, a `"Dragon's Fire (AFR)"` card, and assorted `"Dragon"`/`"Fire"`-adjacent noise)
- **THEN** `parser.results` contains only the `"Fire Dragon (POR)"` row (verified against this change's implementation, reducing 24 unfiltered rows to 1)

#### Scenario: ShopifyParser drops an off-term variant row from a vendor response
*(Illustrative — `f2f` has been checked against its captured fixture only, not live-smoke-tested the way `wt` has.)*
- **WHEN** `ShopifyParser(term="Lightning Bolt")` parses a `hits.hits[]` payload containing a hit titled `"Lightning Bolt [146] [Magic 2010]"` and a hit titled `"Bolt of Keranos"`
- **THEN** `parser.results` contains only rows derived from the `"Lightning Bolt [146] [Magic 2010]"` hit

#### Scenario: StorepassParser drops an off-term product row from a vendor response
*(Illustrative — `hfx` has been checked against its captured fixture only, not live-smoke-tested the way `wt` has.)*
- **WHEN** `StorepassParser(term="Lightning Bolt")` parses a `products[]` payload containing a product with `display_name="Lightning Bolt [Beatdown]"` and a product with `display_name="Lightning Helix"`
- **THEN** `parser.results` contains only rows derived from the `"Lightning Bolt [Beatdown]"` product

### Requirement: Rejected rows do not raise or fail the fetch
Filtering a row for term relevance SHALL NOT raise an exception or mark the parse as failed; it is routine filtering, not a parse error.

#### Scenario: A response containing only off-term rows still parses successfully
- **WHEN** a parser's vendor response contains zero rows that reference every search-term token
- **THEN** `parse_response`/`parse_data` completes normally and `self.results` is an empty list (which the existing `FetchJob.Status.EMPTY` handling in `tracking/scrape.py` already covers)
