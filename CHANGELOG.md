# Changelog

All notable changes to Bookshelf, newest first. Each version is released as the image
`quay.io/enricobianchi/bookshelf:<version>` and, from 1.0.2, as the Helm chart
`oci://quay.io/enricobianchi/bookshelf-chart --version <version>`.

## Unreleased

### New

- **Serials by ISSN:** magazines and journals can be catalogued by their ISSN, in the ISBN field: as typed
  (`0317-8471`, with the issue after a space: `0317-8471 05`) or as their barcode (`977…`, with its 2- or 5-digit
  add-on). Each issue is an item of its own; they are shown as "ISSN 0317-8471 · issue 05", found by searching for
  the ISSN, imported and exported in the CSV's `isbn` column, and read by Scan (which then asks for the issue).
  Look up stays for books. `init-db` widens the field (`BOOKS.ISBN`, 13 to 18 characters) on upgrade.
- **Page size:** "Show", next to the order in the catalogue and the collection and next to the search on the
  authors page, shows 10, 50 or 100 a page. Lists now show 10 a page unless you choose more (they showed 50).
  The size is part of the list's address, so a bookmark or the browser's history keeps it.
- **PostgreSQL:** Bookshelf runs on PostgreSQL 18 or newer as well as on Firebird: set `APP_DB_BACKEND=postgresql`
  and `APP_DB_DSN` to `host[:port]/database`. Existing Firebird deployments need no change. The Helm chart does not
  offer PostgreSQL yet.
- **Word search on PostgreSQL:** the book and author searches use PostgreSQL's full-text search: each word you type
  starts a word of the title or the authors' names, in any order ("herbert dune"), and words are matched by their
  stem ("runs" finds "running"). `APP_SEARCH_LANGUAGE` (default `english`; e.g. `italian`, or `simple` for no
  stemming) picks the language; run `init-db` after changing it. On Firebird the search is unchanged.

### Fixes

- The About dialog's close button (✕) was all but invisible: light text on the dialog's light paper.

### Changes

- The app reads and writes the database through the SQLAlchemy ORM (with `sqlalchemy-firebird`), and its schema
  changes are Alembic migrations. Nothing changes in use. Upgrading: run `init-db` as for any release (the Helm
  chart does): it records a database's existing schema version as the matching migration, then applies any
  newer one. The only schema change is the wider `BOOKS.ISBN` for serials (see New), which 1.4.x still runs
  on. Going back to 1.4.x, do not run its `init-db`: it would add back the old 13-digit check on `ISBN`, and
  serials with an issue could no longer be saved, even after upgrading again.

### Development

- The README shows how to run Bookshelf with Docker Compose, with PostgreSQL or Firebird.
- Two browser tests that failed now and then on CI's slower runners wait properly: for the authors page to load
  before typing a search, and no longer for a scanner that a quick barcode read closes at once.
- The tests run on PostgreSQL too: `uv run tox -e py3-postgres` (a PostgreSQL 18 container per worker), also in CI.
- Schema changes are Alembic revisions in `bookshelf/migrations/versions/`, made with `alembic revision` and
  applied by `init-db` (not `alembic upgrade`); README, "Schema changes".

## 1.4.2 (2026-11-24)

### Changes

- The image runs gunicorn 27 (from 26.2); its settings in `gunicorn.conf.py` are unchanged. Click, email-validator
  and WTForms are updated to their latest patch releases. Nothing changes in use.

### Development

- Updated tools: ruff 0.17, ty 0.0.104, tox 4.67.13, tox-uv 1.37.7, python-dotenv 1.2.4 and uv-build 0.13.8;
  the Helm chart's workflow uses `azure/setup-helm` v5.

## 1.4.1 (2026-11-23)

### Changes

- The app's JavaScript is split into a script per feature (`static/js/`), replacing `app.js`. Nothing changes
  in use. A list saved for offline use before upgrading has no scripts until it is opened online once again:
  shown offline in between, its forms are not disabled.
- htmx, bundled with the app, is updated from 2.0.4 to 2.0.11 (bug fixes; nothing changes in use).

### Development

- Dependabot opens weekly pull requests for outdated Python packages, GitHub Actions and the Dockerfile's base
  images, each a week after its release.

## 1.4.0 (2026-11-23)

### New

