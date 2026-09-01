## Context

`view_terms` renders via `tracking/templates/tracking/searchableitem_list.html`, extending `tracking/templates/tracking/base.html`. The nav in `base.html` is shared by every page in the app. No dropdown/menu component exists anywhere in the app today (confirmed by grep), but Bootstrap 5.3.3's full JS bundle (`bootstrap.bundle.min.js`, includes Popper) is already loaded in `base.html`, so the dropdown component is available with zero new dependencies. The app supports a dark/light theme via `data-bs-theme` on `<html>`, toggled by a button wired through `tracking/static/tracking/theme.js`; Bootstrap's dropdown styling is theme-aware through that same attribute, so no extra theming work is needed beyond using standard Bootstrap classes.

Current nav (`base.html` lines 35-57):
```
Pricing Tracker (brand→view_terms) | Items(→view_terms) | Tags | Sources | Schedules | Scrape History | Bulk Add | Admin     [theme] [logout]
```

Current `view_terms` card-header row (`searchableitem_list.html` lines 32-39):
```
Add New Item | Bulk Add | Manage Tags | Manage Sources | Update Selected | Update All Active | Schedules | Scrape History
```

## Goals / Non-Goals

**Goals:**
- Eliminate redundant links between the persistent nav and the `view_terms` card header
- Group related nav destinations (creation actions, management/config pages) into dropdowns so the nav reads as fewer, clearer choices
- Reduce the card-header row to only actions that are genuinely specific to `view_terms`
- Preserve every existing URL, view, and form submission unchanged — this is a presentation-layer change only

**Non-Goals:**
- Changing any view logic, URL, or form target
- Redesigning the other list pages' own card headers (`tag_list.html`, `source_list.html`, `updateschedule_list.html`, `webupdate_list.html`) — they already have no duplication problem
- Adding active-nav-item highlighting (nice-to-have, not currently present, not required to solve this problem)
- Per-row item actions (View History / Sources / Edit)
- Any broader visual/theme rework beyond the dropdown components introduced here

## Decisions

**Nav restructure — group by intent, not by keeping everything top-level.**
New nav shape:
```
[logo] Pricing Tracker (brand) | Items | Add ▾ | Manage ▾ | Scrape History | Admin      [theme] [logout]
                                           ├ Add New Item      ├ Tags
                                           └ Bulk Add           ├ Sources
                                                                 └ Schedules
```
- `Add ▾` groups the two ways to create items (single vs. bulk) — they're the same intent at different scale.
- `Manage ▾` groups the three configuration/reference list pages (Tags, Sources, Schedules) — none of these are frequent-glance destinations the way Items or Scrape History are, so collapsing them costs little.
- Scrape History stays top-level: it's a monitoring view users check often, not a config page.
- Admin stays top-level and unchanged (Django's own surface, not part of this app's information architecture).
- Alternative considered: also collapsing "Items" into the brand link (since both point to `view_terms`). Rejected for this change — the brand link is a common landing convention but isn't always recognized as a nav item on first glance; keeping an explicit "Items" text link costs one nav slot and avoids relying on brand-as-nav conventions. Revisit only if nav real estate becomes tight after this change.

