# Feedback — Vendor-reference cleanup (living document)

## How to use this document

This is an **append-only, multi-pass** work log. Cleanup of vendor references spans both
**code** and **documentation** and may take several sessions/agents to finish.

- Work is organized into **Passes**. Each Pass is **self-contained and independently
  completable** — do one Pass fully (including its verification) before starting the next.
- Future instructions will be **appended as new Passes** below the existing ones. Do not
  renumber or delete completed Passes; add to the end.
- **Line numbers in this document are indicative only.** Files change between passes, so
  always **re-derive the current state** (open the file / `rg` for the target string)
  before editing, and match on the quoted text rather than the line number.
- Every Pass ends with a **Verification** block. A Pass is "done" only when its verification
  passes. The shared conventions and the exempt list in the next section apply to **all** passes.

**Environment — use exactly this for any command/check:**
```bash
source /home/ross/work/django_scraper/venv/bin/activate
cd /home/ross/work/django_scraper
```

---

## Shared scope & conventions (apply to every Pass)

### Goal
Remove every explicit reference to the tracked vendors **Face to Face Games (F2F)**,
**HFX Games (HFX)**, and **Wizard's Tower (WT)** — their vendor names, website/host names,
and concrete URLs — from the project, **except** in the exempt locations below. Preserve all
technical/design meaning; this is renaming/genericizing, not deleting information.

### Exempt — never treat these as violations
- Anything under `tracking/fixtures/` (fixture data; not committed to git).
- `tracking/tests.py` (may reference the vendors as sample data).
- The vendor investigation docs `tracking/docs/f2f_investigation.md`,
  `tracking/docs/hfx_investigation.md`, `tracking/docs/wt_investigation.md`
  (raw research; **out of scope** unless a future Pass says otherwise).
- **Canada Computers** (`cc`, `canadacomputers.com`, `CC_DEFAULT_SEARCH_URL`) — allowed, keep.
- **Real file-path references** that point at the exempt files above. Because those files still
  exist with `f2f`/`hfx`/`wt` in their names, a path like
  `tracking/fixtures/html/f2f/search_results_sample.json` or
  `tracking/docs/wt_investigation.md` may remain **as an accurate path**. Do not invent new
  vendor references, but keep existing path references pointing at real, exempt files.

### Keep — these are platform/strategy names, NOT the tracked vendors
- Parser classes `ShopifyParser`, `StorepassParser` (and the planned `WtFiltersParser` — but
  see Open Decisions), and the registry keys `shopify`, `storepass`, `wtfilters`.
- The generic platform/tech terms `Shopify`, `Storepass`, `prod-indexer`, `Elasticsearch`.
  These describe *platforms/technology* used by many stores, not the specific vendors.

### Replacement glossary (apply consistently wherever a change is made)
| Vendor token (remove) | Generic replacement |
|-----------------------|---------------------|
| `Face to Face Games`, `F2F` | `Shopify vendor` / "the Shopify example" (or drop the parenthetical) |
| `HFX Games`, `HFX` | `Storepass vendor` / "the Storepass example" |
| `Wizard's Tower`, `WT` | `POST-API vendor` / "the POST-JSON example" |
| `facetofacegames.com` + full path | `https://example.com/search?q={term}` (or a generic Shopify-style path) |
| `store.storepass.co` + query string | `https://storepass.example/...` with `store_id=<STORE_ID>` placeholder |
| `hfxgames.com` (Origin/Referer) | `https://example.com` |
| `wt-filters` (as a vendor app) in prose | `a POST JSON search app` |

---

## Pass 1 — Application code  ✅ (spec complete; execute if not yet applied)

Remove vendor references from the Python code and migrations. The parsers/orchestrator are
already vendor-agnostic (they operate on `Source` rows and fixtures), so this is
string/deletion cleanup plus removing two data-migration files; **no parsing/orchestration
logic changes.**

### Task 1.1 — `tracking/parsers.py`: sanitize parser docstrings
- In `class ShopifyParser(JSONSearchParser):`
  - **Replace:** `"""F2F prod-indexer (Elasticsearch-style) JSON search results."""`
  - **With:** `"""Shopify prod-indexer (Elasticsearch-style) JSON search results."""`
