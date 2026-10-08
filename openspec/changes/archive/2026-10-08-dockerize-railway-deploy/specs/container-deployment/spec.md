## Purpose

Defines what the deployable application image guarantees: it carries no secrets or local data, boots safely as an unprivileged user in production mode, serves its own static files, and listens where the reverse proxy expects it.

## ADDED Requirements

### Requirement: The image contains no secrets or local data
The application image SHALL NOT contain the local environment file, any local database file, log files, coverage data, the local virtualenv, version-control metadata, or local planning and tooling folders. All runtime configuration, including secrets, SHALL come from environment variables provided by the host.

#### Scenario: Local secrets and data stay out of the image
- **WHEN** the image is built from a working copy that contains `.env`, `db.sqlite3`, `django_debug.log` and `venv/`
- **THEN** none of those paths exist inside the built image

#### Scenario: Configuration comes from the environment
- **WHEN** the container starts with `SECRET_KEY`, `ALLOWED_HOSTS` and `DATABASE_URL` set as environment variables and no `.env` file present
- **THEN** the application starts using those values

### Requirement: The container runs as an unprivileged user
The application process SHALL run as a non-root user. With `DEBUG` off, startup SHALL NOT need to write to the application directory.

#### Scenario: Production-mode boot as non-root
- **WHEN** the container starts as its default non-root user with `DEBUG=False`
- **THEN** the application starts and serves requests without any permission error

#### Scenario: No debug log file outside debug mode
- **WHEN** the application starts with `DEBUG=False`
- **THEN** no debug log file is created or opened, and application logs go to the console

### Requirement: The application serves its own static files
In every mode, including when `DEBUG` is off and demo mode is off, the application SHALL serve its collected static files itself, so a reverse proxy running as a separate service can forward static requests like any other path. Static files SHALL be collected when the image is built, not at container start.

#### Scenario: Static asset reachable through the app in production mode
- **WHEN** a client requests a known static asset (for example the admin stylesheet) from a container running with `DEBUG=False` and demo mode off
- **THEN** the application responds with the asset and a success status

### Requirement: The web process listens on the proxy's fixed port
By default the container SHALL apply pending database migrations and then serve HTTP on port 8000 on all interfaces, which is the port the reverse proxy targets over the private network. The listening port SHALL NOT depend on any host-injected port variable. A host-provided start command MAY replace the default, which is how the background worker runs from the same image.

#### Scenario: Default command serves the web app on 8000
- **WHEN** the container starts with no start-command override
- **THEN** migrations are applied and the web app accepts HTTP connections on port 8000

#### Scenario: Host-injected port is ignored
- **WHEN** the container starts with a `PORT` environment variable set to a value other than 8000
- **THEN** the web app still listens on port 8000

#### Scenario: Worker from the same image
- **WHEN** the container starts with the start command `python manage.py run_huey`
- **THEN** the background worker runs and no web server is started
