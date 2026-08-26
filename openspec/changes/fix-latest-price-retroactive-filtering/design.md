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

## Goals / Non-Goals

**Goals:**
- Re-validate a stored `SearchResult` row against the owning item's/item-source's *current* relevance criteria (all five fields above) before it's used in the "Latest price" calculation or the detail-page chart.
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

**3. One windowed query (SQL `RowNumber`, partitioned by item+source) instead of a query per `ItemSource`.** The existing per-item correlated `Subquery` shape can only express "give me the single newest row" — it can't "walk until one still matches" without regex inside SQL, which isn't portable. Fetching a *bounded* recent window (`RowNumber() OVER (PARTITION BY item_id, source_id ORDER BY -timestamp, price, title) <= N`) in one query, then resolving matches in Python, keeps DB round trips constant regardless of page size. `RowNumber`/`Window` is supported natively by both sqlite (≥3.25) and Postgres, so this doesn't reintroduce a portability problem. Fallback if the window-then-filter interaction proves awkward in the Django ORM: one query per `ItemSource` with `[:N]` and an early Python break — strictly worse round-trip-wise but functionally identical, kept as an escape hatch, not the target.

**4. Consolidate five checks into `matching.py`; ingest calls granular functions, read-time calls one aggregate.** `matching.py` gains `term_matches`, `value_matches_any` (generalizes the `expected_product_line`/`expected_category` check), and keeps `title_matches_rules`. `parsers.py`'s `add_result` calls these individually so each rejection keeps its own `logger.debug` reason (today's four distinct messages), while a new aggregate `result_matches_item_source(title, category, product_line, item_source)` runs all four checks and returns one bool — used exclusively by the read-time re-check path (list view, chart), where only a single pass/fail is needed.

**5. Title-pattern enforcement moves into `add_result`, eliminating the separate post-parse filtering pass.** Today ingest is two stages: per-row checks inside `add_result` (term/product_line/category), then a second bulk pass (`filter_results_for_item_source`) after parsing completes. Once `add_result` can reach `item_source.title_include_patterns`/`exclude_patterns` (passed into the parser constructor alongside the existing `expected_*` params), there's no reason to keep two stages — ingest becomes one filtering pass, at one call site, with four log-distinguishable outcomes.

**6. Window size `N` is a tunable constant, not hardcoded forever.** Most sources resolve on the very first row in the window (patterns are edited far less often than fetches happen); `N` only needs to be deep enough to skip however many trailing now-excluded rows accumulated since the last edit. Start at a generous default (e.g. 20) as a Django setting so it can be adjusted without a code change if real data shows sources needing a deeper walk.

## Risks / Trade-offs

- [Risk] A source's now-excluded streak is deeper than window size `N`, so a still-valid older row further back is never reached and the source silently contributes nothing → Mitigation: generous default `N`, plus a log line (not just silent omission) when a source's entire window is exhausted with zero matches, so this is observable rather than mysterious; revisit `N` if seen in practice.
- [Risk] More DB round trips per list-page load than today (windowed query + `ItemSource` fetch vs. one annotated query) → Mitigation: still a small constant number of queries scoped to the current page, not per-item; verify actual latency against production-scale data before wide rollout.
- [Risk] Detail-page chart points can visibly disappear after a pattern/term edit, which may surprise a user with no explanation in the UI → Mitigation: intended behavior for this change (that's the bug being fixed), but a "N results hidden by current filters" affordance is a reasonable presentation-layer follow-up, not required for correctness here.
- [Risk] User-supplied regex patterns re-evaluated on every list-page load (vs. once at ingest) multiplies exposure to a pathological/slow pattern → Mitigation: pre-existing risk, not newly introduced by this change; out of scope here, flagged for a possible follow-up (e.g. a regex complexity/timeout guard).

## Migration Plan

No schema or data migration: no new columns, no backfill, `SearchResult`/`ItemSource`/`SearchableItem` are structurally untouched — this is a pure query-and-logic change. Rollout is a single code deploy behind the updated/new test suite; no feature flag needed given there's no data-migration risk to stage. Recommend a manual latency check of the restructured list-view query against production-like data volume before merging, given the query-shape change (correlated subquery → windowed query + Python resolution). Rollback is a plain code revert — nothing written differently that would need undoing.

## Open Questions

- What should the default window size `N` be, and does it need to be a Django setting from day one, or is a hardcoded constant fine until real data suggests otherwise?
- Should the list view (or detail page) surface any explicit signal when a source drops out of the price comparison entirely (all rows in its window excluded), or is silent omission acceptable for this pass?
- Are there other "Latest price" consumers beyond `SearchableListView` and the detail-page chart worth auditing before implementation (e.g. any API/JSON endpoint not yet identified)?
