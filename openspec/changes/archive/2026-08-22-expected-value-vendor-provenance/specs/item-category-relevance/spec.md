## MODIFIED Requirements

### Requirement: Item form offers vendor-labeled checkbox suggestions plus inline manual entries for both fields
The `SearchableItem` create/edit form SHALL offer non-binding suggestions for both `expected_product_line` and `expected_category` as one checkbox per distinct `ObservedCategoryValue` entry (not `SearchResult`) matching `field_name="product_line"` and `field_name="category"` respectively, scoped to the `Source`s used by the item's own configured `ItemSource`s. Each checkbox's identity SHALL be the `(source, value)` pair it represents, not the bare value alone — so two vendors reporting the identical raw value SHALL render as two independently checkable/uncheckable checkboxes, and checking or unchecking one SHALL have no effect on the other. A stored entry with `source: null` (a manually added value) SHALL render as its own row inline alongside vendor-sourced suggestion rows, labeled with a fixed "Manual entry" signpost, rather than in a separate free-text block. The form SHALL also offer a mechanism to add a new value not present among the current suggestions, stored with `source: null`. On save, the field's stored list SHALL be exactly the set of checked `(source, value)` pairs plus any newly added manual values (each stored as `{value, source: null}`), deduplicated by exact `(value, source)` pair equality — not by value alone. Suggestions SHALL NOT constrain or validate an entered value — any plain-text value SHALL remain acceptable regardless of whether it appears in the suggestion list.

#### Scenario: Operator sees suggestions scoped to the item's configured vendors
- **WHEN** an item has `ItemSource`s configured against `wt` and `f2f`, and `ObservedCategoryValue` rows already exist with various `product_line` and `category` values for `wt` and `f2f` (and unrelated values for other vendors from other items)
- **THEN** the form's suggestion checkboxes for that item include only distinct `(source, value)` pairs observed for `wt` and `f2f`, not values observed only for other vendors

#### Scenario: The same value observed from two vendors renders as two labeled checkboxes
- **WHEN** both `wt` and `f2f` have an `ObservedCategoryValue` row with `field_name="product_line"` and the identical `value`, `"Magic: The Gathering"`
- **THEN** the form renders two separate checkboxes for `expected_product_line`, one representing `(wt, "Magic: The Gathering")` and one representing `(f2f, "Magic: The Gathering")`, each independently checkable

#### Scenario: Checking suggestions from two different vendors stores two independent entries
- **WHEN** an operator checks both the `wt`-labeled and `f2f`-labeled checkboxes described above (identical value `"Magic: The Gathering"`) and saves the form
- **THEN** the item's `expected_product_line` list contains two entries: `{"value": "Magic: The Gathering", "source": "wt"}` and `{"value": "Magic: The Gathering", "source": "f2f"}` — not one deduplicated string

#### Scenario: Unchecking one of two same-valued vendor checkboxes removes only that vendor's entry
- **WHEN** an item's `expected_product_line` already contains both `{"value": "MTG", "source": "wt"}` and `{"value": "MTG", "source": "f2f"}` from a prior save, and an operator unchecks only the `wt`-labeled checkbox (leaving the `f2f`-labeled one checked) and saves
- **THEN** the item's `expected_product_line` contains only `{"value": "MTG", "source": "f2f"}` — the `wt` entry is removed and the `f2f` entry is untouched

#### Scenario: A newly added manual value not in the suggestion list is stored
- **WHEN** an operator uses the form's add-a-value mechanism to add `"MTG"`, a value not present in any current suggestion checkbox, and saves
- **THEN** `"MTG"` is added to the item's `expected_product_line` list as `{"value": "MTG", "source": null}`, alongside any checked vendor suggestions

#### Scenario: A value from a rejected row still appears as a suggestion
- **WHEN** an item's `expected_product_line` includes an entry with value `"Magic"`, a prior fetch for that item's vendor returned and rejected a same-titled Lorcana row (so no matching `SearchResult` row exists), and the operator is now editing a *different* item configured against the same vendor
- **THEN** `"Lorcana"` (or whatever raw value was observed) still appears as a suggestion checkbox for that vendor's `product_line` field, because it was recorded in `ObservedCategoryValue` at parse time independent of the rejection

#### Scenario: Re-editing an item pre-checks only the checkbox matching the stored entry's exact vendor
- **WHEN** an item's stored `expected_product_line` contains `{"value": "Magic: The Gathering", "source": "wt"}` only, and both `wt` and `f2f` have a suggestion checkbox for that identical value (per the two-checkbox scenario above)
- **THEN** only the `wt`-labeled checkbox is pre-checked when the edit form loads; the `f2f`-labeled checkbox is not pre-checked, even though it shares the same raw value

