## Context

Current state that shapes the approach (see proposal.md, Why, for motivation):

- **Auth**: `LoginRequiredMiddleware` gates every view (`django_scraper/settings.py`). The navigation links to `/admin/` and renders a logout form whenever `user.is_authenticated` (`tracking/templates/tracking/base.html`).
- **Outbound HTTP has two paths, not one.**
  - Vendor searches go through `Fetcher` (`tracking/fetcher.py`), built by `Fetcher.from_settings()` inside `fetch_one_unit`/`run_web_update` when no fetcher is injected. The tests already inject fetchers, so this is a proven seam.
  - `ScryfallProvider` calls `requests.get` directly (`tracking/metadata_providers.py:94,123`). The provider registry `PROVIDERS` is imported as a module-level dict in `forms.py`, `views.py` and `tasks.py`.
- **Input paths that can exhaust resources even with no outbound requests** (found during `/opsx:explore`):
  - `Source.build_search_url` calls `str.format()` on operator-supplied text, and a format spec such as `{term:>999999999}` allocates about 1 GB. This runs in `fetch_one_unit` *before* the fetcher, so replacing the fetcher doesn't neutralize it.
  - Title include/exclude patterns are only checked to compile (`forms.py:_validate_regex_patterns`). They run at ingest and also at **read time** on every list and detail page view (`views.py:278,729`), so one backtracking pattern slows every visitor's page load.
- **Background work without Redis**: in immediate mode Huey runs tasks inline, and `get_unit_lock()`/`get_budget_store()` fall back to in-memory implementations when `REDIS_URL` is empty. Periodic tasks (schedules, the metadata drain) never fire without a `run_huey` consumer. Immediate mode defaults to `DEBUG`, so a `DEBUG=False` deployment would currently expect Redis.
- **Database**: SQLite is the default. Django's SQLite backend creates `AUTOINCREMENT` primary keys, so `sqlite_sequence` only ever increases unless a `flush` resets it (`flush` defaults to `reset_sequences=True`). `WebUpdate.timestamp` is `auto_now_add`.
- **Parsers**: the registry is `cc` (HTML), `shopify`, `storepass` and `wtfilters` (JSON). `_response_looks_blocked` treats a `text/html` Content-Type, or an HTML-looking body, as blocked for JSON parsers.

## Goals / Non-Goals

**Goals:**
- Make the security property structural (deny by default) rather than a list of individually patched holes. New views and fields added later should be locked in demo mode without anyone remembering to lock them.
- Every demo price update and metadata action runs the real parse, relevance, pattern, dedup and terminalize code, fed by local data.
- A demo deployment is one web process plus a local SQLite file.
- Demo branches add nothing to, and change nothing in, non-demo behavior.

**Non-Goals:**
- Per-visitor sandboxes or data isolation between visitors. Models have no owner, and the app is designed for one operator.
- A hard maximum-age reset under continuous traffic. Only the idle-gated reset and the boot reset were chosen. See Risks.
- Moderating visitor-entered text beyond the length and volume caps.
- Running the HTML (`cc`) parser in demo mode. Three JSON-format demo sources are enough to show the pipeline.
- Changing `DEBUG`/`HUEY`/Redis behavior or any spec when demo mode is off.

## Decisions

