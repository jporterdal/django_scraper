## 1. Persistent navigation (base.html)

- [x] 1.1 Replace the standalone "Bulk Add" nav link with an "Add" dropdown (Bootstrap `nav-item dropdown`) containing "Add New Item" (→ `add_term`) and "Bulk Add" (→ `bulk_add`)
- [x] 1.2 Replace the standalone "Tags", "Sources", and "Schedules" nav links with a "Manage" dropdown containing those three links (→ `view_tags`, `view_sources`, `view_schedules`)
- [x] 1.3 Leave "Items" (→ `view_terms`), "Scrape History" (→ `view_updates`), and "Admin" as direct top-level links, unchanged
- [x] 1.4 Verify the new dropdowns render and function inside the collapsed mobile navbar (`navbar-collapse`) — open/close on tap, no layout breakage
- [x] 1.5 Verify dropdown menus render correctly in both dark and light theme (toggle via the existing theme button) — no unstyled or invisible menu items

## 2. Site logo (base.html)

- [x] 2.1 Resize/optimize `website_logo.png` from its native 2048×2048 (~1.5MB) down to appropriate navbar display dimensions (roughly 24-32px tall, with reasonable @2x headroom) and reasonable file size, preserving transparency
- [x] 2.2 Move the processed logo to `tracking/static/tracking/images/logo.png` and remove `website_logo.png` from the repo root
- [x] 2.3 Add the logo `<img>` inside the existing `navbar-brand` anchor in `base.html`, before the "Pricing Tracker" text, referenced via `{% load static %}` / `{% static %}` with `alt="Pricing Tracker"`
- [x] 2.4 Verify the logo renders cleanly in both dark and light theme
- [x] 2.5 Verify the logo displays correctly at mobile/collapsed navbar width

## 3. view_terms card header (searchableitem_list.html)

- [x] 3.1 Remove the "Bulk Add", "Manage Tags", "Manage Sources", "Schedules", and "Scrape History" buttons from the "All Items" card header
- [x] 3.2 Keep "Add New Item" as the sole standalone create action in the card header
- [x] 3.3 Replace "Update Selected" and "Update All Active" with a single split-button/dropdown control; both options continue to submit the existing `<form action="{% url 'update' %}">` with their current `mode` values (`selected`, `all`) unchanged
- [x] 3.4 Confirm the "Update Selected" path still correctly relies on the checkbox-selection state (`item_ids`) exactly as before

## 4. Tag-filter row (searchableitem_list.html)

- [x] 4.1 Confirm the contextual "Manage tags" link next to the tag pills is unaffected by the card-header prune (it stays; only the card header's separate "Manage Tags" button is removed)
- [x] 4.2 Confirm "Update items with this tag" and its hidden `tag_id` input are unaffected

## 5. Test coverage

- [x] 5.1 Search existing tests (`tracking/tests/test_theme.py`, `test_auth.py`, and any other test asserting on rendered `view_terms` or base-template nav output) for assertions on now-removed button/link text or structure; update as needed
- [x] 5.2 Add/adjust tests covering the new `view-terms-actions` spec requirements: card header contains add-item and update controls but not the removed nav-duplicating links; both update-scope controls are present and submit the correct `mode` values; nav exposes an "Add" menu (add-item + bulk-add links), a "Manage" menu (tags + sources + schedules links), and a logo `<img>` with alt text inside the brand link; tag-filter row still contains the contextual "Manage tags" link when tags exist
- [x] 5.3 Run the full test suite and confirm no regressions in unrelated pages that extend `base.html`

## 6. Manual verification

- [x] 6.1 Load `view_terms` in a browser: confirm Add New Item, the Update control (both scopes), and (when tags exist) the tag-filter row with contextual "Manage tags" all work end-to-end
- [x] 6.2 Confirm the nav's "Add" and "Manage" menus reach the correct pages from `view_terms` and at least one other page (e.g. `view_tags`)
- [x] 6.3 Check keyboard navigation and `aria-expanded`/`aria-haspopup` behavior on the new dropdowns (Bootstrap's default dropdown markup handles this — verify it wasn't dropped when adapting existing nav markup)
- [x] 6.4 Re-check dark/light theme toggle and mobile collapsed-navbar behavior one more time against the final markup, including the logo
