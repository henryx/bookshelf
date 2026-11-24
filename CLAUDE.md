# Bookshelf library webapp

## Project Overview
Bookshelf library webapp is a webapp to manage a book library built with Flask using Python.

## Stack
- **Frontend**: HTMX
- **Backend**: Flask
- **Language**: Python
- **Package** manager: uv
- **Styling**: Plain CSS
- **Database**: Firebird or PostgreSQL 18+ (`APP_DB_BACKEND`), through the SQLAlchemy ORM (`sqlalchemy-firebird` on `firebird-driver`, psycopg 3); migrations with Alembic
- **Hosting**: Self-hosted
- **Testing**: tox (run via `uv run tox`)

## Common Commands
- `uv sync`: Install dependencies
- `uv run dotenv -f dev.env run flask --app bookshelf run --debug`: Start the development server (needs the local Firebird from README.md)
- `uv run dotenv -f dev.env run flask --app bookshelf <command>`: Run a CLI command (`init-db`, `create-admin`, `list-users`, …)
- `uv run alembic revision -m "<what it does>" --rev-id <next number, e.g. 0003>`: Make a migration revision (applied by `init-db`; `alembic upgrade` refuses)
- `uv run tox -e py3`: Run the test suite in parallel (pytest-xdist, up to 8 workers; Docker must be running: each worker starts its own Firebird via testcontainers; `-- -n0 <path>` for one process)
- `uv run tox -e py3-postgres`: The same test suite on PostgreSQL 18 (`TEST_DB_BACKEND=postgresql`; a container per worker). Run both for any database change
- `uv run tox -e e2e`: Run the browser tests of the scripts in `static/js/` (Playwright with Chromium, installed by the env; Docker must be running). Passing posargs replaces the default path: `uv run tox -e e2e -- tests/e2e -k banner`
- `uv run tox -e e2e-firefox`: The same browser tests in Firefox (the offline tests skip it: `skip_browser("firefox")`); run both for browser behaviour (e.g. events, `<dialog>`, suggestions)
- `uv run tox -e lint`: Run linter
- `uv run tox -e format`: Run formatter (`-- --check .` to only check, as CI does)
- `uv run tox -e type`: Run type check
- `uv run tox -e audit`: Run auditing
- `uv run tox -e report`: Run report coverage

