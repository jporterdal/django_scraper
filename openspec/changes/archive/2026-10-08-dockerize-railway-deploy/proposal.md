## Why

The live Railway deployment is spread across two repos and builds that are set up by hand. The Django web and worker services are built by Railway's auto-detected builder with start commands typed into the dashboard. The nginx proxy is built from a separate `ds-nginx` repo. Moving to Dockerfile builds from this one repo makes the build reproducible and testable locally, and keeps all deployment config in one place. It has to happen without touching the existing Postgres data.

## What Changes

- Add a root `Dockerfile` for the Django app. It installs `requirements.txt`, runs `collectstatic` at build time, runs as a non-root user, and serves gunicorn on a fixed port 8000 (the port the nginx proxy already targets over Railway's private network).
- Add a root `.dockerignore` so secrets, local databases, logs, the virtualenv and other local-only files can never be built into the image.
- Move the nginx proxy's `Dockerfile` and `nginx.conf` into `nginx/` in this repo, unchanged from what is live in `ds-nginx`.
- Settings fixes that the container needs:
  - The debug log file handler is created only when `DEBUG` is on. Today it opens `django_debug.log` at startup in every mode, which fails in a container where the app directory isn't writable.
  - WhiteNoise serves static files in every mode, not only in demo mode. nginx is a separate service and can't read the web container's `staticfiles/`.
- Cutover on Railway, reusing all existing services (no new services, no new domain):
  - Switch the Django web and worker services to the Dockerfile build. The web service drops its dashboard start command and uses the image's command. The worker's start command becomes `python manage.py run_huey`.
  - Repoint the existing nginx service from the `ds-nginx` repo to this repo, with Root Directory `nginx/`. The public domain stays on nginx.
  - Archive the `ds-nginx` repo afterwards.
- Rewrite `docs/deployment_steps.md` as a Docker-based runbook: backup, local smoke test, staged cutover, verification and rollback.

Postgres and Redis are untouched, and there are no migrations.

## Capabilities

### New Capabilities
- `container-deployment`: what the deployable Django image guarantees. It contains no secrets or local data, boots as a non-root user with `DEBUG` off, serves its own static files, and listens on the fixed internal port the proxy expects.

### Modified Capabilities
None. The `demo-mode` spec already requires no debug log file in demo mode. This change extends that to every non-debug deployment, which is consistent with it.

## Impact

- **New files**: `Dockerfile`, `.dockerignore`, `nginx/Dockerfile`, `nginx/nginx.conf`.
- **Code**: `django_scraper/settings.py` (logging handler setup, WhiteNoise middleware placement).
- **Docs**: `docs/deployment_steps.md` rewritten. The README's deployment pointers are updated if they reference the old flow.
- **Railway**: build method and start commands change on the web and worker services, and the nginx service's source repo and root directory change. Environment variables, Postgres, Redis, the private service name `djangoscraper` and the public domain stay as they are.
- **Dependencies**: none new (`gunicorn` and `whitenoise` are already in `requirements.txt`).
- **Downtime**: expected during the cutover, and accepted.
