## ADDED Requirements

### Requirement: SearchableItem may specify one or more expected product lines
`SearchableItem` SHALL have an optional, list-valued, plain-text field, `expected_product_line`, a user can populate with zero or more values to indicate the game or product line(s) the item belongs to (e.g. `["Magic"]`, or `["Magic", "MTG"]` when vendors use divergent wording for the same product line), used to disambiguate rows that would otherwise pass term-relevance filtering despite belonging to an unrelated product line. This field SHALL be an empty list by default.

#### Scenario: User adds an expected product line value to an item
- **WHEN** a user adds `"Magic"` to an item's `expected_product_line` list
- **THEN** the value is stored on the `SearchableItem` and applies to every source configured for that item

#### Scenario: User adds a second expected product line value for a vendor with divergent wording
- **WHEN** a user's item already has `"Magic"` in `expected_product_line`, and the user adds a second value, `"MTG"`, to cover a vendor whose raw signal doesn't contain `"Magic"`
- **THEN** both values are stored on the `SearchableItem`'s `expected_product_line` list, and a row matching either value on this axis satisfies the check (see "Parser results must match at least one expected product line when the list is non-empty")

#### Scenario: Item has no expected product line set
- **WHEN** an item's `expected_product_line` list has never been populated
- **THEN** it is an empty list by default

### Requirement: SearchableItem may independently specify one or more expected categories
`SearchableItem` SHALL have a second, optional, list-valued, plain-text field, `expected_category`, independent of `expected_product_line`, a user can populate with zero or more values to indicate the set(s)/printing(s) the item belongs to (e.g. a specific MTG set name, possibly spelled differently per vendor), used to narrow results beyond product-line disambiguation. This field SHALL be an empty list by default and SHALL NOT require `expected_product_line` to also be non-empty.

#### Scenario: User adds an expected category value to an item
- **WHEN** a user adds `"Strixhaven"` to an item's `expected_category` list
- **THEN** the value is stored on the `SearchableItem`'s `expected_category` list independently of any `expected_product_line` values and applies to every source configured for that item

#### Scenario: Item has no expected category set
- **WHEN** an item's `expected_category` list has never been populated
- **THEN** it is an empty list by default, regardless of whether `expected_product_line` is non-empty

### Requirement: Parser results must match at least one expected product line when the list is non-empty
`JSONSearchParser` SHALL reject a candidate row — omit it from `self.results` — when the owning item's `expected_product_line` list is non-empty and **none** of its values appear as a normalized (case-folded, whitespace-collapsed) substring of the row's product-line signal (a per-row value each vendor-specific parser supplies; see design.md for the exact signal used per vendor). A row passes this check if **any** listed value matches (OR within the field). This check SHALL run inside the shared `add_result` method, alongside and independent of the term-relevance check added by `search-term-relevance-filter` and the expected-category check below.

#### Scenario: Row's product-line signal contains one of the item's expected product line values
- **WHEN** an item's `expected_product_line` is `["Magic", "MTG"]` and a candidate row's product-line signal is `"Magic the Gathering Singles"`
- **THEN** the row is included in `self.results` (subject to also passing the term-relevance check and any `expected_category` check), because at least one listed value (`"Magic"`) matches

