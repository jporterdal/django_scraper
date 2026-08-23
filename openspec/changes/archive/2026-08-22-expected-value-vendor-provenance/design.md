## Context

This surfaced while designing `bulk-item-editing`'s vendor-scoped `expected_product_line`/`expected_category` suggestion controls (`/opsx:explore`, prior to that change's archival): adding a symmetric "remove" action alongside its existing additive-only suggestions required knowing which vendor's claim to remove when a value is shared by more than one vendor — and tracing that back revealed the underlying storage model can't actually answer that question, because it never recorded it.

`SearchableItem.expected_product_line`/`expected_category` (`item-category-relevance-filter`) store a flat `list[str]`, deduplicated by exact string equality at save time (that change's Decision 2). The single-item edit form (`SearchableItemForm`) renders one checkbox per `(source, value)` pair observed in `ObservedCategoryValue`, explicitly *not* deduplicated across vendors so an operator can see which vendor(s) use which wording (Decision 9) — but when two vendors' choices share the same raw string, Django renders two `<input type="checkbox">` elements with the same `name` *and* the same `value`, distinguishable only by `id` (and thus independently clickable in the browser). The server, however, only ever receives the submitted *value*, not which specific `id` produced it — so:

- Both checkboxes pre-check whenever the string is stored, regardless of which vendor the operator actually opted into.
- Unchecking just one of the two is a silent no-op: the other checkbox, sharing the identical value, keeps the string in the submitted set, and `_merge_and_dedupe` doesn't distinguish. The only way to actually remove the value is to uncheck *both* — which nothing in the UI hints the operator needs to do.

Separately, this produces a genuine surprise case (confirmed against the codebase, not hypothetical): an operator checks a `wt`-labeled suggestion for `"MTG"`; later, a second vendor is configured and happens to have independently observed the identical string `"MTG"`; the item's edit form now pre-checks *that* vendor's checkbox too, though the operator never asked for it. Deduplicating by string at storage time is what causes this — the two vendors' claims were never distinct facts to begin with.

Matching (`JSONSearchParser.add_result`, `tracking/parsers.py:113-131`) is unaffected by any of this — it only ever consumes a flat iterable of strings, handed off once at `tracking/scrape.py:633-634`. This confines the fix entirely to the storage shape and the form/template layer.

## Goals / Non-Goals

**Goals:**
- Give each stored `expected_product_line`/`expected_category` entry real vendor provenance: which `Source.key` (if any) it was claimed through.
- Make suggestion-checkbox identity in the single-item form correspond 1:1 with a distinct stored fact, so checking/unchecking one vendor's suggestion has an independent, correct effect regardless of whether another vendor happens to share the same raw string.
- Distinguish manually-typed entries from vendor-claimed ones explicitly in the UI — an inline row signposted "Manual entry", not a separate textarea block — consistently in both the single-item form now and `bulk-item-editing`'s eventual rework later.
- Make each entry's vendor tag functionally significant, not just cosmetic: a vendor-tagged entry's value is matched only against that vendor's own rows; a manually-entered (`source: null`) entry's value is matched against every vendor configured for the item, since it was never claimed for one vendor in particular.
- Migrate existing data with a best-effort vendor-attribution heuristic, falling back honestly to unattributed (`source: null`, i.e. universal) whenever attribution is ambiguous.

**Non-Goals:**
- Not introducing an item-level abstraction (e.g. an implicit "this item is an MTG card" concept) that automatically propagates a value across every configured vendor regardless of which suggestion(s) the operator actually checked. `item-category-relevance-filter`'s Decision 1 — matching stays item-level and vendor-agnostic — is explicitly overturned by this change (see Decision 2); an operator who wants a value to apply everywhere uses manual entry (`source: null`), not automatic per-vendor propagation. Building that propagation was considered and rejected as more machinery than the problem warrants — see Decision 2's alternatives.
- Not reworking `bulk-item-editing`'s vendor-scoped `expected_*` suggestion UI/apply logic — deferred, tracked as a known follow-up on that change's own `design.md`.
- Not adding a "convert this manual entry to a vendor-claimed one" UI action. A manual entry stays manual; if the operator wants vendor provenance for the same string, they separately check that vendor's suggestion checkbox, producing a second, independent entry.
- Not deduplicating a vendor-tagged entry against a manual entry that happens to share the same string — they legitimately coexist as two entries. Harmless for matching (the derived flat value-set collapses identical strings there regardless), and correct for storage (they represent two different claims).

## Decisions

### 1. Storage shape: `list[{"value": str, "source": str | None}]` on the existing `JSONField`, not a second field or a new model.

Considered and rejected:
- **A second, parallel `JSONField` mapping value → source.** Requires the two fields to stay in lockstep on every read/write path; a real risk of drift with no natural enforcement, for no benefit over one field.
- **A separate related model** (e.g. `ExpectedValueClaim`, FK to `SearchableItem` and `Source`). Over-structured for what's a small, item-scoped list — the same reasoning that already keeps `ItemSource.title_include_patterns`/`title_exclude_patterns` as plain `JSONField` lists rather than related rows applies here.
- **Packed-string encoding** (e.g. `"wt:MTG"` as the list element). Fragile if a value itself contains the delimiter, harder to query, and gives up real type structure for no gain over a dict.

`source: null` represents a manually-typed value with no vendor claim — not a sentinel string, an actual JSON `null`, consistent with how `metadata_provider_key` uses a blank string for "none" rather than inventing a sentinel where the type system already offers one.

### 2. Matching is pruned per vendor at the handoff: a vendor-tagged entry filters only that vendor's rows; `source: null` filters every configured vendor; there is no fallback for a vendor with zero applicable entries.

`tracking/scrape.py`'s handoff (`tracking/scrape.py:633-634`) no longer builds one flat value list per item. It builds one **per `(item, ItemSource)` pair** — once per parser instance — pruned to exactly the entries applicable to that instance's vendor:

```python
def _applicable_values(entries, source_key):
    return [e["value"] for e in entries if e["source"] == source_key or e["source"] is None]

parser.expected_product_line = _applicable_values(item.expected_product_line, source.key)
parser.expected_category = _applicable_values(item.expected_category, source.key)
```

`JSONSearchParser.add_result`'s own check is unchanged — still `any(_normalized_substring_match(value, product_line) for value in self.expected_product_line)` — but what it *receives* now varies by vendor. This is a genuine filtering-behavior change, not just a storage-layer change: two rows from two different vendors, for the same item, can now be filtered against two entirely different sets of expected values. `JSONSearchParser` itself needs no changes; the pruning happens entirely at the handoff, one level up.

**Explicitly no fallback when a vendor has zero applicable entries.** If an item has expected-value entries tagged for `wt` only, and is also configured against `f2f`, `f2f`'s pruned list is empty — and an empty list disables the check (existing base-spec behavior), so `f2f`'s rows pass through entirely unfiltered on that axis. A "fall back to the item's full unpruned list when a vendor has no entries of its own" safety net was considered and **rejected**: it would silently filter a vendor using values the operator never associated with it — backwards from the actual goal. The operating principle is: **only filter what the operator explicitly asked to be filtered, for the vendor they asked it for.** An operator who wants coverage across every vendor uses manual entry (`source: null`); an operator who checks one vendor's suggestion is understood to mean exactly that vendor, not "this vendor, and any vendor I might add to this item later."

This directly overturns `item-category-relevance-filter`'s Decision 1, which kept these fields item-level specifically to avoid "the same conceptual 'this is an MTG card' setting would need to be re-entered per source and could silently drift out of sync across them." That drift is now an accepted, intentional consequence: adding a new `ItemSource` to an item with existing vendor-scoped expected values does **not** automatically extend those values to the new vendor. Revisiting `expected_product_line`/`expected_category` when adding a vendor to an already-configured item is the operator's responsibility — this app does not abstract it away. An item-level "this item is an MTG card" concept that automatically propagates across every configured vendor was considered and rejected as disproportionate to the problem for a single-operator tool where the operator is expected to configure each item's per-vendor filtering deliberately.

**`source: null` (manual entry) is the one universal case by design, not a fallback.** It is not "applies to a vendor with no explicit opinion" — it is the operator explicitly choosing "this applies everywhere" by typing the value directly instead of checking a specific vendor's suggestion. The distinction is real and operator-controlled: check a vendor's suggestion for vendor-specific coverage; type a value manually for universal coverage.

Alternatives considered and rejected:
- **Fall back to the item's full unpruned entry list for a vendor with no entries of its own.** Rejected above — reintroduces filtering a vendor using values it was never asked to be filtered on.
- **Warn the operator when a new `ItemSource` is added to an item that already has vendor-scoped expected values, prompting a coverage review.** Deferred, not rejected outright — a reasonable future UX nicety (see Open Questions) — but the correctness contract here doesn't depend on it, and it's out of scope for this change.

### 3. Suggestion checkbox identity, pre-check, and stored-entry membership all key off the exact `(source, value)` pair.

Today's `_suggestion_choices` builds `(value, "value (source_key)")` pairs — the *submitted* choice value is the bare string, so two vendors sharing a string collide. The fix: the submitted choice value becomes a `(source, value)` composite. This reuses the identical JSON-encoding scheme `bulk-item-editing` already introduced for its own vendor-scoped suggestion checkboxes (`tracking/forms.py::_bulk_edit_suggestion_choice_value`, `json.dumps([source_key, value])`) rather than inventing a second encoding — the same problem (a checkbox needs to carry more than a bare string) already has a precedent solution one change over.

Pre-check becomes "is this exact `(source, value)` pair present in the item's stored entries," not "is this string present anywhere in the list." Each checkbox now submits a distinct value, so both the browser (independently clickable, as today) and the server (independently interpretable, unlike today) treat two same-valued vendor checkboxes as what they actually are: two different facts.

### 4. Manual entries render as inline rows (signposted "Manual entry"), not a separate textarea — in both the single-item and bulk-edit forms.

Considered and rejected: fix only the vendor-checkbox collision bug (Decision 3) and leave today's separate manual-entry textarea as-is. Rejected because it would leave two structurally-identical kinds of stored entity — a vendor-tagged `{value, source}` pair and a manual `{value, source: null}` pair — presented through two inconsistent UI idioms: vendor claims as checkboxes, manual entries as raw textarea lines, with no visual cue that they're now the same underlying shape. Unifying to one row-per-entry list (vendor key or "Manual entry" as the row's label) makes every stored fact equally visible, checkable, and — for a vendor row — uncheckable, with no special case for manual entries beyond their fixed label.

This is explicitly meant to carry into `bulk-item-editing`'s eventual `expected_*` rework too (per direct product intent), so both forms present the same mental model of "one row per stored claim, vendor-labeled or 'Manual entry'."

### 5. Storage dedup moves from string-level to `(value, source)`-pair-level; two vendors independently claiming the identical string now legitimately produces two stored entries.

This reverses the storage half of `item-category-relevance-filter`'s Decision 2 ("storage dedup, not choice dedup") — that decision explicitly chose to collapse `[{wt: "MTG"}, {f2f: "MTG"}]`-shaped intent down to one stored string. Here, `[{"value": "MTG", "source": "wt"}, {"value": "MTG", "source": "f2f"}]` becomes a valid, meaningful stored state: two independent operator decisions, not a duplicate to collapse. Choice-level non-dedup (each vendor still gets its own checkbox) is unchanged and still correct — only what's *stored* changes.

Under Decision 2's per-vendor pruning, this now has a real matching consequence, not just a storage one: `[{"value": "MTG", "source": "wt"}, {"value": "MTG", "source": "f2f"}]` means `wt`'s rows and `f2f`'s rows are each independently filtered against `"MTG"` — collapsing them to one string-deduplicated entry (the old behavior) would have made it ambiguous which vendor(s) that single entry was even supposed to apply to. Pair-level storage is what makes per-vendor pruning possible at all; the two decisions are complementary, not independent — Decision 2 is the reason Decision 5 has to exist in this form.

A manually-typed value is still deduplicated against other manual entries by exact string equality — this isn't a separate rule, it falls out of pair-level dedup once `source: null` is itself part of the pair (`(value, None)` is `(value, None)` regardless of how many times the operator types the same string).

### 6. Migration: best-effort single-vendor attribution per item/value, degrading to `source: null` when ambiguous.

For each existing `SearchableItem`, for each currently-stored string value: check the item's currently-configured `ItemSource`s' vendors; if the string exactly matches exactly one of those vendors' `ObservedCategoryValue(field_name=..., value=<string>)` rows, tag the migrated entry with that vendor's key. If it matches zero vendors or more than one, degrade to `source: null`.

This is necessarily a Python data migration (iterating `SearchableItem.objects.all()` and cross-referencing `ObservedCategoryValue`), not a SQL-only schema migration. Item and vendor counts are small in production (single-operator tool), so an `O(items × values × sources)` pass needs no batching or chunking.

Considered and rejected:
- **Blanket `source: null` for every existing entry, no attribution attempt.** Simpler, but discards recoverable information for what — per `item-category-relevance-filter`'s own Non-Goals evidence — is likely the common case (a value matching exactly one configured vendor's observed wording).
- **A one-time operator-facing "review your expected values" prompt post-migration.** Unnecessary process overhead for a small dataset a script can mostly resolve automatically; the unresolvable remainder degrades to exactly today's status quo (no vendor attribution at all), which is not a regression.

This heuristic's `source: null` fallback is what keeps the migration safe under Decision 2's per-vendor pruning, not just tidy: per `item-category-relevance-filter`'s own Non-Goals section, cross-vendor wording convergence (a value matching *multiple* configured vendors' observed strings simultaneously, e.g. `"Magic"` matching all three originally-wired vendors) was the common case for the vendors investigated — and that is exactly the case this heuristic degrades to `source: null` (universal), not to a single vendor. So the common case migrates to behavior nearly identical to today's; only a value that turns out to match *exactly one* configured vendor's wording narrows to that vendor alone post-migration. See Risks below for what that narrowing means now that it has real filtering consequences.

