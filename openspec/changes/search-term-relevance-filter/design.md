## Context

`tracking/parsers.py` has one shared base class for JSON API vendors, `JSONSearchParser`, with a single choke point every subclass already uses: `add_result(title, price, instock, category="")`. Confirmed by reading the three vendor parsers in scope:

- `WtFiltersParser.parse_data` calls `self.add_result(title=row.get("title", ""), ...)` once per row in `data.results[]`.
- `ShopifyParser.parse_data` calls `self.add_result(title=display, ...)` once per variant, where `display` is `f"{title} ({condition})"`.
- `StorepassParser.parse_data` calls `self.add_result(title=display, ...)` once per variant, same `f"{title} ({condition})"` shape.

None of the three do any comparison between the row's title and `self.term` (set in `JSONSearchParser.__init__`, already available on every instance). All captured fixtures (`tracking/fixtures/html/{wt,f2f,hfx}/search_results_sample.json`) happen to contain only rows where the title includes every word of the test query ("Lightning Bolt"), because page-1 results from a relevance-ranked vendor search skew toward exact matches — the fixtures don't exercise the gap, they mask it. `hfx_investigation.md` separately records an unexplained ~3x mismatch between the storefront's on-page result count (329) and the scraped API's count (111) for the same query, consistent with looser vendor-side matching than assumed.

**Live smoke tests against `wt`'s search API confirm the gap is real and quantify it.** Four ad hoc queries were run through the actual `WtFiltersParser` class against live `POST /api/search` responses:

| term | vendor `total_results` | page-1 rows (of 24) that are real matches |
|---|---|---|
| `"The Queen of Dale"` | 30,277 | 6 |
| `"The Unbeatable Squirrel Girl "` | 16,274 | 4 |
| `"Fateful Discovery"` | 113 | 5 |
| `"Fire Dragon"` | 3,646 | 1 |

`WtFiltersParser.results` today contains all 24/24 rows unfiltered for every one of these, confirmed by running the real parser class (not a re-implementation) against the captured payloads.

The `"Fire Dragon"` case is the decisive one for the match-policy decision below: an **all-words-present** check (ignoring order/adjacency) still accepts 9/24 rows, including four listings of `"Dragon Fire"` — a different card from a different game entirely (Disney Lorcana) — plus a Lorcana playmat and two `"Dragon Shield"`-branded deck accessories, none of which are the queried MTG card. Only a **contiguous-phrase** check correctly narrows this to the 1 genuine `"Fire Dragon (POR)"` row. All disagreements between the two policies, on live data, favored the phrase check with zero exceptions in the other direction — no genuine print was ever missed by requiring phrase order/adjacency, because every observed vendor title kept the item's core name as one uninterrupted run of text.

Downstream, `ItemSource.title_include_patterns`/`title_exclude_patterns` (`tracking/matching.py`) already provide per-item regex filtering, but it's optional, empty by default, and configured per `ItemSource` rather than guaranteed by the pipeline itself.

## Goals / Non-Goals

**Goals:**
- One inherited implementation on `JSONSearchParser` that gates every row's title against `self.term` before it is added to `self.results`.
- Zero call-site changes required in `WtFiltersParser`, `ShopifyParser`, or `StorepassParser` — they already route every row through `add_result`, so updating the base class alone is sufficient.
- Any future `JSONSearchParser` subclass inherits the same protection automatically, with no opt-in step.
- Keep the check simple ("basic check"): case-insensitive, whitespace-normalized substring containment of the term as a single phrase, no fuzzy matching, no stemming, no external NLP dependency.
- Leave `ItemSource.title_include_patterns`/`title_exclude_patterns` and `matching.py` completely unchanged — this is a new, always-on baseline stage that runs at parse time, independent of and prior to that existing opt-in stage.

