## Why

`tracking.tests.test_webupdate_history.WebUpdateHistoryTests.test_list_page_pagination` fails intermittently (confirmed via direct reproduction: 6/250 isolated runs, same signature every time). The failing assertion, `assertNotContains(page2, str(newest.result_count))`, checks that the literal digits `"25"` never appear in page 2's rendered HTML. But the page (via `base.html`) embeds the per-request CSRF token twice (nav logout form + "Run Update Now" form); when that random ~64-character token happens to contain the substring `"25"`, the assertion spuriously fails with `AssertionError: 2 != 0`. This has nothing to do with `WebUpdate` row data — root-caused and empirically confirmed by inspecting the exact byte offsets of both matches in a captured failing response, both inside `csrfmiddlewaretoken` values. See `openspec/tmp_bug.txt` for the original bug report; its leading "leaked row from another test" hypothesis was investigated and ruled out as the cause of this specific failure (a real, separate leaked-row issue was found during investigation but does not affect this test — see Impact).

## What Changes

- Replace the fragile bare-digit containment check in `test_list_page_pagination` with an assertion tied to something row-specific and collision-proof: assert page 2 does not contain the `webupdate_fetch_jobs` URL for the newest `WebUpdate` (mirroring the existing pattern already used in `test_list_page_renders_summary_fields` to assert URL presence).
- No application/view code changes — `tracking/views.py::WebUpdateListView` behavior is correct; this is a test-assertion bug only.

## Capabilities

### New Capabilities

- `webupdate-history-pagination`: the scrape-history list view's pagination boundary contract (25 rows per page, newest-first, no cross-page duplication). Not previously documented anywhere in `openspec/specs/`; adding it here since this change's test fix exists specifically to verify this contract, and codifying it makes it clear the fix is about the *test*, not the *behavior* (which is unchanged and already correct).

### Modified Capabilities

None — `WebUpdateListView`'s pagination behavior is unchanged; only the test's assertion strategy changes.

## Impact

- `tracking/tests/test_webupdate_history.py` — the only file changed.
- Test suite reliability: removes a ~2% per-run spurious failure source from CI/local runs of this test.
- Process: implement on a dedicated git branch (not directly on `main`), per bugfix convention for this repo.
- Not in scope, flagged for future follow-up: investigation also found a real, separate leak — a `WebUpdate(pk=1, status='pending')` row with a real (non-synthetic) timestamp that appears to survive Django's `TestCase` per-test rollback in most full-suite runs (first observed before `test_export.ExportTests` runs). The exact origin test was not pinned down (best guess: `tracking/tests/test_background.py` or `tracking/tests/test_dedup.py`, both of which run alphabetically before `test_export`). Verified this leak does not affect `test_list_page_pagination` itself (row count was exactly 26, matching only what the test created, at the moment of the reproduced failure). Worth its own investigation/change later.
