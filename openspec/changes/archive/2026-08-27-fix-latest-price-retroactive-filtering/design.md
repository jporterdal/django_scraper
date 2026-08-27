## Context

Five user-editable fields currently gate which parsed rows become `SearchResult` rows, and all five are enforced exactly once, at fetch time, never again:

| Field | Lives on | Enforcement point (today) |
|---|---|---|
| `text` (search term) | `SearchableItem` | `JSONSearchParser.add_result` (parsers.py) |
| `expected_product_line` | `SearchableItem` (per-vendor) | `JSONSearchParser.add_result` |
| `expected_category` | `SearchableItem` (per-vendor) | `JSONSearchParser.add_result` |
| `title_include_patterns` | `ItemSource` | `matching.filter_results_for_item_source`, called separately post-parse from `scrape.py` |
| `title_exclude_patterns` | `ItemSource` | `matching.filter_results_for_item_source` |

Once a `SearchResult` row is stored, it is permanently eligible for price/chart calculations regardless of later edits to any of these fields. `SearchableListView.get_queryset` (views.py) computes "Latest price" per item as the minimum, across the item's `ItemSource`s, of each source's single most-recent in-stock row — a pure SQL `Subquery`/`OuterRef` construction with no relevance check at all. The detail page's price-history chart (`_build_source_chart_series`) has the same gap; it already computes a `matches` flag per row (`result_matches_item_source`, views.py) but only uses it to gray out a table row cosmetically — it's never consulted by the chart or the list-view calculation.

Constraint: `title_include_patterns`/`title_exclude_patterns` are arbitrary regex, evaluated in Python (`re.search`). There is no portable equivalent inside a single SQL query across this app's supported backends (sqlite by default, Postgres via `DATABASE_URL` in prod) — the codebase already avoids backend-specific SQL tricks for a simpler case (`source_pattern_groups_for_items`, models.py). Any read-time re-check therefore has to happen in Python, which shapes how the list-view query can be restructured.

