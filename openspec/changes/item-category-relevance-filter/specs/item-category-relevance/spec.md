## ADDED Requirements

### Requirement: SearchableItem may specify an expected category value
`SearchableItem` SHALL have an optional, plain-text field a user can set to indicate the category or product line the item belongs to (e.g. `"Magic"`, `"Pokemon"`), used to disambiguate rows that would otherwise pass term-relevance filtering despite belonging to an unrelated product line. This field SHALL be blank by default.

#### Scenario: User sets an expected category on an item
- **WHEN** a user sets an item's expected category value to `"Magic"`
- **THEN** the value is stored on the `SearchableItem` and applies to every source configured for that item

#### Scenario: Item has no expected category set
- **WHEN** an item's expected category value has never been set
- **THEN** it is blank/empty by default

### Requirement: Parser results must match the item's expected category when one is set
`JSONSearchParser` SHALL reject a candidate row — omit it from `self.results` — when the owning item's expected category value is non-blank and does not appear as a normalized (case-folded, whitespace-collapsed) substring of the row's category signal (a per-row value each vendor-specific parser supplies; see design.md for the exact signal used per vendor). This check SHALL run inside the shared `add_result` method, alongside and independent of the term-relevance check added by `search-term-relevance-filter`, so every subclass (including future ones) inherits it without additional wiring.

#### Scenario: Row's category signal contains the item's expected category
- **WHEN** an item's expected category is `"Magic"` and a candidate row's category signal is `"Magic the Gathering Singles"`
- **THEN** the row is included in `self.results` (subject to also passing the term-relevance check)

#### Scenario: Row's category signal does not contain the item's expected category
- **WHEN** an item's expected category is `"Magic"` and a candidate row's category signal is `"Pokémon Trading Card Game"` — e.g. a `"Energy Retrieval"` search returning both the MTG card and the Pokémon TCG card of that exact name
- **THEN** the row is excluded from `self.results`

#### Scenario: Category matching is case-insensitive and tolerates incidental whitespace
- **WHEN** an item's expected category is `" magic "` (incidental whitespace) and a candidate row's category signal is `"MAGIC: THE GATHERING"`
- **THEN** the row is included in `self.results`

#### Scenario: Blank expected category disables the check
- **WHEN** an item's expected category value is blank
- **THEN** every candidate row passes this check regardless of its category signal (nothing to require, nothing to reject against)

### Requirement: Expected category input is plain text, not regex
The expected category value SHALL be matched as literal text, not interpreted as a regular expression — a user's input SHALL NOT need to be regex-safe (e.g. literal parentheses or other regex metacharacters in the value must not change matching behavior or raise an error).

#### Scenario: Expected category containing regex metacharacters matches literally
- **WHEN** an item's expected category is `"Magic (Core Set)"` and a candidate row's category signal is `"Magic (Core Set) Singles"`
- **THEN** the row is included in `self.results`, with the parentheses treated as literal characters, not a regex group

### Requirement: Vendor-specific JSONSearchParser subclasses supply the category signal with no per-parser filtering logic
`WtFiltersParser`, `ShopifyParser`, and `StorepassParser` SHALL each extract their vendor's own per-row category/product-line signal and pass it through to the shared `add_result` check; the comparison logic itself SHALL be implemented once on `JSONSearchParser` and inherited, not duplicated per subclass.

#### Scenario: WtFiltersParser drops a same-title, different-category row
- **WHEN** `WtFiltersParser` is configured for an item with expected category `"Magic"` and parses a vendor response containing both a genuine Magic: the Gathering row and a same-titled row from an unrelated product line
- **THEN** `parser.results` contains only the Magic: the Gathering row

#### Scenario: ShopifyParser drops a same-title, different-category row
*(Illustrative — mirrors the WtFiltersParser scenario for the f2f payload shape.)*
- **WHEN** `ShopifyParser` is configured for an item with expected category `"Magic"` and parses a vendor response containing both a genuine Magic: the Gathering hit and a same-titled hit from an unrelated product line
- **THEN** `parser.results` contains only the Magic: the Gathering row

#### Scenario: StorepassParser drops a same-title, different-category row
*(Illustrative — mirrors the WtFiltersParser scenario for the hfx payload shape.)*
- **WHEN** `StorepassParser` is configured for an item with expected category `"Magic"` and parses a vendor response containing both a genuine Magic: the Gathering product and a same-titled product from an unrelated product line
- **THEN** `parser.results` contains only the Magic: the Gathering row

### Requirement: Rejected rows do not raise or fail the fetch
Filtering a row for category relevance SHALL NOT raise an exception or mark the parse as failed; it is routine filtering, not a parse error, consistent with the enforcement posture of the term-relevance check added by `search-term-relevance-filter`.

#### Scenario: A response containing only off-category rows still parses successfully
- **WHEN** a parser's vendor response contains zero rows whose category signal matches the item's non-blank expected category
- **THEN** `parse_response`/`parse_data` completes normally and `self.results` is an empty list (which the existing `FetchJob.Status.EMPTY` handling in `tracking/scrape.py` already covers)