**Card-header prune — keep only what's genuinely page-specific.**
New card-header row:
```
Add New Item | Update ▾
                ├ Update Selected
                └ Update All Active
```
- "Add New Item" stays on the page even though `Add ▾` now exists in nav. This mirrors a common pattern (e.g., a list page's own "New" button coexisting with a global create menu): the user is already looking at the list they're about to add to, so the primary CTA stays where the action happens. It is not treated as a duplicate to eliminate.
- "Bulk Add", "Manage Tags", "Manage Sources", "Schedules", "Scrape History" are removed from the card header — each is one click away via the reorganized nav, and none of them are contextual to the table below.
- "Update Selected" (needs table selection state) and "Update All Active" (needs no selection) are two scopes of one action. Consolidated into a single Bootstrap split-button/dropdown: a primary button labeled "Update" that defaults to "Update Selected" behavior, with a caret dropdown offering "Update All Active" as the alternate scope. Both remain `<button type="submit" name="mode" value="...">` inside the existing `<form action="{% url 'update' %}">` — no server-side change.
- Alternative considered: two plain buttons side by side, just visually smaller. Rejected — the split-button reads as "one action, two scopes" rather than "two unrelated actions," which better matches what they actually do.

**Tag-filter row — keep the contextual instance, drop the generic one.**
The tag-filter row's inline "Manage tags" link (next to the tag pills) is the best-placed of the three current occurrences of this destination: it sits directly beside the thing it manages. It stays. The card header's separate "Manage Tags" button is removed as part of the general prune above — it's now reachable via nav `Manage ▾` → Tags. "Update items with this tag" is unaffected; it's a scope-specific action with no nav equivalent.

**Site logo — icon-and-wordmark lockup in the existing brand link, as a proper static asset.**
The navbar brand currently renders as plain text ("Pricing Tracker") linking to `view_terms`. This change adds the site logo as an `<img>` inside that same `navbar-brand` anchor, positioned before the text (icon + wordmark), sized to navbar height (roughly 24-32px) via CSS rather than shipping a pre-scaled single size. The image is served as a normal Django static asset at `tracking/static/tracking/images/logo.png` and referenced with `{% static %}`, never a hardcoded path. The `<img>` carries `alt="Pricing Tracker"` so the link stays meaningful if the image fails to load.

The source, `website_logo.png` at the repo root, is an RGBA PNG with real alpha transparency — the same asset works unmodified in both the dark and light theme, no theme-specific variants needed. It is 2048×2048 / ~1.5MB, oversized for a navbar icon, so it needs resizing/optimizing before it lands in `tracking/static/tracking/images/` (see `tasks.md`).

- Alternative considered: crop/composite the icon onto a fixed solid-color rectangle matching the navbar background, sidestepping the need for transparency entirely. Rejected — the source already has real transparency, so this would only add a step and would tie the logo to a single theme's background color.

**Specs delta scoped to entry-point contracts, not markup.**
The new `view-terms-actions` capability specifies *which actions are reachable from where* (e.g. "exactly one create action lives in the card header," "Tags/Sources/Schedules are reachable via a single grouped nav destination") rather than exact CSS classes or DOM structure. This keeps the spec testable via response-content assertions (existing Django test client patterns already used elsewhere in this app) while leaving implementation details — Bootstrap dropdown markup, button styling — free to evolve without triggering a spec change.

## Risks / Trade-offs

- **[Risk] Hiding Tags/Sources/Schedules behind a dropdown could make them feel less discoverable to new users.** → Mitigation: these remain one extra click away (standard, well-understood dropdown pattern), and `view_terms`'s tag-filter row still surfaces "Manage tags" contextually for the most commonly touched of the three.
- **[Risk] Tests may assert on the current button/link text or DOM structure in rendered `view_terms` (or other pages extending `base.html`) output.** → Mitigation: covered in tasks.md as a verification step; a grep pass during exploration found no test currently pinning exact nav/card-header button text, but this will be re-checked against the final markup before considering the change complete.
- **[Risk] Collapsed-navbar (mobile) behavior with nested dropdowns inside Bootstrap's `navbar-collapse` can be fiddly.** → Mitigation: use Bootstrap's standard `nav-item dropdown` markup as documented (no custom JS), and manually verify on a narrow viewport as part of tasks.md.
- **[Trade-off] Keeping "Add New Item" on the page while also introducing a nav `Add ▾` means one intentional duplication remains.** → Accepted: it's the page's primary CTA, not incidental duplication like the other five removed items.
- **[Risk] The source (`website_logo.png`, 2048×2048 / ~1.5MB) is full-resolution and unoptimized.** → Mitigation: tasks.md includes an explicit resize/optimize step before the asset lands in `tracking/static/tracking/images/` — the full-size file should never be served directly.

## Migration Plan

Not applicable in the deployment sense — this is a template-only change with no data migration. Rollout is a normal code change: edit `base.html` and `searchableitem_list.html`, verify manually and via existing test suite, merge. No feature flag or staged rollout needed given the small, reversible blast radius (two template files).

## Open Questions

- None outstanding — direction, nav shape, and card-header disposition were confirmed during exploration prior to this proposal.