#### Scenario: A manually entered value is never reclassified as a vendor suggestion
- **WHEN** an item has a stored entry `{"value": "MTG", "source": null}`, and a vendor later reports the identical raw string `"MTG"` as a newly observed `ObservedCategoryValue`
- **THEN** the manual entry still renders as its own "Manual entry" row on next edit, distinct from that vendor's now-available suggestion checkbox (which renders separately, unchecked, since it represents a different stored pair)

#### Scenario: A stored vendor-tagged entry whose vendor no longer offers a live suggestion still renders and remains editable
- **WHEN** an item's stored `expected_product_line` contains `{"value": "Some Old Value", "source": "wt"}`, and `wt` no longer has an `ObservedCategoryValue` row for that exact value (e.g. the vendor's wording changed)
- **THEN** that entry still renders as its own checked row labeled `wt` when the edit form loads, remains individually removable via its own row, rather than being silently dropped from the form

#### Scenario: No suggestions exist yet
- **WHEN** an item has no `ItemSource`s configured, or its configured vendors have no `ObservedCategoryValue` rows yet (e.g. no fetch has run since this change shipped, and no backfill migration has populated `category` from prior history)
- **THEN** the form's suggestion checkboxes for the affected field are absent, and the manual add-a-value mechanism remains available with no error

### Requirement: SearchableItem may specify one or more expected product lines
`SearchableItem` SHALL have an optional, list-valued field, `expected_product_line`, a user can populate with zero or more `{"value": <str>, "source": <str|null>}` entries to indicate the game or product line(s) the item belongs to (e.g. a value `"Magic"`, or both `"Magic"` and `"MTG"` when vendors use divergent wording for the same product line). This field SHALL be an empty list by default. Each entry's `source` determines which vendor's rows it is matched against — a vendor-tagged entry (added via that vendor's suggestion checkbox) applies only to that vendor's rows; an entry with `source: null` (added via manual entry) applies to every vendor configured for the item. See "Parser results must match at least one expected product line when the list is non-empty" for the full matching contract.

#### Scenario: A vendor-checked suggestion applies only to that vendor
- **WHEN** a user checks a `wt`-labeled suggestion checkbox to add `"Magic"` to an item's `expected_product_line`
- **THEN** the value is stored as `{"value": "Magic", "source": "wt"}`, and disambiguates only `wt`'s rows for that item

#### Scenario: A manually entered value applies to every configured vendor
- **WHEN** a user adds `"Magic"` to an item's `expected_product_line` via the manual add-a-value mechanism, not a suggestion checkbox
- **THEN** the value is stored as `{"value": "Magic", "source": null}`, and disambiguates every vendor configured for that item

#### Scenario: A second value covers a vendor whose wording diverges from an already-covered value
- **WHEN** a user's item already has `{"value": "Magic", "source": null}` (applying to every configured vendor) in `expected_product_line`, and the user additionally checks a `wt`-labeled suggestion to add a second entry, `{"value": "MTG", "source": "wt"}`, covering wording `wt`'s own raw signal uses that `"Magic"` alone doesn't literally contain
- **THEN** both entries are stored, and a `wt` row matching either `"Magic"` or `"MTG"` satisfies the check on this axis (OR within the field — see "Parser results must match at least one expected product line when the list is non-empty")

#### Scenario: Item has no expected product line set
- **WHEN** an item's `expected_product_line` list has never been populated
- **THEN** it is an empty list by default

### Requirement: SearchableItem may independently specify one or more expected categories
`SearchableItem` SHALL have a second, optional, list-valued field, `expected_category`, independent of `expected_product_line`, a user can populate with zero or more `{"value": <str>, "source": <str|null>}` entries to indicate the set(s)/printing(s) the item belongs to (e.g. a specific MTG set name, possibly spelled differently per vendor). This field SHALL be an empty list by default and SHALL NOT require `expected_product_line` to also be non-empty. As with `expected_product_line`, each entry's `source` determines which vendor's rows it is matched against — a vendor-tagged entry applies only to that vendor's rows; a `source: null` entry applies to every vendor configured for the item.

#### Scenario: A vendor-checked suggestion applies only to that vendor
- **WHEN** a user checks a suggestion checkbox to add `"Strixhaven"` to an item's `expected_category`, sourced from vendor `wt`
- **THEN** the value is stored as `{"value": "Strixhaven", "source": "wt"}`, independently of any `expected_product_line` entries, and narrows only `wt`'s rows for that item

#### Scenario: A manually entered category value applies to every configured vendor
- **WHEN** a user adds `"Strixhaven"` to an item's `expected_category` via the manual add-a-value mechanism
- **THEN** the value is stored as `{"value": "Strixhaven", "source": null}`, and narrows every vendor configured for that item

#### Scenario: Item has no expected category set
- **WHEN** an item's `expected_category` list has never been populated
- **THEN** it is an empty list by default, regardless of whether `expected_product_line` is non-empty

### Requirement: Parser results must match at least one expected product line when the list is non-empty
For a candidate row parsed from vendor `V`, define the row's *applicable expected product line values* as the subset of the owning item's `expected_product_line` entries whose `source` equals `V`'s `Source.key`, plus every entry whose `source` is `null` (these apply to every vendor). `JSONSearchParser` SHALL reject a candidate row — omit it from `self.results` — when its applicable expected product line values are non-empty and **none** of them appear as a normalized (case-folded, whitespace-collapsed) substring of the row's product-line signal (a per-row value each vendor-specific parser supplies; see design.md for the exact signal used per vendor). A row passes this check if **any** applicable value matches (OR within the field). An item's `expected_product_line` entries tagged for a *different* vendor than the row's own SHALL NOT be considered for that row — a vendor with zero applicable entries SHALL have this check disabled entirely for its rows, even when the item has entries tagged for other vendors. This check SHALL run inside the shared `add_result` method, alongside and independent of the term-relevance check added by `search-term-relevance-filter` and the expected-category check below.

#### Scenario: Row's product-line signal contains one of the row's applicable expected product line values
- **WHEN** an item's `expected_product_line` contains `{"value": "Magic", "source": null}` and `{"value": "MTG", "source": null}` (both universal), and a candidate row parsed from vendor `wt` has product-line signal `"Magic the Gathering Singles"`
- **THEN** the row is included in `self.results` (subject to also passing the term-relevance check and any `expected_category` check), because `"Magic"` matches

#### Scenario: A universal value covers a vendor whose wording diverges
- **WHEN** an item's `expected_product_line` contains `{"value": "Magic", "source": null}` and `{"value": "MTG", "source": null}`, and a candidate row parsed from vendor `f2f` has product-line signal `"MTG Singles"` (a vendor that doesn't use the word "Magic")
- **THEN** the row is included, because `"MTG"` matches even though `"Magic"` does not

