## Context

The live Railway project has four services: the Django web service (private name `djangoscraper`, gunicorn on `0.0.0.0:8000`), the Huey worker, Postgres and Redis. The nginx proxy is a fifth service, built from the separate `ds-nginx` repo. It is the only service with a public domain and proxies everything to `djangoscraper.railway.internal:8000`. Web and worker are currently built by Railway's auto-detected Python builder, with dashboard start commands `python manage.py migrate && gunicorn ...` and `python manage.py migrate && python manage.py run_huey`. Copies of the nginx `Dockerfile` and `nginx.conf` sit untracked in this repo's root.

Two facts shape the approach:
- With a Dockerfile build, a Railway dashboard start command replaces the image's entrypoint and runs in **exec form**, without a shell. The current `migrate && ...` commands would not work unchanged.
- `settings.py` creates the `file` log handler (`BASE_DIR/django_debug.log`) on every startup, even though it is only attached when `DEBUG` is on. A non-root container with a read-only app directory fails at startup because of it.

## Goals / Non-Goals

**Goals:**
- Same runtime behavior as today, delivered through Dockerfile builds from one repo.
- Every step can be checked before it touches production, and rollback is one action.

**Non-Goals:**
- Changing gunicorn worker count, tuning, health checks, multi-stage builds, image size optimization, CI image builds, or `railway.json` config-as-code.
- Changing the nginx config itself.
- Any database or Redis change.

## Decisions

**D1. The app stays at the repo root, and nginx moves to `nginx/`.** The root `Dockerfile` is Django's. Railway auto-detects it for the web and worker services, which have no Root Directory set. The nginx service gets Root Directory `nginx/`, so its build context is only that folder and its `COPY nginx.conf ...` line works unchanged. *Alternative considered:* moving Django into a subfolder. Rejected because it touches settings paths, `.env` loading, coverage config, docs and history for no benefit.

**D2. Single-stage `python:3.13-slim` image.** It matches the local Python 3.13 venv. `psycopg[binary]` ships wheels, so no compiler or system packages are needed. Set `PYTHONUNBUFFERED=1` (logs reach Railway immediately) and `PYTHONDONTWRITEBYTECODE=1` (no `.pyc` writes into the read-only code tree). Install with `pip install --no-cache-dir -r requirements.txt`.

**D3. `collectstatic` runs at build time with throwaway values.** `SECRET_KEY` and `ALLOWED_HOSTS` are required at settings import, so the build step sets them inline on that one `RUN` line (`SECRET_KEY=build-only ALLOWED_HOSTS=localhost python manage.py collectstatic --noinput`). They are not `ENV`, so they don't persist into the image. collectstatic doesn't connect to a database. A missing `.env` only makes django-environ log a warning.

**D4. Non-root user, read-only code, one writable data directory.** Code is copied in owned by root, and the process runs as an unprivileged `app` user. Demo mode writes a SQLite file (default `BASE_DIR/demo.sqlite3`). The image therefore creates `/data` owned by `app` and sets `ENV DEMO_DATABASE_PATH=/data/demo.sqlite3`. That setting only matters in demo mode, and the real deployment ignores it. *Alternative considered:* `chown` the whole app directory to `app`. That's simpler, but it lets the running process modify its own code.

**D5. The image's default command is the web process, and the worker overrides it.** `CMD` uses shell form: `python manage.py migrate --noinput && exec gunicorn --bind 0.0.0.0:8000 --no-control-socket django_scraper.wsgi --error-logfile - --access-logfile -`. The bind address and flags are identical to today. `exec` makes gunicorn PID 1 so it receives Railway's stop signal.
- **Web service:** clear the dashboard start command so the image's `CMD` runs.
- **Worker service:** start command `python manage.py run_huey`. A single command works in exec form, and the worker no longer runs `migrate`, so the two services can't race to migrate on a simultaneous deploy. The web service owns migrations.
- **Demo deployments** (a separate service, if used): start command `/bin/sh -c "python manage.py migrate && python manage.py demo_reset && exec gunicorn --bind 0.0.0.0:8000 --no-control-socket django_scraper.wsgi --error-logfile - --access-logfile -"`. The `collectstatic` step that used to be in the demo start command is no longer needed.

*Alternative considered:* keep both dashboard commands and wrap them in `/bin/sh -c`. That works, but it keeps the start logic in the dashboard rather than the repo.

