## Context

This change assumes `search-term-relevance-filter` has already landed: `JSONSearchParser.add_result` already gates rows on whether `self.term` appears as a contiguous, normalized phrase in the row's title before the row reaches `self.results`. That gate solves word-order/word-selection false positives (`"Dragon Fire"` for a `"Fire Dragon"` search) but not this change's target: a row whose title genuinely, contiguously matches the term but is still the wrong item because the same name is reused across unrelated product lines (e.g. `"Energy Retrieval"` exists as both an MTG card and a Pokémon TCG card).

Investigation confirmed every in-scope vendor's raw response actually carries **two** distinct category-shaped signals, not one — a broad, game/product-line-level signal and a narrower, set/printing-level signal — and today only the narrow one is captured, under the name `category`:

| Vendor | Parser | Broad signal (→ `product_line`) | Narrow signal (→ `category`, currently captured) |
|---|---|---|---|
| `wt` | `WtFiltersParser` | `row["category"]`, e.g. `"Magic the Gathering Singles"` | `row.get("subcategory") or row.get("category", "")` (`tracking/parsers.py:183`) — prefers `subcategory`, e.g. `"Strixhaven - Mystical Archive"` |
| `f2f` | `ShopifyParser` | `src["General_Game_Type"]` (array, e.g. `["Magic: The Gathering"]`), also present as `src["Game Type"]` | `src.get("MTG_Set_Name") or src.get("Set")` |
| `hfx` | `StorepassParser` | `product["vendor"]`, e.g. `"Magic: The Gathering"` | `productLineData["set"]` |

Notably, **wt's own raw JSON field literally named `"category"` is the broad signal**, not the narrow one — our app's internal `category` field only ended up meaning "narrow/set" because `WtFiltersParser` chose to prefer `subcategory` when present. This confirms "category" is not a vendor-universal term for either granularity; it is genuinely vendor-defined. That is why both of this change's fields — the existing `category` and the new `product_line` — must be documented as "meaning is defined per-parser," not assumed to line up with any vendor's own field-naming convention.

Two other vendors, investigated but not yet wired to a parser, independently corroborate and stress-test this two-tier shape:
- **Mastermind (LEGO)**: `resources.results.products[].tags[]` contains merchant-defined `MAINCAT_Building Sets` (broad) vs. `SUBCAT_Brick Sets` (narrow) tags (`tracking/docs/mastermind_investigation.md:145`) — the same broad/narrow split, for a non-TCG vendor, confirming this generalizes past card games.
- **Javablend (coffee)**: only one coarse signal exists (`resources.results.products[].type`, e.g. `"Coffee"` — `tracking/docs/javablend_investigation.md:122`), with no second tier. When/if a parser is written for this vendor, it will only ever be able to populate one of the two fields, not both — the two-field model is a superset capability, not a guarantee every vendor supplies both.

This app is not TCG-specific: other vendors (`mastermind` — LEGO/toys, `javablend` — coffee, `cc` — computer parts) exist as fixtures/investigation docs or as an implemented parser, so both fields this change adds must be framed generically (plain text a user types), not as TCG "game" concepts.

The existing `category` kwarg on `add_result` / column on `SearchResult` already has a job: it displays set/printing information to a human in `searchableitem_detail.html` and the CSV/JSON export (`tracking/views.py` `EXPORT_FIELDNAMES`). This change keeps that meaning and that column untouched, and adds a second, independent field/column, `product_line`, for the broad signal — see Decision 6.

**Naming collision to note explicitly:** `tracking/docs/hfx_investigation.md` already uses the literal string `product_line` as a **Storepass API query parameter** name — a request-shaping, per-`ItemSource` concern (e.g. `&product_line=Magic: the Gathering` baked into `base_search_url`/`url_suffix`, `tracking/docs/hfx_investigation.md:37`). This change's `product_line` is a **different layer**: a response-side filtering concern (an item-level expected value checked against a per-row signal extracted from the parsed response). The vocabulary overlap is coincidental but real — see Decision 6 for how naming avoids conflating the two.

## Goals / Non-Goals