#### Scenario: Row's product-line signal matches a second listed value from a different vendor's wording
- **WHEN** an item's `expected_product_line` is `["Magic", "MTG"]` and a candidate row's product-line signal is `"MTG Singles"` (a vendor that doesn't use the word "Magic")
- **THEN** the row is included in `self.results`, because the listed value `"MTG"` matches even though `"Magic"` does not

#### Scenario: Row's product-line signal matches none of the item's expected product line values
- **WHEN** an item's `expected_product_line` is `["Magic"]` and a candidate row's product-line signal is `"Pokémon Trading Card Game"` — e.g. an `"Energy Retrieval"` search returning both the MTG card and the Pokémon TCG card of that exact name
- **THEN** the row is excluded from `self.results`

#### Scenario: Product-line matching is case-insensitive and tolerates incidental whitespace
- **WHEN** an item's `expected_product_line` is `[" magic "]` (incidental whitespace) and a candidate row's product-line signal is `"MAGIC: THE GATHERING"`
- **THEN** the row is included in `self.results`

#### Scenario: Empty expected product line list disables the check
- **WHEN** an item's `expected_product_line` list is empty
- **THEN** every candidate row passes this check regardless of its product-line signal

### Requirement: Parser results must match at least one expected category when the list is non-empty
`JSONSearchParser` SHALL reject a candidate row — omit it from `self.results` — when the owning item's `expected_category` list is non-empty and **none** of its values appear as a normalized (case-folded, whitespace-collapsed) substring of the row's category signal (the existing per-row set/printing value each vendor-specific parser already extracts). A row passes this check if **any** listed value matches (OR within the field). This check SHALL run inside the shared `add_result` method, alongside and independent of the term-relevance check and the expected-product-line check above.

#### Scenario: Row's category signal contains one of the item's expected category values
- **WHEN** an item's `expected_category` is `["Strixhaven"]` and a candidate row's category signal is `"Strixhaven - Mystical Archive"`
- **THEN** the row is included in `self.results` (subject to also passing the term-relevance check and any `expected_product_line` check)

#### Scenario: Row's category signal matches none of the item's expected category values
- **WHEN** an item's `expected_category` is `["Strixhaven"]` and a candidate row's category signal is `"Kaldheim"`
- **THEN** the row is excluded from `self.results`

#### Scenario: Empty expected category list disables the check
- **WHEN** an item's `expected_category` list is empty
- **THEN** every candidate row passes this check regardless of its category signal, regardless of whether `expected_product_line` is non-empty

### Requirement: Expected product-line and category checks combine with OR within a field and AND between fields
When an item sets both `expected_product_line` and `expected_category` to non-empty lists, a candidate row SHALL be required to pass both fields' checks (in addition to the term-relevance check) to be included in `self.results` — matching any one value within a field is sufficient for that field (OR), but both fields must each have at least one match when both are non-empty (AND). Neither field's check SHALL be short-circuited or skipped because of the other's presence, absence, or list length.

#### Scenario: Row passes product-line check but fails category check
- **WHEN** an item's `expected_product_line` is `["Magic"]` and `expected_category` is `["Strixhaven"]`, and a candidate row's product-line signal is `"Magic the Gathering Singles"` but its category signal is `"Kaldheim"`
- **THEN** the row is excluded from `self.results`

#### Scenario: Row passes both checks via different listed values
- **WHEN** an item's `expected_product_line` is `["Magic", "MTG"]` and `expected_category` is `["Strixhaven"]`, and a candidate row's product-line signal is `"MTG Singles"` (matching the second listed value) and its category signal is `"Strixhaven - Mystical Archive"`
- **THEN** the row is included in `self.results` (subject to also passing the term-relevance check)

### Requirement: Expected product-line and category values are plain text, not regex
Every value in `expected_product_line` and `expected_category` SHALL be matched as literal text, not interpreted as a regular expression — a user's input SHALL NOT need to be regex-safe (e.g. literal parentheses or other regex metacharacters in any listed value must not change matching behavior or raise an error).

#### Scenario: A listed value containing regex metacharacters matches literally
- **WHEN** an item's `expected_product_line` is `["Magic (Core Set)"]` and a candidate row's product-line signal is `"Magic (Core Set) Singles"`
- **THEN** the row is included in `self.results`, with the parentheses treated as literal characters, not a regex group

### Requirement: Vendor-specific JSONSearchParser subclasses supply both signals with no per-parser filtering logic
`WtFiltersParser`, `ShopifyParser`, and `StorepassParser` SHALL each extract their vendor's own per-row product-line signal (newly wired by this change) and category signal (already extracted today) and pass both through to the shared `add_result` check; the comparison logic itself — including the list-valued, OR-within-field matching — SHALL be implemented once on `JSONSearchParser` and inherited, not duplicated per subclass.

#### Scenario: WtFiltersParser drops a same-title, different-product-line row
- **WHEN** `WtFiltersParser` is configured for an item with `expected_product_line` `["Magic"]` and parses a vendor response containing both a genuine Magic: the Gathering row and a same-titled row from an unrelated product line
- **THEN** `parser.results` contains only the Magic: the Gathering row

#### Scenario: ShopifyParser drops a same-title, different-product-line row
*(Illustrative — mirrors the WtFiltersParser scenario for the f2f payload shape.)*
- **WHEN** `ShopifyParser` is configured for an item with `expected_product_line` `["Magic"]` and parses a vendor response containing both a genuine Magic: the Gathering hit and a same-titled hit from an unrelated product line
- **THEN** `parser.results` contains only the Magic: the Gathering hit

#### Scenario: StorepassParser drops a same-title, different-product-line row
*(Illustrative — mirrors the WtFiltersParser scenario for the hfx payload shape.)*
- **WHEN** `StorepassParser` is configured for an item with `expected_product_line` `["Magic"]` and parses a vendor response containing both a genuine Magic: the Gathering product and a same-titled product from an unrelated product line
- **THEN** `parser.results` contains only the Magic: the Gathering product

### Requirement: Product-line signal is persisted and displayed alongside category
`SearchResult` SHALL have a `product_line` column, populated from the per-row product-line signal supplied by the parser at storage time, displayed in `searchableitem_detail.html` and included in the CSV/JSON export field set (`EXPORT_FIELDNAMES`) alongside the existing `category` column. This column holds a single value per row, unaffected by the list-valued shape of `SearchableItem.expected_product_line`.

#### Scenario: Stored result exposes its product-line value
- **WHEN** a fetch stores a `SearchResult` row whose parsed product-line signal was `"Magic the Gathering Singles"`
- **THEN** `SearchResult.product_line` is `"Magic the Gathering Singles"`, visible in the item detail view and present in CSV/JSON export output

### Requirement: Rejected rows do not raise or fail the fetch
Filtering a row for product-line or category relevance SHALL NOT raise an exception or mark the parse as failed; it is routine filtering, not a parse error, consistent with the enforcement posture of the term-relevance check added by `search-term-relevance-filter`.

#### Scenario: A response containing only non-matching rows still parses successfully
- **WHEN** a parser's vendor response contains zero rows whose product-line signal, category signal, or both (per whichever of the item's expected-value lists are non-empty) match at least one of the item's listed expectations
- **THEN** `parse_response`/`parse_data` completes normally and `self.results` is an empty list (which the existing `FetchJob.Status.EMPTY` handling in `tracking/scrape.py` already covers)

