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

`--debug` is the only way to turn on Flask's debug mode and its interactive debugger, which runs any code
typed into it: never use it where others can reach the app. `python -m bookshelf.wsgi` and the Docker
image never run in debug mode, whatever `APP_LOG_LEVEL` is.

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
| `APP_MIN_PASSWORD_LENGTH` | no | `12` | Characters a new password needs; values below 12 stop the app from starting |
| `APP_LOG_LEVEL` | no | `debug` | Log level (does not turn on Flask's debug mode) |
| `APP_HOST` / `APP_PORT` | no | `localhost` / `8000` | Bind address (Docker image: `0.0.0.0` / `8080`) |
| `APP_DB_POOL_SIZE` | no | `5` | Idle database connections kept for reuse, per process |
| `APP_TRUSTED_PROXIES` | no | `0` | Reverse proxies in front of the app; set it (usually `1`) behind a proxy so client IP and HTTPS come from `X-Forwarded-*`. Required behind a proxy: login limits are per IP. Leave `0` when exposed directly, or clients can fake their IP |
| `APP_WORKERS` | no | 2 × CPUs + 1 | gunicorn worker processes; set it in containers with a CPU limit |
| `APP_TIMEOUT` | no | `30` | Seconds before gunicorn restarts a worker stuck on a request |

## Books

The main page is the book list: one search box over title, author and ISBN, sorting by title, author or date
added, in pages of 50. The round "+" button at the bottom right (here and in your collection) opens the
"Add book" dialog. Authors are rows of name and surname, and categories rows of one field (a book can have
several); in both "+" adds a row and "−" removes one. The author, category and publisher fields suggest existing
entries as you type, and picking an author fills both name and surname. The book is added to the shared catalogue
and to your own collection.

Clicking a book's title opens it in a dialog to edit every field but the title; the ISBN can be set while the
book has none, and only admins can change or remove one already set. Admins can also delete a book from that
dialog, once it is in nobody's collection. A book can belong to a series, with its volume in it, and has a plot.
"Your collection" is the first page, shown after login and by the "Bookshelf" title: it lists your own books, with
their copies (changed in each book's dialog) and a "Read" checkbox (books start as not read). The catalogue is "Catalogue" in the account menu.
"Your account" (in the account menu) also exports your collection to a CSV file ("Export CSV") and imports a CSV
file into it ("Import"): each row is matched to the catalogue by ISBN, or by title and authors without one, and
added to the catalogue when it is not there; books already in your collection are skipped, and rows that cannot be
imported are listed.

"Authors" (in the account menu) lists every author with their number of books. Renaming an author changes them
on every book; renaming to a name another author already has merges the two. An author's name, on that page or
under any book, opens their books: in the catalogue (`/catalogue?author=<id>`), or in your collection
(`/?author=<id>`) when you are browsing it. Details and decisions are in
[docs/bookshelf.md](docs/bookshelf.md).

## User management

Admins manage users in the web app: the "Admin" link in the account menu (top right) opens the users list.
"Create user" opens a dialog to add a regular user, and clicking a user's email opens a dialog to change their
role, lock state or password, or to delete them. The same operations are available from the CLI. Deleting or
demoting an admin is refused when no other admin would remain.

### CLI

Run with `uv run dotenv -f dev.env run flask --app bookshelf <command>`, or with the
production environment set. Passwords are always prompted, never passed as arguments.
New passwords (here and in the setup wizard) must be at least `APP_MIN_PASSWORD_LENGTH`
characters long (12 by default).

| Command | What it does |
|---|---|
| `init-db` | Create missing tables, columns, constraints and indexes, and create or replace the functions (safe to rerun) |
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
delete their own account, and demoting or deleting the only admin left is refused.
`create-admin` and `create-user` only accept valid email addresses (no reserved or LAN-style
domains such as `.local`), the same rule as the login form.

## Tests and checks

```sh
uv run tox -e py3                     # tests (starts Firebird via testcontainers: Docker must be running)
uv run tox -e e2e                     # browser tests of app.js (Playwright; installs Chromium, Docker too)
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

The app sets its own security headers (a strict `Content-Security-Policy`, `X-Frame-Options: DENY`,
`X-Content-Type-Options` and `Referrer-Policy`). `Strict-Transport-Security` is left to the proxy that
terminates HTTPS: set it there, e.g. `Strict-Transport-Security: max-age=31536000`.

Schedule `prune-login-failures` on the host, or the failed-login table only grows. For example, hourly
from cron:

```sh
0 * * * * docker exec <container> /app/venv/bin/flask --app bookshelf prune-login-failures
```

After upgrading, run `init-db` again: it adds any new tables, columns, constraints and indexes to an existing
database, and replaces the functions (such as the book search) with the current ones.
New check constraints apply to rows written from then on; Firebird does not recheck existing rows.

## Schema changes

The schema lives in `bookshelf/db/schema.py`: `DOMAINS`, `TABLES`, `CONSTRAINTS`, `INDEXES` and `FUNCTIONS`,
created in that order. `init-db` creates whatever is missing and is safe to rerun, but it does not change objects
that already exist, except functions: each is `CREATE OR ALTER`ed on every run, so a change to one (e.g.
`BOOK_MATCHES`, the book lists' search) reaches existing databases with no upgrade step.

- `TABLES` holds only columns, with their types, `NOT NULL` and defaults. Every table constraint (primary key,
  unique, check, foreign key) is a named `ALTER TABLE … ADD CONSTRAINT` in `CONSTRAINTS`, never written inside
  `CREATE TABLE`; a test fails if one is. Constraints run in order, so a foreign key comes after the key it
  references.

- Until the first release is tagged, the schema is not final: change a table by editing its `CREATE TABLE`
  in `TABLES` directly, and recreate your development database (drop it, then run `init-db` or the setup
  wizard). Tests always start from an empty database. With the Docker container from "Development setup",
  delete the container and its volume, then start it again with the same `docker run` command:

  ```sh
  docker rm -f bookshelf-firebird
  docker volume rm bookshelf-firebird-data
  ```
- Once a release is tagged, released tables must not be edited in place: changes to them will need upgrade
  steps that `init-db` applies to existing databases. That mechanism will be added with the first change that
  needs it.

## CI and releases

GitHub Actions (`tests.yml`) run lint, format check, type check, audit, tests and browser tests on
every push to `main`, on version tags and on pull requests. Only once all of them pass is the image
built (`docker.yml`, called by `tests.yml`): pushes to `main` publish `quay.io/enricobianchi/bookshelf:latest`,
version tags their version tags, and pull requests just build it. A commit or tag whose checks fail
publishes no image. Pushing to quay.io needs the repository secrets `QUAY_USERNAME` and `QUAY_PASSWORD`
(a quay.io robot account with write access to the repository works).

To release a version:

```sh
uv version 1.2.3                      # must match the tag, CI checks it
git commit -am "Release 1.2.3"
git tag v1.2.3
git push origin main v1.2.3
```

Once the checks pass on the tag, this publishes the image tags `1.2.3`, `1.2` and `1`.
