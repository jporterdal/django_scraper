## ADDED Requirements

### Requirement: SearchableItem may specify an expected product line
`SearchableItem` SHALL have an optional, plain-text field, `expected_product_line`, a user can set to indicate the game or product line the item belongs to (e.g. `"Magic"`, `"Pokemon"`), used to disambiguate rows that would otherwise pass term-relevance filtering despite belonging to an unrelated product line. This field SHALL be blank by default.

#### Scenario: User sets an expected product line on an item
- **WHEN** a user sets an item's `expected_product_line` value to `"Magic"`
- **THEN** the value is stored on the `SearchableItem` and applies to every source configured for that item

#### Scenario: Item has no expected product line set
- **WHEN** an item's `expected_product_line` value has never been set
- **THEN** it is blank/empty by default

### Requirement: SearchableItem may independently specify an expected category
`SearchableItem` SHALL have a second, optional, plain-text field, `expected_category`, independent of `expected_product_line`, a user can set to indicate the set or printing the item belongs to (e.g. a specific MTG set name), used to narrow results beyond product-line disambiguation. This field SHALL be blank by default and SHALL NOT require `expected_product_line` to also be set.

#### Scenario: User sets an expected category on an item
- **WHEN** a user sets an item's `expected_category` value to `"Strixhaven"`
- **THEN** the value is stored on the `SearchableItem` independently of any `expected_product_line` value and applies to every source configured for that item

#### Scenario: Item has no expected category set
- **WHEN** an item's `expected_category` value has never been set
- **THEN** it is blank/empty by default, regardless of whether `expected_product_line` is set

### Requirement: Parser results must match the item's expected product line when one is set
`JSONSearchParser` SHALL reject a candidate row — omit it from `self.results` — when the owning item's `expected_product_line` is non-blank and does not appear as a normalized (case-folded, whitespace-collapsed) substring of the row's product-line signal (a per-row value each vendor-specific parser supplies; see design.md for the exact signal used per vendor). This check SHALL run inside the shared `add_result` method, alongside and independent of the term-relevance check added by `search-term-relevance-filter` and the expected-category check below.

#### Scenario: Row's product-line signal contains the item's expected product line
- **WHEN** an item's `expected_product_line` is `"Magic"` and a candidate row's product-line signal is `"Magic the Gathering Singles"`
- **THEN** the row is included in `self.results` (subject to also passing the term-relevance check and any `expected_category` check)

#### Scenario: Row's product-line signal does not contain the item's expected product line
- **WHEN** an item's `expected_product_line` is `"Magic"` and a candidate row's product-line signal is `"Pokémon Trading Card Game"` — e.g. an `"Energy Retrieval"` search returning both the MTG card and the Pokémon TCG card of that exact name
- **THEN** the row is excluded from `self.results`

#### Scenario: Product-line matching is case-insensitive and tolerates incidental whitespace
- **WHEN** an item's `expected_product_line` is `" magic "` (incidental whitespace) and a candidate row's product-line signal is `"MAGIC: THE GATHERING"`
- **THEN** the row is included in `self.results`

#### Scenario: Blank expected product line disables the check
- **WHEN** an item's `expected_product_line` value is blank
- **THEN** every candidate row passes this check regardless of its product-line signal

### Requirement: Parser results must match the item's expected category when one is set
`JSONSearchParser` SHALL reject a candidate row — omit it from `self.results` — when the owning item's `expected_category` is non-blank and does not appear as a normalized (case-folded, whitespace-collapsed) substring of the row's category signal (the existing per-row set/printing value each vendor-specific parser already extracts). This check SHALL run inside the shared `add_result` method, alongside and independent of the term-relevance check and the expected-product-line check above.

#### Scenario: Row's category signal contains the item's expected category
- **WHEN** an item's `expected_category` is `"Strixhaven"` and a candidate row's category signal is `"Strixhaven - Mystical Archive"`
- **THEN** the row is included in `self.results` (subject to also passing the term-relevance check and any `expected_product_line` check)

