# django_scraper

Web server for gently scraping search results and accumulating price data over time.

## Prerequisites

- Python 3.12+ (match your local venv)
- A virtual environment

## Setup

```bash
python -m venv venv
source venv/bin/activate
cd django_scraper
cp .env_sample .env   # edit SECRET_KEY and other values
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open http://localhost:8000/ — you will be redirected to log in. Use the superuser account you created. Configure sources and item-source links in the UI or Django admin.

## Running tests

```bash
source venv/bin/activate
cd django_scraper
python manage.py test tracking --settings=django_scraper.settings_test
```

Tests run offline with no Redis, Postgres, or live HTTP. The dedicated test settings module (`django_scraper/settings_test.py`) forces an in-memory SQLite database, synchronous (immediate) Huey tasks, and a fast password hasher, independent of `.env`.

To measure test coverage (configured via `.coveragerc`):

```bash
coverage run manage.py test tracking --settings=django_scraper.settings_test
coverage report -m
```

## Background updates

Manual “Update All Active” / “Update Selected” runs fan out one background task per item-source (`dispatch_fan_out`/`fetch_one`) rather than one task per run, so a single rate-limited or slow source doesn't hold up the others. In development and during tests, Huey runs tasks **immediately** in-process (`HUEY["immediate"] = True` when `DEBUG` is on) — no separate worker is required.

For real queued/scheduled runs in production:

```bash
# Requires Redis (settings.REDIS_URL, e.g. redis://localhost:6379/0; unset/"")
python manage.py run_huey
```

Only a running `run_huey` consumer dispatches periodic schedules; the web process alone does not. With `DEBUG=False` (or `HUEY_IMMEDIATE=False`) and a live Redis, start `run_huey` alongside the web app.

**Schedules** (`/schedules/`): named recurring scrapes with preset frequencies (Hourly, Twice Daily, Daily) and an anchor time in **America/Halifax**. Optional tag scope limits a run to active items with that tag; no tag means all active items. The dispatcher wakes each minute and fires due schedules once per interval (no backfill of missed windows). Outcomes appear in Scrape History like manual updates.

## Item metadata enrichment

An item can optionally be enriched with external reference metadata (image, description, link) from a **metadata provider** — e.g. a Magic: the Gathering card's art and text pulled from Scryfall. This is separate from vendor price search (`Source`/`ItemSource`) and purely display-only: it never affects search-relevance filtering.

- **Registry**: providers are a pure code registry (`tracking/metadata_providers.py::PROVIDERS`), the same pattern as the parser registry (`tracking/parsers.py::sources`) — no database table, no per-deployment configuration. An item's `metadata_provider_key` selects a registered key (or blank, to disable enrichment).
- **Fetched state**: lives on a separate `ItemMetadata` row (one-to-one with the item) — `status` (`unfetched`/`pending`/`matched`/`needs_review`/`no_match`/`error`), `external_id`, an operator `pinned_external_id` override, and the provider's opaque `payload`.
- **Display contract**: every provider maps its raw `payload` to exactly three fields — `thumbnail_url`, `description`, `external_url` — via `to_display(payload)`, computed at render time. Generic templates (item detail page, item list thumbnail) render only these three fields; provider-specific data (e.g. mana cost, rarity) stays inert inside `payload`.
- **Refresh queue**: refresh requests are enqueued through the single shared entrypoint `tracking/metadata.py::request_metadata_refresh` and drained at a bounded rate (`tracking/tasks.py::drain_metadata_fetch_queue`, ~once/minute, small batch per wake) — independent of the vendor rate-limit/budget subsystem (`tracking/ratelimit/`), which paces a different problem (live vendor-reported quota).
- **Failure handling**: a failed fetch sets `status=error` with no automatic retry. Recovery is the "Retry metadata fetch" action on the item detail page, a `text` edit (if unpinned), or a provider change.

### Seeing fetches actually happen in dev

Setting a provider on an item only *enqueues* a `MetadataFetchRequest` — nothing drains that queue except the periodic `drain_metadata_fetch_queue` task, and (like `dispatch_scheduled_updates`/schedules — see "Background updates" above) that task only fires inside a live `python manage.py run_huey` consumer process. Running `runserver` alone (with no separate `run_huey` process) leaves every request sitting at `unfetched`/`pending` forever — no error, just silent inertness. The item detail page shows a warning in this situation, but the fix is the same as for schedules: start `run_huey` alongside `runserver`.

The one thing that's *not* the blocker here, and is easy to assume otherwise: **Redis is not required** for `run_huey` to drive this queue under the dev/test default (`HUEY_IMMEDIATE=True`, i.e. `DEBUG=True`). Huey transparently swaps in an in-memory broker whenever immediate mode is on, so `run_huey` starts and its scheduler fires periodic tasks synchronously in-process with no Redis connection at all. Redis only becomes required once `HUEY_IMMEDIATE=False` (real production mode) — that's when `run_huey` is pulling real queued work off Redis instead of executing inline.

| `HUEY_IMMEDIATE` | `runserver` alone | `runserver` + `run_huey` |
|---|---|---|
| `True` (dev/test default) | Requests enqueue; nothing ever drains them | Works — **no Redis needed** |
| `False` (prod / explicit) | Same — stuck forever | Requires a real `REDIS_URL` |

### Adding a new provider

1. In `tracking/metadata_providers.py`, subclass `MetadataProvider` and implement:
   - `resolve(item) -> ResolutionResult` — search/match the item, returning `MATCHED` (single confident match), `NEEDS_REVIEW` (candidates), or `NO_MATCH`.
   - `to_display(payload) -> {thumbnail_url, description, external_url}` — a pure mapping from your provider's raw shape to the generic three-slot contract.
   - `fetch_by_id(external_id)` — fetch a known identifier directly (used for a pinned/manually-entered ID).
2. Register it in the `PROVIDERS` dict: `PROVIDERS = {"scryfall": ScryfallProvider, "your_key": YourProvider}`. The key immediately becomes a selectable choice on the item create/edit/bulk-add forms — no migration needed.
3. Send a descriptive `User-Agent` on any outbound requests, per your provider's API guidelines (see `ScryfallProvider` for the pattern).

### Deferred / future directions

Explicitly out of scope for the initial implementation, noted here for when the need actually arises:

- A periodic re-sweep re-fetching metadata for already-`matched` items (only creation/update/manual-retry populate the queue today).
- Bulk "refresh all" / "refresh errored" actions across many existing items.
- General bulk-editing of any field across multiple existing items — the `item_ids` checkbox selection and `mode=selected` plumbing already on `view_terms` (see `UpdateFromWebView`) is reusable groundwork for this.
- A second registered provider — only the registry seam is proven out so far; a real second provider may reveal the three-slot display contract needs widening.

## PostgreSQL

Database backend is selected by `DATABASE_URL`:

- **Unset** → SQLite file `db.sqlite3` (local dev and tests)
- **`DATABASE_URL=postgres://user:pass@host:5432/dbname`** → PostgreSQL