**Bug discovered during implementation (dedup timestamp masking).** While implementing task 4, we found that the `source_latest`/`cheapest_item_source` correlated-subquery pair this change replaces has a second, independent defect, unrelated to relevance filtering: dedup (`_dedupe_unit_candidates`, scrape.py) is keyed `(item, source, title)`, not `(item, source)`, so a single source routinely carries several concurrently-valid, independently-timestamped "threads" of results — one per matching title/variant (the existing tiebreak test `test_latest_price_picks_cheapest_of_same_source_tied_rows` already proves this happens). Ranking a source's rows for "latest price" by `-timestamp, price, title` across the whole `(item, source)` pair, without partitioning by title, lets a freshly-touched thread (its price just changed, so dedup let a new row through) shadow a sibling thread whose price is unchanged, and therefore un-restored by dedup, and therefore still sitting on an older timestamp — even though that sibling's price is just as current. This bug predates this change (it's present in the subquery being removed) and was carried forward into this change's first-draft `RowNumber` window, because Decision 3 as originally written preserved "same ordering as today" verbatim rather than questioning the partition key. It surfaced only because implementing the retroactive-filtering walk required looking hard at this exact query. Folded into this change rather than deferred, since fixing it means revising the same function this change was already restructuring, and both defects share one root cause: trusting a "most recent row" without asking "most recent *for what*."

## Goals / Non-Goals

**Goals:**
- Re-validate a stored `SearchResult` row against the owning item's/item-source's *current* relevance criteria (all five fields above) before it's used in the "Latest price" calculation or the detail-page chart.
- Resolve each source's latest price per independently-deduped title thread, not per source as a whole, so a fresh timestamp on one thread cannot shadow a still-current price on a sibling thread that dedup left untouched — corrects the dedup-timestamp-masking bug described above.
- Keep this to one DB round trip for the list view regardless of how many items are on the page (not N+1 per item or per source).
- Consolidate the five checks into `matching.py` as a single source of truth, without losing today's per-check debug-log granularity at ingest.
- Preserve full `SearchResult` history untouched — this is a read-time/display concern, not a data-retention one.

**Non-Goals:**
- Not deleting, flagging, or otherwise mutating existing `SearchResult` rows. Prospective-only: changes what counts as "current," not the stored record.
- Not extending retroactive matching to `CCSearchParser` (HTML-based) or other non-`JSONSearchParser` parsers — same boundary the `search-term-relevance`/`item-category-relevance` changes already drew for ingest-time enforcement.
- Not addressing fields that change *what gets fetched* rather than what's accepted from an already-fetched candidate (`pinned_url`, `url_suffix`, `Source.base_search_url`/`parser_key`) — these have no well-defined retroactive replay; there's no candidate to re-check without an actual new fetch.
- Not touching CSV/JSON export (`_item_export_rows`) — it's an intentional full raw history dump with no "latest price" concept, out of scope by design.
- Not solving regex-based ReDoS exposure from user-supplied patterns — pre-existing risk, not newly introduced, flagged separately below.

## Decisions

**1. Prospective-only semantics, not purge-on-edit.** Excluding a title going forward does not delete or hide it from `SearchResult` history. Rejected alternative: deleting/flagging rows the moment a pattern changes, which would silently rewrite the detail-page chart's history and destroy data irreversibly for what might be a user experimenting with a pattern before confirming it's right.

**2. Read-time re-match, not a stored/materialized flag on `SearchResult`.** Chosen because a stored boolean (e.g. `matches_current_filters`) needs an invalidation hook on every write path that touches any of the five fields — `ItemSource` save, the bulk-edit path (`forms.py`), and `SearchableItem` save (`text`/`expected_*`) — three-plus places to keep in sync, with silent drift as the failure mode if one is missed (the same class of bug this change fixes, one layer deeper). Read-time re-match has a single source of truth by construction and reuses the one place this partially already happens (`r.matches` display flag). Trade-off accepted: repeated computation on every read, mitigated by Decision 3's bounded window.

**3. Two-level resolution — partition by `(item_id, source_id, title)`, not `(item_id, source_id)`, and do not cap rows per thread.** The existing per-item correlated `Subquery` shape can only express "give me the single newest row" — it can't "walk until one still matches" without regex inside SQL, which isn't portable, so a windowed query (`RowNumber`/`Window`, supported natively by both sqlite ≥3.25 and Postgres) resolved in Python is still the right shape. But the partition key must be `(item_id, source_id, title)`, not `(item_id, source_id)`: partitioning by title-thread is what corrects the dedup-timestamp-masking bug described in Context, by preventing one thread's fresh timestamp from ever being compared against, or displacing, a sibling thread's row in the same ranking. Resolution becomes two levels instead of one:
  - **Per thread** (`item_id, source_id, title`): rank rows newest-first, walk them for the first row where `result_matches_item_source(...)` is true — the existing retroactive-filtering behavior, now scoped to one thread instead of an entire source.
  - **Per source**: the source's "latest known price" is the minimum across its threads' winners (a source can have several currently-valid threads at once; today's tiebreak scenarios — tied timestamp, tied price — still apply within and across them).
  - **Per item**: minimum across sources, unchanged from before this change.

  The per-thread window is **not capped** at a fixed size — every row in a thread is a candidate for the walk. A fixed cap (the original Decision 6 approach) would just reintroduce a narrower version of the same masking bug: a thread that's been excluded long enough that its last-matching row falls outside the cap would silently contribute nothing, indistinguishable from a thread that's genuinely gone stale. Uncapping trades that correctness risk for unbounded per-thread scan cost — see Risks, and the Migration Plan note on follow-up work. Fallback if the window-then-filter interaction proves awkward in the Django ORM: one query per `(ItemSource, title)` thread with an early Python break — strictly worse round-trip-wise but functionally identical, kept as an escape hatch, not the target.

**4. Consolidate five checks into `matching.py`; ingest calls granular functions, read-time calls one aggregate.** `matching.py` gains `term_matches`, `value_matches_any` (generalizes the `expected_product_line`/`expected_category` check), and keeps `title_matches_rules`. `parsers.py`'s `add_result` calls these individually so each rejection keeps its own `logger.debug` reason (today's four distinct messages), while a new aggregate `result_matches_item_source(title, category, product_line, item_source)` runs all four checks and returns one bool — used exclusively by the read-time re-check path (list view, chart), where only a single pass/fail is needed.

**5. Title-pattern enforcement moves into `add_result`, eliminating the separate post-parse filtering pass.** Today ingest is two stages: per-row checks inside `add_result` (term/product_line/category), then a second bulk pass (`filter_results_for_item_source`) after parsing completes. Once `add_result` can reach `item_source.title_include_patterns`/`exclude_patterns` (passed into the parser constructor alongside the existing `expected_*` params), there's no reason to keep two stages — ingest becomes one filtering pass, at one call site, with four log-distinguishable outcomes.

**6. `RETROACTIVE_MATCH_WINDOW` is removed, not tuned.** Superseded by Decision 3's per-thread partitioning: a fixed-size window was a workaround for walking a whole source's mixed-thread history, and threads made it both wrong (see Context) and unnecessary (each thread is its own, much shorter, walk). There is no longer a "how deep should the shared window be" question to answer with a setting.

## Risks / Trade-offs

- [Risk] With no per-thread cap, a thread with a very long excluded streak (e.g. months of non-matching rows before a still-matching one, or a thread that never matches again) is scanned in full on every list-page load → Mitigation: none in this change — this is the explicit motivating case for a follow-up change to either archive/prune old `SearchResult` rows or bound "latest price" resolution to a rolling time period (e.g. results older than the last week/month are not considered regardless of match status), trading a bit of correctness at the very long tail for a hard cap on scan cost. Flagged as required follow-up work, not solved here; still preferable to a silent-cap bug in the interim.
- [Risk] More DB round trips per list-page load than today (windowed query + `ItemSource` fetch vs. one annotated query) → Mitigation: still a small constant number of queries scoped to the current page, not per-item; verify actual latency against production-scale data before wide rollout.
- [Risk] Detail-page chart points can visibly disappear after a pattern/term edit, which may surprise a user with no explanation in the UI → Mitigation: intended behavior for this change (that's the bug being fixed), but a "N results hidden by current filters" affordance is a reasonable presentation-layer follow-up, not required for correctness here.
- [Risk] User-supplied regex patterns re-evaluated on every list-page load (vs. once at ingest) multiplies exposure to a pathological/slow pattern → Mitigation: pre-existing risk, not newly introduced by this change; out of scope here, flagged for a possible follow-up (e.g. a regex complexity/timeout guard).

## Migration Plan

No schema or data migration: no new columns, no backfill, `SearchResult`/`ItemSource`/`SearchableItem` are structurally untouched — this is a pure query-and-logic change. Rollout is a single code deploy behind the updated/new test suite; no feature flag needed given there's no data-migration risk to stage. Recommend a manual latency check of the restructured list-view query against production-like data volume before merging, given the query-shape change (correlated subquery → windowed query + Python resolution). Rollback is a plain code revert — nothing written differently that would need undoing.

## Open Questions

- Should the list view (or detail page) surface any explicit signal when a source drops out of the price comparison entirely (all of its threads excluded), or is silent omission acceptable for this pass?
- Are there other "Latest price" consumers beyond `SearchableListView` and the detail-page chart worth auditing before implementation (e.g. any API/JSON endpoint not yet identified)?
- When should the follow-up archiving/time-bounded "latest price" change (Risks) be scheduled — immediately after this one, given it's now an unbounded-scan-cost concern rather than a hypothetical, or deferred until real data shows the per-thread walk actually getting deep?
