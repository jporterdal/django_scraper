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
| nginx | this repo, Root Directory `/nginx`, watch path `/nginx/**` | `nginx/Dockerfile` | none (image default) | yes |
| web (`djangoscraper`) | this repo | root `Dockerfile` | **none** (image default: `migrate`, then gunicorn on 8000) | no |
| worker | this repo | root `Dockerfile` | `python manage.py run_huey` | no |
| Postgres, Redis | Railway databases | — | — | no |

- **nginx's Root Directory is a service setting, not a variable.** In the nginx service's Settings, it sits in the **Source** section, directly under the connected GitHub repo, not with the Dockerfile settings in the Build section. Set it to `/nginx`, and leave the Dockerfile Path setting empty. Don't use `RAILWAY_DOCKERFILE_PATH` for this: it only picks a Dockerfile *file* and doesn't restrict the build to `nginx/`. With it, or with Dockerfile Path `/Dockerfile`, the nginx service builds the root (Django) Dockerfile and crashes on boot without `SECRET_KEY`. The build log should show `FROM nginx:alpine`.
- **Web and worker both use the Dockerfile builder** (Settings → Build). Check that both build logs show `FROM python:3.13-slim`. A worker left on Railway's Python builder would still run `run_huey`, so the mismatch would go unnoticed.
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

## Checking a deploy
- Web logs: "No migrations to apply." (or the migrations being applied), then gunicorn listening at `0.0.0.0:8000`.
- Worker logs: "Huey consumer started".
- Through the public domain: login works, the item list loads, admin pages are styled, and a manual "Update Selected" run completes.

If nginx returns 502, check that the web service is still named `djangoscraper` and is listening on 8000. If both are fine, the private network may be IPv6-only. Then set the web start command to `/bin/sh -c "python manage.py migrate --noinput && exec gunicorn --bind [::]:8000 --no-control-socket django_scraper.wsgi --error-logfile - --access-logfile -"`.

## Rollback
- In Railway, redeploy the service's previous deployment, or revert the commit and redeploy.
- A rollback doesn't undo migrations that the newer deploy already applied. Before deploying a change with migrations, take a Postgres backup in Railway (or `pg_dump "$DATABASE_URL" > backup.sql`).

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
