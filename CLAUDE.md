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
- `uv run tox -e lint`: Run linter
- `uv run tox -e format`: Run formatter (`-- --check .` to only check, as CI does)
- `uv run tox -e type`: Run type check
- `uv run tox -e audit`: Run auditing
- `uv run tox -e report`: Run report coverage

## Project Layout
- `bookshelf/__init__.py`: `create_app()` factory, error handlers, logging
- `bookshelf/config.py`: `Config` (environment variables)
- `bookshelf/logging_config.py`: JSON logging config shared by the app and gunicorn
- `gunicorn.conf.py`: gunicorn settings (bind, workers, timeout, log level, logging); loaded from the working directory
- `bookshelf/cli.py`: Flask CLI commands; admin operations go through `_require_admin`
- `bookshelf/db/`: per-process `ConnectionPool`; `get_db` takes a connection for the app context and teardown returns it (rolled back); `schema.py` holds table DDL and `init_db`
- `bookshelf/models/`: data access and domain logic (`users.py`)
- `bookshelf/routes/`: blueprints (`auth`, `main`, `setup`, `admin`); `__init__.py` has the `is_htmx()` and `htmx_redirect()` helpers; `forms.py` holds the Flask-WTF forms
- `bookshelf/routes/admin.py`: admin area (`/admin/users`), admins only; users list, create, details/edit, delete
- `bookshelf/routes/setup.py`: first-run wizard; redirects every request to `/setup` until an admin exists
- `bookshelf/templates/`, `bookshelf/static/`: Jinja templates and CSS; htmx is vendored in `static/`; `app.js` holds the error-banner and dialog handlers
- `tests/conftest.py`: session Firebird container, `app`/`db_app`/`empty_db_app` fixtures; autouse `setup_complete` skips the first-run wizard (overridden in `test_setup.py`)
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
  - The schema (`DOMAINS`, `TABLES`, `INDEXES` in `bookshelf/db/schema.py`) is not final until the first release is tagged:
    edit `CREATE TABLE` statements in place, with no migration steps.
    Once a release is tagged, released tables must not be edited in place; changes then need upgrade steps.
  - Write each query as a complete literal string.
  - Never use f-strings or other interpolation for column names, table names or clauses.
  - Pass values only as `?` parameters.
- Other:
  - Write clear, descriptive commit messages

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
- Create partial templates for HTMX responses (`_partial.html` naming)
- Modal dialogs use `<dialog>` with `app.js`: `data-dialog-open="<id>"` opens one, `data-dialog-close` closes it, and content swapped by htmx into a closed dialog opens it. Keep a no-JS fallback (a page or a dialog rendered `open`)
- When two forms can share a page (e.g. two dialogs), give their element ids a prefix (`_new_password_fields.html` takes `id_prefix`)
- Always include `hx-indicator` for loading states
- Handle `HX-Request` header for partial vs full page responses

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
