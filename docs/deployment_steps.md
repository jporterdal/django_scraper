# Deployment steps
Examples will assume Railway but instructions should ideally be given to work generically on any similar host.

## Link Github repo + Procfile
Link Github repo for optional automatic deployment from PR.

Procfile is optional but Railway dashboard is "preferred" for adding custom start command (see `gunicorn` below). Binding to 8000 is necessary since Railway (or gunicorn on Railway) default seems to be to bind to 8080.

```
python manage.py migrate && gunicorn --bind 0.0.0.0:8000 django_scraper.wsgi --error-logfile - --access-logfile -
python manage.py migrate && python manage.py run_huey
```


## Update .ENV variables
Either editing `.env` raw or through web interface, these keys need valid values:
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

Ensure that `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` both use valid URLs if given by Railway. Ensure that the same ENV variables are avaialble to **both** `web` and `worker` services, responsible for `gunicorn` and `run_huey` processes, respectively.


## Redis
Install and start redis
```
sudo apt install redis-server
sudo systemctl status redis-server
sudo systemctl start redis-server
```

## Postgres
Install and start postgresql
```
sudo apt install postgresql libpq-dev
sudo systemctl status postgresql
sudo systemctl start postgresql
```

## Huey
Run Huey as separate process
```
python manage.py run_huey
```

## Create superuser
Create user for login, assign as superuser in Django
```
python manage.py createsuperuser
```

## Web server
Install and set up nginx:
```
sudo apt install nginx
```

Can (should?) also use the Dockerfile repo which is set up to work with Railway:
```
git clone https://github.com/jporterdal/ds-nginx.git
```

Point `nginx` to gunicorn via Railway's internal address.


## WSGI server
Ensure gunicorn is in `requirements.txt` and run WSGI project with:
```
gunicorn django_scraper.wsgi --error-logfile - --access-logfile -
```
This should be entered as custom start-up command in Railway dashboard **or** Procfile.


## Railway CLI
It will likely be necessary to install Railway CLI in order to run createsuperuser:
https://docs.railway.com/cli

It may be necessary to rename all the instances of `sh` in the downloaded scripts to `bash` in order to avoid "bad substitution" errors when trying to run.

Can then run an SSH command to connect and run the necessary shell commands on the Railway container:
https://station.railway.com/questions/how-do-you-create-a-superuser-for-django-28a85dea


## Demo deployment
A public demo (see "Demo mode" in `README.md`) is a **separate** deployment from the real one: its own service, its own data, and a single extra variable. It needs only the web service behind the nginx proxy. Don't add Postgres, Redis or a Huey worker.

Set these variables on the demo web service:
```
DEMO_MODE="True"
SECRET_KEY=valid_secret_key
ALLOWED_HOSTS="localhost,some_demo_url_here"
CSRF_TRUSTED_ORIGINS=https://some_demo_url_here
SECURE_DEPLOYMENT="True"
```

`DEBUG`, `DATABASE_URL`, `REDIS_URL`, `HUEY_IMMEDIATE` and the scrape delay settings are forced or ignored by demo mode, so leave them unset. The optional `DEMO_*` settings in `README.md` tune reset timing and visitor caps.

Start command (Railway dashboard or Procfile):
```
python manage.py migrate && python manage.py collectstatic --noinput && python manage.py demo_reset && gunicorn --bind 0.0.0.0:8000 django_scraper.wsgi --error-logfile - --access-logfile -
```

`demo_reset` restores the seed data on every boot, and refuses to run unless `DEMO_MODE` is on, so it can't wipe a real deployment. The demo database is a SQLite file on the container's own disk, and a restart or redeploy starts it fresh.

nginx: proxy `/static/` to gunicorn like every other path. nginx runs as a separate service and can't read the web container's `staticfiles/` directory, so in demo mode gunicorn serves static files itself (WhiteNoise). nginx can still cache them.

No `createsuperuser` step: the demo has no admin and no login.

Rollback: delete the demo service. Nothing in the real deployment changes.
