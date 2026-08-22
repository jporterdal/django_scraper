## Context

`view_terms` (`SearchableListView`) already renders a checkbox per active row and a `mode=selected` POST path (`UpdateFromWebView`, `tracking/views.py:894-972`) that turns checked `item_ids` into a scoped price-update dispatch. That plumbing was named in the archived `item-metadata-enrichment` change and in `README.md`'s "Deferred / future directions" section as the reusable groundwork for general bulk-editing, but it was built for a single-purpose, fire-and-forget action (dispatch a background price scrape) — it doesn't need to carry state across multiple requests or express "leave this field unchanged."

Two existing single-item mechanisms are being generalized here without being modified themselves:
- `request_metadata_refresh(item)` (`item-metadata-enrichment`) is the sole entrypoint allowed to enqueue a metadata refresh and to perform the provider-change reset (clear `payload`/`external_id`/`pinned_external_id`, reset `status` to `unfetched`). Bulk-editing `metadata_provider_key` must call this per affected item rather than duplicate its logic.
- `observed_values_for_item(item, field_name)` and the vendor-labeled suggestion-checkbox pattern (`item-category-relevance`) scope `ObservedCategoryValue` lookups to one item's configured `Source`s. Bulk-editing needs the same underlying data source but scoped to the union of vendors across a whole selection, with per-vendor subset counts — a new query shape, not a reuse of the existing function signature.

This design was worked out interactively in `/opsx:explore`; the decisions below restate that conversation's conclusions with rationale.

## Goals / Non-Goals

**Goals:**
- Let an operator change one or more fields (`priority`, `active`, `tags`, `metadata_provider_key`, `expected_product_line`, `expected_category`) across an arbitrary selection of existing items in as few selection actions as possible.
- Preserve the selection across multiple sequential partial edits — re-selecting items is the most tedious part of the flow and must not be discarded after a single Apply.
- Reuse `request_metadata_refresh` and `ObservedCategoryValue` as the authoritative mechanisms for their respective concerns, rather than reimplementing them at bulk scope.
- Apply best-effort, per-item: one item's failure must not prevent the rest of the selection from being attempted in the same round.

**Non-Goals:**
- Bulk-editing `text` — no sensible shared value exists across an arbitrary selection.
- Manual free-text entry for `expected_product_line`/`expected_category` in bulk mode — suggestions only for v1; manual entry would need a per-value vendor picker, deferred.
- An audit/undo log of bulk changes — no existing precedent in this app (`WebUpdate`/`FetchJob` are fetch-job history, not field-edit history).
- A periodic or scheduled bulk operation — this is an operator-triggered, synchronous-apply feature only.
- Handling concurrent mutation of a selected item by another user or process mid-session (see Risks below) — multi-user interaction in general is a future concern for this app, not solved here.

## Decisions

### 1. The workspace is a persistent, multi-round session, not a one-shot form

A single POST-then-redirect form (mirroring `BulkAddItemsForm`) was considered and rejected: it would force the operator back to `view_terms` and through re-selection for every additional field they want to change, which is exactly the friction this feature exists to remove. Instead, `BulkEditItemsView` renders a workspace that survives repeated Apply rounds against the same working selection, only returning to `view_terms` on an explicit "Done" action.

### 2. Working selection carried as hidden POST fields, not a URL or server-side token

Three options were considered for carrying the working `item_ids` set across the initial POST from `view_terms` and every subsequent Apply:
- **GET query string + redirect** (standard Post/Redirect/Get): refresh-safe and bookmarkable, but `view_terms`' "select all" has no cap and `BULK_ADD_MAX_TERMS = 200` already signals the scale this app expects for batches — a few hundred integer ids risks an unwieldy URL with no guaranteed safe limit.
- **Server-side token** (session or a short-lived DB row keyed by an opaque id in the URL): avoids both the URL-length and refresh-resubmit problems, but introduces new server-side state with no existing precedent in this app (no session-backed workflow exists today) — expiry policy and concurrent-tab handling would be new infrastructure for a feature that doesn't otherwise need it.
- **Hidden POST fields, re-rendering the same view on each Apply** (chosen): no URL-length risk at any selection size, no new server-side state. Trade-off: the workspace page is not a plain idempotent GET, so a manual browser refresh mid-session prompts to resubmit the last Apply.

The trade-off is accepted because nearly every field operation here is naturally idempotent — `priority`/`active` overwrite to the same value harmlessly, tag add/remove and the `expected_*` suggestion apply are dedup-safe by construction. The one non-idempotent case, `metadata_provider_key`, would at worst produce a redundant `request_metadata_refresh` enqueue on an accidental resubmit — wasteful, not harmful, since the existing bounded-rate drain queue already absorbs large batches of enqueues from bulk creation today.

### 3. Every field defaults to "leave unchanged" — a real sentinel, not a blank/false value

Neither existing form in this codebase needs this: `SearchableItemForm` and `BulkAddItemsForm` both bind every field to a concrete value on every save. A bulk-edit round, by contrast, is usually a partial edit — the operator wants to change one or two fields and leave the rest of the selection's existing values alone. Each field therefore needs a tri-state-or-wider choice (e.g. `active`: leave / activate / deactivate; `metadata_provider_key`: leave / set-to-X / clear) rather than reusing the single-item field's natural blank/false default, which would otherwise be indistinguishable from "clear this."

This sentinel is what makes the multi-round workspace (Decision 1) safe to use repeatedly: an Apply round only ever touches the fields explicitly set away from "leave unchanged," so an earlier round's changes are never at risk of being silently reverted by a later, unrelated round.

### 4. `tags` and `expected_product_line`/`expected_category` are additive/subtractive, never a full replace