- In `class StorepassParser(JSONSearchParser):`
  - **Replace:** `"""HFX Storepass SaaS JSON search results."""`
  - **With:** `"""Storepass SaaS JSON search results."""`

Do not change `parse_data`, `add_result`, or the `sources = {...}` registry line.

### Task 1.2 — `tracking/forms.py`: replace the example URL in help text
- The `BASE_SEARCH_URL_HELP_TEXT` constant:
  - **Replace the whole constant:**
    ```python
    BASE_SEARCH_URL_HELP_TEXT = (
        "Search URL template; use {term} for the URL-encoded query string. "
        "Example: https://facetofacegames.com/apps/prod-indexer/search"
        "/pageSize/100/page/1/keyword/{term}"
    )
    ```
  - **With:**
    ```python
    BASE_SEARCH_URL_HELP_TEXT = (
        "Search URL template; use {term} for the URL-encoded query string. "
        "Example: https://example.com/search?q={term}"
    )
    ```
- Leave `INCLUDE_HELP_TEXT` / `EXCLUDE_HELP_TEXT` unchanged — their examples (`Lightning Bolt`,
  `RTX 5070`, `Foil`, …) are item/product examples, not vendor or website names.

### Task 1.3 — `tracking/models.py`: remove vendor examples from the `key` verbose_name
- The `Source.key` field:
  - **Replace:** `verbose_name="Short abbreviation identifying this source (user-facing; e.g. 'f2f', 'hfx')",`
  - **With:** `verbose_name="Short abbreviation identifying this source (user-facing; e.g. 'cc')",`
- Leave the `parser_key` verbose_name as-is (`'shopify', 'storepass'` are platform names).

### Task 1.4 — Delete the two vendor data migrations
Delete entirely (they embed vendor names, hosts, URLs, `store_id`, `Origin`/`Referer`):
- `tracking/migrations/0008_f2f_source.py`
- `tracking/migrations/0009_hfx_source.py`

They only `RunPython`-seed `Source` rows (no schema change), so removing them is safe. Those
`Source` rows will instead be created at runtime via the Source-management UI (`add_source` /
`view_sources` from Phase 2 Step 7) — no vendor data lives in the repo.

### Task 1.5 — Repoint the dependency of migration `0010`
In `tracking/migrations/0010_itemsource_unique_item_source.py`, so the chain becomes
`0007 → 0010 → 0011`:
- **Replace:**
  ```python
  dependencies = [
      ("tracking", "0009_hfx_source"),
  ]
  ```
- **With:**
  ```python
  dependencies = [
      ("tracking", "0007_source_page_size_source_parser_key_and_more"),
  ]
  ```

### Task 1.6 — Sync migration `0011` text with Task 1.3
In `tracking/migrations/0011_alter_source_key_alter_source_page_size_and_more.py`, the
`AlterField` for `key`:
- **Replace:** `field=models.CharField(max_length=20, primary_key=True, serialize=False, verbose_name="Short abbreviation identifying this source (user-facing; e.g. 'f2f', 'hfx')"),`
- **With:** `field=models.CharField(max_length=20, primary_key=True, serialize=False, verbose_name="Short abbreviation identifying this source (user-facing; e.g. 'cc')"),`

Leave the `page_size` and `parser_key` `AlterField`s unchanged.
> `0007_...` contains an older `parser_key` verbose_name (`"...; blank falls back to 'key'"`,
> examples `'shopify', 'storepass'`). No vendor names → leave as-is (historical record).

### Task 1.7 — Fix the one test that relies on a seeded vendor row
In `tracking/tests.py`, `class SourceManagementTests`:
- In `test_create_source_valid`:
  - **Replace:**
    ```python
        created = Source.objects.get(key="f2f")
        self.assertEqual(created.parser_key, "shopify")
    ```
  - **With:**
    ```python
        created = Source.objects.get(key="zz")
        self.assertEqual(created.parser_key, "shopify")
    ```