### Requirement: Every parsed row's category and product-line signals are recorded, regardless of filtering outcome
`JSONSearchParser.add_result` SHALL record each non-blank raw `category` and `product_line` signal it receives into a per-vendor observation log (`ObservedCategoryValue`: `source`, `field_name`, `value`, `last_seen`), for **every** row processed — before, and independent of, whether that row goes on to pass or fail the term-relevance, `expected_product_line`, or `expected_category` checks. A repeat observation of the same `(source, field_name, value)` SHALL update `last_seen` rather than create a duplicate row.

#### Scenario: An accepted row's signals are recorded
- **WHEN** `WtFiltersParser` parses a row whose title matches the search term and whose product-line/category signals also satisfy the item's expected values (the row is appended to `self.results`)
- **THEN** an `ObservedCategoryValue` row exists (or has its `last_seen` updated) for `wt`/`"product_line"`/that row's product-line value, and likewise for `"category"`

#### Scenario: A rejected row's signals are still recorded
- **WHEN** `WtFiltersParser` parses a row whose product-line signal does not contain any of the item's non-empty `expected_product_line` list (the row is omitted from `self.results`)
- **THEN** an `ObservedCategoryValue` row still exists (or has its `last_seen` updated) for that vendor/field/value, even though the row itself never reaches `self.results` or `SearchResult`

