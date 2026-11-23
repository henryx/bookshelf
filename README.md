# Bookshelf - A Book library app

A self-hosted web app to manage a book library, built with Flask, HTMX and Firebird.
Requirements and decisions are in [docs/bookshelf.md](docs/bookshelf.md).

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/)
- Docker (development database, tests, image builds)
- The Firebird client library, `libfbclient.so.2`, which `firebird-driver` loads (dev server, CLI, tests):
  `libfbclient2` on Fedora (`sudo dnf install libfbclient2`) and Debian/Ubuntu (`sudo apt install libfbclient2`).
  Without it every database call, and every test, fails with "The location of Firebird Client Library could not
  be determined". The Docker image already installs it.

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
| `APP_LOG_LEVEL` | no | `info` | Log level (does not turn on Flask's debug mode) |
| `APP_HOST` / `APP_PORT` | no | `localhost` / `8000` | Bind address (Docker image: `0.0.0.0` / `8080`) |
| `APP_DB_POOL_SIZE` | no | `5` | Idle database connections kept for reuse, per process |
| `APP_TRUSTED_PROXIES` | no | `0` | Reverse proxies in front of the app; set it (usually `1`) behind a proxy so client IP and HTTPS come from `X-Forwarded-*`. Required behind a proxy: login limits are per IP. Leave `0` when exposed directly, or clients can fake their IP |
| `APP_WORKERS` | no | 2 × CPUs + 1 | gunicorn worker processes; set it in containers with a CPU limit |
| `APP_TIMEOUT` | no | `30` | Seconds before gunicorn restarts a worker stuck on a request |
| `APP_GOOGLE_CLIENT_ID` / `APP_GOOGLE_CLIENT_SECRET` | no | — | Google OAuth client for logging in with Google (see "Logging in with Google"); both or neither, or the app does not start |
| `APP_GOOGLE_BOOKS_API_KEY` | no | — | Google Books API key for the add form's Look up (a key of a Google Cloud project with the Books API enabled). Without one Google Books allows fewer lookups a day, shared by everyone without a key, and Open Library is asked when it refuses |

## Books

The main page is the book list: one search box over title, author and ISBN, sorting by title, author or date
added, in pages of 50. The round "+" button at the bottom right (here and in your collection) opens the
"Add book" dialog. Authors are rows of name and surname, and categories rows of one field (a book can have
several); in both "+" adds a row and "−" removes one. The author, category and publisher fields suggest existing
entries as you type, and picking an author fills both name and surname. The book is added to the shared catalogue
and to your own collection. "Look up", next to the ISBN, fills in the empty fields from Google Books or Open
Library (see `APP_GOOGLE_BOOKS_API_KEY`). "Scan" reads the ISBN from the book's barcode with the phone camera and
looks it up; browsers give pages the camera only over HTTPS (or on localhost), so it shows only there.

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
role, lock state, how they log in or password, or to delete them. The same operations are available from the CLI.
Deleting or demoting an admin is refused when no other admin would remain.

### Logging in with Google

Each user logs in either locally, with a password (the default), or with their Google account, with no password.
An admin chooses which when creating the user, and can switch it later (from the admin area or the CLI); switching
ends the user's open sessions, and switching to local needs a new password. An admin cannot switch their own
account from the admin area. Nobody can sign up with Google: only a user an admin created for Google can log in
with it. Their first Google login matches their email (which Google must have verified) and binds the user to that
Google account; later logins use the account, so another Google account that later gets the same email cannot log
in as them. Switching a user to Google again unbinds them, for their next login to bind again.

To turn it on, create an OAuth client in the Google Cloud console (APIs & Services → Credentials → OAuth client ID,
type "Web application") with the authorised redirect URI `https://<your host>/login/google/callback`, and set
`APP_GOOGLE_CLIENT_ID` and `APP_GOOGLE_CLIENT_SECRET`. The login page then shows "Log in with Google". The app asks
Google only for the account's ID and email. Behind a reverse proxy, `APP_TRUSTED_PROXIES` must be set so the
redirect URI the app sends is `https://` (see "Configuration").

The CLI's admin operations check an admin's password, so they need a local admin: keep at least one.

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
| `create-user EMAIL [--auth local\|google]` | Create a regular user, logging in with a password (`local`, the default: asked for) or with Google (no password) |
| `set-auth-method EMAIL local\|google` | Switch how a user logs in (a new password is asked for `local`); ends their open sessions |
| `list-users` | Show all users with role, how they log in, and lock status |
| `reset-user-password EMAIL` | Set a new password for a user (refused for a Google user: `set-auth-method` first) |
| `promote-user EMAIL` / `demote-user EMAIL` | Give or remove the admin role |
| `lock-user EMAIL` / `unlock-user EMAIL` | Block or allow a user's login; locking ends open sessions |
| `delete-user EMAIL [--yes]` | Permanently delete a user (asks for confirmation) |
| `copy-collection SOURCE TARGET [--mode keep\|add\|replace] [--yes]` | Copy a user's collection into another user's; for books the target already has, `keep` theirs (the default), `add` the copies, or `replace` their whole collection (asks for confirmation). Copied books start unread |

All commands except `init-db`, `prune-login-failures` and `create-admin` require admin credentials
(`--admin-email`, or prompted, plus the admin password). Admins cannot lock, demote or
delete their own account, and demoting or deleting the only admin left is refused.
`create-admin` and `create-user` only accept valid email addresses (no reserved or LAN-style
domains such as `.local`), the same rule as the login form.

## Kubernetes

A Helm chart is published with each release at `oci://quay.io/enricobianchi/bookshelf-chart` (chart version =
app version); its source is [deploy/helm/bookshelf-chart](deploy/helm/bookshelf-chart) and its settings are in
[values.yaml](deploy/helm/bookshelf-chart/values.yaml). It deploys the app (2 replicas), its Service, an optional
HTTPS Ingress, an hourly CronJob running `prune-login-failures` and, with `firebird.enabled`, a Firebird 3
StatefulSet with a 5 GiB volume. A hook Job runs `init-db` after install and before each upgrade, so a release's
schema additions are created before its pods serve; if it fails, the upgrade fails and the old pods keep serving.

With Firebird in the cluster:

```sh
helm install bookshelf oci://quay.io/enricobianchi/bookshelf-chart --version 1.3.0 \
  -n bookshelf --create-namespace \
  --set firebird.enabled=true \
  --set ingress.enabled=true,ingress.host=books.example.com \
  --set secretKey=... --set database.password=... --set firebird.rootPassword=...
```

With an existing Firebird server, set `database.dsn` (e.g. `db.example.com:/var/lib/firebird/data/bookshelf.fdb`)
instead of `firebird.*`. `helm upgrade bookshelf oci://quay.io/enricobianchi/bookshelf-chart --version <new>
--reuse-values` moves to a new release; `helm show values oci://quay.io/enricobianchi/bookshelf-chart` lists
every setting. From a checkout, use `deploy/helm/bookshelf-chart` in place of the `oci://` reference. Rather than passing secrets on the command line, a values file kept out of git or an
existing Secret (`existingSecret`, with the keys `APP_SECRET_KEY`, `APP_DB_USER`, `APP_DB_PASSWORD`,
and `FIREBIRD_ROOT_PASSWORD` with `firebird.enabled`) works too.
Logging in with Google keeps its OAuth client in a Secret of its own: set `google.clientId` and
`google.clientSecret` and the chart creates `<release>-google`, or name an existing Secret with the keys
`APP_GOOGLE_CLIENT_ID` and `APP_GOOGLE_CLIENT_SECRET` in `google.existingSecret` (it may be the same Secret as
`existingSecret`). Only the app's pods read it, not the `init-db` and `prune-login-failures` jobs.
A Google Books API key goes in the app's Secret: set `googleBooksApiKey`, or add the key
`APP_GOOGLE_BOOKS_API_KEY` to `existingSecret`. It is optional, and only the app's pods read it.

The setup wizard is open to whoever reaches it first, so create the first admin before the Ingress is reachable
by anyone else (the chart's install notes show the command):

```sh
kubectl -n bookshelf exec -it deploy/bookshelf -- /app/venv/bin/flask --app bookshelf create-admin admin@example.com
```

Liveness is checked on `/sw.js` (no database), so a database outage does not restart the pods. Readiness is
checked on `/login` (it queries the database), so pods get no traffic while the database is unreachable.
`APP_TRUSTED_PROXIES` is `1` (`config` in the values): the ingress controller must set `X-Forwarded-For` and
`X-Forwarded-Proto`, as most do. Firebird has no replication, so the in-cluster database is a single pod: back
up its volume.

## Tests and checks

```sh
uv run tox -e py3                     # tests, in parallel (each worker starts its own Firebird via testcontainers: Docker must be running)
uv run tox -e e2e                     # browser tests of app.js (Playwright; installs Chromium, Docker too)
uv run tox -e e2e-firefox             # the same in Firefox
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

After upgrading, back up the database with `gbak`, then run `init-db` again: it adds any new tables,
constraints and indexes to an existing database, applies the release's upgrade steps (see "Schema changes"),
and replaces the functions (such as the book search) with the current ones. Until it has run, the app answers
every request with 503 ("run init-db"); the container keeps running, so `docker exec` works:

```sh
docker exec <container> /app/venv/bin/flask --app bookshelf init-db
```

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

- The schema is frozen since 1.0.0: databases created by a release must keep working after an upgrade, so
  the domains, tables, constraints and indexes it created must not be edited in place in `schema.py`.
  - Adding a new domain, table or index needs no upgrade step: `init-db` creates whatever is missing.
  - Changing a released one (a column added, dropped, renamed or retyped, a constraint added to a released
    table or changed) needs an upgrade step that `init-db` applies to existing databases, as below.

### Upgrade steps

Written into `init-db` rather than taken from a migration tool: Alembic would bring in SQLAlchemy only for
this and has no Firebird support of its own, yoyo-migrations has no Firebird backend, and Flyway or Liquibase
would add a Java runtime to the deployment.

- **Version.** The `SETTINGS` table holds the app's own settings: `NAME` (the key) and `"VALUE"`, text
  (`VALUE` is a reserved word in Firebird, so it is always written quoted). The `SCHEMA_VERSION` setting is
  the last step applied; `init-db` adds it as `0`, the frozen 1.0.0 schema, the first time it runs, and a
  database from before versions (1.0.x) is at version 0.
- **Steps.** `UPGRADES` in `bookshelf/db/upgrades.py` lists the steps (with the code applying them); step *n* (`UPGRADES[n - 1]`) takes a database from
  version *n − 1* to *n*. Append only: never change or remove a released step. Each is an `UpgradeStep` of two
  Python functions taking the connection, `upgrade` and `rollback`, running complete literal SQL strings, as
  everywhere else. `rollback` undoes `upgrade`, taking the database back to version *n − 1*. Both commit their
  own work; what `upgrade` leaves uncommitted is committed with its version.
- **Order.** `init-db` creates whatever is missing (version 0, plus any new objects), applies the pending steps
  in order, then `CREATE OR ALTER`s the functions, which may use what the steps added. A new database takes the
  same path as an old one, so the two cannot drift apart.
- **Rollback on failure.** Firebird rolls back DDL with its transaction, but a column added in a transaction
  cannot be used until that transaction commits. A step that adds a column and fills it therefore commits
  twice and is not atomic: when it fails, what it committed stays. So when a step fails, `init-db` runs its
  `rollback`, removes `SCHEMA_UPGRADE_STEP`, and stops with the step's error; the database is back at version
  *n − 1*,
  still working with the running release.
- **Rollback after a crash.** A step killed halfway (a pod evicted, the connection lost) gets no chance to
  roll back. Before calling `upgrade`, `init-db` adds the `SCHEMA_UPGRADE_STEP` setting, the step's number;
  once the step is applied, it sets `SCHEMA_VERSION` and removes `SCHEMA_UPGRADE_STEP` in one transaction. The
  next `init-db` finds a `SCHEMA_UPGRADE_STEP` left behind, runs that step's `rollback`, then applies the step
  again from the start. So does a `rollback` that failed: the setting stays until one succeeds.
- **Guarded changes.** Since `rollback` can run after a failure at any point of `upgrade`, it undoes only what
  is there: every change in both functions first checks whether it is already applied or already undone (as
  `init-db` does for tables, constraints and indexes), so a `rollback` that itself fails can simply be run
  again.
- **Compatible with the running release.** The Helm chart runs `init-db` as a pre-upgrade hook, while the old
  pods keep serving (Firebird lets them go on using a table altered meanwhile). Each step must therefore work
  with the previous release's code: add columns nullable or with a default, and drop or rename one only in a
  later release, once no running code uses it. A step that drops a column a function uses (such as
  `BOOK_MATCHES`) alters that function first: Firebird refuses to drop a column a function depends on.
- **One run at a time.** `init-db` locks the `SCHEMA_UPGRADE_LOCK` setting, which nothing else writes, in a
  transaction of its own held until it ends (and always rolled back), so a second run started meanwhile waits
  for it. The lock cannot be the `SCHEMA_VERSION` row: the run updates that one after each step.
- **Newer database.** `init-db` refuses a database at a version this release does not know: run from an older
  release, it would replace the functions with older ones.
- **Request check.** While the database is behind its code, the app answers every request with 503 and logs
  that `init-db` is needed (static files, the service worker and its offline page are still served). It does
  not refuse to start, so `init-db` can run in its container. A database with no schema at all is not behind:
  the setup wizard creates it. A database ahead of the code is fine: that is the old pods during an upgrade.
- **What rollback cannot undo.** `rollback` restores the schema, not data a step destroyed: a dropped column's
  values are gone. Keeping drops to the later, contract releases (above) means a failed step rarely has data to
  lose, but back up the database with `gbak` before upgrading all the same. `rollback` is for a step that fails;
  going back to an older release after a successful upgrade means restoring that backup.
- **Tests.** `tests/test_upgrade_steps.py` tests the mechanism with steps of its own (`empty_db_app` gives each
  test a database of its own). Each real step is tested on a database at the previous version holding data:
  `upgrade` alone, then
  `rollback` after making `upgrade` fail at each of its changes in turn, checking that the database is back at
  version *n − 1* with its data, and that `rollback` run twice changes nothing more. A database upgraded from
  version 0 ends with the same metadata as a new one.

## CI and releases

GitHub Actions (`tests.yml`) run lint, format check, type check, audit, tests and browser tests (in
Chromium and in Firefox) on every push to `main`, on version tags and on pull requests. A `versions` job also
checks that `pyproject.toml`'s version and the Helm chart's `version` and `appVersion` are the same, and on a
version tag that they match the tag. Only once all of them pass is the image
built (`docker.yml`, called by `tests.yml`): pushes to `main` publish `quay.io/enricobianchi/bookshelf:latest`,
version tags their version tags, and pull requests just build it. A commit or tag whose checks fail
publishes no image. The Helm chart is then linted and rendered (`helm.yml`), and pushed to
`oci://quay.io/enricobianchi/bookshelf-chart` for version tags. Pushing to quay.io needs the repository secrets `QUAY_USERNAME` and `QUAY_PASSWORD`
(a quay.io robot account with write access to the repository works).

To release a version:

```sh
uv version 1.2.3                      # must match the tag, CI checks it
# and set `version: 1.2.3` and `appVersion: "1.2.3"` in deploy/helm/bookshelf-chart/Chart.yaml;
# in CHANGELOG.md, turn "Unreleased" into "1.2.3 (<date>)"; in README.md, the `helm install --version`
git commit -am "Release 1.2.3"
git tag v1.2.3
git push origin main v1.2.3
```

Once the checks pass on the tag, this publishes the image tags `1.2.3`, `1.2` and `1`, and the chart `1.2.3`.
