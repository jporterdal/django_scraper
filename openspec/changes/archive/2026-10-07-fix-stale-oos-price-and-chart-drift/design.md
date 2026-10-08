## Context

See proposal.md (Why) for the two symptoms. All the relevant code is in `tracking/views.py`:

- `_resolve_latest_known_prices(items)` runs one windowed query over `instock=1` rows, partitioned by `(item, source, title)`. In Python it then walks each thread newest-first to the first row that still matches, takes the minimum per source, then the minimum across sources.
- `_build_source_chart_series(item, results, fetch_jobs)` (detail page) groups the *stored* matching in-stock rows by `(source, update)` and keeps the cheapest. It then calls `_source_price_points`, which adds "unchanged" carry-forward points for successful fetches that stored nothing (`result_count > 0`, `stored_count == 0`), reusing the last stored minimum.
- The `SearchableListView.get_context_data` sparkline rebuilds the same per-update stored-minimum dict for the winning `(item, source)` pair. It does **not** apply `result_matches_item_source`, and feeds the result into `_source_price_points`.

Scrape-time dedup (`scrape.py::_dedupe_unit_candidates`) stores a row only when a title's `(price, instock)` differs from that title's latest stored row. The DB therefore holds a per-title change log, and the correct reading at any moment is "the latest state of every thread", not "the rows stored this update". An out-of-stock transition is a state change, so it is stored as an `instock=0` row. The read side never looks at those rows.

## Goals / Non-Goals

**Goals:**
- One function decides a source's thread state and per-update lowest price. Latest price, the detail chart, and the list sparkline all call it, so they cannot disagree.
- Keep the existing guarantees from `item-list-latest-price` and `retroactive-result-matching`: per-thread resolution, deterministic tie-breaks, relevance re-validation at read time, and stored history left untouched.
- Keep the list view at a bounded number of queries, independent of how many items are on the page.

**Non-Goals:**
- Changing scraping, parsing, or dedup.
- Treating a title that is *absent* from a successful fetch as gone (see Decisions → D5).
- Fixing dedup's behavior when one vendor response contains two rows with the same title (see Risks).

## Decisions

### D1: A single per-source replay of thread state

Add one helper (working name `_replay_source_series`) that takes one `(item_source, rows, fetch_jobs)` unit. `rows` holds all of that pair's `SearchResult`s, in stock and out of stock, with their update timestamps. The helper walks the source's updates in chronological order and keeps a `title → current row` map:

```
for each update U of this source, oldest → newest:
    for each row stored in U that currently matches item_source:
        candidate for state[row.title]; within U pick in-stock first, then cheapest, then id
    if U is evaluated (stored rows, or SUCCESS with result_count > 0):
        in_stock = [r for r in state.values() if r.instock and r.price is not None]
        emit point(U, min(in_stock) or None)
final_state = state  # → this source's Latest-price contribution
```

It returns the series of points and the source's final winner `(price, title)` or `None`.

- `_resolve_latest_known_prices` becomes: load rows and jobs for every item on the page, run the replay once per `ItemSource`, then take the cross-source minimum (tie-break: cheapest, then `source_id`, as now).
- `_build_source_chart_series` and the list sparkline format the replay's points. They no longer build their own stored-minimum dicts.
- `_source_price_points` is removed; the replay does its job.

*Why not just drop `instock=1` from the existing windowed query and add a "skip the thread if its first match is out of stock" check?* That fixes bug 1 only. Bug 2 comes from the chart computing a different quantity, so the chart needs per-thread state as of each update. Once a replay exists, having Latest price read the replay's final state is what guarantees the "Chart and sparkline agree with Latest price" requirement. A separate SQL path would let the two drift again.

*Why not a SQL window solution for the chart?* "Latest matching row per thread as of each update" depends on regex-based relevance matching, which already forced the resolution into Python (see the comment in `SearchableListView.get_queryset`). A replay over rows we already load is simpler and works the same on SQLite and Postgres.

### D2: A thread's state is its newest *currently-matching* row, of either stock status