- **Look up a book by its ISBN:** "Look up", next to the ISBN in the add-book form, fills in the empty fields
  (title, authors, publisher, year, plot and categories) from Google Books, or from Open Library when Google
  Books doesn't have the book. What you typed is kept, and nothing is saved until you add the book. The server
  needs outbound HTTPS access to `www.googleapis.com` and `openlibrary.org`. An optional Google Books API key
  (`APP_GOOGLE_BOOKS_API_KEY`; the Helm chart's `googleBooksApiKey`, or that key in `existingSecret`) raises
  Google's daily limit on lookups.
- **About dialog:** "About" in the account menu shows the version of Bookshelf, its licence, a link to its source
  code and the credits for the fonts, htmx and ZXing it bundles. Without JavaScript it opens the `/about` page.
- **Scan a book's barcode:** "Scan", next to "Look up", opens the phone camera and reads the barcode on the
  back cover. A book's barcode is its ISBN-13 (starting 978 or 979), so the ISBN is filled in and looked up.
  Another barcode, such as a price, is not an ISBN: the dialog says so and keeps scanning. The camera stops
  when the dialog closes or the barcode is read, and the image never leaves the phone. Browsers give pages the
  camera only over HTTPS, so "Scan" shows only when Bookshelf is served over HTTPS (or on localhost) and the
  browser has a camera to offer. The browser reads the barcode itself where it can (e.g. Chrome on Android);
  elsewhere the bundled ZXing library does, loaded the first time "Scan" is used.

### Fixes

- Renaming an author no longer fails with "unknown author" when another author takes the new name and gives it
  up again at the same moment. The rename is tried again instead, up to 10 times.
- Dialogs fit on a phone's screen. They were a little wider than a phone (398 px on a 390 px screen) and ran
  off its right edge.
- On a phone the add-book form's ISBN field takes the whole width, with its buttons underneath, instead of
  being squeezed beside them.

### Development

- The unit tests run in parallel (pytest-xdist, up to 8 workers, each with its own Firebird container):
  about 90 seconds instead of 200. `uv run tox -e py3 -- -n0 <path>` runs them in one process.
- CI checks on every push that `pyproject.toml`'s version and the Helm chart's `version` and `appVersion`
  match, and on a version tag that they match the tag, before any image or chart is published.

## 1.3.0 (2026-11-23)

### New

- **Copy a collection from one user to another** (admin area). A user's details have a "Copy collection" form: the
  admin picks another user and chooses what happens to the books that user already has. They can keep theirs (add
  only the books they lack), add the copies together (up to 999), or replace their whole collection. Copied books
  start unread, and the first user's collection does not change.
- **`copy-collection` CLI command:** the same copy from the command line,
  `copy-collection SOURCE TARGET --mode keep|add|replace`. It requires admin credentials, and `replace` asks for
  confirmation unless `--yes` is given.

## 1.2.1 (2026-11-23)

### ⚠️ Breaking change: the Helm chart's Google login secret

The Google OAuth client now has a Kubernetes Secret of its own, apart from the app's Secret and ConfigMap.
`APP_GOOGLE_CLIENT_ID` has moved from the ConfigMap and `APP_GOOGLE_CLIENT_SECRET` from the app's Secret.

- **You set `google.clientId` and `google.clientSecret`:** nothing to do. The chart now creates the Secret
  `<release>-google` for them.
- **You keep the Google secret in `existingSecret`:** before upgrading, put both `APP_GOOGLE_CLIENT_ID` and
  `APP_GOOGLE_CLIENT_SECRET` in a Secret and name it in `google.existingSecret`. It can be the same Secret as
  `existingSecret`:

  ```sh
  kubectl -n bookshelf patch secret <your-secret> --type merge \
    -p '{"stringData":{"APP_GOOGLE_CLIENT_ID":"<client id>"}}'
  helm upgrade bookshelf oci://quay.io/enricobianchi/bookshelf-chart --version 1.2.1 \
    --reuse-values --set google.existingSecret=<your-secret>
  ```

  If you upgrade without doing this, Google login is turned off.

### Fixes

- **Helm:** upgrading with Google login turned on no longer fails. The pre-upgrade `init-db` hook used to
  reference a Secret the chart hadn't created yet. Only the app's pods read the Google client now; the
  `init-db` and `prune-login-failures` jobs never log anyone in.

### Documentation

- The README lists the Firebird client library (`libfbclient2` on Fedora and Debian/Ubuntu) as a requirement
  for the dev server, the CLI and the tests. Without it, every database call fails with "The location of
  Firebird Client Library could not be determined". The Docker image already includes it.

### Images

- `quay.io/enricobianchi/bookshelf:1.2.1` (also `1.2` and `1`)
- Helm chart: `oci://quay.io/enricobianchi/bookshelf-chart --version 1.2.1`

There are no app code or database schema changes since 1.2.0.

## 1.2.0 (2026-11-23)

### Upgrading

- This release changes the database schema (upgrade step 1). Run `init-db` before the new version serves.
  The Helm chart's pre-upgrade hook does this for you. Until it has run, every page answers 503 ("run init-db").
  Existing users keep logging in with their password.

### New

- **Log in with Google.** Each user logs in either locally, with a password (the default), or with their
  Google account (OpenID Connect, asking only for the `openid email` scope). Turn it on with
  `APP_GOOGLE_CLIENT_ID` and `APP_GOOGLE_CLIENT_SECRET` (both or neither), or with `google.clientId` and
  `google.clientSecret` in the Helm chart.
  - There is no sign-up: a Google account can log in only as a Google user an admin created.
  - The first login matches the user's email (Google must have verified it) and binds the user to that
    Google account. Later logins match the account, so a different Google account that later gets the same
    email cannot take the user over.
- Admins choose how each user logs in: `create-user --auth local|google`, the new `set-auth-method` CLI command,
  and a "Logs in" choice in the admin area's create and edit forms (not for their own account). Switching ends
  the user's open sessions; switching to local needs a new password.
- `list-users` and the admin users page show how each user logs in. A Google user's account page has no
  password section, and `reset-user-password` refuses Google users.

## 1.1.1 (2026-11-23)

### Fixes

- Title, ISBN, author, publisher and category suggestions now show in Firefox. They are a list of the app's
  own (an ARIA combobox and listbox) instead of a `<datalist>`, whose dropdown Firefox opened before the
  suggestions arrived. Up and Down move through the list, Enter or a click picks (Enter without submitting
  the form), Escape closes the list, not the dialog. A title several books share can now be picked.
- The add-book dialog is empty again when reopened. After a catalogue book had been picked, closing and
  reopening the dialog used to show that book again until the page was reloaded.

### Development

- The browser tests also run in Firefox (`uv run tox -e e2e-firefox`), in CI too.

## 1.1.0 (2026-11-22)

### Upgrading

- `init-db` now creates a `SETTINGS` table that records the schema version. Run it when upgrading; the Helm
  chart's pre-upgrade hook does. Until it has run, every page answers 503 ("run init-db").

### New

- **Schema upgrade steps.** `init-db` applies versioned upgrade steps, each with an upgrade and a rollback.
  - A step that fails is rolled back to the previous version, and one left halfway by a crash is rolled back
    and applied again on the next run.
  - Concurrent runs wait for each other.
  - A database newer than the release is refused, so an older `init-db` cannot downgrade it.

### Fixes

- Changes racing a deletion no longer fail with an error page. This happened when deleting a book being
  deleted at the same moment, renaming or merging an author being deleted, or changing the copies or read
  state of a book (or removing it) while it left the collection. The change is now retried: it reports that
  the book or author is gone, or goes ahead when only something else was deleted.

## 1.0.2 (2026-11-17)

### New

- **Helm chart**, published as `oci://quay.io/enricobianchi/bookshelf-chart` with each release. It deploys the
  app, its Service, an optional Ingress, an `init-db` hook Job (after install and before each upgrade), an
  hourly `prune-login-failures` CronJob and, with `firebird.enabled`, a single-pod Firebird 3 StatefulSet.

### Changes

- The log level defaults to `info`: a bare gunicorn deploy no longer logs at debug level.
- `APP_PORT` must be a port number (1–65535). Anything else stops startup with a clear error instead of a raw
  `ValueError`.

## 1.0.1 (2026-11-16)

### Changes

- The package and the Docker image build with uv 0.13.
- The database schema is frozen as of 1.0.0: later changes to it come as upgrade steps (see 1.1.0).

## 1.0.0 (2026-11-16)

First release: a self-hosted web app to manage a book library, built with Flask, HTMX and Firebird.

- **Catalogue** of every book, with search, ordering and paging. Add and edit books with their authors and
  categories, with suggestions as you type. Admins can delete books.
- **Your collection:** add books from the catalogue, set the number of copies, mark them read, remove them.
- **Authors:** list every author with their books, and rename or merge authors.
- **Your account:** name, surname and password. Export your collection to CSV and import it back.
- **Admin area:** create, edit, lock and delete users.
- **First-run wizard** that creates the first admin.
- **Installable app (PWA):** shows the collection and catalogue you last saw online, read-only, when the
  server can't be reached.
- **CLI:** `init-db`, `create-admin`, `create-user`, `reset-user-password`, `promote-user`, `demote-user`,
  `lock-user`, `unlock-user`, `delete-user`, `list-users` and `prune-login-failures`.
- **Security:**
  - Argon2 password hashing.
  - Failed logins limited per email and IP and per IP.
  - Persistent login cookies (30 days, `Secure`, `HttpOnly`, `SameSite=Lax`).
  - A strict Content-Security-Policy.
  - Sessions end when a user is locked or their password is reset.
- **Docker image** `quay.io/enricobianchi/bookshelf`, run by gunicorn with JSON logs, configured through
  `APP_*` environment variables.
