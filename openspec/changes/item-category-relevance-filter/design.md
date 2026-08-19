## Context

This change assumes `search-term-relevance-filter` has already landed: `JSONSearchParser.add_result` already gates rows on whether `self.term` appears as a contiguous, normalized phrase in the row's title before the row reaches `self.results`. That gate solves word-order/word-selection false positives (`"Dragon Fire"` for a `"Fire Dragon"` search) but not this change's target: a row whose title genuinely, contiguously matches the term but is still the wrong item because the same name is reused across unrelated product lines (e.g. `"Energy Retrieval"` exists as both an MTG card and a Pokémon TCG card).

Investigation confirmed a usable per-row signal already exists in every in-scope vendor's raw response, currently unread for this purpose:

| Vendor | Parser | Raw category/product-line signal | Currently captured as `category`? |
|---|---|---|---|
| `wt` | `WtFiltersParser` | `row["category"]`, e.g. `"Magic the Gathering Singles"` | No — `row.get("subcategory") or row.get("category", "")` (`tracking/parsers.py:183`) prefers `subcategory` (set name, e.g. `"Strixhaven - Mystical Archive"`), so the product-line signal is discarded whenever a set name is present |
| `f2f` | `ShopifyParser` | `src["General_Game_Type"]` (array, e.g. `["Magic: The Gathering"]`), also present as `src["Game Type"]` | No — only `src.get("MTG_Set_Name") or src.get("Set")` (set name) is captured |
| `hfx` | `StorepassParser` | `product["vendor"]`, e.g. `"Magic: The Gathering"` | No — only `productLineData["set"]` (set name) is captured |

This app is not TCG-specific: other vendors (`mastermind` — LEGO/toys, `javablend` — coffee, `cc` — computer parts) exist as fixtures/investigation docs or as an implemented parser, so the item-level field this change adds must be framed generically (a plain "category" the user types), not as a TCG "game" concept.

The existing `category` kwarg on `add_result` / column on `SearchResult` already has a job: it displays set/printing information to a human in `searchableitem_detail.html` and the CSV/JSON export (`tracking/views.py` `EXPORT_FIELDNAMES`). The raw "set" signal and the raw "category/product-line" signal are different fields per vendor — sharpest for `wt`, which has both `category` (product line) and `subcategory` (set) available in the same row but only surfaces one today.

## Goals / Non-Goals

**Goals:**
- Let a user opt an item into category disambiguation by typing a plain-text value (e.g. `"Magic"`, `"Pokemon"`) on `SearchableItem` — no regex knowledge required.
- Gate rows in `WtFiltersParser`, `ShopifyParser`, `StorepassParser` on that value, using each vendor's own raw category/product-line signal, via one shared comparison implemented once on `JSONSearchParser`.
- Leave the check fully opt-out when the item's category value is blank (the default) — no behavior change for items that don't set it.
- Keep matching simple: normalize (case-fold, collapse/strip whitespace) both sides, then a substring check — internally implemented with `re.escape()` on the user's text so it is functionally a plain substring match with no regex metacharacter surprises, even though the mechanism under the hood is a regex search, mirroring the sibling change's normalization approach.
- Build on the sibling change's established pattern (gate inside `add_result`, silent drop, DEBUG log) rather than introducing a second enforcement style.

**Non-Goals:**
- Not resolving, in this change's implementation, the reuse-vs-new-field fork below without a decision — see Open Questions.
- Not extending to `CCSearchParser` (`cc`, HTML-based) or to vendors without an implemented parser (`mastermind`, `javablend`) — same scope boundary the sibling change drew for the same structural reason (different base class / not yet buildable).
- Not building a closed enum/taxonomy of categories (MTG, Pokémon, Lorcana, …) or a per-vendor normalization/mapping table. A single user-typed substring (e.g. `"Magic"`) was checked against all three vendors' actual raw strings during exploration and matches all of them without any mapping table:
  - `wt`: `"Magic the Gathering Singles"` ⊇ `"Magic"`
  - `f2f`: `"Magic: The Gathering"` ⊇ `"Magic"`
  - `hfx`: `"Magic: The Gathering"` ⊇ `"Magic"`

  A mapping table would add maintenance burden (one entry per vendor per game) for no observed benefit over plain substring matching.
- Not changing `title_include_patterns`/`title_exclude_patterns` or `matching.py` — this is a new, independent, item-level gate, parallel to but not replacing the existing per-`ItemSource` regex mechanism.
- Not modifying the `search-term-relevance-filter` gate itself — this change only adds a second, independent check at the same choke point.

## Decisions

**1. New field lives on `SearchableItem`, not `ItemSource`.**
An item's category/product-line identity doesn't vary by vendor — `"Fire Dragon"` the MTG card is the same card regardless of which of `wt`/`f2f`/`hfx` is being searched. Setting the value once per item (rather than duplicating it across every `ItemSource` row for that item) avoids drift between sources and matches the mental model a user has ("this item is an MTG card"), even though the *raw signal* it's compared against is vendor-shaped and extracted per-parser.