**Goals:**
- Let a user opt an item into product-line disambiguation by typing a plain-text value (e.g. `"Magic"`, `"Pokemon"`) — no regex knowledge required.
- Let a user **independently** opt an item into set/printing-level filtering by typing a second, separate plain-text value (e.g. a specific MTG set name) — same mechanism, different axis, off by default.
- Gate rows in `WtFiltersParser`, `ShopifyParser`, `StorepassParser` on either or both values, using each vendor's own raw broad/narrow signals, via one shared comparison implemented once on `JSONSearchParser`.
- Keep each check fully and independently opt-out when its corresponding expected value is blank (the default) — no behavior change for items that don't set either.
- Keep matching simple: normalize (case-fold, collapse/strip whitespace) both sides, then a substring check — internally implemented with `re.escape()` on the user's text so it is functionally a plain substring match with no regex metacharacter surprises, mirroring the sibling change's normalization approach.
- Build on the sibling change's established pattern (gate inside `add_result`, silent drop, DEBUG log) rather than introducing a second enforcement style.
- Persist the new `product_line` signal on `SearchResult` (mirroring the existing `category` column) so it is visible in the detail view and export, not just consumed transiently during filtering.
- Give operators a way to discover which raw strings will actually match, for both fields, sourced from every raw value ever observed for the vendors an item is actually tracked against — not just the values from rows the app chose to keep, and not a hand-maintained taxonomy.

**Non-Goals:**
- Not building a closed enum/taxonomy of categories (MTG, Pokémon, Lorcana, …) or a per-vendor normalization/mapping table. A single user-typed substring (e.g. `"Magic"`) was checked against all three vendors' actual raw broad-signal strings during exploration and matches all of them without any mapping table:
  - `wt`: `"Magic the Gathering Singles"` ⊇ `"Magic"`
  - `f2f`: `"Magic: The Gathering"` ⊇ `"Magic"`
  - `hfx`: `"Magic: The Gathering"` ⊇ `"Magic"`

  A mapping table would add maintenance burden (one entry per vendor per game) for no observed benefit over plain substring matching. The value-discovery UI (Decision 9) surfaces real persisted values as *suggestions*, not a constrained/enforced set of valid choices — it does not reintroduce a taxonomy.
- Not changing `title_include_patterns`/`title_exclude_patterns` or `matching.py` — this is a new, independent, item-level gate, parallel to but not replacing the existing per-`ItemSource` regex mechanism.
- Not modifying the `search-term-relevance-filter` gate itself — this change only adds two further, independent checks at the same choke point.

## Decisions

**1. New fields live on `SearchableItem`, not `ItemSource`.**
An item's category/product-line identity doesn't vary by vendor — `"Fire Dragon"` the MTG card is the same card regardless of which of `wt`/`f2f`/`hfx` is being searched. Setting the value once per item (rather than duplicating it across every `ItemSource` row for that item) avoids drift between sources and matches the mental model a user has ("this item is an MTG card"), even though the *raw signals* they're compared against are vendor-shaped and extracted per-parser.

**2. Plain text, not regex — normalized substring match, user input escaped before use.**
`title_include_patterns` already gives operators a raw-regex escape hatch; these fields are deliberately not that. A user typing `"Magic"` should not need to know regex syntax works, nor risk a typo like an unescaped `(` breaking the check. Internally: normalize both the user's value and the row's raw signal (case-fold, collapse/strip whitespace), `re.escape()` the normalized user value, and search for it as a substring of the normalized signal.

**3. A blank expected value disables that field's check (pass everything on that axis).**
Same posture as `title_include_patterns` (empty list = no filtering) and the sibling change's blank-`self.term` rule. Nothing to validate against with no value; the check must not silently drop every row for items that haven't opted in. This applies independently to each of the two fields.

**4. Rejected rows are dropped silently (no result), not raised as errors or merely flagged.**
Consistency with the sibling change's established behavior at the same `add_result` choke point: this is routine filtering (a vendor returning a same-named item from a different product line or set), not a parse failure, and not something requiring a human to triage via an "unmatched" UI flag. A DEBUG-level log line noting the rejected title, the row's raw signal(s), and the item's expected value(s) is enough for troubleshooting.

**5. Scope: `JSONSearchParser` subclasses only (`WtFiltersParser`, `ShopifyParser`, `StorepassParser`).**
Identical boundary to `search-term-relevance-filter`. `CCSearchParser` builds titles incrementally during DOM traversal rather than receiving a complete row dict with distinct category fields readily available, and remains a documented follow-up, not a blocker here. `mastermind` and `javablend` have investigation docs and fixtures but no parser class in `tracking/parsers.py` yet, so there is nothing to wire this change into for them.