#### Scenario: Row's product-line signal matches none of the row's applicable values
- **WHEN** an item's `expected_product_line` contains `{"value": "Magic", "source": null}`, and a candidate row from any configured vendor has product-line signal `"Pokémon Trading Card Game"` — e.g. an `"Energy Retrieval"` search returning both the MTG card and the Pokémon TCG card of that exact name
- **THEN** the row is excluded from `self.results`

#### Scenario: Product-line matching is case-insensitive and tolerates incidental whitespace
- **WHEN** an item's `expected_product_line` contains `{"value": " magic ", "source": null}` (incidental whitespace) and a candidate row's product-line signal is `"MAGIC: THE GATHERING"`
- **THEN** the row is included in `self.results`

#### Scenario: Empty expected product line list disables the check for every vendor
- **WHEN** an item's `expected_product_line` list is empty
- **THEN** every candidate row, from every vendor configured for that item, passes this check regardless of its product-line signal

#### Scenario: A vendor-tagged entry only filters that vendor's own rows
- **WHEN** an item's `expected_product_line` contains only `{"value": "MTG", "source": "wt"}`, the item is configured against both `wt` and `f2f`, a candidate row parsed from `wt` has product-line signal `"MTG Singles"`, and a candidate row parsed from `f2f` has an unrelated product-line signal
- **THEN** the `wt` row is evaluated against `"MTG"` and included (since it matches); the `f2f` row's applicable expected product line values are empty (the `wt`-tagged entry does not apply to `f2f`), so the `f2f` row passes this check unfiltered regardless of its own signal

#### Scenario: A manually entered value filters every configured vendor
- **WHEN** an item's `expected_product_line` contains only `{"value": "Magic", "source": null}`, and the item is configured against both `wt` and `f2f`
- **THEN** a candidate row from `wt` and a candidate row from `f2f` are each evaluated against `"Magic"`, since the `null`-sourced entry is an applicable value for every vendor

#### Scenario: A vendor with no applicable entries is unfiltered even when the item has entries for other vendors
- **WHEN** an item's `expected_product_line` contains only `{"value": "Magic", "source": "wt"}` (no `null` entries, no `f2f` entries), the item is also configured against `f2f`, and a candidate row parsed from `f2f` has a product-line signal for an entirely unrelated product line (e.g. `"Pokémon Trading Card Game"`)
- **THEN** the `f2f` row still passes this check, because `f2f` has zero applicable expected product line values — this is intentional operator-scoped behavior, not a gap requiring automatic propagation from `wt`'s entry