**D6. Settings: file logging only in debug, and WhiteNoise always on.**
- Define the `file` handler only inside the existing `if DEBUG:` block, where it is attached. Debug logging behavior is unchanged.
- Move `whitenoise.middleware.WhiteNoiseMiddleware` into the base `MIDDLEWARE` list right after `SecurityMiddleware`, and delete the demo-only insert.
- Keep Django's default static storage. Not adopting WhiteNoise's manifest storage keeps the change small and avoids errors when a template references a file that is missing from the manifest.

**D7. A root `.dockerignore` keeps the build context small and safe.** It excludes `.env`, `db.sqlite3*`, `demo.sqlite3*`, `*.log`, `.coverage`, `venv/`, `node_modules/`, `.git/`, `planning/`, `logs/`, `staticfiles/`, `.claude/`, `.cursor/`, `openspec/`, `nginx/`, `__pycache__/`, `bugdata.csv` and `website_logo.png`. `tracking/demo/` data and `fixtures/` are deliberately kept.

**D8. Repoint the existing services; don't replace them.** The nginx service's source changes from `ds-nginx` to this repo (Root Directory `nginx/`, watch path `/nginx/**`), so its public domain and settings stay. The Django web service keeps its name, so `djangoscraper.railway.internal` still resolves. No variables change.

## Risks / Trade-offs

- [Dashboard start command left in place runs in exec form and fails on `&&`] → The runbook explicitly clears the web start command and sets the worker's to the single command. Deploy logs are checked after each service.
- [The nginx resolver uses IPv6 (`[fd12::10]`), and gunicorn binds `0.0.0.0` (IPv4)] → This is the bind that works in production today, and it stays identical. If nginx returns 502 after cutover, check this first. The fix is a dual-stack bind (`--bind [::]:8000`).
- [How production serves static files today is unverified] → Before cutover, note whether `/static/admin/css/base.css` loads through the public domain, then confirm it loads after. WhiteNoise serving it is the target either way.
- [Running the test suite without a `staticfiles/` directory makes WhiteNoise warn that the directory is missing] → This is a harmless warning. Fresh clones don't have the directory until `collectstatic` runs. Tests are unaffected.
- [A needed file is caught by `.dockerignore`] → The local smoke test boots the image and loads pages before production sees it.
- [The live nginx config differs from the untracked copies] → Diff against `ds-nginx` before committing `nginx/`, and commit the live version.
- [Both services redeploy on every push] → Accepted for web and worker. The nginx watch path limits nginx redeploys to `nginx/` changes.

## Migration Plan

Downtime is acceptable, and the database is never modified. The full runbook goes in `docs/deployment_steps.md`. In short:

1. **Prepare:** take a Postgres backup in Railway (or `pg_dump`). Confirm `python manage.py makemigrations --check` reports no changes. Note the current deployment IDs of web, worker and nginx so you can roll back to them.
2. **Verify locally:** `docker build`, then run the container with `DEBUG=False`, a throwaway `SECRET_KEY`, `ALLOWED_HOSTS=localhost` and `DATABASE_URL=sqlite:////data/smoke.sqlite3` (the default SQLite path is in the read-only code tree). Load the login page and an admin static file. Run it once more with `PORT=1234` and confirm it still listens on 8000. Run with `python manage.py run_huey` as the command and confirm it starts. With `HUEY_IMMEDIATE` and no Redis it may refuse to start, which is expected locally, so check that it gets past settings import.
3. **Cut over Django:** merge to the deployed branch. On the web service, set the builder to Dockerfile if it isn't picked up automatically, and clear the start command. On the worker, set the start command to `python manage.py run_huey`. Deploy, then check the web logs (migrate is a no-op, gunicorn is listening on 8000), the worker logs (Huey consumer started), and the site through the existing nginx (login, item list, admin CSS, one manual update).
4. **Cut over nginx:** repoint the nginx service to this repo with Root Directory `nginx/` and watch path `/nginx/**`, deploy, and check the site again.
5. **Clean up:** archive the `ds-nginx` repo once the site has been stable.

**Rollback:** redeploy the previous deployment of the affected service in Railway. If the dashboard start commands were already cleared, also restore the old ones. Or revert the commit. Because the database is never modified, rollback restores the previous state exactly.
