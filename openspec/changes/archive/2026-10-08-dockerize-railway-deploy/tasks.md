## 1. Settings fixes

- [x] 1.1 In `django_scraper/settings.py`, define the `file` log handler only inside the `if DEBUG:` block (D6). Verify: `DEBUG=False SECRET_KEY=x ALLOWED_HOSTS=localhost python manage.py check` run from a read-only copy of the project directory does not create or open `django_debug.log`.
- [x] 1.2 Move `whitenoise.middleware.WhiteNoiseMiddleware` into the base `MIDDLEWARE` list right after `SecurityMiddleware`, and remove the demo-only insert (D6). Verify: a test with `DEBUG=False` and demo mode off requests `/static/admin/css/base.css` after `collectstatic` and gets 200.
- [x] 1.3 Run the full test suite and verify it passes. A WhiteNoise warning about a missing `staticfiles/` directory is acceptable.

## 2. Django image

- [x] 2.1 Add a root `.dockerignore` with the exclusions in D7. Verify: after building (2.3), `docker run --rm <image> ls -a /app` shows no `.env`, `db.sqlite3`, `*.log`, `venv`, `.git` or `nginx`.
- [x] 2.2 Add the root `Dockerfile` per D2–D5: `python:3.13-slim`, the two `PYTHON*` env vars, pip install, build-time `collectstatic` with inline throwaway values, a root-owned code tree, an `app` user, a `/data` directory owned by `app` with `ENV DEMO_DATABASE_PATH=/data/demo.sqlite3`, `EXPOSE 8000`, and a shell-form `CMD` running `migrate` and then `exec gunicorn` on `0.0.0.0:8000`. Verify: `docker build -t django-scraper .` succeeds.
- [x] 2.3 Smoke-test production mode locally: `docker run --rm -p 8000:8000 -e DEBUG=False -e SECRET_KEY=x -e ALLOWED_HOSTS=localhost -e DATABASE_URL=sqlite:////data/smoke.sqlite3 django-scraper` (SQLite must live in `/data`, because the code tree is read-only to the app user). Verify: the container runs as non-root (`docker exec <id> id -u` is not 0), the logs show migrate and then gunicorn listening on 8000, `curl -I localhost:8000/accounts/login/` returns 200, and `curl -I localhost:8000/static/admin/css/base.css` returns 200.
- [x] 2.4 Re-run 2.3 with `-e PORT=1234` added. Verify: gunicorn still listens on 8000.
- [x] 2.5 Run the image with the worker command (`docker run ... django-scraper python manage.py run_huey`). Verify: the command replaces the web process (no gunicorn in the logs) and gets past settings import. A Redis connection error is expected here without Redis.
- [x] 2.6 Smoke-test demo mode: run with `-e DEMO_MODE=True` and the start command `/bin/sh -c "python manage.py migrate && python manage.py demo_reset && exec gunicorn --bind 0.0.0.0:8000 --no-control-socket django_scraper.wsgi"`. Verify: the item list loads at `localhost:8000/` and `demo.sqlite3` is created under `/data`.

## 3. nginx in this repo

- [x] 3.1 Diff the untracked root `Dockerfile` and `nginx.conf` against the live `ds-nginx` repo, and settle on the live version. Verify: the diff is empty, or the differences are understood and the live content is used.
- [x] 3.2 Move both files to `nginx/Dockerfile` and `nginx/nginx.conf` with their content unchanged (do this before 2.2 writes the root `Dockerfile`). Verify: `docker build nginx/` succeeds.

## 4. Docs

- [x] 4.1 Rewrite `docs/deployment_steps.md` as the Docker runbook: the service layout, the per-service Railway settings (builder, start command, Root Directory, watch path), the required env vars (keep the existing list), the cutover steps and rollback from design.md's Migration Plan, `createsuperuser` via `railway ssh`, and an updated demo section with the `/bin/sh -c` start command. Verify: every Railway setting named in design D5/D8 appears in the doc.
- [x] 4.2 Update the README "Production deployment" section to point to `docs/deployment_steps.md` for the Railway/Docker flow, and correct the line saying nginx serves `staticfiles/`. Verify: the README no longer says nginx serves static files.

## 5. Production cutover (manual, operator)

- [x] 5.1 Take a Postgres backup and note the current web, worker and nginx deployment IDs. Verify: the backup exists in Railway, or a `pg_dump` file exists locally.
- [x] 5.2 Confirm `python manage.py makemigrations --check` reports no changes. Note whether `/static/admin/css/base.css` loads on the public domain today.
- [x] 5.3 Merge to the deployed branch. On **both** the web and worker services, set the builder to Dockerfile. On the web service, clear the start command. On the worker, set the start command to `python manage.py run_huey`. Verify: both services' build logs show a Dockerfile build, web logs show migrate as a no-op and gunicorn on 8000, and worker logs show the Huey consumer started.
- [x] 5.4 Check through the existing (still `ds-nginx`) nginx: login works, the item list loads, admin CSS loads, and a manual "Update Selected" run completes via the worker. Verify: all four pass, or roll back per the runbook.
- [x] 5.5 Repoint the nginx service to this repo: set Root Directory to `/nginx` in Settings → Source, directly under the GitHub repo (not via `RAILWAY_DOCKERFILE_PATH`), leave Dockerfile Path empty, set watch path `/nginx/**`, then deploy. Check the build log shows `FROM nginx:alpine`. Verify: the same four checks pass on the public domain.
- [x] 5.6 After a stable period, archive the `ds-nginx` repo on GitHub and remove the "ds-nginx" references from the docs. Verify: `grep -rn ds-nginx docs README.md` returns nothing.
