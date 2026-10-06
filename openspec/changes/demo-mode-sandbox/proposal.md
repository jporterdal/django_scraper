## Why

The app has no safe way to be shown publicly. Every page requires a login, and a visitor who could trigger "Update All Active" would make the server scrape real vendors and Scryfall on their behalf. Linking the app from a CV or portfolio page needs a public, no-login deployment that shows off the real scrape, relevance and bulk-edit pipeline. It must not make outbound requests, must not let anonymous visitors reach input paths that can exhaust the server (format-string templates, free-text regex), and must never show a new visitor an empty app.

## What Changes

- A single `DEMO_MODE` environment variable switches a deployment into demo mode. It forces the settings a public demo needs: `DEBUG` off, in-process (immediate) background tasks with no Redis, a local SQLite database, zero scrape delay, no admin site and no file logging. The usual deployment variables (`SECRET_KEY`, `ALLOWED_HOSTS`, CSRF origins, `SECURE_DEPLOYMENT`) are still required.
- **No login**: every request runs as a fixed, non-staff demo user. No sessions are stored server-side, and login, logout and admin are not exposed.
- **No outbound HTTP**: vendor searches are served by a replay layer over bundled local catalogue data, rendered in each demo source's real response format and parsed by the real parsers. Item metadata comes from a local-file demo provider. Any attempt to build the real HTTP fetcher, or to request a non-demo URL, fails instead of falling back to the network.
- **Deny-by-default writes**: only an explicit allowlist of actions accepts POSTs. All Sources and all Schedules are preset and read-only. `pinned_url` and `url_suffix` cannot be set. Title include/exclude patterns can only be picked from a preset list (no free-text regex anywhere). Request body size and field count are tightly capped.
- **Visitor caps**: limits on how many items and tags visitors can create, a smaller bulk-add batch, a global minimum interval between update runs, and a ceiling on stored search results.
- **Protected seed data**: 4–5 seed items with fixed IDs (plus their item-sources, tags and metadata) always exist and cannot be edited or deleted. Running price updates on them is still allowed. Visitors can create and edit their own items alongside them.
- **Automatic reset to the seed state**: the sandbox is restored on boot, and lazily on the first request after the demo has been idle for at least `DEMO_RESET_IDLE_SECONDS` (default 1 hour) **and** at least `DEMO_RESET_MIN_INTERVAL_SECONDS` (default 1 hour) have passed since the last reset. The reset is a single atomic reseed whose price history ends at the moment of the reset, so the demo never looks stale.
- **Visible demo framing**: a demo banner showing when the data was last reset, read-only presentation of Sources and Schedules, and the Admin link hidden.
- With `DEMO_MODE` unset (the default), behavior is unchanged.

## Capabilities

### New Capabilities
- `demo-mode`: turning demo mode on with one variable, no-login access as a fixed demo user, the deny-by-default write allowlist, read-only Sources and Schedules, preset-only patterns, request-size limits and visitor caps.
- `demo-replay-data`: the guarantee of no outbound HTTP, plus simulated vendor search results and item metadata served from bundled local data through the real parsing and relevance pipeline.
- `demo-sandbox-reset`: the always-present protected seed data and the idle-gated, atomic reset back to that seed state, configured by the two demo reset settings.

### Modified Capabilities
None. The behaviors that differ in demo mode (inline metadata draining, preset-only patterns in bulk edit, read-only Sources) are specified as demo-conditional requirements in the new capabilities. Existing requirements still hold unchanged whenever demo mode is off.

## Impact

- **Settings**: new `DEMO_MODE` plus a demo-only settings group (`DEMO_RESET_IDLE_SECONDS`, `DEMO_RESET_MIN_INTERVAL_SECONDS`, cap settings). Conditional overrides for `DEBUG`, `HUEY`, `REDIS_URL`, `DATABASES` (SQLite with WAL, immediate transactions and a busy timeout), sessions and message storage, upload limits, middleware and static file serving.
- **Code**:
  - `tracking/fetcher.py` (demo guard plus a replay fetcher), `tracking/metadata_providers.py` (registry read at call time, demo provider) and `tracking/metadata.py` (inline drain in demo).
  - New demo middleware for the user, allowlist and reset; pattern validation in `tracking/forms.py`; seed-protection checks in the item, item-source, tag, metadata and bulk-edit views.
  - New management commands to seed and reset the demo, a small singleton demo-state model with a migration, and template changes for the banner, read-only pages and hidden controls.
- **Data**: a bundled demo dataset (catalogues per demo source, a seed manifest, metadata payloads and local images).
- **Dependencies**: `whitenoise`, used only in demo mode so gunicorn can serve static files to the separate nginx proxy service, which can't read the web container's disk.
- **Deployment**: a demo deployment is one web service (gunicorn) behind the existing nginx proxy, with no Redis, Postgres or Huey worker. Start command: `migrate && demo_reset && gunicorn`. A new section is added to `docs/deployment_steps.md`.