### Requirement: Item form offers vendor-labeled checkbox suggestions plus manual entry for both fields
The `SearchableItem` create/edit form SHALL offer non-binding suggestions for both `expected_product_line` and `expected_category` as one checkbox per distinct `ObservedCategoryValue` entry (not `SearchResult`) matching `field_name="product_line"` and `field_name="category"` respectively, scoped to the `Source`s used by the item's own configured `ItemSource`s. Each checkbox SHALL be labeled with its source so an operator can tell which vendor a suggestion came from. Suggestion checkboxes are not deduplicated across vendors: if two sources report the identical raw value, each SHALL render as its own labeled checkbox. The form SHALL also offer a manual free-text entry mechanism, independent of the checkboxes, for values not present in the suggestion list. On save, checked suggestion values and manually entered values SHALL be merged and deduplicated by exact string equality before being stored on the field's list. Suggestions SHALL NOT constrain or validate the entered value — any plain-text value SHALL remain acceptable regardless of whether it appears in the suggestion list.

#### Scenario: Operator sees suggestions scoped to the item's configured vendors
- **WHEN** an item has `ItemSource`s configured against `wt` and `f2f`, and `ObservedCategoryValue` rows already exist with various `product_line` and `category` values for `wt` and `f2f` (and unrelated values for other vendors from other items)
- **THEN** the form's suggestion checkboxes for that item include only distinct `(source, value)` pairs observed for `wt` and `f2f`, not values observed only for other vendors

#### Scenario: The same value observed from two vendors renders as two labeled checkboxes
- **WHEN** both `wt` and `f2f` have an `ObservedCategoryValue` row with `field_name="product_line"` and the identical `value`, `"Magic: The Gathering"`
- **THEN** the form renders two separate checkboxes for `expected_product_line`, one labeled for `wt` and one labeled for `f2f`, both with the same underlying value

#### Scenario: Checking suggestions from two different vendors stores one deduplicated entry
- **WHEN** an operator checks both the `wt`-labeled and `f2f`-labeled checkboxes described above (identical value `"Magic: The Gathering"`) and saves the form
- **THEN** the item's `expected_product_line` list contains `"Magic: The Gathering"` exactly once, not twice

#### Scenario: A manually entered value not in the suggestion list is stored
- **WHEN** an operator types `"MTG"` into the manual entry field for `expected_product_line`, a value not present in any current suggestion checkbox, and saves the form
- **THEN** `"MTG"` is added to the item's `expected_product_line` list alongside any checked suggestions

#### Scenario: A value from a rejected row still appears as a suggestion
- **WHEN** an item's `expected_product_line` includes `"Magic"`, a prior fetch for that item's vendor returned and rejected a same-titled Lorcana row (so no matching `SearchResult` row exists), and the operator is now editing a *different* item configured against the same vendor
- **THEN** `"Lorcana"` (or whatever raw value was observed) still appears as a suggestion checkbox for that vendor's `product_line` field, because it was recorded in `ObservedCategoryValue` at parse time independent of the rejection

#### Scenario: Re-editing an item pre-checks every matching vendor's checkbox for a stored value
- **WHEN** an item's stored `expected_product_line` list contains `"Magic: The Gathering"`, and both `wt` and `f2f` have a suggestion checkbox for that identical value (per the two-checkbox scenario above)
- **THEN** both the `wt`-labeled and `f2f`-labeled checkboxes are pre-checked when the edit form loads

#### Scenario: A stored value with no matching current suggestion appears in the manual entry field instead
- **WHEN** an item's stored `expected_product_line` list contains a value that does not match any current suggestion checkbox for the item's configured vendors (e.g. the vendor stopped returning it, or it was set before any fetch ran)
- **THEN** that value appears in the manual free-text entry field when the edit form loads, rather than being silently dropped from the form

#### Scenario: No suggestions exist yet
- **WHEN** an item has no `ItemSource`s configured, or its configured vendors have no `ObservedCategoryValue` rows yet (e.g. no fetch has run since this change shipped, and no backfill migration has populated `category` from prior history)
- **THEN** the form's suggestion checkboxes for the affected field are absent, and the manual free-text entry remains available with no error
