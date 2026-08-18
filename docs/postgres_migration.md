# PostgreSQL migration path

This project runs on **SQLite by default** (local dev and the test suite) and
supports **PostgreSQL** as a drop-in production database. The database is chosen
entirely by the `DATABASE_URL` environment variable — no code changes are needed
to switch.

- **Unset `DATABASE_URL`** → local SQLite file (`db.sqlite3`). This is the
  dev/test default; the test suite always runs here (no Postgres, no network).
- **`DATABASE_URL` set to a Postgres URL** → PostgreSQL.

The wiring lives in `django_scraper/settings.py`:

```python
DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}
```

`env.db_url` is [django-environ](https://django-environ.readthedocs.io/)'s URL
parser: a `postgres://…` URL resolves to
`ENGINE = "django.db.backends.postgresql"`, so a plain env var flips the backend.

> **Migrations are backend-agnostic.** The `tracking` migrations (head:
> `0015_updateschedule`) use only the Django ORM — no raw SQL, no vendor-specific
> operations — so the exact same migration set applies cleanly on both SQLite and
> PostgreSQL. There is nothing Postgres-specific to add.

---

## 1. Install the driver

`psycopg` (psycopg 3) is already pinned in `requirements.txt`
(`psycopg[binary]==3.3.4`). The `[binary]` extra ships prebuilt wheels so no
system build tools or `libpq-dev` are needed. It is only actually *used* when
`DATABASE_URL` points at Postgres; SQLite dev/test setups can ignore it.

```bash
source /home/ross/work/django_scraper/venv/bin/activate
cd /home/ross/work/django_scraper
pip install -r requirements.txt
```

---

## 2. Provision the PostgreSQL database and user

On the Postgres host (adjust names/passwords):

```sql
CREATE DATABASE django_scraper;
CREATE USER scraper WITH PASSWORD 'change-me';

-- Recommended defaults for a Django app DB.
ALTER ROLE scraper SET client_encoding TO 'utf8';
ALTER ROLE scraper SET default_transaction_isolation TO 'read committed';
-- Store everything in UTC (see the timezone section below).
ALTER ROLE scraper SET timezone TO 'UTC';

GRANT ALL PRIVILEGES ON DATABASE django_scraper TO scraper;
```

On PostgreSQL 15+ the `public` schema is locked down by default, so also grant
schema rights (run while connected to the `django_scraper` database):

```sql
GRANT ALL ON SCHEMA public TO scraper;
```

---

## 3. Point the app at Postgres

Set `DATABASE_URL` in the environment the app reads. `settings.py` currently
loads `.env_sample` (`environ.Env.read_env(...)`); in production point that at a
real `.env` and add:

```bash
DATABASE_URL=postgres://scraper:change-me@localhost:5432/django_scraper
```

`.env_sample` already carries this line commented out as a template. If the
password contains URL-special characters (`@ : / ?`), URL-encode them.

Confirm Django sees Postgres:

```bash
python manage.py check
python -c "import django, os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','django_scraper.settings'); django.setup(); from django.conf import settings; print(settings.DATABASES['default']['ENGINE'])"
# -> django.db.backends.postgresql
```

---

## 4. Create the schema

Apply the full migration chain to the empty Postgres database:

```bash
python manage.py migrate
```

This builds every table (`Source`, `SearchableItem`, `ItemSource`, `Tag`,
`WebUpdate`, `FetchJob`, `SearchResult`, `UpdateSchedule`, plus Django's own
apps) up to migration `0015_updateschedule`.

---

## 5. Move existing data (optional)

If you have local SQLite data to carry over, use Django's `dumpdata` /
`loaddata`. This is DB-independent (it serializes ORM objects, not SQL).

### 5a. Dump from SQLite

Run with `DATABASE_URL` **unset** (so it reads the SQLite file):

```bash
python manage.py dumpdata \
    --natural-foreign --natural-primary \
    --exclude contenttypes --exclude auth.permission \
    --indent 2 \
    -o dump.json
```

Notes specific to this project's models:

- **`Source.key` is a string primary key** (not an auto id). It serializes and
  reloads as its string value, so foreign keys pointing at a `Source`
  (`ItemSource.source`, `FetchJob.source`, `SearchResult.source`) round-trip by
  that key. `--natural-primary`/`--natural-foreign` keep references portable and
  avoid clashing with any Postgres sequence.
- Excluding `contenttypes` and `auth.permission` avoids the classic
  `IntegrityError` on load, because `migrate` already recreated those rows on the
  fresh Postgres DB. Let the loaded app data re-link to them.
- `Tag.name` is unique and `ItemSource` has a `unique_together (item, source)`;
  load into an **empty** Postgres DB so these constraints don't collide with
  pre-seeded rows.

### 5b. Load into Postgres

With `DATABASE_URL` set to the Postgres URL and migrations already applied:

```bash
python manage.py loaddata dump.json
```

### 5c. Reset sequences

Because rows were inserted with explicit integer PKs, bump Postgres' auto-id
sequences so new inserts don't collide:

```bash
python manage.py sqlsequencereset tracking | python manage.py dbshell
```

(`Source` is unaffected — it has no sequence, being a string PK.)

---

## Field portability notes

- **`JSONField`** — used by `Source.request_headers` (dict),
  `ItemSource.title_include_patterns` / `title_exclude_patterns` (lists). Django's
  `JSONField` is portable: on SQLite it stores JSON in a text column, on Postgres
  it maps to native `jsonb`. Values round-trip through `dumpdata`/`loaddata`
  unchanged. Postgres additionally enables JSON querying, but no data migration is
  required.
- **`FloatField` / `SmallIntegerField` / `PositiveIntegerField`** — map to
  standard Postgres numeric types; no special handling.
- **`TextChoices` / `IntegerChoices`** (e.g. `WebUpdate.Status`,
  `FetchJob.Status`, `SearchableItem.Priority`, `UpdateSchedule.Frequency`) are
  stored as plain `varchar`/`integer`; choices are enforced in Python, so both
  backends behave identically.

## Timezone / datetime notes

- `settings.py` sets `USE_TZ = True` and `TIME_ZONE = "America/Halifax"`. With
  `USE_TZ = True`, Django **stores all datetimes in UTC** in the database and
  converts to the local zone for display. This holds on both SQLite and Postgres,
  so `WebUpdate.timestamp`, `FetchJob`, and `UpdateSchedule.last_run_at` transfer
  correctly.
- `UpdateSchedule.anchor_time` is a naive `TimeField` interpreted as local
  (America/Halifax) time by the scheduling logic; it carries no offset and is
  backend-independent.
- Set the Postgres role/session timezone to UTC (see step 2) to keep raw
  `psql`-level inspection consistent with what Django writes.

---

## Verification checklist

After migrating, with `DATABASE_URL` pointing at Postgres:

- [ ] `pip install -r requirements.txt` succeeds (psycopg installed).
- [ ] `python -c "...print ENGINE..."` prints `django.db.backends.postgresql`.
- [ ] `python manage.py check` passes.
- [ ] `python manage.py migrate` applies cleanly to the empty DB.
- [ ] `python manage.py makemigrations --check --dry-run` → `No changes detected`
      (schema matches models; migrations are backend-agnostic).
- [ ] (If migrating data) `dumpdata` on SQLite then `loaddata` on Postgres
      completes without `IntegrityError`; then `sqlsequencereset` applied.
- [ ] Row counts match, e.g. `Source.objects.count()`,
      `SearchResult.objects.count()`, `SearchableItem.objects.count()` equal the
      SQLite originals.
- [ ] App smoke test: item list and item detail pages render; a `WebUpdate` run
      stores `SearchResult` rows.

> The **test suite stays on SQLite**. Do not set `DATABASE_URL` when running
> `python manage.py test tracking`; leaving it unset keeps tests fast, offline,
> and independent of any Postgres server.