### D1. One flag forces all dependent settings
`DEMO_MODE = env.bool("DEMO_MODE", default=False)` is read at the top of `settings.py`. A single `if DEMO_MODE:` block at the end of the file then forces:
- `DEBUG=False` (so the `DEBUG` default of True can't leak tracebacks publicly)
- `HUEY["immediate"]=True` and `REDIS_URL=""`
- `DATABASES` set to SQLite at `BASE_DIR/"demo.sqlite3"`, ignoring `DATABASE_URL`
- `SCRAPE_REQUEST_DELAY_SECONDS=0` and jitter 0
- no `file` log handler (this already follows from `DEBUG=False`)
- `SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies"` and `MESSAGE_STORAGE` set to cookie storage
- `DATA_UPLOAD_MAX_MEMORY_SIZE` and `DATA_UPLOAD_MAX_NUMBER_FIELDS` lowered
- WhiteNoise inserted after `SecurityMiddleware`. The demo sits behind the existing nginx proxy, but nginx runs as a separate service (see `docs/deployment_steps.md`) and can't read the web container's `staticfiles/`. So nginx proxies `/static/` through to gunicorn, WhiteNoise serves it from there, and nginx can still cache it.

The demo middleware (D3, D4, D9) is **always** listed in `MIDDLEWARE` and does nothing unless `settings.DEMO_MODE` is true. Demo behavior is then testable with `override_settings(DEMO_MODE=True)` without rebuilding the middleware stack or URLconf at import time.
- a `demo` context processor

The demo-only settings live in the same block, each overridable by an environment variable of the same name:

| Setting | Default |
|---|---|
| `DEMO_RESET_IDLE_SECONDS` | 3600 |
| `DEMO_RESET_MIN_INTERVAL_SECONDS` | 3600 |
| `DEMO_ACTIVITY_WRITE_INTERVAL_SECONDS` | 60 |
| `DEMO_MAX_VISITOR_ITEMS` | 25 |
| `DEMO_MAX_VISITOR_TAGS` | 10 |
| `DEMO_BULK_ADD_MAX_TERMS` | 10 |
| `DEMO_UPDATE_MIN_INTERVAL_SECONDS` | 30 |
| `DEMO_MAX_SEARCH_RESULTS` | 20000 |

Request limits: `DATA_UPLOAD_MAX_MEMORY_SIZE` is set to 64 KiB, and `DATA_UPLOAD_MAX_NUMBER_FIELDS` to 1000.

The `DEMO_*` names are also given inert defaults outside the block, so tests can toggle demo mode with `override_settings` without a `NameError`. Nothing reads them unless `DEMO_MODE` is true. `settings_test.py` explicitly sets `DEMO_MODE=False`.

*Alternative considered:* separate variables per concern. Rejected because the requirement is a single variable, and forgetting one (for example `DEBUG`) is exactly the failure to prevent.

### D2. SQLite configured for concurrent web workers
The demo `DATABASES["default"]["OPTIONS"]` sets:
- `"init_command": "PRAGMA journal_mode=WAL;"`, so readers are never blocked by the reset's write transaction and see a consistent snapshot
- `"transaction_mode": "IMMEDIATE"`, so every `atomic()` block takes the write lock at `BEGIN`; this is what serializes competing resets in D9
- `"timeout": 20`, a busy timeout so concurrent writers wait instead of raising `database is locked`

*Alternative considered:* Postgres for the demo. Rejected because it adds a service, which goes against the resource goal. The reset design (D9) is specific to SQLite.

### D3. Demo user, without login or sessions
A `DemoUserMiddleware` runs after `AuthenticationMiddleware` and before `LoginRequiredMiddleware`. It sets `request.user` to a single fixed `demo` user: not staff, not superuser, unusable password, created by `demo_reset` if missing. There is no `login()` call, so nothing is written to the session. In demo mode the demo middleware returns 404 for any path under `admin/` or `accounts/`, so the routes stay mounted but can't be reached. This avoids changing the URLconf at import time (see D1). `base.html` hides the Admin link and the logout form behind the context-processor flag, since `{% url 'logout' %}` would fail to resolve otherwise.

### D4. Deny-by-default write allowlist
A `DemoWriteAllowlistMiddleware` resolves `request.resolver_match.url_name` for any method outside GET/HEAD/OPTIONS. It returns 403 unless the name is in a single `DEMO_ALLOWED_WRITE_URL_NAMES` frozenset, defined in a new `tracking/demo/` package:

`add_term`, `edit_term`, `bulk_add`, `bulk_edit_items`, `add_item_source`, `edit_item_source`, `delete_item_source`, `add_tag`, `edit_tag`, `delete_tag`, `metadata_retry`, `metadata_select_candidate`, `metadata_set_external_id`, `update`.

Anything missing from the set, including views added in future, is rejected. I confirmed during exploration that no GET handler writes data, so checking only the method is sound. A test iterates over every URL pattern and asserts that each unsafe-method route either appears in the allowlist or returns 403. This means a new route forces someone to make a conscious allowlist decision.

*Alternative considered:* adding a demo check inside each view. Rejected because it fails open: a new view would be writable until someone remembered to add the check.

### D5. Allowlisted views are narrowed to bounded inputs
- **Sources and Schedules**: not on the allowlist. Their list templates hide the add, edit and delete controls in demo mode.
- **Item-source `pinned_url`/`url_suffix`**: removed from `ItemSourceForm` and the item-create source formset in demo mode. Every save path also blanks them explicitly, including `forms.py:752` and bulk add. The blanking matters because `ModelForm` exclusion alone doesn't cover code that copies from `cleaned_data`.
- **Patterns**: `DEMO_PRESET_PATTERNS`, a small list of human-labelled regexes defined in the demo data (D6), is the only source of patterns.
  - `_validate_regex_patterns` rejects any pattern not in the preset set when demo mode is on. This one shared validator covers both `ItemSourceForm` and the bulk-edit textareas.
  - In demo mode the textareas render as multi-select checkboxes over the presets.
  - Bulk edit hides its free-text add box, and its tri-state rows are built from the presets plus the patterns already present.
  - A second check before ingest/read-time matching is unnecessary: every write path goes through the validator, and the seed is built from presets.
- **Caps**:
  - Item and tag counts are checked in the create and bulk-add views (visitor rows are those not in the seed manifest).
  - The demo bulk-add limit replaces `BULK_ADD_MAX_TERMS` in the form.
  - The update throttle is an atomic conditional update on the demo-state row (D9): `DemoState.objects.filter(pk=1, last_update_started_at__lt=cutoff).update(last_update_started_at=now)`. A row count of 0 means the update is refused, which also handles two concurrent requests correctly.
  - The `SearchResult.objects.count()` ceiling is checked before dispatch.
- **CSV export**: in demo mode, cells beginning with `=`, `+`, `-` or `@` get a leading `'`, because visitor-entered item text appears in exports other visitors download.

### D6. Bundled demo dataset in neutral form
A `tracking/demo/data/` package holds JSON, plus images under `tracking/static/tracking/demo/`:
- **`sources.json`**: three preset Sources with fictional store names. Each has `parser_key` set to `shopify`, `storepass` or `wtfilters`, `http_method=GET`, `max_pages=1`, a blank `rate_limit_profile`, and `base_search_url="demo://<source_key>/search?q={term}"`.
- **`catalogue/<source_key>.json`**: products in a neutral shape `{id, title, price, instock, category, product_line}`. Each catalogue deliberately includes near-miss products (other product lines, accessories, word-order collisions such as the existing "Fire Dragon" fixture) so the relevance filters visibly reject things.
- **`seed.json`**: the manifest. It holds seed items with fixed PKs 1–5, seed tags with fixed PKs, item-sources (using preset patterns on at least one), seed metadata, two example Schedules for the read-only page, the history length (28 days) and the RNG seed.
- **`metadata.json`**: demo metadata entries with their display fields. Thumbnails reference static files.
- **`patterns.json`**: the preset patterns.

The demo dataset is fictional rather than trimmed copies of the captured vendor fixtures. This avoids publishing real stores' names and prices and real vendor URLs, and keeps the files small (the captured hfx fixture is 2.6 MB).

**Seed items** are five invented Magic: the Gathering card names. Each was checked against Scryfall's exact and fuzzy name lookup on 2026-10-06, and none matches a real card:

| PK | Seed item | Colour / art motif |
|---|---|---|
| 1 | Emberwake Phoenix | red, flame |
| 2 | Thornvault Sentinel | green, shield |
| 3 | Gloomtide Archivist | blue, eye |
| 4 | Sunforged Reliquary | white/gold, sun |
| 5 | Cindermaw Tyrant | red/black, crown |

Catalogue near-misses for these names include word-order collisions ("Phoenix of the Emberwake"), accessories ("Emberwake Phoenix Playmat"), same-named products from another product line, and an alternate printing ("Thornvault Sentinel (Showcase)"), which doubles as the ambiguous metadata term that resolves to needs-review.

**Thumbnails** are tiny hand-templated SVG files, one per metadata entry. Each is a card-shaped rounded rectangle in the card's colour, a simple motif glyph, and the card name in text. A short script (`tracking/demo/make_thumbnails.py`) fills one SVG template string from `metadata.json` and writes `tracking/static/tracking/demo/<slug>.svg`. The generated files are committed, so there is no runtime generation and no image library. Scripts inside an SVG don't run when it is loaded through `<img>`, and the files are generated from our own data anyway.

*Alternative considered:* replaying the captured fixtures in `tracking/fixtures/html/` verbatim. Rejected because they have no price variation, return nothing for visitor terms, and expose real vendor data.

### D7. Replay fetcher, renderers and egress guard
- **`ReplayFetcher`** has the same `get`/`post`/`request`/`wait` interface as `Fetcher`, with `wait()` a no-op.
  - It accepts only `demo://<source_key>/search?q=…` URLs. Any other scheme or host raises an error, which `fetch_one_unit`'s existing error handling records as a failed FetchJob.
  - It loads the catalogue and selects products sharing at least one normalized word with the term (reusing `matching._normalize_for_match`), so loosely related products come back the way real vendor searches return them.
  - It applies price drift, then renders the products with a per-`parser_key` renderer into that vendor's JSON shape.
  - It returns a real `requests.Response` built locally (`_content`, `status_code=200`, `Content-Type: application/json`, `url`), so `.json()`, `.text`, `.headers`, `_response_looks_blocked` and the size-cap logic all behave normally with no network.
- **Price drift**: a deterministic, bounded function of `(product id, timestamp)`, a sum of slow sinusoids plus hashed noise clamped to ±5%. Repeated runs give different but smooth prices, and history generation (D9) can pass simulated timestamps. The fetcher takes an optional `clock` for this.
- **Wiring**:
  - `Fetcher.from_settings()` returns a `ReplayFetcher` when `DEMO_MODE` is on.
  - `Fetcher.__init__` raises when `DEMO_MODE` is on, so any code path that builds the real fetcher directly fails closed.
  - `PROVIDERS` users move to a call-time accessor `get_metadata_providers()`, which returns `{"demo": DemoMetadataProvider}` in demo mode and the existing dict otherwise. This also makes `override_settings` work in tests.
- **Egress guard (defense in depth)**: in demo mode, `AppConfig.ready()` wraps `socket.socket.connect`/`connect_ex` and `socket.create_connection`. Only `AF_UNIX` and loopback addresses are allowed; anything else raises. Nothing in a demo process needs outbound connections. SQLite is file-based and WhiteNoise serves from disk.
- **Test**: a renderer round-trip test proves that each renderer's output parses through the real parser into the same neutral products.

*Alternative considered:* a network-level egress firewall. It's worth turning on if the host supports one, but it can't be relied on across hosts, so the in-process guard plus fail-closed construction is the portable guarantee.

### D8. Metadata drains inline in demo mode
In demo mode, `request_metadata_refresh` calls `drain_pending_metadata_fetch_requests()` right after enqueuing. In immediate mode this runs `fetch_metadata` inline. The demo provider only does dictionary lookups, so this avoids the foreground blocking the README cites for rejecting the same fallback in normal mode. `DemoMetadataProvider.resolve` matches the item's term against entry names: one hit is matched, several are needs-review, none is no-match. `fetch_by_id` returns only known IDs. The item-detail "queue not draining" warning is suppressed in demo mode.

### D9. Demo state, idle-gated reset and reseeding
- **`DemoState` model**: a new singleton table (`pk=1`) with `last_reset_at`, `last_activity_at` and `last_update_started_at`. Its migration is additive and the table is unused outside demo mode. The reset never deletes this table or the `auth` tables.
- **`DemoResetMiddleware`** runs first among the demo middleware. On each request it reads `DemoState`.
  - **Due** means:
    - `now - last_activity_at >= DEMO_RESET_IDLE_SECONDS + DEMO_ACTIVITY_WRITE_INTERVAL_SECONDS`, **and**
    - `now - last_reset_at >= DEMO_RESET_MIN_INTERVAL_SECONDS`.
  - The extra term keeps "idle" a true lower bound, because activity is only written once per write interval (below).
  - If due, it calls `perform_reset()` before the view runs. It then sets `last_activity_at=now` if the stored value is older than `DEMO_ACTIVITY_WRITE_INTERVAL_SECONDS`, so there's at most one write per minute rather than per request.
- **`perform_reset()`** runs in one `transaction.atomic()`, which is `BEGIN IMMEDIATE` per D2.
  1. Re-read `DemoState` inside the transaction and return immediately if it's no longer due. When several requests arrive at once, all see "due", but they serialize on the write lock, and all except the first exit here. Every one is served post-reset data, because its view reads happen after this point.
  2. Delete every `tracking` table except `DemoState`, children first, including all Sources. Item PKs are kept monotonic by never touching `sqlite_sequence` and never using `flush`.
  3. Load the seed manifest with explicit PKs and ensure the `demo` user exists.
  4. Generate history.
  5. Set `last_reset_at=now`.
- **History generation** runs the real pipeline. For each simulated day `d` in the history window, it creates a `WebUpdate`, runs `fetch_one_unit` for every seed item-source with a `ReplayFetcher(clock=d)`, then backdates the `WebUpdate.timestamp` with `.update()` because of `auto_now_add`. The newest simulated run is stamped 5 minutes before the reset. Otherwise a visitor's first update straight after a reset would fall in the same drift-noise minute as that run and record every price as unchanged. This gives consistent FetchJobs, dedup counts, `ObservedCategoryValue` rows for bulk-edit suggestions, and visible relevance rejections, with no second writer of results to keep in sync.
  - Budget: 28 days × about 12 item-sources × small JSON comes to under ~2 s. A test asserts a time ceiling.
  - If the budget is missed, the fallback is to pre-generate the history rows once at boot and copy them in during resets.
- **In-flight requests** can't collide with an in-request reset. Every in-flight request recorded activity no more than one write interval before it started, so a reset can only become due once all requests have been idle for at least `DEMO_RESET_IDLE_SECONDS`, and no request runs that long. The atomic transaction plus WAL protects concurrent *arrivals*, not long-running writers.
- **Boot**: the start command is `migrate && collectstatic --noinput && demo_reset && gunicorn …`. `demo_reset` calls `perform_reset(force=True)`, which ignores the gate. A boot reset with no later activity can be followed by another reset after an hour. That's harmless and keeps the history current.

*Alternatives considered:*
- An external cron trigger. Rejected because a separate cron service generally can't reach the web container's local SQLite file, and it adds a service.
- Using Huey periodic tasks. Rejected because there's no consumer in demo mode.
- Restoring by copying a golden SQLite file. Rejected because the file would be swapped under open connections.

### D10. Seed protection: view checks plus a model-signal backstop
- **Protected set**: the PKs listed in `seed.json` (items and tags), resolved by `is_protected(obj)`. An item-source or `ItemMetadata` row is protected through its item. No model field or migration is needed.
- **View checks (primary)**:
  - `edit_term`, the item-source add/edit/delete views, the three metadata views, and tag edit/delete check protection and refuse with a "protected demo item" message and a redirect.
  - Bulk edit treats a protected item as a per-item outcome "skipped (protected)", using the existing best-effort per-item reporting.
  - Templates hide edit and delete controls for protected rows.
  - Seed items stay selectable for "Update Selected" and the bulk-edit workspace. Price updates don't write to protected models: they write `SearchResult`/`FetchJob`/`WebUpdate`/`ObservedCategoryValue` only.
- **Backstop**: in demo mode, `pre_save`/`pre_delete`/`m2m_changed` receivers on `SearchableItem`, `ItemSource`, `ItemMetadata` and `Tag` raise for protected rows unless inside the `allow_seed_writes()` context manager, which `perform_reset` uses. `QuerySet.update()` skips signals, which is why the view checks are primary. Tests cover every allowlisted endpoint against seed PKs.

## Risks / Trade-offs

- **[Continuous traffic prevents reset]** Without a hard max-age, a demo that never goes idle for an hour is never reset. → The caps bound the worst case to a full but bounded sandbox; seed items are always intact; any restart or redeploy resets. A max-age setting can be added later without changing the design.
- **[Visitor-entered content is visible to other visitors]** Item and tag text is shown to later visitors until the next reset. → Length limits (125/50 characters), count caps and the 64 KiB request ceiling bound it; seed items can't be altered; templates autoescape everything and `next` redirects are already validated. Accepted as the cost of an interactive demo.
- **[The Python-level egress guard can be bypassed by native code]** → No demo code path uses native networking. The guard is a backstop behind fail-closed fetcher construction and the demo-only provider registry, and a test runs a full update plus metadata actions with sockets patched to fail.
- **[The first visitor after an idle period waits for the reset]** → About 1–2 s, with the budget enforced by a test (D9). It happens at most once per idle period.
- **[`QuerySet.update()` bypasses the signal backstop]** → View-level checks are primary, and each allowlisted endpoint has a seed-PK test.
- **[Demo branches regress normal mode]** → Every branch is gated on `settings.DEMO_MODE`. The existing suite runs with demo mode off, and demo tests use `override_settings(DEMO_MODE=True)` plus the demo database options where relevant.
- **[`DEMO_MODE` set on the production deployment by mistake]** → The demo uses its own `demo.sqlite3` and never touches `DATABASE_URL`, so the production Postgres data is unaffected. The banner makes the mode obvious.

## Migration Plan

1. Merge with `DEMO_MODE` off everywhere. The only schema change is the additive `DemoState` table, which is inert outside demo mode.
2. Create a separate deployment, for example a new Railway project with one web service and no Postgres, Redis or worker. Set `DEMO_MODE=True`, `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` and `SECURE_DEPLOYMENT=True`. Start command: `python manage.py migrate && python manage.py collectstatic --noinput && python manage.py demo_reset && gunicorn --bind 0.0.0.0:8000 django_scraper.wsgi`.
3. **Rollback**: delete the demo service. Nothing in the production deployment changes.

## Open Questions

- Final default values for the caps, to be tuned after watching real demo usage.