### Requirement: Parser results must match at least one expected category when the list is non-empty
For a candidate row parsed from vendor `V`, define the row's *applicable expected category values* as the subset of the owning item's `expected_category` entries whose `source` equals `V`'s `Source.key`, plus every entry whose `source` is `null`. `JSONSearchParser` SHALL reject a candidate row — omit it from `self.results` — when its applicable expected category values are non-empty and **none** of them appear as a normalized (case-folded, whitespace-collapsed) substring of the row's category signal (the existing per-row set/printing value each vendor-specific parser already extracts). A row passes this check if **any** applicable value matches (OR within the field). An item's `expected_category` entries tagged for a *different* vendor than the row's own SHALL NOT be considered for that row — a vendor with zero applicable entries SHALL have this check disabled entirely for its rows, even when the item has entries tagged for other vendors. This check SHALL run inside the shared `add_result` method, alongside and independent of the term-relevance check and the expected-product-line check above.

#### Scenario: Row's category signal contains one of the row's applicable expected category values
- **WHEN** an item's `expected_category` contains `{"value": "Strixhaven", "source": null}`, and a candidate row's category signal is `"Strixhaven - Mystical Archive"`
- **THEN** the row is included in `self.results` (subject to also passing the term-relevance check and any `expected_product_line` check)

#### Scenario: Row's category signal matches none of the row's applicable values
- **WHEN** an item's `expected_category` contains `{"value": "Strixhaven", "source": null}`, and a candidate row's category signal is `"Kaldheim"`
- **THEN** the row is excluded from `self.results`

#### Scenario: Empty expected category list disables the check for every vendor
- **WHEN** an item's `expected_category` list is empty
- **THEN** every candidate row, from every vendor configured for that item, passes this check regardless of its category signal, regardless of whether `expected_product_line` is non-empty

#### Scenario: A vendor-tagged entry only filters that vendor's own rows
- **WHEN** an item's `expected_category` contains only `{"value": "Strixhaven", "source": "wt"}`, the item is configured against both `wt` and `f2f`, a candidate row parsed from `wt` has category signal `"Strixhaven - Mystical Archive"`, and a candidate row parsed from `f2f` has an unrelated category signal
- **THEN** the `wt` row is evaluated against `"Strixhaven"` and included; the `f2f` row's applicable expected category values are empty, so the `f2f` row passes this check unfiltered regardless of its own signal

#### Scenario: A vendor with no applicable entries is unfiltered even when the item has entries for other vendors
- **WHEN** an item's `expected_category` contains only `{"value": "Strixhaven", "source": "wt"}`, the item is also configured against `f2f`, and a candidate row parsed from `f2f` has an unrelated category signal
- **THEN** the `f2f` row still passes this check, because `f2f` has zero applicable expected category values

### Requirement: Expected product-line and category checks combine with OR within a field and AND between fields
For a candidate row parsed from vendor `V`, when both `expected_product_line` and `expected_category` have a non-empty *applicable* subset for `V` (per the "Parser results must match..." requirements above — that vendor's own tagged entries plus every `source: null` entry), the row SHALL be required to pass both fields' checks (in addition to the term-relevance check) to be included in `self.results` — matching any one applicable value within a field is sufficient for that field (OR), but both fields must each have at least one match when both have a non-empty applicable subset (AND). Neither field's check SHALL be short-circuited or skipped because of the other's presence, absence, or list length.

#### Scenario: Row passes product-line check but fails category check
- **WHEN** an item's `expected_product_line` contains `{"value": "Magic", "source": null}` and `expected_category` contains `{"value": "Strixhaven", "source": null}` (both universal), and a candidate row's product-line signal is `"Magic the Gathering Singles"` but its category signal is `"Kaldheim"`
- **THEN** the row is excluded from `self.results`

#### Scenario: Row passes both checks via different listed values
- **WHEN** an item's `expected_product_line` contains `{"value": "Magic", "source": null}` and `{"value": "MTG", "source": null}`, and `expected_category` contains `{"value": "Strixhaven", "source": null}`, and a candidate row's product-line signal is `"MTG Singles"` (matching the second value) and its category signal is `"Strixhaven - Mystical Archive"`
- **THEN** the row is included in `self.results` (subject to also passing the term-relevance check)

## ADDED Requirements

### Requirement: Expected product-line and category entries record vendor provenance, or none for manual entries
Each entry in `SearchableItem.expected_product_line`/`expected_category` SHALL be a `{"value": <str>, "source": <str|null>}` pair. `source` SHALL hold the `Source.key` of the vendor whose suggestion checkbox the operator checked to add that entry, or `null` when the entry was added via the form's manual add-a-value mechanism rather than a suggestion checkbox.

#### Scenario: A vendor-checked suggestion is tagged with that vendor
- **WHEN** an operator checks the `wt`-labeled suggestion checkbox for `"MTG"` and saves
- **THEN** the item's `expected_product_line` contains `{"value": "MTG", "source": "wt"}`

#### Scenario: A manually added value is tagged with no vendor
- **WHEN** an operator adds `"MTG"` via the manual add-a-value mechanism, not a suggestion checkbox, and saves
- **THEN** the item's `expected_product_line` contains `{"value": "MTG", "source": null}`