A single shared field value cannot safely overwrite every selected item's whole `tags` or `expected_*` list — each item likely carries pre-existing values (other tags, manually-curated `expected_*` entries) unrelated to the current bulk intent, and a full replace would silently destroy them. `tags` therefore exposes two independent sets (add / remove); `expected_product_line`/`expected_category` are add-only, merged and deduplicated by exact string equality into each affected item's existing list — the same dedup rule `item-category-relevance`'s single-item form already uses.

### 5. `expected_product_line`/`expected_category` suggestions are vendor-scoped across the selection, and apply only to the matching subset

`item-category-relevance`'s `observed_values_for_item(item, field_name)` scopes to one item's configured `Source`s. Bulk editing needs the union of vendors across every selected item instead: for each `Source` present via `ItemSource` on at least one selected item (no minimum-shared threshold — even a vendor used by just one of N selected items gets its own labeled group, e.g. "1 of 20"), a suggestion checkbox group is built from `ObservedCategoryValue` scoped to that vendor. On Apply, each checked `(vendor, value)` pair is added only to the items in the working selection that have that vendor's `ItemSource` configured — items without that vendor are unaffected by that specific checkbox, and this is stated plainly in the UI next to each group's label.

This requires the checkbox's submitted value to carry `(vendor, value)`, not just the raw string the single-item form uses — the single-item form can get away with a bare string because it only ever reconciles against one item's list; the bulk form must know which vendor each checked value came from in order to compute the correct subset at apply time.

**Alternative considered**: only show vendor groups shared by two or more selected items (a "genuinely bulk" threshold). Rejected — the per-group count already communicates scope honestly ("1 of 20"), and filtering out singleton vendors would silently hide a suggestion an operator might still want, for no real complexity savings.

### 6. `metadata_provider_key` routes through the existing entrypoint, per item

Bulk-setting or bulk-clearing `metadata_provider_key` calls `request_metadata_refresh` once per affected item, exactly as `BulkAddItemsView` and `SearchableUpdateView` already do. The provider-change reset (clearing stale `payload`/`external_id`/`pinned_external_id`, resetting `status` to `unfetched`) lives entirely inside that entrypoint's call graph already, so bulk-edit gains it for free rather than needing its own copy. This also means a bulk-set across many items enqueues many `MetadataFetchRequest` rows at once, which the existing bounded-per-wake periodic drain already handles identically to a large bulk-add batch — no new queueing concern.

### 7. Per-item best-effort apply; errors reported per item, not aggregated

Each Apply round attempts every item in the current working selection independently for whatever fields changed that round. An item-level failure (e.g. a save/validation error) is recorded against that item and does not prevent the remaining items from being attempted. The round's summary message reports success/failure per item rather than a single pass/fail for the whole batch — consistent with the workspace's overall philosophy of never discarding progress on the rest of the selection because of one item.

### 8. Per-row "remove from selection" instead of forcing a return to `view_terms`

Since the roster panel already lists every selected item (text, active badge, tags, priority) as part of showing what a round will affect, a lightweight per-row removal action costs little to add and avoids sending the operator back through `view_terms`'s full checkbox re-selection over a single mis-included item. It operates purely on the working `item_ids` carried in the form — no new server-side state.

## Risks / Trade-offs

- **[Risk]** An item in the working selection could be deleted, deactivated, or otherwise mutated by another user or background process between the initial selection and a later Apply round in the same session. This change does not detect or handle that case — multi-user interaction is a broader, unaddressed concern for this app (single-operator tool today), not something to solve here. → **Mitigation**: none implemented; documented explicitly in `tasks.md` and `README.md`'s "Deferred / future directions" as a known limitation to revisit if/when this app gains real multi-user usage.
- **[Trade-off]** Carrying the selection as hidden POST fields (Decision 2) means the workspace page is not refresh-safe in the ordinary browser sense. → Mitigation: accepted because the underlying field operations are idempotent by construction; the one non-idempotent case (metadata refresh enqueue) is merely wasteful, not incorrect, on an accidental resubmit.
- **[Risk]** The vendor-scoped `expected_*` suggestion query (Decision 5) is a new query shape against `ObservedCategoryValue`, run across a potentially large item selection and its vendors — needs to stay a bounded number of queries (mirroring the existing discipline in `SearchableListView.get_queryset`'s eager-loading), not one query per selected item. → Mitigation: implementation must batch the vendor/count computation and the per-vendor suggestion lookup, not loop per item; covered as an explicit task.
- **[Trade-off]** No audit/undo log means a mistaken bulk Apply (e.g. the wrong tag added to 50 items) has no built-in recovery path beyond manually reversing it (e.g. bulk-remove the same tag). → Accepted as proportionate for v1, consistent with this app's existing lack of any edit-history mechanism outside fetch-job records.

## Migration Plan

1. No schema or migration changes — this change is purely new views/forms/templates plus a non-model helper function for vendor-scoped `ObservedCategoryValue` lookup.
2. Ship with the "Bulk Edit Selected" action additive alongside existing `view_terms` actions; no existing behavior (single-item edit, bulk add, price-update selection) is altered except the checkbox-visibility widening, which is backward compatible (widens what can be selected, doesn't change what `mode=selected` price-update does with that selection).
3. No rollback complexity beyond removing the new view/URL/template — no data is migrated or backfilled.

## Open Questions

- Whether a future revisit of the multi-user/concurrency non-goal (Risk 1) should reuse Django's standard optimistic-locking patterns or something lighter — deferred until real multi-user usage surfaces the need.
- Whether manual free-text entry for `expected_product_line`/`expected_category` in bulk mode (deferred per Non-Goals) is worth building once this ships — revisit based on actual usage friction, same posture `item-metadata-enrichment` took toward its own deferred scope.