## Project Layout
- `bookshelf/__init__.py`: `create_app()` factory, error handlers, logging
- `bookshelf/config.py`: `Config` (environment variables)
- `bookshelf/logging_config.py`: JSON logging config shared by the app and gunicorn
- `bookshelf/wsgi.py`: WSGI entry point (`bookshelf.wsgi:app` for gunicorn); run as a script it never enables debug mode (only `flask run --debug` does)
- `gunicorn.conf.py`: gunicorn settings (bind, workers, timeout, log level, logging); loaded from the working directory
- `bookshelf/cli.py`: Flask CLI commands; admin operations go through `_require_admin`
- `bookshelf/db/`: `__init__.py`: one SQLAlchemy engine per app (`create_db_engine(backend, …)`: Firebird via `FirebirdDialect` = sqlalchemy-firebird with firebird-driver fixes, URL `firebird+bookshelf://`; PostgreSQL via psycopg, repeatable read by default); `backend_of(session|connection|engine)` the `DatabaseBackend` (`bookshelf/config.py`) it talks to; `get_session` gives the app context's `Session` (closed, so rolled back, at teardown), `get_engine` the engine; `execute_change` runs an ORM `insert`/`update`/`delete` and returns its rowcount, `try_change` the same in a savepoint (for a write whose error is handled); `is_unique_violation`, `is_foreign_key_violation`, `is_update_conflict`, `is_concurrent_delete` tell database errors apart (Firebird GDS codes, PostgreSQL SQLSTATEs); `read_committed(session)` a read committed transaction of its own; `retry_after_concurrent_delete` runs a change again when a row it changed was deleted by a concurrent transaction (Firebird's "cannot update erased record"). `sql.py`: SQL the backends write differently, as constructs compiled per backend (`matches_search`: Firebird CONTAINING, PostgreSQL full-text; `starts_with_ci`, `minutes_ago`, `record_key`, `least`). `orm.py` maps the tables the models use (`UserRow`, `BookRow`, `AuthorRow`, `BookAuthorRow`, `BookCategoryRow`, `CollectionBookRow`, `LoginFailureRow`; `UUIDType`: Firebird's `UUID` domain, PostgreSQL's uuid); `baseline.py` the frozen 1.0.0 DDL (domains, tables, constraints, indexes; `POSTGRESQL_COLLATIONS` / `POSTGRESQL_INDEXES` replace what PostgreSQL writes differently); `changes.py` `SchemaChange` (a guarded, rerunnable change of a revision; SQL for both backends or one per backend; no `undo_sql` for one kept on downgrade) and `KeyedChange` (a change Firebird makes only with a key dropped, e.g. retyping a column in a UNIQUE); `schema.py` `init_db` (applies the revisions one at a time, rolls back a failed or interrupted one, stamps Firebird databases from before 1.5.0 from their `SCHEMA_VERSION` setting; on PostgreSQL checks the server is 18+ and records `APP_SEARCH_LANGUAGE`; `SETTINGS."VALUE"` is always quoted: a reserved word), `schema_behind` (the 503 check in `routes/setup.py`), `FUNCTIONS` (`BOOK_MATCHES`, per backend) and `POSTGRESQL_HELPERS` (`SEARCH_CONFIG`, `SEARCH_QUERY`, `SEARCH_MATCHES`, `CONTAINS_PATTERN`), and `FirebirdImpl` (Alembic's Firebird support); `catalog.py` the catalog lookups per backend (`table_exists`, `column_exists`, `constraint_exists`, …)
- `bookshelf/migrations/`: Alembic revisions (`versions/`: `0001` the 1.0.0 schema, `0002` how users log in, `0003` serials' codes in `BOOKS.ISBN`), `script.py.mako` (a new revision's template), `env.py` (refuses `alembic upgrade`); `alembic.ini` at the root
- `bookshelf/models/`: data access and domain logic (`users/`: a package whose `__init__.py` is the API the app imports (`bookshelf.models.users`): `model.py` data types (users with optional name and surname, how they log in: `AuthMethod`, `LOCAL` with a password or `GOOGLE`), errors and the new-password rule, `passwords.py` Argon2 hashing, `listing.py` finding and listing users, `editing.py` create, change and delete users (never leaving no admin), `login.py` `authenticate` and `google_login` (binds a Google account), `login_limits.py` the login limits; `books/`: a package whose `__init__.py` is the API the app imports (`bookshelf.models.books`): `model.py` data types, errors and limits, `isbn.py` the ISBN field's codes: ISBNs, and serials' ISSNs as their 977 barcode with its add-on (`normalise_isbn`, `format_code`, `code_label`, also Jinja filters), `listing.py` book lists and finding a book, `collection.py` the user's collection, `editing.py` add, edit and delete book (with its authors and categories), `suggestions.py` book, author, publisher and category suggestions, `lookup.py` looking a book up online by its ISBN (Google Books, then Open Library; the add form's Look up button, `_look_up()` in `routes/main.py`, also pressed by Scan); `authors.py`: authors list, renaming and merging; `collection_csv.py`: a collection's CSV export and import)
- `bookshelf/routes/auth.py`: password login (rate-limited), logout, and logging in with Google (`/login/google` and its `/login/google/callback`, Authlib, set up by `init_google` only when `APP_GOOGLE_CLIENT_ID` / `APP_GOOGLE_CLIENT_SECRET` are set)
- `bookshelf/routes/`: blueprints (`auth`, `main`, `suggestions`, `collection`, `authors`, `account`, `setup`, `admin`, `pwa`); `__init__.py` has the `is_htmx()` and `htmx_redirect()` helpers; `forms.py` holds the Flask-WTF forms
- `bookshelf/routes/main.py`: the catalogue (`/catalogue`), add book (`/books/new`), edit book (`/books/<id>`), delete book (`/books/<id>/delete`, admins only); `/books/new?book=<id>` is the add form filled, read-only, with a picked catalogue book, to add it to the collection; `_change_rows()` handles the author and category rows' + and − buttons without JavaScript; `list_state()` / `list_url()` read a list's search, order and page, `render_catalogue()` renders the catalogue; `book_list_endpoint()` is the list a form came from (`list=collection`, else the catalogue: the add-book form of both pages carries it); both book lists take `?author=<id>` for one author's books (`author_filter()`)
- `bookshelf/routes/suggestions.py`: the book form fields' suggestion lists (`/books/suggestions` for the add form's title and ISBN, `/authors/suggestions`, `/publishers/suggestions`, `/categories/suggestions`)
- `bookshelf/routes/collection.py`: the current user's collection (`/`), and adding a book to it, marking it read and removing it (`/collection/books/<id>/add|read|remove`); its copies are changed in the edit-book dialog (`main.book`, `EditBookForm.copies`) (the first page: login, setup and the top bar's title lead to it)
- `bookshelf/routes/authors.py`: the authors list (`/authors`) and renaming an author for every book (`/authors/<id>`)
- `bookshelf/routes/account.py`: the current user's account page (`/account`): name, surname, password (needs the current one); CSV export and import of their collection (`/account/export`, `/account/import`, the `_collection_csv.html` card)
- `bookshelf/routes/admin.py`: admin area (`/admin/users`), admins only; users list, create, details/edit, delete, copying a user's collection to another user (`/admin/users/<id>/copy-collection`, `copy_collection` in `models/books/collection.py`)
- `bookshelf/routes/setup.py`: first-run wizard; redirects every request to `/setup` until an admin exists (not static files or `pwa`)
- `bookshelf/routes/pwa.py`: the service worker (`/sw.js`, from `static/sw.js`) and its `/offline` page, no login needed; `static/manifest.webmanifest` and `static/icons/` make the app installable; the worker shows the collection and catalogue last seen online, read-only, when the server can't be reached (`<body data-offline>`, handled in `js/offline.js`)
- `bookshelf/templates/`, `bookshelf/static/`: Jinja templates and CSS; htmx is vendored in `static/` (`htmx.min.js`, 2.0.11, from npm's `htmx.org`); `js/` holds the scripts, one IIFE per feature, deferred in `base.html`'s order (their listeners run in it): `errors.js` (error banner; first, so a request clears it), `dialogs.js`, `rows.js` (repeatable rows), `suggestions.js` (suggestions and the book pick: `[data-book-pick]`, `[data-book-loader]`), `menu.js` (account menu), `scan.js` (barcode scan: `[data-barcode-scan]`, the add form's Scan button and its `#book-scan-dialog`; the browser's `BarcodeDetector`, else ZXing), `offline.js` (service worker registration, offline copies); they share nothing, so each can be read alone; `static/zxing/` is the vendored @zxing/library UMD build (0.23.0, with its LICENSE), loaded by `js/scan.js` on first scan; `_topbar.html` is the top bar (its title links to the collection) with the account menu (`<details class="avatar">`); `_name_fields.html` renders a person's name and surname (takes `id_prefix`); `_author_rows.html` and `_category_rows.html` render a book form's author and category rows (take `id_prefix`: `book-` in the add form, `edit-book-` in the edit form); `_book_list.html` and `_book_search.html` serve both the catalogue and the collection (`list_endpoint`, and `author` for one author's books); `_book_details.html` / `_edit_book_form.html` are the edit-book dialog (with the admins' delete form); `_book_list_state.html` holds the hidden fields that take a form back to the list it came from; `_author_list.html` / `_rename_author_form.html` the authors page
- `bookshelf/static/fonts/`: vendored Fraunces, Bricolage Grotesque and JetBrains Mono (latin and latin-ext WOFF2, with their OFL licences); `@font-face` rules are at the top of `style.css`
- `bookshelf/templates/_hero.html`: `hero(tall=False)` macro, the bookcloth band with the shelf of spines; pages fill `base.html`'s `hero` block with it (tall on main, login and setup, with the page-load reveal; slim elsewhere)
- `tests/conftest.py`: session database container (`TEST_DB_BACKEND`: Firebird by default, or PostgreSQL), `app`/`db_app`/`empty_db_app` fixtures, `other_conn` (a second connection), `race` / `waiting` (a statement waiting on another's lock), `fail_statement`, `migrations` (the revisions `init_db` knows: the real ones up to one, then test ones); `@pytest.mark.firebird` / `@pytest.mark.postgresql` mark tests of one backend's behaviour (skipped on the other); autouse `setup_complete` skips the first-run wizard (overridden in `test_setup.py`); `tests/raw_db.py`: `raw(session)` / `RawConnection`, a cursor-like helper for the tests' own SQL (setup and checks) on either backend (`?` parameters, UUIDs as `UUID`), `table_columns` / `table_constraints`, and `BACKEND`
- `tests/e2e/`: Playwright tests of the scripts in `static/js/` against a live server (`live_server`, `reader_page`, `admin_page` fixtures); `py3` skips them, the `e2e` tox env runs them. CI's runners are slower: after following a link, wait for the page (`wait_for_url`) before typing into an htmx form (the deferred scripts may not listen yet), and assert only states that last (not a dialog a quick action closes)
- `.github/workflows/`: `tests.yml` (the tox envs and the `versions` check on every push, tag and pull request; then `docker.yml` builds the image and `helm.yml` the chart, published for `main` and version tags); `.github/dependabot.yml`: weekly updates of `uv.lock`, GitHub Actions and the Dockerfile's base images (not Python), a week after their release
- `deploy/helm/bookshelf-chart/`: Helm chart, published as `oci://quay.io/enricobianchi/bookshelf-chart` on version tags (`.github/workflows/helm.yml`; its `version` and `appVersion` must match `pyproject.toml` and the tag, checked by the `versions` job of `tests.yml`: bump them together on release; resource names stay `bookshelf`): app Deployment, Service, optional Ingress, `init-db` hook Job (post-install, pre-upgrade), `prune-login-failures` CronJob, optional in-cluster Firebird (`firebird.enabled`) or PostgreSQL 18 (`postgresql.enabled`; at most one; `database.backend` for an external server, `database.searchLanguage`); check with `helm lint` and `helm template` (needs `secretKey`, `database.password` and `database.dsn` or an in-cluster database; CI renders all four database modes)
- `docs/bookshelf.md`: requirements, decisions and open questions
- `dev.env` (dev server) and `test.env` (tests) are deliberately separate: tests wipe tables

## Code Style
- Python:
  - Project uses 3.14 or greater (see `requires-python` in `pyproject.toml`). Use modern syntax:
    - `X | None` instead of `Optional[X]`
    - `list[str]` instead of `List[str]`
    - `dict[str, Any]` instead of `Dict[str, Any]`
    - `tuple[int, str]` instead of `Tuple[int, str]`
    - `type X = ...` for type aliases (Python 3.12)
  - Follow PEP 8 style guidelines
  - Use type hints for function signatures
  - Prefer f-strings for string formatting
  - Keep functions small and focused on a single responsibility
  - Remove imports, variables, or functions made unused by your own changes.
  - Avoid speculative abstractions for single-use code.
  - Match statements for enum/type dispatch where appropriate
  - No `Any` types - use proper type hints or `object`
  - Use early returns, avoid nested conditionals
  - Prefer composition to inheritance
- Docker:
  - Limit layers in Dockerfile
- Database (SQLAlchemy ORM, see README "Schema changes"):
  - Models query with the ORM on the mapped `*Row` classes of `bookshelf/db/orm.py` (`select()`, and `insert()`/`update()`/`delete()` through `execute_change`); they take a `Session`, commit their own writes, and return the dataclasses of `bookshelf/models/`, never ORM objects.
  - Use ORM statements, not `session.add()`, for writes whose errors are caught, and run them with `try_change` (a savepoint): a failed flush ends the transaction, and PostgreSQL also ends it at a failed statement.
  - No `text()` or raw SQL in models, and no backend's own syntax: what Firebird and PostgreSQL write differently is a construct of `bookshelf/db/sql.py` (add one there, compiled for both). `text()` is for migrations, `catalog.py`, `schema.py` and tests only.
  - Never interpolate names or clauses into SQL; values only as bound parameters. Bind a Python string compared with no column as `literal(value, String(n))`: the dialect casts every parameter, and `VARCHAR` without a length is invalid.
  - Mapped column types carry no collation (the dialect would put it in each parameter's `CAST`, which Firebird refuses); the DDL keeps it (`UNICODE_CI`, on PostgreSQL a nondeterministic ICU collation of that name). Names are lower case (unquoted, so upper case in Firebird and lower case in PostgreSQL, as the DDL's unquoted names).
  - The schema changes only through Alembic revisions in `bookshelf/migrations/versions/`, made from `script.py.mako`: a list of guarded `SchemaChange`s (`upgrade` and `downgrade`, both rerunnable), compatible with the previous release's code. Never change a released revision, nor the frozen 1.0.0 DDL in `bookshelf/db/baseline.py`. Update `orm.py` to match, and test the revision on a database at the previous one (as `tests/test_migration_0002.py`).
  - `FUNCTIONS` (functions such as `BOOK_MATCHES`, the book lists' search condition; per backend) and `POSTGRESQL_HELPERS` are created or replaced on every `init_db`: edit them in place at any time, for both backends.
  - Every database change is tested on both backends (`py3` and `py3-postgres`); a test of one backend's own behaviour is marked `@pytest.mark.firebird` or `@pytest.mark.postgresql`.
- Other:
  - Write clear, descriptive commit messages

## Frontend aesthetics

Avoid generic AI aesthetics. Make creative, distinctive choices.

### Typography
- Never use Inter, Roboto, Open Sans, Lato, Arial, or system fonts.
- Body: Bricolage Grotesque. Display: Fraunces. Mono: JetBrains Mono.
- Use weight extremes: 200 vs 800, not 400 vs 600.
- Size jumps of 3x+, not 1.5x.

### Color & theme
- Commit to a single dominant color with one sharp accent.
- All colors live in CSS variables in `bookshelf/static/style.css`.
- Forbidden: purple-to-blue gradients on white backgrounds.

### Backgrounds
- Layered CSS gradients or geometric patterns over solid colors.
- Hero sections must have atmospheric depth.

### Motion
- CSS-only for non-React. Motion (formerly Framer Motion) for React.
- One well-orchestrated page-load reveal beats scattered micro-interactions.

## Working Style
- State assumptions before editing.
- If the task is ambiguous, ask one clarifying question.
- Turn each task into verifiable goals before coding.
- Prefer the smallest change that solves the problem.
- Touch only files needed for the requested change.
- Do not refactor unrelated code unless asked.
- Before finishing, verify with the narrowest relevant test.
- Do not commit files unless asked.
- do not touch `uv.lock` manually but use `uv` command to modify it.

## Critical Rules

### Error Handling
- NEVER swallow errors silently
- Always show user feedback for errors (Flask messages, HTMX response headers)
- Log errors with proper context for debugging

### Templates & HTMX
- Use template inheritance (`{% extends %}`, `{% block %}`)
- No inline `<script>`, `<style>`, `style=""` or `on*=` handlers: the Content-Security-Policy (`SECURITY_HEADERS` in `bookshelf/__init__.py`) blocks them. Put JS in `static/js/` (a file per feature, listed in `base.html` and `sw.js`'s `PRECACHE`) and CSS in `style.css`; `tests/e2e/test_csp.py` fails on any violation
- Create partial templates for HTMX responses (`_partial.html` naming)
- Modal dialogs use `<dialog>` with `js/dialogs.js`: `data-dialog-open="<id>"` opens one, `data-dialog-close` closes it, content swapped by htmx into a closed dialog opens it, and `data-dialog-reset` on a dialog brings back the content it first opened with when the user closes it. Keep a no-JS fallback (a page or a dialog rendered `open`)
- When two forms can share a page (e.g. two dialogs), give their element ids a prefix (`_new_password_fields.html` takes `id_prefix`)
- Always include `hx-indicator` for loading states
- Handle `HX-Request` header for partial vs full page responses
- Elements inside an htmx form inherit its `hx-*` attributes (e.g. `hx-swap="outerHTML"`): give inner htmx elements their own `hx-target` and `hx-swap`
- Repeatable rows: a `[data-rows]` container with `[data-row]` rows, a `<template data-row-template>` (`__index__` placeholder), `[data-row-add]` / `[data-row-remove]` submit buttons (the server adds or removes the row without JavaScript)
- Suggestions: an input with `data-suggest`, `role="combobox"`, `aria-controls` and `hx-get` filling its `_suggestion_list.html` listbox with `<li role="option" data-value>` options, inside a `.suggest` wrapper (the form macros' `suggest=True` does this); `js/suggestions.js` sends the text as `q`, shows the list and handles the keys and the pick. Never a `<datalist>`: Firefox opens its dropdown only for the options there at the keystroke, not for those htmx adds after
- WTForms renders `data_*` / `aria_*` keyword arguments with hyphens but not `hx_*`: pass htmx attributes as a dict (`**{"hx-get": ...}`)

### Forms
- Use Flask-WTF (`FlaskForm`) for forms; keep CSRF protection enabled
- Validate with WTForms validators and `validate_<field>()` methods
- Always handle form errors in templates
- Disable submit buttons during HTMX requests

## Testing
- Write failing test first (TDD)
- Use pytest fixtures in `conftest.py`
- Test behavior, not implementation
- Run tests before committing

## Communication
- Be concise.
- Call out trade-offs when multiple reasonable approaches exist.
- Push back if a safer or smaller approach would meet the goal.