#### Scenario: Row's category signal does not contain the item's expected category
- **WHEN** an item's `expected_category` is `"Strixhaven"` and a candidate row's category signal is `"Kaldheim"`
- **THEN** the row is excluded from `self.results`

#### Scenario: Blank expected category disables the check
- **WHEN** an item's `expected_category` value is blank
- **THEN** every candidate row passes this check regardless of its category signal, regardless of whether `expected_product_line` is set

### Requirement: Expected product-line and category checks combine independently
When an item sets both `expected_product_line` and `expected_category`, a candidate row SHALL be required to pass both checks (in addition to the term-relevance check) to be included in `self.results`. Neither check SHALL be short-circuited or skipped because of the other's presence or absence.

#### Scenario: Row passes product-line check but fails category check
- **WHEN** an item's `expected_product_line` is `"Magic"` and `expected_category` is `"Strixhaven"`, and a candidate row's product-line signal is `"Magic the Gathering Singles"` but its category signal is `"Kaldheim"`
- **THEN** the row is excluded from `self.results`

#### Scenario: Row passes both checks
- **WHEN** an item's `expected_product_line` is `"Magic"` and `expected_category` is `"Strixhaven"`, and a candidate row's product-line signal is `"Magic the Gathering Singles"` and its category signal is `"Strixhaven - Mystical Archive"`
- **THEN** the row is included in `self.results` (subject to also passing the term-relevance check)

### Requirement: Expected product-line and category input is plain text, not regex
Both `expected_product_line` and `expected_category` SHALL be matched as literal text, not interpreted as regular expressions — a user's input SHALL NOT need to be regex-safe (e.g. literal parentheses or other regex metacharacters in either value must not change matching behavior or raise an error).

#### Scenario: Expected product line containing regex metacharacters matches literally
- **WHEN** an item's `expected_product_line` is `"Magic (Core Set)"` and a candidate row's product-line signal is `"Magic (Core Set) Singles"`
- **THEN** the row is included in `self.results`, with the parentheses treated as literal characters, not a regex group

### Requirement: Vendor-specific JSONSearchParser subclasses supply both signals with no per-parser filtering logic
`WtFiltersParser`, `ShopifyParser`, and `StorepassParser` SHALL each extract their vendor's own per-row product-line signal (newly wired by this change) and category signal (already extracted today) and pass both through to the shared `add_result` check; the comparison logic itself SHALL be implemented once on `JSONSearchParser` and inherited, not duplicated per subclass.

#### Scenario: WtFiltersParser drops a same-title, different-product-line row
- **WHEN** `WtFiltersParser` is configured for an item with `expected_product_line` `"Magic"` and parses a vendor response containing both a genuine Magic: the Gathering row and a same-titled row from an unrelated product line
- **THEN** `parser.results` contains only the Magic: the Gathering row

#### Scenario: ShopifyParser drops a same-title, different-product-line row
*(Illustrative — mirrors the WtFiltersParser scenario for the f2f payload shape.)*
- **WHEN** `ShopifyParser` is configured for an item with `expected_product_line` `"Magic"` and parses a vendor response containing both a genuine Magic: the Gathering hit and a same-titled hit from an unrelated product line
- **THEN** `parser.results` contains only the Magic: the Gathering hit

#### Scenario: StorepassParser drops a same-title, different-product-line row
*(Illustrative — mirrors the WtFiltersParser scenario for the hfx payload shape.)*
- **WHEN** `StorepassParser` is configured for an item with `expected_product_line` `"Magic"` and parses a vendor response containing both a genuine Magic: the Gathering product and a same-titled product from an unrelated product line
- **THEN** `parser.results` contains only the Magic: the Gathering product

### Requirement: Product-line signal is persisted and displayed alongside category
`SearchResult` SHALL have a `product_line` column, populated from the per-row product-line signal supplied by the parser at storage time, displayed in `searchableitem_detail.html` and included in the CSV/JSON export field set (`EXPORT_FIELDNAMES`) alongside the existing `category` column.