`psycopg` is already pinned in `requirements.txt`; it is used only when Postgres is configured. Migrations are backend-agnostic — apply them to an empty Postgres DB with `python manage.py migrate`.

To move existing SQLite data: `dumpdata` on SQLite (with `DATABASE_URL` unset), then `loaddata` on Postgres after migrate. Run `python manage.py sqlsequencereset tracking | python manage.py dbshell` on Postgres afterward so new integer PKs do not collide.

**Always run tests without `DATABASE_URL` set** so they stay on SQLite.

## Production deployment

The app is intended to run behind a reverse proxy that terminates TLS. Django serves the app via a WSGI server; the proxy handles HTTPS and static files.

### Architecture

1. **Reverse proxy** (nginx or Caddy) — terminates TLS (e.g. Let's Encrypt), serves collected static files from `staticfiles/`, and `proxy_pass`es dynamic requests to the WSGI server. Set `X-Forwarded-Proto: https` so Django detects HTTPS when `SECURE_DEPLOYMENT=True`.
2. **WSGI server** — run gunicorn or uvicorn against `django_scraper.wsgi:application`, e.g. `gunicorn django_scraper.wsgi:application --bind 127.0.0.1:8000`.
3. **Huey worker** — a separate process running `python manage.py run_huey` with Redis (`REDIS_URL`). The web process alone does not dispatch scheduled scrapes. See `docs/scheduling.md` for schedule behaviour.
4. **PostgreSQL** — set `DATABASE_URL` to a Postgres connection string. See `docs/postgres_migration.md` for migration from SQLite.
5. **Rate-limit awareness** (optional) — API-profiled `Source` rows (`rate_limit_profile`) pace themselves against vendor quota; the same Redis as the Huey worker (`REDIS_URL`) is required for that pacing (and its idempotency locks) to be shared correctly across more than one worker. See `docs/rate_limiting.md`.

### Required production `.env`

| Variable | Example / notes |
|----------|-----------------|
| `DEBUG` | `False` |
| `SECRET_KEY` | Fresh random key — **distinct from dev** (see below) |
| `ALLOWED_HOSTS` | Your public domain, e.g. `tracker.example.com` |
| `SECURE_DEPLOYMENT` | `True` — enables SSL redirect, secure cookies, HSTS, and proxy SSL header |
| `CSRF_TRUSTED_ORIGINS` | `https://tracker.example.com` (comma-separated if multiple) |
| `DATABASE_URL` | `postgres://user:pass@host:5432/dbname` |
| `REDIS_URL` | Redis for Huey; unset/`""` means not configured. Example: `redis://localhost:6379/0` |
| `HUEY_IMMEDIATE` | `False` (default when `DEBUG=False`) so tasks queue to Redis |

Generate a new secret key:

```bash
python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"
```

Never commit `.env` or reuse a dev key in production.

### Deploy checklist

```bash
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput   # writes to staticfiles/; proxy serves these
python manage.py createsuperuser           # first deploy only
python manage.py check --deploy            # run with SECURE_DEPLOYMENT=True in .env
```

Start services:

```bash
gunicorn django_scraper.wsgi:application --bind 127.0.0.1:8000
python manage.py run_huey                  # separate terminal/service; requires Redis
```

Configure nginx/Caddy to:

- Redirect HTTP → HTTPS
- Serve `/static/` from `staticfiles/` (or your `STATIC_ROOT` path)
- Proxy other requests to the WSGI bind address with `X-Forwarded-Proto: https`

Local dev and tests keep `SECURE_DEPLOYMENT=False` (default) so the test client and `runserver` are unaffected.
