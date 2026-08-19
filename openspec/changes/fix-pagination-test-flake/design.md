## Context

`test_list_page_pagination` (`tracking/tests/test_webupdate_history.py`) intermittently fails with `AssertionError: 2 != 0 : '25' unexpectedly found in the following response`. Investigation (see `proposal.md` and `openspec/tmp_bug.txt`) traced this to `assertNotContains(page2, str(newest.result_count))` matching against the page's CSRF token rather than any `WebUpdate` data — the token is embedded twice per page (`base.html`'s nav logout form, plus the "Run Update Now" form in `webupdate_list.html`), and is random per request, so it occasionally contains the substring `"25"` (the fixed value this test always checks for, since the test always creates exactly 26 rows). This is a single-file, test-only change with no architectural surface — design is included here only because the schema requires it before `tasks`, not because the fix itself has meaningful design decisions.

## Goals / Non-Goals

**Goals:**
- Make `test_list_page_pagination` deterministic: pass on every run, fail only on an actual pagination regression.
- Keep the fix isolated to the one fragile assertion; do not touch `WebUpdateListView` or the templates.

**Non-Goals:**
- Fixing the separately-discovered `WebUpdate` row leak (real, but unrelated to this failure — tracked as a future follow-up in the proposal's Impact section, not addressed here).
- Adding a secondary sort key (`-timestamp, -pk`) to `WebUpdateListView.get_queryset` — mentioned as a defensive idea in the original bug report, but out of scope: this failure was never actually caused by ordering ties (all 26 test timestamps are distinct by construction), so it wouldn't fix anything here and isn't otherwise motivated by this change.

## Decisions

- **Assert on the fetch-jobs URL instead of a bare digit string.** `test_list_page_renders_summary_fields` already asserts presence of `reverse("webupdate_fetch_jobs", args=[update.pk])` to identify a specific row; reusing that pattern for the negative assertion (`assertNotContains(page2, reverse("webupdate_fetch_jobs", args=[newest.pk]))`) ties the check to a value that is unique per row and cannot collide with unrelated page content (CSRF tokens, static template text, etc.). Alternative considered: scope the check to a rendered HTML fragment (e.g. a table cell) via `assertNotContains(..., html=True)` — rejected as more brittle to markup changes and no more collision-proof, since it would still need to embed the same short digit string.
- **Leave `newest`/`oldest` queries as whole-table lookups.** The original bug report flagged these as fragile in a polluted table, but since this failure is unrelated to table pollution, changing the query scope is not needed to fix it. Revisit only if the separate leaked-row issue is confirmed to affect this test in the future.
- **Develop on a dedicated branch.** Per repo convention for bugfixes, this change is implemented on `fix/pagination-test-flake`, a branch the user creates and commits the OpenSpec change artifacts to beforehand — not committed directly to `main`. The implementing agent uses that pre-existing branch rather than creating one.

## Risks / Trade-offs

- [The new assertion only checks the fetch-jobs URL, not the row's other rendered fields] → Acceptable: the URL already uniquely identifies the row (same guarantee `test_list_page_renders_summary_fields` relies on), and the test's positive assertions on page 1 continue to check `result_count` directly.
- [Root cause of the separate WebUpdate row leak remains unresolved] → Out of scope for this change; documented in `proposal.md` Impact section so it isn't lost.

## Migration Plan

No migration. Single test-file change:
1. Work on the dedicated bugfix branch `fix/pagination-test-flake` (created and committed by the user beforehand) — do not create a new branch and do not work on `main`.
2. Apply the assertion fix.
3. Verify via repeated local test runs (isolated + full suite) that the previous failure mode no longer reproduces.
4. Stage the diff and stop for user review — do not commit, push, or open a PR until the user has reviewed the staged change and says to proceed.
