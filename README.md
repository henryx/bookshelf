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

Start a local Firebird 5 server for the dev server. `dev.env` already points to it:

```sh
docker run -d --name bookshelf-firebird \
  -p 127.0.0.1:3050:3050 \
  -e FIREBIRD_ROOT_PASSWORD=masterkey \
  -e FIREBIRD_DATABASE=bookshelf.fdb \
  -e FIREBIRD_DATABASE_DEFAULT_CHARSET=UTF8 \
  -e FIREBIRD_USER=bookshelf \
  -e FIREBIRD_PASSWORD=bookshelf \
  -v bookshelf-firebird-data:/var/lib/firebird/data \
  firebirdsql/firebird:5.0.4
```

Create the tables and the first admin, then start the dev server:

```sh
uv run dotenv -f dev.env run flask --app bookshelf init-db
uv run dotenv -f dev.env run flask --app bookshelf create-admin admin@example.com
uv run dotenv -f dev.env run flask --app bookshelf run --debug
```

The app is at <http://localhost:5000>. The passwords in `dev.env` and above are for local use only.

## Configuration

All settings come from environment variables:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `APP_SECRET_KEY` | yes | — | Signs session and login cookies |
| `APP_DB_DSN` | yes | — | Firebird database, e.g. `host:/var/lib/firebird/data/bookshelf.fdb` |
| `APP_DB_USER` | yes | — | Firebird user |
| `APP_DB_PASSWORD` | yes | — | Firebird password |
| `APP_COOKIE_SECURE` | no | `true` | Set to `false` only for local HTTP |
| `APP_LOG_LEVEL` | no | `debug` | Log level |
| `APP_HOST` / `APP_PORT` | no | `localhost` / `8000` | Bind address (Docker image: `0.0.0.0` / `8080`) |

## User management (CLI)

Run with `uv run dotenv -f dev.env run flask --app bookshelf <command>`, or with the
production environment set. Passwords are always prompted, never passed as arguments.

| Command | What it does |
|---|---|
| `init-db` | Create missing tables (safe to rerun) |
| `create-admin EMAIL` | Create an admin user |
| `list-users` | Show all users with role and lock status |
| `reset-user-password EMAIL` | Set a new password for a user |
| `lock-user EMAIL` / `unlock-user EMAIL` | Block or allow a user's login; locking ends open sessions |
| `delete-user EMAIL [--yes]` | Permanently delete a user (asks for confirmation) |

All commands except `init-db` and `create-admin` require admin credentials
(`--admin-email`, or prompted, plus the admin password). Admins cannot lock or delete
their own account.

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
gunicorn as a non-root user. Serve it over HTTPS: cookies are `Secure` by default.

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