**6. Two independent fields, not one — `product_line` (new) alongside `category` (existing, untouched). Resolves the previously open reuse-vs-new-field fork as "Option B," with the specific field name `product_line`.**
`category` keeps its current meaning, column, and display/export behavior exactly as-is (rejecting the alternative "Option A" of repurposing `category` to mean product line, which would have silently changed existing `wt`/`f2f`/`hfx` display output). A new, distinct `product_line` field/column carries the broad game/product-line signal. This decouples the app's internal vocabulary from any single vendor's field-naming convention (see the wt `category`/`subcategory` evidence in Context) and avoids the display regression Option A would have caused.

The name `product_line` was chosen deliberately, including in the face of the naming collision noted in Context: hfx's own Storepass API already uses `product_line` as a **request-side query parameter** name for essentially the same underlying concept (scoping a search to MTG vs. Pokémon). That overlap is treated as validation of the term's fitness, not a reason to avoid it — but to keep the two layers unambiguous in code, the item-level field is `SearchableItem.expected_product_line` (the `expected_` prefix marks it as a response-filtering input, distinct from any vendor request parameter), while the underlying persisted/display column on `SearchResult` is simply `product_line`. The sibling `category` field gains an analogous item-level counterpart, `SearchableItem.expected_category`, for symmetry.

`add_result`'s signature becomes `add_result(title, price, instock, category="", product_line="")`. Both raw signals are supplied per-parser; both expected values are checked independently and must both pass (when non-blank) for a row to survive, alongside the existing term-relevance gate.

**7. `expected_category` extends filtering to the set/printing axis — independent of, and asymmetric in cost to, `expected_product_line`.**
Originally this change's target was only the game/product-line collision (`"Energy Retrieval"`, MTG vs. Pokémon). Extending the same opt-in/blank-disables mechanism to `category` lets an operator additionally narrow to a specific set/printing when that's the actual disambiguation they need. The two fields are independently optional and independently checked (AND-combined when both are set) — an item can use neither, either, or both.

This is cheaper to build than it looks symmetric: `category`'s raw per-row signal is **already extracted by every parser today** (it's what populates the existing display column), so enabling it as a filter needs no new parser wiring — only the new `SearchableItem.expected_category` field and a check in `add_result`. `product_line`'s raw signal needs new extraction work in all three parsers (see Impact/tasks).

**8. `product_line` is persisted as a new `SearchResult` column, mirroring the existing `category` column.**
Consumed-and-discarded-during-filtering was considered and rejected: since this change adds a whole new axis of silent filtering, being able to see *why* a row was kept or rejected (the actual `product_line` value returned) matters for troubleshooting, matching the precedent that `category` is already persisted and displayed rather than transient. `product_line` becomes visible in `searchableitem_detail.html` and the CSV/JSON export (`EXPORT_FIELDNAMES`) alongside the existing `category` column.

**9. Vendor-scoped live value-discovery UI (datalist/autocomplete) for both fields, sourced from a dedicated observation log — not `SearchResult`, and not a taxonomy.**
An operator typing an expected value has no visibility into what raw strings vendors actually return (`"Magic"` vs. `"MTG"` vs. `"Magic: The Gathering"`), and a wrong guess causes the change's top-named risk: silent, unrecoverable false negatives. Rather than hand-maintain a mapping table (explicitly rejected — see Non-Goals) or read suggestions from `SearchResult`, this change adds a dedicated table, `ObservedCategoryValue`:

- Fields: `source` (FK to `Source`), `field_name` (`"category"` | `"product_line"`), `value` (raw string as observed, not normalized), `last_seen` (bumped on every repeat observation). Unique on `(source, field_name, value)`.
- Populated by an **unconditional** upsert inside `JSONSearchParser.add_result`, for every row the parser processes — both the `category` and `product_line` raw signals, whenever non-blank — **before** any of the term-relevance / `expected_product_line` / `expected_category` checks run. This is the same choke point as the existing conditional DEBUG-level rejection log (Decision 4), just unconditional rather than gated on rejection.

**Why not `SearchResult`?** `SearchResult` only ever contains *accepted* rows — after term-relevance filtering, this change's own product-line/category filtering, title include/exclude patterns, and dedup. Sourcing suggestions from it has a structural blind spot that gets *worse over time*: once an item's `expected_product_line`/`expected_category` filter is active, any row it rejects going forward never reaches `SearchResult`, so the exact collision the suggestion UI exists to surface — "this vendor also returns a same-titled Lorcana card for this search" — disappears from view right as filtering elsewhere makes it relevant to a different item. `ObservedCategoryValue` decouples "what raw vocabulary has this vendor ever returned" from "what rows did we choose to keep," closing that gap. It is a strict superset of what `SearchResult` could ever offer for this purpose, so `SearchResult` is dropped as a suggestion source entirely rather than queried alongside the new table.

