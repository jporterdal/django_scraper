## Context

`SearchableListView.get_queryset()` (`tracking/views.py`) computes each item's "Latest price" via a two-level subquery: `source_latest` picks each linked source's own most recent in-stock `SearchResult` (`ORDER BY -update__timestamp`, `[:1]`), then `cheapest_item_source` takes the minimum price across those per-source picks. This structure — fixed in the prior `item-list-latest-price` change — is correct for comparing across sources.

What it doesn't handle: a single source's own scrape can legitimately store multiple `SearchResult` rows under one `WebUpdate`, because one `SearchableItem` intentionally matches multiple product variants (e.g. regular/foil/extended-art printings) from that source. When that happens, every one of those rows ties on `update__timestamp`, and `ORDER BY -update__timestamp` alone gives SQL no further tiebreak — the row `[:1]` returns is whichever the database happens to return first for that tie, which is not guaranteed to be (and in the reproduced bug, was not) the cheapest.

## Goals / Non-Goals

**Goals:**
- Make the per-source "latest price" pick deterministic and always resolve to the cheapest tied row when a source's own latest `WebUpdate` stored more than one in-stock result for the item.

**Non-Goals:**
- Changing how broadly a source's results are matched to an item (title include/exclude patterns) — matching multiple variants per item per source is intended behavior.
- Changing the cross-source minimum-price comparison structure — that part is already correct.
- Deduplicating or merging same-timestamp rows in storage — they remain distinct `SearchResult` rows; only which one is treated as "the source's latest price" changes.

## Decisions

- **Add secondary and tertiary sort keys to `source_latest`**: `.order_by("-update__timestamp", "price", "title")`. Among rows tied on timestamp for one source, ascending price puts the cheapest first; among rows additionally tied on price (e.g. two variants coincidentally priced the same), ascending alphabetical title makes the pick deterministic rather than falling back to arbitrary database row order.
  - Alternative considered: collapse same-timestamp rows via `Min("price")` aggregation instead of ordering. Rejected — the subquery also needs to return the matching `title` (`_latest_title`) for that same winning row, which an aggregate can't provide without a second correlated lookup; ordering keeps title and price sourced from one consistent row, matching the existing "no independently resolved values" requirement in the `item-list-latest-price` spec.
  - Alternative considered: `pk` (or `instock`) as the tertiary tiebreak instead of `title`. Rejected — `instock=1` is already filtered before ordering, so it can't discriminate further; `pk` would be deterministic but arbitrary (insertion order, not a meaningful ordering), whereas `title` gives a human-legible, reproducible tiebreak and keeps "which variant wins" answerable by inspection.

## Risks / Trade-offs

- [Two variants tie on both timestamp and price] → Resolved deterministically by the new tertiary `title` sort (ascending alphabetical) rather than falling back to arbitrary database row order.
- [Two variants tie on timestamp, price, AND title] → Falls back to the database's arbitrary row order for that fully-tied residual case — displayed price is correct either way (all tied rows share the same price), only source/title attribution could vary between reads. Not expected in practice (would require two distinct product listings with identical scraped titles); acceptable residual risk, out of scope.
- [Adding `price`/`title` to `order_by` could interact with a database index tuned only for `update__timestamp`] → No such index exists in this codebase (checked `WebUpdate.Meta.indexes`, only a plain `timestamp` index); no measurable regression expected at current data volumes.

## Migration Plan

Pure code change, no data migration. Deploy as a normal code release; no rollback considerations beyond reverting the commit.
