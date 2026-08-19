## Why

`search-term-relevance-filter` closes the gap where a vendor row shares words with the search term but isn't the term as a contiguous phrase (e.g. `"Dragon Fire"` for a `"Fire Dragon"` search). It cannot close a distinct gap: a row whose title **is** a legitimate phrase match for the term but is still the wrong item, because the same name exists in an unrelated category or product line — e.g. searching `"Energy Retrieval"` can legitimately return both the MTG card and the Pokémon TCG card of that exact name from a single vendor. No amount of title-phrase strictness distinguishes these, because the title text itself is identical or near-identical; only a category-level signal can. This app is not TCG-specific (vendors span card singles, LEGO sets, coffee, computer parts), so the same collision risk generalizes to any vendor where one title string is reused across unrelated product lines.

Investigation confirms the raw signal to make this distinction already exists in every in-scope vendor's response, unused: `wt` returns `row["category"]` (e.g. `"Magic the Gathering Singles"`), `f2f` returns `src["General_Game_Type"]` (e.g. `["Magic: The Gathering"]`), and `hfx` returns `product["vendor"]` (e.g. `"Magic: The Gathering"`) — none of which are currently read for filtering purposes.

## What Changes

- Add a new, opt-in, plain-text field on `SearchableItem` letting a user specify the category/product-line their item belongs to (e.g. `"Magic"`, `"Pokemon"`), disambiguating same-named items across unrelated categories.
- Add a shared check on `JSONSearchParser` (assumes `search-term-relevance-filter` has landed and its `add_result` gate already exists) that, when the item has a non-blank category value, rejects a candidate row unless a per-row category signal contains that value as a normalized (case-folded, whitespace-collapsed), substring match — implemented as an internally regex-escaped comparison so the user never writes or sees regex syntax, unlike the existing raw-regex `title_include_patterns`.
- Wire `WtFiltersParser`, `ShopifyParser`, and `StorepassParser` to supply their vendor's raw category/product-line signal into this check — same scope boundary as `search-term-relevance-filter` (`JSONSearchParser` subclasses only; `CCSearchParser`/`cc` and not-yet-implemented parsers such as `mastermind`/`javablend` are out of scope, left as future follow-ups).
- Leave a blank item category value as a full opt-out — the check is skipped entirely, same posture as `title_include_patterns` and the sibling change's blank-term rule.
- Rejected rows are dropped silently (DEBUG-level log only), matching the sibling change's enforcement posture at the same `add_result` choke point — not surfaced as a parse failure, not merely flagged/advisory.
- **Open design question, deliberately not resolved in this proposal:** whether the per-row raw category signal reuses the existing `category` parser field/kwarg (today: means "set/printing," shown to users in `searchableitem_detail.html` and CSV/JSON export) or requires a new, distinct field threaded through `add_result` so display semantics for wt/f2f/hfx are undisturbed. See `design.md` for the full tradeoff.

## Capabilities

### New Capabilities
- `item-category-relevance`: opt-in, item-level requirement that a search result row must match the item's specified category/product-line (plain-text, normalized substring) before being surfaced as a candidate, implemented on the shared `JSONSearchParser` base class and inherited by vendor-specific parsers.

### Modified Capabilities
(none — this is a new, independent filtering stage layered on top of, not altering, the requirements added by `search-term-relevance-filter`)

## Impact

- `tracking/models.py`: `SearchableItem` (new plain-text category field).
- `tracking/parsers.py`: `JSONSearchParser` (new shared category-check method/hook, building on the `add_result` gate from `search-term-relevance-filter`), `WtFiltersParser`, `ShopifyParser`, `StorepassParser` (wired to supply their vendor's raw category signal).
- `tracking/tests/test_wtfilters_parser.py` and equivalent f2f/hfx parser tests: new same-title-different-category rejection cases.
- `tracking/fixtures/html/{wt,f2f,hfx}/search_results_sample.json`: may need added rows representing a same-title, different-category false positive, since current fixtures are single-category (`"Lightning Bolt"`, all MTG).
- Depends on: `search-term-relevance-filter` (must be implemented first; this change assumes its `add_result` gate already exists and does not re-implement or alter it).
- Not affected: `tracking/matching.py`, `ItemSource.title_include_patterns`/`title_exclude_patterns`, `CCSearchParser` (HTML-based, out of scope, same boundary as the sibling change), `mastermind`/`javablend` (no parser class implemented yet).