The suggestion query is unchanged in scope — still filtered to the `Source`s the item's `ItemSource`s actually use (via `Source.key` for the short vendor code, `Source.name` for display) — just pointed at the new table, most-recently-observed first:

```python
ObservedCategoryValue.objects.filter(
    source__in=Source.objects.filter(itemsource__item=item),
    field_name="product_line",  # or "category"
).order_by("-last_seen").values_list("source__key", "value")
```

This is suggestion data, not an enforced set of valid choices — an operator can still type any plain-text value.

**Cold start, revisited.** Because `ObservedCategoryValue` is a brand-new table, both `category` and `product_line` suggestions start empty on deploy — `category`'s previously-assumed advantage (an existing, already-populated `SearchResult` column) no longer carries over automatically now that suggestions read from the new table instead. A one-time backfill migration seeding `ObservedCategoryValue(field_name="category")` from distinct historical `SearchResult.category` values (grouped by `source`) restores that advantage cheaply; `product_line` has no equivalent backfill source, since it was never captured before this change, and stays cold-start only regardless. See tasks.md.

One risk asymmetry remains, unrelated to the storage source and not solved by this change:
- **Elevated false-negative risk on `expected_category`**: the Non-Goals section's cross-vendor substring check (`"Magic"` matching all three vendors' *broad* signals) has no equivalent verification for *narrow* (set-name) signals, which are more vendor-idiosyncratic in exact wording/punctuation. Silent-drop-on-mismatch is more exposed on the `expected_category` axis than on `expected_product_line` — part of why the discovery UI matters for both fields, arguably more for `expected_category`.

## Risks / Trade-offs

- **False negatives are not recoverable downstream**, same structural risk as the sibling change's phrase gate: if a vendor's raw signal is absent, malformed, or doesn't contain the user's expected substring for a genuinely correct item, the row never reaches `parser.results` and no `ItemSource` configuration can bring it back. This risk is **not evenly distributed across the two new fields** — see Decision 9's note on `expected_category`'s weaker cross-vendor convergence guarantee compared to `expected_product_line`'s verified one. → Mitigation: opt-in (blank = no check, independently per field) limits blast radius; DEBUG logging and the value-discovery UI aid troubleshooting/prevention.
- **Vendor field coverage is unverified beyond the single `"Lightning Bolt"` fixture per vendor.** All raw signals (broad and narrow) were confirmed present in the existing single-category fixtures; whether they're reliably populated across all rows in production is unconfirmed. → Mitigation: fixture work in this change should add a second, distinct-category row per vendor fixture (mirroring the sibling's plan to add a false-positive row) to exercise the gate before shipping.
- **Coincidental substring collisions remain possible** (e.g. a category string for one product line happens to contain a shorter, unrelated category value as a substring) — not observed in current fixtures, structurally the same category of risk the sibling design accepted for title phrase-matching. → Mitigation: accepted risk, consistent with the precedent set by the sibling change; revisit if a real collision surfaces.
- **`ObservedCategoryValue` cold start** (Decision 9): the new table starts empty on deploy, so both `category` and `product_line` suggestions are unavailable for a vendor until at least one fetch has run against it post-deploy (or a backfill migration has populated `category` from historical `SearchResult` data). `product_line` has no backfill source at all, since it was never captured before this change. → Mitigation: the backfill migration for `category` (see tasks.md); for `product_line`, none needed beyond documenting it — the field remains free-text and functions without suggestions, just less discoverably at first.
- **Unconditional per-row upsert cost** (Decision 9): every parsed row now triggers an `ObservedCategoryValue` upsert regardless of whether it's accepted or rejected, not just a conditional log line. → Mitigation: table growth is bounded by the cardinality of distinct raw strings per vendor (dozens, not fetch/row volume), and the values themselves are ordinary vendor-supplied product-category text, not sensitive data.
- **Naming proximity between hfx's `product_line` query parameter and this change's `product_line` field/column** (Decision 6) could confuse a future reader skimming `hfx_investigation.md` alongside `models.py`/`parsers.py`. → Mitigation: the `expected_` prefix on the `SearchableItem` field, plus the explicit callout in this document and in `hfx_investigation.md` (see tasks.md), keeps the two layers distinguishable.

## Open Questions

None remaining. The reuse-vs-new-field fork (previously open) is resolved as Decision 6; the resulting scope (two independent fields, persistence, value-discovery UI) is resolved as Decisions 6–9.
