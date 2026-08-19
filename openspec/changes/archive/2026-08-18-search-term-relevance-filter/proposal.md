## Why

`WtFiltersParser`, `ShopifyParser` (backs `f2f`), and `StorepassParser` (backs `hfx`) each parse every row a vendor's search endpoint returns with no check that the row's title actually references the search term. Live smoke tests against the `wt` vendor's search API confirmed this is not a theoretical risk: searching `"The Queen of Dale"` (30,277 vendor-reported total results) returns page-1 rows like `"Chip 'N' Dale, Recovery Rangers"` and `"Wulfgar of Icewind Dale"`; searching `"The Unbeatable Squirrel Girl"` (16,274 total) returns `"The Girl in the Fireplace"` and `"Massacre Girl"`; searching `"Fire Dragon"` (an MTG card) returns four listings of `"Dragon Fire"` — a *different card from a different game* (Disney Lorcana) — plus two unrelated deck-accessory products, because both words appear somewhere in the title regardless of order. `hfx`'s own investigation doc separately recorded an unexplained ~3x discrepancy between the storefront's on-page result count and the scraped API's count, consistent with the same pattern. The only existing safety net, `ItemSource.title_include_patterns`/`title_exclude_patterns`, is per-item, opt-in, and empty by default, so unrelated rows currently flow straight through into `candidates` and `SearchResult` unless a store admin has hand-written a regex for that specific item. This silently corrupts price tracking rather than failing loudly.

## What Changes

- Add a shared base-class contract on `JSONSearchParser` that requires the search term to appear as a contiguous, whitespace/case-normalized phrase within each row's title before the row is added to `self.results` — not merely that all of the term's words appear somewhere in the title. Token-presence-only matching was tested and rejected: for `term="Fire Dragon"`, it still accepts `"Dragon Fire (0130)"` (a reversed-name Lorcana card) and `"Deck Protectors - Dragon Shield Matte Dual Fire Horse 100ct"` (an unrelated accessory), because both words are present, just not adjacent or in order. Requiring the term as a contiguous phrase rejects all of these while still accepting every genuine print observed in testing, since vendor titles consistently keep the item's name intact and place print/language/foil metadata around it (as a prefix, suffix, or bracketed/parenthetical addition) rather than interleaved into the name itself.
- Wire `WtFiltersParser`, `ShopifyParser`, and `StorepassParser` through this shared check so all three vendors get the same baseline filtering with no per-parser duplication.
- Design the check as an inherited default so future `JSONSearchParser` subclasses get it automatically without extra wiring.
- Leave the existing per-`ItemSource` `title_include_patterns`/`title_exclude_patterns` mechanism unchanged — this new check is a stricter, always-on baseline that runs before those optional, item-specific patterns, not a replacement for them.
- Add fixture/test coverage that exercises false-positive rows — both a missing-word case and a reordered/wrong-item case (modeled on the live `"Fire Dragon"` / `"Dragon Fire"` collision) — and asserts both are excluded from parser results.

## Capabilities

### New Capabilities
- `search-term-relevance`: baseline parser-level requirement that a search result row must contain the search term as a contiguous phrase before being surfaced as a candidate, implemented once on the shared JSON parser base class and inherited by vendor-specific parsers.

### Modified Capabilities
(none — no existing spec currently governs parser result filtering)

## Impact

- `tracking/parsers.py`: `JSONSearchParser` (new shared method/hook), `WtFiltersParser`, `ShopifyParser`, `StorepassParser` (wired to use it).
- `tracking/tests/test_wtfilters_parser.py` and equivalent f2f/hfx parser tests: new false-positive-rejection cases.
- `tracking/fixtures/html/{wt,f2f,hfx}/search_results_sample.json`: may need added rows (or a synthetic supplementary fixture) representing both a missing-word false positive and a reordered/wrong-item false positive, since the current captured fixtures happen to contain only full-phrase-match rows.
- Not affected: `tracking/matching.py`, `ItemSource.title_include_patterns`/`title_exclude_patterns`, `CCSearchParser` (HTML-based, different base class — out of scope for this change; a follow-up could extend the same idea to it).
