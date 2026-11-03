# Bookshelf - A Book library app

A self-hosted web app to manage a book library, built with Flask, HTMX and Firebird.
Requirements and decisions are in [docs/bookshelf.md](docs/bookshelf.md).

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- Docker (development database, tests, image builds)

## Development setup

```sh
uv sync
```

Start a local Firebird 3 server for the dev server. `dev.env` already points to it:

```sh
docker run -d --name bookshelf-firebird \
  -p 127.0.0.1:3050:3050 \
  -e FIREBIRD_ROOT_PASSWORD=masterkey \
  -e FIREBIRD_DATABASE=bookshelf.fdb \
  -e FIREBIRD_DATABASE_DEFAULT_CHARSET=UTF8 \
  -e FIREBIRD_USER=bookshelf \
  -e FIREBIRD_PASSWORD=bookshelf \
  -v bookshelf-firebird-data:/var/lib/firebird/data \
  firebirdsql/firebird:3.0.14
```

Start the dev server:

```sh
uv run dotenv -f dev.env run flask --app bookshelf run --debug
```

The app is at <http://localhost:5000>. The passwords in `dev.env` and above are for local use only.

## First run

While the database has no tables or no admin, every page redirects to a setup wizard at
`/setup`, which creates the tables and the first admin and logs that admin in. Once an admin
exists, `/setup` returns 404. The wizard is open to whoever reaches it first: finish it before
the app is reachable by anyone else, or create the admin from the CLI instead:

```sh
uv run dotenv -f dev.env run flask --app bookshelf init-db
uv run dotenv -f dev.env run flask --app bookshelf create-admin admin@example.com
```

The wizard creates the tables with the app's own database user (`APP_DB_USER`), so that user
must be allowed to create them (e.g. the database owner); otherwise run `init-db` first.

## Configuration

All settings come from environment variables:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `APP_SECRET_KEY` | yes | — | Signs session and login cookies |
| `APP_DB_DSN` | yes | — | Firebird database, e.g. `host:/var/lib/firebird/data/bookshelf.fdb` |
| `APP_DB_USER` | yes | — | Firebird user |
| `APP_DB_PASSWORD` | yes | — | Firebird password |
| `APP_COOKIE_SECURE` | no | `true` | Set to `false` only for local HTTP |
| `APP_LOGIN_MAX_FAILURES` | no | `5` | Failed logins for an email from one IP before that IP's logins for it are refused |
| `APP_LOGIN_MAX_IP_FAILURES` | no | `20` | Failed logins from one IP, across all emails, before that IP's logins are refused |
| `APP_LOGIN_BLOCK_MINUTES` | no | `15` | Window in which failed logins are counted, and so how long logins stay refused |
| `APP_LOG_LEVEL` | no | `debug` | Log level |
| `APP_HOST` / `APP_PORT` | no | `localhost` / `8000` | Bind address (Docker image: `0.0.0.0` / `8080`) |
| `APP_DB_POOL_SIZE` | no | `5` | Idle database connections kept for reuse, per process |
| `APP_TRUSTED_PROXIES` | no | `0` | Reverse proxies in front of the app; set it (usually `1`) behind a proxy so client IP and HTTPS come from `X-Forwarded-*`. Required behind a proxy: login limits are per IP. Leave `0` when exposed directly, or clients can fake their IP |
| `APP_WORKERS` | no | 2 × CPUs + 1 | gunicorn worker processes; set it in containers with a CPU limit |
| `APP_TIMEOUT` | no | `30` | Seconds before gunicorn restarts a worker stuck on a request |

## User management (CLI)

Run with `uv run dotenv -f dev.env run flask --app bookshelf <command>`, or with the
production environment set. Passwords are always prompted, never passed as arguments.

| Command | What it does |
|---|---|
| `init-db` | Create missing tables, columns, constraints and indexes (safe to rerun) |
| `prune-login-failures` | Delete failed logins older than `APP_LOGIN_BLOCK_MINUTES` (run periodically, e.g. hourly from cron) |
| `create-admin EMAIL` | Create the first admin (refused once any admin exists) |
| `create-user EMAIL` | Create a regular user |
| `list-users` | Show all users with role and lock status |
| `reset-user-password EMAIL` | Set a new password for a user |
| `promote-user EMAIL` / `demote-user EMAIL` | Give or remove the admin role |
| `lock-user EMAIL` / `unlock-user EMAIL` | Block or allow a user's login; locking ends open sessions |
| `delete-user EMAIL [--yes]` | Permanently delete a user (asks for confirmation) |

All commands except `init-db`, `prune-login-failures` and `create-admin` require admin credentials
(`--admin-email`, or prompted, plus the admin password). Admins cannot lock, demote or
delete their own account. `create-admin` and `create-user` only accept valid email
addresses (no reserved or LAN-style domains such as `.local`), the same rule as the login form.

## Tests and checks

```sh
uv run tox -e py3                     # tests (starts Firebird via testcontainers: Docker must be running)
uv run tox -e lint                    # ruff check
uv run tox -e format                  # ruff format (use `-- --check .` to only check)
uv run tox -e type                    # ty
uv run tox -e audit                   # pip-audit
```

## Docker image

```sh
docker build -t bookshelf .
docker run -p 8080:8080 \
  -e APP_SECRET_KEY=... -e APP_DB_DSN=... -e APP_DB_USER=... -e APP_DB_PASSWORD=... \
  bookshelf
```

The image is built from source in three stages (dependencies, wheel, release) and runs
gunicorn as a non-root user, configured by `gunicorn.conf.py`. All logs, gunicorn's
included, are written to stderr as JSON lines. Serve it over HTTPS: cookies are `Secure` by default.
Behind a reverse proxy that terminates HTTPS, set `APP_TRUSTED_PROXIES` to the number of proxies
(usually `1`) so the app sees the client IP and the request as HTTPS. This is required, not
optional: failed-login limits are per client IP, so without it every client shares the proxy's IP
and 20 failed logins from anyone block logins for everyone.

Schedule `prune-login-failures` on the host, or the failed-login table only grows. For example, hourly
from cron:

```sh
0 * * * * docker exec <container> /app/venv/bin/flask --app bookshelf prune-login-failures
```

After upgrading, run `init-db` again: it adds any new tables, columns, constraints and indexes to an existing database.
New check constraints apply to rows written from then on; Firebird does not recheck existing rows.

## Schema changes

The schema lives in `bookshelf/db/schema.py`, and `init-db` creates whatever is missing, in this order:
`DOMAINS`, `TABLES`, `COLUMNS`, `CONSTRAINTS`, `INDEXES`.

- A new table goes in `TABLES`.
- A change to an existing table goes in `COLUMNS` (`ALTER TABLE … ADD`), `CONSTRAINTS` or `INDEXES`. Never
  edit a released `TABLES` entry: databases that already have the table would not get the change. Each
  column or constraint is defined in one place only, so new and existing databases get it through the
  same statement; a test fails if it also appears in `CREATE TABLE`.
- `init-db` only adds. Renaming, retyping or dropping something needs a hand-written step.

## CI and releases

GitHub Actions run lint, format check, type check, audit and tests on every push to
`main` and on pull requests, and build the image. Pushes to `main` publish
`ghcr.io/<owner>/<repo>:latest`.

To release a version:

```sh
uv version 1.2.3                      # must match the tag, CI checks it
git commit -am "Release 1.2.3"
git tag v1.2.3
git push origin main v1.2.3
```

This publishes the image tags `1.2.3`, `1.2` and `1`.
