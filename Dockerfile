FROM python:3.13-slim

# Logs reach Railway immediately; no .pyc writes into the read-only code tree.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code stays root-owned (read-only to the app user).
COPY . .

# SECRET_KEY and ALLOWED_HOSTS are required at settings import; these
# throwaway values exist only for this build step and don't persist.
# DEBUG=False keeps the debug log file out of the image.
RUN DEBUG=False SECRET_KEY=build-only ALLOWED_HOSTS=localhost python manage.py collectstatic --noinput

# Unprivileged runtime user. /data is the only writable path: demo mode keeps
# its SQLite file there (the real deployment uses Postgres and ignores it).
RUN useradd --system --no-create-home app \
    && mkdir /data && chown app:app /data
ENV DEMO_DATABASE_PATH=/data/demo.sqlite3
USER app

# Fixed port: nginx proxies to djangoscraper.railway.internal:8000, so ignore
# any Railway-injected PORT. The worker overrides this with its own start
# command (python manage.py run_huey).
EXPOSE 8000
CMD ["/bin/sh", "-c", "python manage.py migrate --noinput && exec gunicorn --bind 0.0.0.0:8000 --no-control-socket django_scraper.wsgi --error-logfile - --access-logfile -"]
