# Bookshelf library webapp

## Project Overview
Bookshelf library webapp is a webapp to manage a book library built with Flask using Python.

## Stack
- **Frontend**: HTMX
- **Backend**: Flask
- **Language**: Python
- **Package** manager: uv
- **Styling**: Plain CSS
- **Database**: Firebird
- **Hosting**: Self-hosted
- **Testing**: tox (run via `uv run tox`)

## Common Commands
- `uv sync`: Install dependencies
- `uv run dotenv -f dev.env run flask --app bookshelf run --debug`: Start the development server (needs the local Firebird from README.md)
- `uv run dotenv -f dev.env run flask --app bookshelf <command>`: Run a CLI command (`init-db`, `create-admin`, `list-users`, …)
- `uv run tox -e py3`: Run the test suite (Docker must be running: tests start Firebird via testcontainers)
- `uv run tox -e e2e`: Run the browser tests of `app.js` (Playwright with Chromium, installed by the env; Docker must be running). Passing posargs replaces the default path: `uv run tox -e e2e -- tests/e2e -k banner`
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
- `bookshelf/db/`: per-process `ConnectionPool`; `get_db` takes a connection for the app context and teardown returns it (rolled back); `schema.py` holds the DDL (domains, tables, constraints, indexes, and the `BOOK_MATCHES` search function) and `init_db`
- `bookshelf/models/`: data access and domain logic (`users.py`: users with optional name and surname, login limits; `books/`: a package whose `__init__.py` is the API the app imports (`bookshelf.models.books`): `model.py` data types, errors and limits, `isbn.py` ISBN checks, `listing.py` book lists and finding a book, `collection.py` the user's collection, `editing.py` add, edit and delete book (with its authors and categories), `suggestions.py` book, author, publisher and category suggestions; `authors.py`: authors list, renaming and merging; `collection_csv.py`: a collection's CSV export and import)
- `bookshelf/routes/`: blueprints (`auth`, `main`, `collection`, `authors`, `account`, `setup`, `admin`, `pwa`); `__init__.py` has the `is_htmx()` and `htmx_redirect()` helpers; `forms.py` holds the Flask-WTF forms
- `bookshelf/routes/main.py`: the catalogue (`/catalogue`), add book (`/books/new`), edit book (`/books/<id>`), delete book (`/books/<id>/delete`, admins only), suggestions (`/books/suggestions` for the add form's title and ISBN, `/authors/suggestions`, `/publishers/suggestions`, `/categories/suggestions`); `/books/new?book=<id>` is the add form filled, read-only, with a picked catalogue book, to add it to the collection; `_change_rows()` handles the author and category rows' + and − buttons without JavaScript; `list_state()` / `list_url()` read a list's search, order and page, `render_catalogue()` renders the catalogue; `book_list_endpoint()` is the list a form came from (`list=collection`, else the catalogue: the add-book form of both pages carries it); both book lists take `?author=<id>` for one author's books (`author_filter()`)
- `bookshelf/routes/collection.py`: the current user's collection (`/`), and adding a book to it, marking it read and removing it (`/collection/books/<id>/add|read|remove`); its copies are changed in the edit-book dialog (`main.book`, `EditBookForm.copies`) (the first page: login, setup and the top bar's title lead to it)
- `bookshelf/routes/authors.py`: the authors list (`/authors`) and renaming an author for every book (`/authors/<id>`)
- `bookshelf/routes/account.py`: the current user's account page (`/account`): name, surname, password (needs the current one); CSV export and import of their collection (`/account/export`, `/account/import`, the `_collection_csv.html` card)
- `bookshelf/routes/admin.py`: admin area (`/admin/users`), admins only; users list, create, details/edit, delete
- `bookshelf/routes/setup.py`: first-run wizard; redirects every request to `/setup` until an admin exists (not static files or `pwa`)
- `bookshelf/routes/pwa.py`: the service worker (`/sw.js`, from `static/sw.js`) and its `/offline` page, no login needed; `static/manifest.webmanifest` and `static/icons/` make the app installable; the worker shows the collection and catalogue last seen online, read-only, when the server can't be reached (`<body data-offline>`, handled in `app.js`)
- `bookshelf/templates/`, `bookshelf/static/`: Jinja templates and CSS; htmx is vendored in `static/`; `app.js` holds the error-banner, dialog, account-menu, repeatable-row, suggestion and book-pick (`[data-book-pick]`, `[data-book-loader]`) handlers; `_topbar.html` is the top bar (its title links to the collection) with the account menu (`<details class="avatar">`); `_name_fields.html` renders a person's name and surname (takes `id_prefix`); `_author_rows.html` and `_category_rows.html` render a book form's author and category rows (take `id_prefix`: `book-` in the add form, `edit-book-` in the edit form); `_book_list.html` and `_book_search.html` serve both the catalogue and the collection (`list_endpoint`, and `author` for one author's books); `_book_details.html` / `_edit_book_form.html` are the edit-book dialog (with the admins' delete form); `_book_list_state.html` holds the hidden fields that take a form back to the list it came from; `_author_list.html` / `_rename_author_form.html` the authors page
- `bookshelf/static/fonts/`: vendored Fraunces, Bricolage Grotesque and JetBrains Mono (latin and latin-ext WOFF2, with their OFL licences); `@font-face` rules are at the top of `style.css`
- `bookshelf/templates/_hero.html`: `hero(tall=False)` macro, the bookcloth band with the shelf of spines; pages fill `base.html`'s `hero` block with it (tall on main, login and setup, with the page-load reveal; slim elsewhere)
- `tests/conftest.py`: session Firebird container, `app`/`db_app`/`empty_db_app` fixtures; autouse `setup_complete` skips the first-run wizard (overridden in `test_setup.py`)
- `tests/e2e/`: Playwright tests of `app.js` against a live server (`live_server`, `reader_page`, `admin_page` fixtures); `py3` skips them, the `e2e` tox env runs them
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
- SQL:
  - The schema (`DOMAINS`, `TABLES`, `CONSTRAINTS`, `INDEXES` in `bookshelf/db/schema.py`) is frozen since 1.0.0:
    never edit a released definition in place. New domains, tables and indexes are fine (`init_db` creates missing objects);
    changing a released one needs an upgrade step applied by `init_db` (no mechanism yet: it comes with the first change that needs it, see README "Schema changes").
  - `FUNCTIONS` (PSQL functions such as `BOOK_MATCHES`, the book lists' search condition) are `CREATE OR ALTER`ed on every `init_db`: edit them in place at any time.
  - Write each query as a complete literal string.
  - Never use f-strings or other interpolation for column names, table names or clauses.
  - Pass values only as `?` parameters.
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
- No inline `<script>`, `<style>`, `style=""` or `on*=` handlers: the Content-Security-Policy (`SECURITY_HEADERS` in `bookshelf/__init__.py`) blocks them. Put JS in `app.js` and CSS in `style.css`; `tests/e2e/test_csp.py` fails on any violation
- Create partial templates for HTMX responses (`_partial.html` naming)
- Modal dialogs use `<dialog>` with `app.js`: `data-dialog-open="<id>"` opens one, `data-dialog-close` closes it, and content swapped by htmx into a closed dialog opens it. Keep a no-JS fallback (a page or a dialog rendered `open`)
- When two forms can share a page (e.g. two dialogs), give their element ids a prefix (`_new_password_fields.html` takes `id_prefix`)
- Always include `hx-indicator` for loading states
- Handle `HX-Request` header for partial vs full page responses
- Elements inside an htmx form inherit its `hx-*` attributes (e.g. `hx-swap="outerHTML"`): give inner htmx elements their own `hx-target` and `hx-swap`
- Repeatable rows: a `[data-rows]` container with `[data-row]` rows, a `<template data-row-template>` (`__index__` placeholder), `[data-row-add]` / `[data-row-remove]` submit buttons (the server adds or removes the row without JavaScript)
- Suggestions: an input with `data-suggest`, `list` and `hx-get` filling its `<datalist>`; `app.js` sends the text as `q`
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