**Non-Goals:**
- `CCSearchParser` (HTML/DOM-based, a different base class — `HTMLResponseParserMixin` + `SearchParser`) is not touched by this change. Its `check_within_item_object`/`check_element_title` methods have the same structural gap, but extending it requires a different mechanism (it builds titles incrementally during DOM traversal rather than receiving a complete row dict) and is left as a follow-up.
- Not changing which field feeds the display title (e.g. f2f's unused `General_Card_Name`) — that's a separate, orthogonal question about title *quality*, not term *relevance*, and mixing it in here would broaden this change's blast radius.
- Not attempting to fix vendor-side search behavior — vendors are out of our control; this only filters what the app does with what they return.
- Not making the check configurable/toggleable per parser or per `ItemSource` in this change — YAGNI until a concrete case demands it.

## Decisions

**1. Gate lives inside `JSONSearchParser.add_result`, not a new abstract method.**
Every current subclass already calls `add_result` for every row; folding the check in there means `WtFiltersParser`, `ShopifyParser`, and `StorepassParser` need no changes at all. Alternative considered: a separate `_passes_term_filter(title)` hook that subclasses must call explicitly before `add_result` — rejected because it relies on every subclass author remembering to call it, which is exactly the kind of per-parser duplication this change exists to eliminate.

**2. Match policy: `self.term`, normalized, must appear as a contiguous phrase (case-insensitive substring) in the row's title — not merely have all its words present somewhere.**
This is the policy the assumption behind this whole change requires: the search term corresponds to an actual item's name, so the item's title should contain that name intact, with print/language/foil metadata appended around it rather than interleaved into it. Confirmed on live data — every genuine print across all four smoke-test queries kept the term as one uninterrupted run of text, so phrase-matching never produced a false negative in testing.

Two weaker alternatives were considered and rejected on the strength of live evidence, not just reasoning:
- **All tokens present, any position ("token-AND").** Directly falls to the `"Fire Dragon"` test: `"Dragon Fire (0130)"` (a different card, a different game, words reversed) and `"Deck Protectors - Dragon Shield Matte Dual Fire Horse 100ct"` (an unrelated accessory) both contain every word of the term and would incorrectly pass. Token-AND is a strict superset of phrase-matching — it can never reject something phrase-matching would keep, only wrongly accept things phrase-matching correctly rejects — so there is no correctness reason to prefer it, and phrase-matching is simpler to implement besides (a single substring check vs. a per-token loop).
- **Zero-tokens-present ("coarse net"), floated earlier during exploration.** Rejected because it leaves the originally reported bug (single-shared-word false positives, e.g. `"Chip 'N' Dale, Recovery Rangers"` for a `"Queen of Dale"` search) completely unaddressed — it only screens out rows with no relation to the term at all, which live testing shows is a small minority of the actual noise.

**3. Normalization before comparison: lowercase both sides, collapse runs of whitespace, strip leading/trailing whitespace.**
Confirmed necessary, not just tidy, by the `"The Unbeatable Squirrel Girl "` smoke test (note the trailing space, copied from how a user might plausibly type or paste a term) — without stripping, an unnormalized comparison would be fragile against incidental input whitespace. Punctuation (hyphens, apostrophes, brackets) is deliberately left untouched for this pass: fixtures and live results show vendors use punctuation as a *separator* around the core name (`" - Foil"`, `" - Japanese"`), never glued into the name itself, so naive substring matching hasn't misfired on it in any sample tested. Flagged as an open question below in case a future vendor breaks this pattern.

**4. Blank/empty `self.term` disables the check (pass everything).**
`JSONSearchParser.__init__` defaults `term=""`, and there's nothing to validate a row's relevance against with no term. Filtering would otherwise silently drop every row for any parser instantiated without a term (none of the current production call sites do this, but nothing guarantees a future one won't).

**5. Rejected rows are dropped silently (no result), not raised as errors.**
This is expected, routine filtering (a vendor over-returning results), not a parse failure — it must not surface as a `FetchJob.PARSE_ERROR`. A DEBUG-level log line noting the rejected title is enough for troubleshooting without polluting normal logs.

## Risks / Trade-offs

- **False negatives are not recoverable downstream, and phrase-matching is stricter than token-AND, so this risk is somewhat higher than a weaker policy would carry.** `ItemSource.title_include_patterns` can rescue false *positives* (over-broad results) by narrowing further, but if the base-class gate incorrectly drops a genuinely relevant row (e.g. a vendor title that reorders or splits the term's words around other text), no downstream `ItemSource` configuration can bring it back — it never reaches `parser.results`. → Mitigation: all four live smoke tests (`wt`, four different terms, 96 total page-1 rows) found zero cases of a genuine print failing the phrase check, so this is an accepted risk grounded in observed vendor behavior, not merely a theoretical trade-off; revisit if a production vendor is found to legitimately reorder or split names.
- **Non-English / transliterated titles.** Fixtures show rows like `Lightning Bolt - Japanese [105] ...` — the phrase check works here because the English name is still present intact in the title, but a hypothetical vendor returning only a foreign-language title with no English rendering of the term would be incorrectly rejected. → Mitigation: none in this change; flagged as an open question below.
- **A term could theoretically appear as a contiguous phrase inside an unrelated title purely by coincidence** (e.g. as a substring spanning a condition suffix and the start of unrelated trailing text) — not observed in any of the 96 live rows sampled across four queries, and inherently rarer than the token-AND coincidences that were observed (e.g. `"Fire"` and `"Dragon"` both appearing separately in `"Dragon Shield ... Fire Horse"`). → Mitigation: accepted risk; a stricter check (matching against a dedicated `card_name` field where a vendor exposes one) remains a natural follow-up, not blocking here.

## Open Questions

- Should the eventual follow-up for `CCSearchParser` (HTML path) reuse the same phrase-containment helper, or does its incremental DOM-based title assembly need a different integration point?
- Is there a real-world case (beyond the hypothetical transliteration scenario above) where phrase-containment is too strict for a vendor already in production — e.g. a vendor that legitimately formats titles as `"<Set> <Name>"` rather than `"<Name> <Set info>"`? Not observed in any smoke test so far (`wt` consistently leads with the name), but `f2f`/`hfx` haven't been live-smoke-tested the way `wt` has, only fixture-checked. Worth watching `FetchJob.Status.EMPTY` rates for `wt`/`f2f`/`hfx` after this ships, and worth running an equivalent live smoke test against `f2f`/`hfx` before or shortly after implementation.
