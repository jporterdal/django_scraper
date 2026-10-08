# Deployment steps
The live deployment runs on Railway, built from the Dockerfiles in this repo. The images are ordinary Docker images, so the same setup works on any host that runs containers, private networking aside.

## Services
One Railway project, one repo:

```
public domain ──▶ nginx ──▶ djangoscraper.railway.internal:8000 (web) ──▶ Postgres
                                                                    └───▶ Redis ◀── worker
```

| Service | Source | Builds from | Start command | Public domain |
|---------|--------|-------------|---------------|---------------|
| nginx | this repo, Root Directory `nginx/`, watch path `/nginx/**` | `nginx/Dockerfile` | none (image default) | yes |
| web (`djangoscraper`) | this repo | root `Dockerfile` | **none** (image default: `migrate`, then gunicorn on 8000) | no |
| worker | this repo | root `Dockerfile` | `python manage.py run_huey` | no |
| Postgres, Redis | Railway databases | — | — | no |

- **The web service name matters.** nginx proxies to `djangoscraper.railway.internal:8000` (`nginx/nginx.conf`), so renaming the web service breaks the proxy.
- **The port is fixed at 8000.** The web image ignores Railway's `PORT` variable.
- **Start commands run in exec form.** With a Dockerfile build, a dashboard start command replaces the image's command and runs without a shell, so `&&` and `$VARS` don't work in it. Leave the web start command empty. If you need more than one command, wrap them: `/bin/sh -c "first && exec second"`.
- **Static files** are collected at build time and served by gunicorn (WhiteNoise). nginx proxies `/static/` like any other path.
- **Migrations** run when the web service boots. The worker doesn't run them, so the two can't race each other.

## Environment variables
Set these in Railway on **both** the web and worker services:
```
DEBUG="False"
CSRF_COOKIE_SECURE="True"
CSRF_TRUSTED_ORIGINS=some_url_here
SECRET_KEY=valid_secret_key
ALLOWED_HOSTS="localhost,some_url_here"
SECURE_DEPLOYMENT="False"
SCRAPE_REQUEST_DELAY_SECONDS="3.0"
SCRAPE_REQUEST_DELAY_JITTER_SECONDS="1.0"
SCRAPE_REQUEST_TIMEOUT_SECONDS="30"
DATABASE_URL="${{Postgres.DATABASE_URL}}"
REDIS_URL="${{Redis.REDIS_URL}}"
SESSION_COOKIE_SECURE="True"
```

Make sure `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` use the public (nginx) domain. Never put a `.env` file in the image: `.dockerignore` excludes it, and every setting comes from Railway variables.

## Build and test locally
```
docker build -t django-scraper .
docker run --rm -p 8000:8000 -e DEBUG=False -e SECRET_KEY=x -e ALLOWED_HOSTS=localhost \
    -e DATABASE_URL=sqlite:////data/smoke.sqlite3 django-scraper
```
Then check that `curl -I localhost:8000/accounts/login/` and `curl -I localhost:8000/static/admin/css/base.css` both return 200. Keep SQLite in `/data`: the app runs as a non-root user and can't write anywhere else.

Worker from the same image: `docker run --rm ... django-scraper python manage.py run_huey`.

nginx: `docker build nginx/`.

## Cutover from the old setup (one-time)
Before this change, web and worker were built by Railway's Python builder with dashboard start commands, and nginx was built from the separate `ds-nginx` repo. Downtime is acceptable. Postgres and Redis are never modified.

1. **Back up.** Take a Postgres backup in Railway (or `pg_dump "$DATABASE_URL" > backup.sql`). Note the current deployment IDs of web, worker and nginx so you can roll back.
2. **Pre-checks.** `python manage.py makemigrations --check` should report no changes. Note whether `/static/admin/css/base.css` loads on the public domain today.
3. **Cut over web and worker.** Merge to the deployed branch. On the **web** service, make sure the builder is Dockerfile and **clear the start command**. On the **worker**, set the start command to `python manage.py run_huey`. Deploy both.
   - Web logs: "No migrations to apply.", then gunicorn listening at `0.0.0.0:8000`.
   - Worker logs: "Huey consumer started".
4. **Verify** through the public domain (nginx still on `ds-nginx`): login works, the item list loads, admin pages are styled, and a manual "Update Selected" run completes.
5. **Cut over nginx.** In the nginx service, change the source repo from `ds-nginx` to this repo, set Root Directory `nginx/` and watch path `/nginx/**`, and deploy. Repeat the checks from step 4.
6. **Clean up.** Once it has been stable for a while, archive the `ds-nginx` repo on GitHub.

If nginx returns 502 after step 3, check that the web service is still named `djangoscraper` and is listening on 8000. If both are fine, the private network may be IPv6-only. Then set the web start command to `/bin/sh -c "python manage.py migrate --noinput && exec gunicorn --bind [::]:8000 --no-control-socket django_scraper.wsgi --error-logfile - --access-logfile -"`.

## Rollback
The database isn't modified, so a rollback restores the previous state exactly:
- In Railway, redeploy the service's previous deployment. If you are going back to the pre-Docker build, also restore the old start commands: `python manage.py migrate && gunicorn --bind 0.0.0.0:8000 django_scraper.wsgi --error-logfile - --access-logfile -` (web) and `python manage.py migrate && python manage.py run_huey` (worker).
- Or revert the commit and redeploy.
- nginx: point the service back at the `ds-nginx` repo (until it's archived).

## Create superuser
Use the Railway CLI (https://docs.railway.com/cli) to open a shell on the web service and run:
```
python manage.py createsuperuser
```
Use `railway ssh` (see https://station.railway.com/questions/how-do-you-create-a-superuser-for-django-28a85dea). You may need to rename `sh` to `bash` in the downloaded install scripts to avoid "bad substitution" errors.

## Demo deployment
A public demo (see "Demo mode" in `README.md`) is a **separate** deployment from the real one: its own web service built from the same root `Dockerfile`, behind its own nginx, with its own data. Don't add Postgres, Redis or a worker.

Set these variables on the demo web service:
```
DEMO_MODE="True"
SECRET_KEY=valid_secret_key
ALLOWED_HOSTS="localhost,some_demo_url_here"
CSRF_TRUSTED_ORIGINS=https://some_demo_url_here
SECURE_DEPLOYMENT="True"
```

`DEBUG`, `DATABASE_URL`, `REDIS_URL`, `HUEY_IMMEDIATE` and the scrape delay settings are forced or ignored by demo mode, so leave them unset. The optional `DEMO_*` settings in `README.md` tune reset timing and visitor caps. The image already sets `DEMO_DATABASE_PATH=/data/demo.sqlite3`, the only writable location.

Start command:
```
/bin/sh -c "python manage.py migrate && python manage.py demo_reset && exec gunicorn --bind 0.0.0.0:8000 --no-control-socket django_scraper.wsgi --error-logfile - --access-logfile -"
```

`demo_reset` restores the seed data on every boot, and refuses to run unless `DEMO_MODE` is on, so it can't wipe a real deployment. The demo database lives on the container's own disk, so a restart or redeploy starts it fresh.

No `createsuperuser` step: the demo has no admin and no login.

Rollback: delete the demo service. Nothing in the real deployment changes.