While replaying, rows that fail `result_matches_item_source` are skipped, so they never become a thread's state. This matches the existing "walk newest-first to the first match" rule and its scenario "newest row excluded, older row still matches". Within one update and one title, an in-stock row beats an out-of-stock row and the cheaper in-stock row wins. That keeps the existing deterministic tie-break and covers vendors that list several same-title variants with different stock levels.

Alternative considered: let an out-of-stock row override the thread's state even when that row no longer matches. Rejected. Within a thread the title is fixed, so a mismatch can only come from category/product-line drift on that row, and the existing rule "non-matching rows are invisible" is simpler and already specified.

### D3: A gap is a `null` price entry

Chart.js leaves a break at `null` data points by default (`spanGaps` is off). The replay emits `price: None` for an evaluated update where nothing is in stock, and drops `None` entries that come before the first priced entry. On the detail chart, gap entries get `point_style: "gap"` (rendered with radius 0) and a tooltip like `"Aug 23 — out of stock"`. The sparkline already returns `''` from its tooltip when `price == null`; its `length > 1` guard counts entries, which is acceptable.

Alternative considered: draw an "out of stock" marker instead of a gap. Rejected; the user chose a gap.

### D4: Solid/hollow means "the lowest price changed"

An entry is solid when it is the first priced entry, the first after a gap, or its price differs from the previous priced entry. Otherwise it is hollow. Previously solid meant "this update stored a row", which, once the 08-21 spike is fixed, would label an unchanged $1.25 as "(price changed)". The new rule matches the tooltip text. The existing `test_dedup.py::test_chart_carry_forward_hollow_and_solid_points` (stored, then unchanged → `["solid", "hollow"]`) still holds under the new rule.

### D5: A title absent from a fetch keeps its last state (possible future change)

If a vendor stops returning a title altogether, rather than returning it flagged out of stock, no row is stored and the replay keeps that thread's last state, possibly an in-stock price. This is intentional for now. Every current vendor parser (wt `in_stock`, hfx `inventory_quantity`, f2f `inventoryQuantity`, the HTML parsers' availability tags) returns out-of-stock listings with an explicit flag, so this case is not expected.

**Possible future change, not planned:** at `terminalize` time, for a `SUCCESS` fetch with `result_count > 0`, compare the parsed titles with the unit's snapshot map and store a synthetic `instock=0` row for each previously in-stock title that is missing. That would make vanishing titles flow through the same replay path without any read-side change. It is deferred because a partial or paginated vendor response would then wrongly mark titles out of stock.

### D6: Query shape in the list view

Replace the windowed query with one query that loads every `SearchResult` for the visible items (both stock states, `select_related("update")`, ordered by `item_id, source_id, update__timestamp, id`), plus one query for their `ItemSource`s and one for their `FetchJob`s (`select_related("webupdate")`). Today the list view already loads every in-stock row for those items, plus FetchJobs for the winning pairs. The new shape adds the out-of-stock rows and the FetchJobs for non-winning pairs. The replay needs those FetchJobs only to know which updates are evaluated, and it ignores them when computing the final state, so the job query can stay limited to the winning pairs if profiling shows it matters.

## Risks / Trade-offs

- [Behavior change visible to users: items that are out of stock everywhere now show no Latest price instead of a stale one] → This is the requested fix. The template already renders the `None` case (see the "No source has ever stored an in-stock price" scenario).
- [Chart shapes change: the 08-21-style spikes disappear and solid/hollow labels move] → Covered by new spec scenarios and regression tests. Existing chart tests are checked in tasks; any that encoded the old "minimum of rows stored this update" behavior get updated, with a note in that test explaining why.
- [Same-title duplicates within one vendor response: dedup's snapshot keeps one `(price, instock)` per title, so two same-title variants can make dedup store rows on every scrape] → This already happens today. D2's same-update tie-break gives a correct read even when it does. The dedup side is out of scope.
- [List view now reads more rows] → Still a bounded number of queries. Items are paginated, and history per item is small (one row per change).
- [The replay depends on `update.timestamp` ordering; two updates with equal timestamps] → Order by `(update__timestamp, update_id)` so ties are deterministic.

## Migration Plan

Read-time change only: no migrations and no data backfill. Deploy as usual. Rolling back the code restores the previous display, and no stored data is affected.