#### Scenario: Stored result exposes its product-line value
- **WHEN** a fetch stores a `SearchResult` row whose parsed product-line signal was `"Magic the Gathering Singles"`
- **THEN** `SearchResult.product_line` is `"Magic the Gathering Singles"`, visible in the item detail view and present in CSV/JSON export output

### Requirement: Rejected rows do not raise or fail the fetch
Filtering a row for product-line or category relevance SHALL NOT raise an exception or mark the parse as failed; it is routine filtering, not a parse error, consistent with the enforcement posture of the term-relevance check added by `search-term-relevance-filter`.

#### Scenario: A response containing only non-matching rows still parses successfully
- **WHEN** a parser's vendor response contains zero rows whose product-line signal, category signal, or both (per whichever of the item's expected values are non-blank) match the item's expectations
- **THEN** `parse_response`/`parse_data` completes normally and `self.results` is an empty list (which the existing `FetchJob.Status.EMPTY` handling in `tracking/scrape.py` already covers)

### Requirement: Every parsed row's category and product-line signals are recorded, regardless of filtering outcome
`JSONSearchParser.add_result` SHALL record each non-blank raw `category` and `product_line` signal it receives into a per-vendor observation log (`ObservedCategoryValue`: `source`, `field_name`, `value`, `last_seen`), for **every** row processed — before, and independent of, whether that row goes on to pass or fail the term-relevance, `expected_product_line`, or `expected_category` checks. A repeat observation of the same `(source, field_name, value)` SHALL update `last_seen` rather than create a duplicate row.

#### Scenario: An accepted row's signals are recorded
- **WHEN** `WtFiltersParser` parses a row whose title matches the search term and whose product-line/category signals also satisfy the item's expected values (the row is appended to `self.results`)
- **THEN** an `ObservedCategoryValue` row exists (or has its `last_seen` updated) for `wt`/`"product_line"`/that row's product-line value, and likewise for `"category"`

#### Scenario: A rejected row's signals are still recorded
- **WHEN** `WtFiltersParser` parses a row whose product-line signal does not contain the item's non-blank `expected_product_line` (the row is omitted from `self.results`)
- **THEN** an `ObservedCategoryValue` row still exists (or has its `last_seen` updated) for that vendor/field/value, even though the row itself never reaches `self.results` or `SearchResult`

### Requirement: Item form offers vendor-scoped value suggestions for both fields
The `SearchableItem` create/edit form SHALL offer non-binding suggestions (e.g. via an HTML `datalist`) for both `expected_product_line` and `expected_category`, sourced from distinct `ObservedCategoryValue` entries (not `SearchResult`) matching `field_name="product_line"` and `field_name="category"` respectively, scoped to the `Source`s used by the item's own configured `ItemSource`s. Suggestions SHALL NOT constrain or validate the typed value — any plain-text value SHALL remain acceptable regardless of whether it appears in the suggestion list.

#### Scenario: Operator sees suggestions scoped to the item's configured vendors
- **WHEN** an item has `ItemSource`s configured against `wt` and `f2f`, and `ObservedCategoryValue` rows already exist with various `product_line` and `category` values for `wt` and `f2f` (and unrelated values for other vendors from other items)
- **THEN** the form's suggestions for that item include only distinct values observed for `wt` and `f2f`, not values observed only for other vendors

#### Scenario: A value from a rejected row still appears as a suggestion
- **WHEN** an item's `expected_product_line` is `"Magic"`, a prior fetch for that item's vendor returned and rejected a same-titled Lorcana row (so no matching `SearchResult` row exists), and the operator is now editing a *different* item configured against the same vendor
- **THEN** `"Lorcana"` (or whatever raw value was observed) still appears in that vendor's `product_line` suggestions, because it was recorded in `ObservedCategoryValue` at parse time independent of the rejection

#### Scenario: No suggestions exist yet
- **WHEN** an item has no `ItemSource`s configured, or its configured vendors have no `ObservedCategoryValue` rows yet (e.g. no fetch has run since this change shipped, and no backfill migration has populated `category` from prior history)
- **THEN** the form's suggestion list for the affected field is empty, and the field remains a plain free-text input with no suggestions offered
