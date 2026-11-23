# Changelog

All notable changes to Bookshelf, newest first. Each version is released as the image
`quay.io/enricobianchi/bookshelf:<version>` and, from 1.0.2, as the Helm chart
`oci://quay.io/enricobianchi/bookshelf-chart --version <version>`.

## Unreleased

### Fixes

- Renaming an author no longer fails with "unknown author" when another author takes the new name and gives it
  up again at the same moment. The rename is tried again instead, up to 10 times.

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
