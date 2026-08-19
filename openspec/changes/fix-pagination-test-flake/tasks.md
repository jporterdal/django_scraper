## 1. Branch check

- [ ] 1.1 Confirm the current git branch is `fix/pagination-test-flake` (created and committed by the user beforehand) before making any changes; do not create a new branch and do not work on `main`

## 2. Fix the flaky assertion

- [ ] 2.1 In `tracking/tests/test_webupdate_history.py::test_list_page_pagination`, replace `self.assertNotContains(page2, str(newest.result_count))` with an assertion against `reverse("webupdate_fetch_jobs", args=[newest.pk])`, matching the pattern already used in `test_list_page_renders_summary_fields`
- [ ] 2.2 Re-read the surrounding test body to confirm the page 1 positive assertions (`assertContains(page1, str(newest.result_count))`, `"Page 1 of 2"`, `"Next"`) and page 2 positive assertions still make sense unchanged

## 3. Verify the fix

- [ ] 3.1 Run `test_list_page_pagination` in isolation at least 250 times in a loop and confirm zero failures (baseline before the fix: ~6/250 failures with the old assertion)
- [ ] 3.2 Run the full suite (`python manage.py test tracking --settings=django_scraper.settings_test`) at least 20 times in a loop and confirm this test no longer fails
- [ ] 3.3 Run the full `tracking` test suite once normally and confirm no other test regressed

## 4. Wrap up

- [ ] 4.1 Review the diff to confirm only `tracking/tests/test_webupdate_history.py` changed
- [ ] 4.2 Stage the change (`git add`) on `fix/pagination-test-flake`, but do **not** commit or push — leave the staged diff for the user to review first
- [ ] 4.3 Stop and hand back to the user for review. Do not create a commit, push the branch, or open a PR until the user has reviewed the staged diff and explicitly says to proceed
