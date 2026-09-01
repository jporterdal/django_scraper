## Why

The `view_terms` page ("Historical Pricing Data" / "All Items") exposes the same destinations through two competing sets of controls: the persistent nav in `base.html` and an 8-button row in the page's own card header. Of those 8 buttons, only 3 (Add New Item, Update Selected, Update All Active) are actually specific to this page — the other 5 (Bulk Add, Manage Tags, Manage Sources, Schedules, Scrape History) just duplicate a nav link one click away. Tags alone is reachable three different ways from this single page (nav "Tags", card-header "Manage Tags", and the tag-filter row's "Manage tags" link). This is visual noise with no corresponding pattern on the app's other list pages (tags, sources, schedules), which each carry only their own single "Add X" button and no nav duplication — confirming `view_terms` is the outlier, not the norm.

## What Changes

- Reorganize the persistent nav (`base.html`) into grouped dropdowns using Bootstrap's existing dropdown component (already loaded via `bootstrap.bundle.min.js`, no new dependency):
  - `Add ▾` groups Add New Item and Bulk Add
  - `Manage ▾` groups Tags, Sources, and Schedules
  - Scrape History and Admin remain top-level links
- Prune the `view_terms` card-header row down to only page-specific actions:
  - Keep "Add New Item" as the page's primary call-to-action (intentionally still present alongside the new nav `Add ▾`, mirroring the common pattern of a page-level create action coexisting with a global create menu)
  - Remove "Bulk Add", "Manage Tags", "Manage Sources", "Schedules", and "Scrape History" from the card header — each is now reachable via the reorganized nav
  - Consolidate "Update Selected" and "Update All Active" into a single split-button/dropdown control (one action — run an update — at two different scopes)
- In the tag-filter row, drop the redundant "Manage tags" duplication from the card header (now covered by nav `Manage ▾`) but keep the contextual "Manage tags" link inline with the tag pills, since it sits directly next to what it manages
- Keep "Update items with this tag" as-is (contextual, scope-specific, no nav equivalent)
- Add the site logo to the persistent nav's brand link, alongside the existing "Pricing Tracker" text, served as a proper Django static asset instead of living as a loose file at the repo root

**BREAKING**: None — this is a UI reorganization. All existing URLs, view names, and form actions are unchanged; only the markup/placement of links and buttons changes.

## Capabilities

### New Capabilities
- `view-terms-actions`: the contract for which item-management actions are exposed directly on the `view_terms` page versus reachable only through the persistent navigation, how the navigation itself groups related destinations, and the navigation's display of the site logo. No capability like this exists yet — the current duplication is undocumented incidental behavior, not a specified contract.

### Modified Capabilities
None. No existing spec's requirements change — all destinations, form submissions, and view logic behind them are unchanged. Only the entry points (nav structure, card-header contents) are newly specified.

## Impact

- **Templates**: `tracking/templates/tracking/base.html` (nav restructure, logo added to brand link), `tracking/templates/tracking/searchableitem_list.html` (card-header prune, Update button consolidation, tag-filter row cleanup)
- **Static assets**: new logo image under `tracking/static/tracking/images/`, referenced via Django's `{% static %}` tag. The source file at the repo root (`website_logo.png`) is a 2048×2048 / ~1.5MB RGBA PNG and needs resizing/optimizing to navbar dimensions before landing in `tracking/static/`; see `design.md` and `tasks.md`.
- **Tests**: Any test asserting on current button/link text or nav structure in rendered `view_terms` (and other pages that extend `base.html`) output may need updated assertions — to be confirmed while implementing tasks.md
- **No changes** to: URLs (`tracking/urls.py`), views (`tracking/views.py`), models, or any other list page's own card-header markup (`tag_list.html`, `source_list.html`, `updateschedule_list.html`, `webupdate_list.html`) beyond what naturally follows from those pages now also being reachable via the nav's `Manage ▾` dropdown
- **Explicitly out of scope**: per-row item actions (View History / Sources / Edit on each table row), any visual/theme rework beyond adding the dropdown components, and the unrelated `bulk-item-editing` change (confirmed unrelated, no coordination needed)