**2. Plain text, not regex — normalized substring match, user input escaped before use.**
`title_include_patterns` already gives operators a raw-regex escape hatch; this field is deliberately not that. A user typing `"Magic"` should not need to know regex syntax works, nor risk a typo like an unescaped `(` breaking the check. Internally: normalize both the user's value and the row's raw category signal (case-fold, collapse/strip whitespace), `re.escape()` the normalized user value, and search for it as a substring of the normalized signal. This is mechanically identical in spirit to the sibling change's phrase-containment check (same normalization approach), just applied to a different field and with input-escaping added since this field's input is unstructured user text rather than a search term the pipeline already controls.

**3. Blank item category value disables the check (pass everything).**
Same posture as `title_include_patterns` (empty list = no filtering) and the sibling change's blank-`self.term` rule. Nothing to validate against with no value; the check must not silently drop every row for items that haven't opted in.

**4. Rejected rows are dropped silently (no result), not raised as errors or merely flagged.**
Consistency with the sibling change's established behavior at the same `add_result` choke point: this is routine filtering (a vendor returning a same-named item from a different product line), not a parse failure, and not something requiring a human to triage via an "unmatched" UI flag. A DEBUG-level log line noting the rejected title, the row's raw category signal, and the item's expected value is enough for troubleshooting.

**5. Scope: `JSONSearchParser` subclasses only (`WtFiltersParser`, `ShopifyParser`, `StorepassParser`).**
Identical boundary to `search-term-relevance-filter`. `CCSearchParser` builds titles incrementally during DOM traversal rather than receiving a complete row dict with a distinct category field readily available, and remains a documented follow-up, not a blocker here. `mastermind` and `javablend` have investigation docs and fixtures but no parser class in `tracking/parsers.py` yet, so there is nothing to wire this change into for them.

## Open Questions

**Does the per-row raw category signal reuse the existing `category` field/kwarg on `add_result`, or does a new, distinct field need to be threaded through?** This is the one design fork this proposal deliberately leaves open rather than pre-deciding:

- **Option A — Reuse `category`.** Change what each parser passes as `category` to be the product-line signal (`wt`: prefer `row["category"]` over `subcategory`; `f2f`: prefer `General_Game_Type` over `MTG_Set_Name`; `hfx`: prefer `vendor` over `productLineData["set"]`). Simplest change — no new parameter, no schema addition beyond the `SearchableItem` field. **Cost:** changes what users see today in `searchableitem_detail.html` and CSV/JSON export for `wt` (loses set-name granularity, e.g. `"Strixhaven - Mystical Archive"` → `"Magic the Gathering Singles"`) and for `f2f`/`hfx` (currently show set name, would show product line instead) — a real, visible behavior change to an existing, working display field, not just an internal implementation detail.
- **Option B — Add a new, distinct field.** Widen `add_result(title, price, instock, category="", product_category="")` (name TBD) and thread a second value through each parser (`wt`: `row["category"]`; `f2f`: `General_Game_Type`; `hfx`: `vendor`), used only for this new gate. `category` keeps its current set/printing display meaning untouched. **Cost:** wider shared-method signature, a new entry in `JSONSearchParser.data_keys`, and a decision about whether/how the new signal is also persisted or surfaced (e.g. a new `SearchResult` column) versus consumed only transiently during filtering.

Resolve this during `tasks.md`/implementation planning for this change, informed by whichever is cheaper given the actual state of `search-term-relevance-filter`'s landed implementation and whether product wants set-name display preserved for `wt`.

## Risks / Trade-offs

- **False negatives are not recoverable downstream**, same structural risk as the sibling change's phrase gate: if a vendor's raw category signal is absent, malformed, or doesn't contain the user's expected substring for a genuinely correct item (e.g. a vendor omits the product-line field for some rows), the row never reaches `parser.results` and no `ItemSource` configuration can bring it back. → Mitigation: opt-in (blank = no check) limits blast radius to items where a user has deliberately set a category value; DEBUG logging aids troubleshooting if a user reports a missing result.
- **Vendor field coverage is unverified beyond the single `"Lightning Bolt"` fixture per vendor.** All three raw signals were confirmed present in the existing single-category fixtures; whether they're reliably populated across all rows in production (not just the sampled fixture) is unconfirmed. → Mitigation: fixture work in this change should add a second, distinct-category row per vendor fixture (mirroring the sibling's plan to add a false-positive row) to exercise the gate before shipping.
- **Coincidental substring collisions remain possible** (e.g. a category string for one product line happens to contain a shorter, unrelated category value as a substring) — not observed in current fixtures, structurally the same category of risk the sibling design accepted for title phrase-matching. → Mitigation: accepted risk, consistent with the precedent set by the sibling change; revisit if a real collision surfaces.