- In `_valid_data`, the stale comment:
  - **Replace:** `# Use a novel key: migrations 0008/0009 already create f2f/hfx rows.`
  - **With:** `# Use a source key that does not already exist.`

Do not remove other F2F/HFX/WT references in `tests.py` — permitted as sample data.

### Task 1.8 — Rebuild the local database
Removing applied migrations `0008`/`0009` desyncs `db.sqlite3`
(`InconsistentMigrationHistory`). Local data may be wiped, so rebuild:
```bash
rm -f db.sqlite3
python manage.py migrate
```
(The test runner builds its own DB; this only keeps the dev DB usable.)

### Pass 1 — Verification
1. **Migrations consistent:** `python manage.py makemigrations --check --dry-run` → `No changes detected`.
2. **Tests pass:** `python manage.py test tracking` → `OK`.
3. **No vendor references in application code** (returns nothing):
   ```bash
   rg -i 'facetofacegames|hfxgames|wizardtower|store\.storepass\.co|face to face|hfx games|wizard.?s tower|\bf2f\b|\bhfx\b' \
     tracking --glob '!tracking/fixtures/**' --glob '!tracking/docs/**' --glob '!tracking/tests.py'
   ```
   `shopify`/`storepass`/`prod-indexer` are allowed platform names and are intentionally NOT in
   this pattern; `cc`/Canada Computers is allowed.

---

## Pass 2 — Documentation: `plan.md` and `phase02_subplans.md`

Genericize vendor identity in the two planning docs **while preserving all technical/design
content**, and **reconcile them with the Pass 1 code changes** (no committed data migrations,
no `*_DEFAULT_SEARCH_URL` constants for these vendors, `Source` rows now created via the UI).

Apply the **Replacement glossary** above throughout. Prefer keying tables/registry comments by
**parser/platform** (`ShopifyParser` / Shopify, `StorepassParser` / Storepass,
`WtFiltersParser` / POST-JSON) instead of by vendor. Keep real path references to exempt files
(`tracking/docs/*_investigation.md`, `tracking/fixtures/html/{f2f,hfx,wt}/...`) intact.

### Task 2.1 — `plan.md`
Re-`rg` for the tokens first, then address each location (line numbers approximate):

- **Architecture diagram (§3, ~L78–79):** the "Vendor JSON search APIs" box lists
  `Shopify prod-indexer, Storepass, wt-filters`. Replace `wt-filters` with `POST JSON apps`
  (platforms only; no vendor named).
- **§4.3 variant note (~L127):** "Shopify **(F2F)** and Storepass **(HFX)** return multiple
  condition variants…" → drop the `(F2F)` / `(HFX)` parentheticals: "Shopify and Storepass
  return multiple condition variants…".
- **§5.1 registry comments (~L140–142):** remove the vendor parentheticals:
  - `# Phase 2 — Shopify prod-indexer JSON (F2F, ...)` → `# Phase 2 — Shopify prod-indexer JSON`
  - `# Phase 2 — Storepass SaaS JSON (HFX, ...)` → `# Phase 2 — Storepass SaaS JSON`
  - `# Phase 4 — wt-filters POST JSON (Wizard's Tower)` → `# Phase 4 — POST JSON search app`
- **§5.2 intro (~L153):** "Vendor investigations **(F2F, HFX, WT)** all found the same shape…"
  → "Vendor investigations all found the same shape…" (keep the `tracking/docs/*_investigation.md`
  path reference if present — it points at exempt files).
- **§5.2 mapping table (~L156–160):** re-key the table by parser instead of vendor:
  - `| **F2F** | Shopify `prod-indexer` … |` → `| `ShopifyParser` | Shopify `prod-indexer` … |`
  - `| **HFX** | Storepass SaaS | … |` → `| `StorepassParser` | Storepass SaaS | … |`
  - `| **WT** (Phase 4) | wt-filters app | **POST** … |` → `| `WtFiltersParser` (Phase 4) | POST JSON search app | **POST** … |`
  (Rename the first column header from `Vendor` to `Parser`.)
- **§5.3 (~L172):** "…Phase 4 adds POST for **wt-filters**." → "…Phase 4 adds POST for the
  POST-JSON API."