## Risks / Trade-offs

- **[Risk]** An item's `expected_product_line`/`expected_category` coverage can now differ per vendor, and there is no automatic mechanism keeping them in sync — a vendor with zero applicable entries has no filtering on that axis at all, even when the item has entries for other vendors, and even when a vendor is added to an already-configured item after the fact. This is intentional (Decision 2), not a bug, but it's a real behavior an operator must understand to configure correctly, and getting it wrong fails silently (unfiltered rows, not an error). → Mitigation: none built into matching itself — that's the point. The single-item form's inline, vendor-labeled rows (Decision 4) exist precisely so an operator reviewing an item can see at a glance which vendor(s) are actually covered on each field, and add a vendor-specific or manual entry themselves if a newly added vendor needs coverage. A proactive nudge when a new `ItemSource` is added (see Open Questions) would reduce this further but isn't required for correctness.
- **[Risk]** The migration's best-effort heuristic now has real filtering consequences, not merely cosmetic ones: an item whose value previously applied uniformly across every configured vendor (the old, vendor-agnostic behavior) will, post-migration, only be filtered per-vendor according to Decision 6's heuristic — a value that narrows to a single vendor stops filtering that item's *other* vendors' rows on that axis, immediately upon migration. → Mitigation: Decision 6's discussion above shows the common case (convergent cross-vendor wording) degrades to `source: null` — universal — preserving near-identical coverage to today. Only genuinely vendor-specific wording narrows to one vendor, which is the correct outcome under this change's model, not a defect. Still worth a one-time manual spot-check of migrated data on a sample of multi-vendor items before relying on it in production (added to Migration Plan below), since this is the one place a migration mistake would silently change live filtering behavior rather than just a UI label.
- **[Trade-off]** `list[dict]` is a heavier stored shape than `list[str]` — more bytes per row, less immediately readable in the Django admin or a raw DB shell. → Accepted: list sizes stay small (a handful of entries per item per field, per the archived change's own accepted "list growth" risk), and readability cost is minor against the correctness gained.
- **[Risk]** Several tests in `tracking/tests/test_item_category_relevance.py` assert the *old* string-dedup/shared-pre-check/vendor-agnostic-matching behavior as correct (e.g. `test_checking_suggestions_from_two_vendors_dedupes_to_one_stored_value`, `test_stored_value_from_multiple_vendors_prechecks_all_matching_checkboxes`) — these need to be rewritten to assert the *new* pair-level, per-vendor-scoped behavior, not merely extended alongside. → Mitigation: called out explicitly as its own task rather than left to be discovered mid-implementation; the new expected behavior for each is spelled out in the delta spec.
- **[Trade-off]** `bulk-item-editing`'s Sections 4–5 remain built against the *old* flat-string, vendor-agnostic storage model until a separate follow-up change reworks them, so there's a window where the single-item and bulk-edit forms present genuinely different `expected_*` storage *and matching* semantics for the same underlying fields. → Accepted per explicit sequencing decision (this change first, bulk rework second); already documented on `bulk-item-editing`'s own `design.md` (its branch) as a known follow-up, so it isn't mistaken for finished.

## Migration Plan

1. Add the `list[str]` → `list[{"value", "source"}]` data migration (Decision 6's heuristic) for both `expected_product_line` and `expected_category`.
2. Update `SearchableItemForm` (choice-building, pre-check, save/merge — `_suggestion_choices`, `_merge_and_dedupe`, `_split_stored_values`), `searchableitem_form.html` (inline rows + "Manual entry" signpost), the `tracking/scrape.py` handoff (Decision 2's per-vendor pruning), and the migration together in one atomic change — there is no safe intermediate state where matching has become vendor-scoped but the form still presents values as universal, or vice versa.
3. Before relying on production data, spot-check a sample of migrated multi-vendor items' `expected_product_line`/`expected_category` entries against what their pre-migration behavior actually was, given the migration now has real filtering consequences (see Risks).
4. Provide a `reverse_code` for the data migration (`list[dict]` → `list[str]`, dropping `source`) — lossless in that direction, so rollback stays possible rather than one-way. Note that reversing *after* per-vendor pruning has been live would also need the `scrape.py` handoff reverted in the same rollback, since the two are not independently safe to run against mismatched code.

## Open Questions

- Exact input mechanism for adding a brand-new manual value once the old free-text textarea is replaced by inline rows — a small "add a value" text input + button reads as closest to right, but whether it accepts one value at a time or a multi-line paste (like today's textarea) needs a concrete answer before implementation.
- Whether the Django admin should be updated to present the new `list[dict]` shape more usably (today's admin presumably shows the raw field as-is), or left alone as a raw-JSON-editable fallback — not blocking, worth deciding before or shortly after implementation.
- Whether a future revisit should add a proactive nudge (e.g. a warning banner) when an operator adds a new `ItemSource` to an item that already has vendor-scoped `expected_*` entries, prompting a coverage review — deferred per Decision 2; not required for correctness, but would reduce the silent-gap risk noted in Risks.
