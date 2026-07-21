## Agent rules (read before editing)

0. **Only edit your own section of the plan file** Agents assigned to implement instructions within a given section should only edit the main plan file (likely "plan.md") make changes within that given section and only to either add comments, add short notes, or to tick off the "Definition of done" checkboxes, as in Agent Rule 9 below.

1. **Environment — use exactly this in every step:**
   ```bash
   cd "$(git rev-parse --show-toplevel)"
   source venv/bin/activate
   python manage.py test tracking --settings=django_scraper.settings_test
   ```
   The full suite must pass (`OK`) before you consider your step done.

2. **No external services or network in tests.** Tests must pass with no Redis, no Postgres, and
   no live HTTP. Mock fetchers; use fixtures under `tracking/fixtures/`.

3. **New dependencies:** add to `requirements.txt` with a pinned version AND install into the
   venv. Do not invent versions.

4. **Migrations:** run `python manage.py makemigrations tracking` when models change; chain off
   the current migration head.

5. **App is deployed** Make note of any data backfill migrations performed, such as when a scheme change makes old rows meaningless.

6. **Result dict contract:** parser results are
   `{"title": str, "price": float, "category": str, "instock": 0|1}`. Out-of-stock storage uses
   `price=None` in `scrape.py`, not in the parser contract.

7. **Vendor names in committed code:** No vendor
   hosts/names in application code; platform parser names only (`ShopifyParser`, `storepass`,
   etc.).

8. **Tests:** put new tests in `tracking/tests/test_<topic>.py` (Django discovers `test*.py`
   under the `tracking.tests` package). Add or adjust assertions in the relevant topic module;
   do not invent a new top-level `tracking/tests.py` monolith.

9. **When you finish a step:** tick its Definition of done checkboxes in the main plan.md only.

10. **Scheduling constraints are mandatory.** Before starting your step, check for a **Dependency
    overview** section in the plan file (Recommended order, coordinate notes, and explicit "do not run in parallel"
    warnings). If the **Dependency overview** section exists, treat the given ordering as hard prerequisites for assignment — a **Parallel-safe**
    identification alone is not sufficient to decide whether two steps may run concurrently.

11. **Do not stage or commit changes.** All edits need to be reviewed locally before staging or committing. Agents **must not** stage or commit any changes using git. If this interferes with implementing instructions, stop further work on the blocked instructions, write a clear note in your section, and highlight the question in the completion summary.