- **§9 fixtures (~L252):** the fixture-path line lists `.../f2f/*.json`, `.../hfx/*.json`.
  These are **real fixture paths** → keep them (exempt), no change needed.
- **§10 approach-change note (~L272–276):** rewrite to remove `F2F`, `HFX`, `WT` vendor tokens
  ("Investigations of the tracked vendors all found products are JS-rendered but backed by a
  JSON search API…"). Keep the `tracking/docs/{f2f,hfx,wt}_investigation.md` path reference
  (exempt files).
- **§10 roadmap checkboxes (~L279, L286–289, L301–302, L311):** genericize per glossary AND
  reconcile with Pass 1:
  - `Investigate vendor search pages … (F2F, HFX, WT docs)` → drop vendor tokens; keep doc paths.
  - `ShopifyParser (F2F prod-indexer): …` / `StorepassParser (HFX): …` → drop `(F2F)` / `(HFX)`.
  - **Data-migrations bullet** (`Source rows for F2F (…) and HFX (…) using API URL templates`)
    → rewrite to reflect the new design: *"`Source` rows for Shopify/Storepass vendors are
    created at runtime via the Source-management UI (not committed data migrations)."*
  - JSON-fixture bullet: keep the real fixture paths; drop vendor names from prose.
  - Pagination/payload bullets (`F2F /page/{n}`, `HFX ~1.9 MB`) → "the Shopify API `/page/{n}`",
    "large Storepass responses", etc.
  - `WtFiltersParser (Wizard's Tower): …` → drop `(Wizard's Tower)`.
- **§11 (~L324):** class list `ShopifyParser, StorepassParser, WtFiltersParser` — these are class
  names, keep as-is (no vendor prose around them to change).
- **§12 requirements (~L344):** "Second/third vendors | **Face to Face Games** (Shopify),
  **HFX Games** (Storepass); **Wizard's Tower** (wt-filters, POST)…" → "A Shopify vendor and a
  Storepass vendor; a POST-API vendor (Phase 4)."
- **§13 decision log (~L365–367, L370):** genericize the vendor names in the log entries
  ("Phase 2 vendors: Face to Face Games + HFX Games" → "Phase 2 vendors: a Shopify vendor + a
  Storepass vendor"; "wt-filters/WT" → "the POST-JSON API"). Keep the technical rationale.

### Task 2.2 — `phase02_subplans.md`
Re-`rg` first, then address each location (line numbers approximate):

- **Prerequisite note (~L5):** drop vendor tokens from prose; keep the real
  `tracking/docs/{f2f,hfx,wt}_investigation.md` and `tracking/fixtures/html/{f2f,hfx,wt}/...`
  path references (exempt).
- **Step table (~L34–35):** `ShopifyParser (F2F) + F2F Source row + tests` →
  `ShopifyParser (Shopify vendor) + Source row + tests`; same for `StorepassParser (HFX)`.
- **Step 3 heading/intro (~L282–298, L339):** replace "Face to Face Games", "F2F", and the
  `f2f_investigation.md` prose references with generic Shopify wording; keep the real fixture
  path `tracking/fixtures/html/f2f/search_results_sample.json`.
- **Step 3 constant + migration code blocks (~L307–357):** these show a literal
  `F2F_DEFAULT_SEARCH_URL` and an `add_f2f_source` migration with `key="f2f"`,
  `name="Face to Face Games"`, and the real `facetofacegames.com` URL. **This has been
  superseded by Pass 1** (constant not committed; migration deleted). Rewrite this block to
  either a generic placeholder example (`key="<key>"`, `name="<Shopify vendor>"`,
  `base_search_url="https://example.com/search?q={term}"`) **and** add a one-line note:
  *"Superseded: `Source` rows are created via the Source-management UI, not a committed
  migration (see feedback.md Pass 1)."*
- **Step 3 Definition-of-Done (~L373–374):** the `F2F_DEFAULT_SEARCH_URL constant added` and
  `Migration creates Source(key="f2f", …)` items are now false. Update them to describe the
  generic `ShopifyParser` + the runtime-UI seeding approach (or mark superseded).
- **Step 4 (~L384–479):** apply the exact same treatment as Step 3 for the Storepass side:
  vendor names ("HFX Games", "HFX"), the `HFX_DEFAULT_SEARCH_URL` block, `store.storepass.co`
  URL with `store_id=Q5MjnQr1MA`, `hfxgames.com` Origin/Referer, `add_hfx_source` migration,
  and the DoD items. Replace `store_id=Q5MjnQr1MA` with `store_id=<STORE_ID>`; keep the real
  fixture path `tracking/fixtures/html/hfx/search_results_sample.json`.
- **Step 5 examples (~L534–535, L550):** the include/exclude pattern examples are prefixed
  "F2F (…)" / "HFX (…)". Replace with "Shopify vendor (…)" / "Storepass vendor (…)". The card
  titles themselves (`Lightning Bolt …`) and condition codes (`(NM)`, `(Near Mint)`) are
  product data — keep them.
- **Step 7 form help text (~L700):** `Example: https://facetofacegames.com/...{term}` →
  `Example: https://example.com/search?q={term}` (match the Pass 1 forms.py wording).
- **Bottom status checklist (~L760–761):** `ShopifyParser (F2F) + F2F Source row` →
  `ShopifyParser (Shopify vendor) + Source row`; same for the Storepass line.

### Pass 2 — Verification
1. **No vendor names/hosts remain in the two docs, except accurate paths to exempt files**
   (the second `rg` filters out legitimate `tracking/docs/` and `tracking/fixtures/` paths):
   ```bash
   rg -in 'facetofacegames|hfxgames|store\.storepass\.co|face to face|hfx games|wizard.?s tower|\bf2f\b|\bhfx\b|\bwt\b|wt-filters' \
     plan.md phase02_subplans.md | rg -v 'tracking/(docs|fixtures)/'
   ```
   Expected: no output. (`shopify`, `storepass`, `prod-indexer`, and the class/registry names
   `ShopifyParser`/`StorepassParser`/`WtFiltersParser`/`wtfilters` are allowed and not matched.)
2. **Docs still agree with the code:** spot-check that any remaining "how it works" statements
   match the Pass 1 outcome — no committed vendor migrations, no `F2F_/HFX_DEFAULT_SEARCH_URL`
   constants, `Source` rows created via the UI.

---

## Open decisions / flags (resolve with the user before or during the relevant Pass)

1. **`WtFiltersParser` / `wtfilters` naming (Phase 4, not yet built).** "wt" derives from
   *Wizard's Tower*. Options: (a) keep `WtFiltersParser`/`wtfilters` as an accepted
   platform-ish name (consistent with keeping `ShopifyParser`/`StorepassParser`); or
   (b) rename to a neutral platform name once the underlying API tech is known. Default taken
   in Pass 2: **keep the class/registry identifiers**, but genericize the *prose* "wt-filters"
   and "Wizard's Tower". Confirm before Phase 4 implementation.
2. **Vendor investigation docs (`tracking/docs/{f2f,hfx,wt}_investigation.md`).** Currently
   **exempt/out of scope**. They (and their filenames) still contain vendor names, and
   `tracking/tests.py::test_f2f_investigation_doc_exists` asserts the `f2f_investigation.md`
   path. If a future Pass wants these scrubbed/renamed, it must also update that test and every
   path reference in `plan.md` / `phase02_subplans.md`. Flag for a future Pass.
3. **Fixture directory names (`tracking/fixtures/html/{f2f,hfx,wt}/`).** Exempt (fixtures), but
   the vendor-named subdirectories are referenced by parser tests. Renaming them would touch
   `tests.py` and the doc path references — out of scope unless requested.

---

## Cross-cutting consequence to remember

After Pass 1, the F2F/HFX `Source` rows are **no longer seeded by committed code**. To use
those vendors, create the `Source` rows at runtime via the Source-management UI
(`/sources/add/`) — API URL template, `parser_key` (`shopify` / `storepass`), and request
headers — or via an **uncommitted** local data migration/fixture kept out of git. Pass 2's doc
edits must describe this new reality rather than the old committed-migration approach.
