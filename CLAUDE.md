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
- `uv run flask --app bookshelf run --debug`: Start the development server
- `uv sync`: Install dependencies
- `uv run tox -e py3`: Run the test suite
- `uv run tox -e lint`: Run linter
- `uv run tox -e format`: Run formatter
- `uv run tox -e type`: Run type check
- `uv run tox -e audit`: Run auditing
- `uv run tox -e report`: Run report coverage

## Code Style
- Python 3.14 or greater (see `requires-python` in `pyproject.toml`). Use modern syntax:
  - `X | None` instead of `Optional[X]`
  - `list[str]` instead of `List[str]`
  - `dict[str, Any]` instead of `Dict[str, Any]`
  - `tuple[int, str]` instead of `Tuple[int, str]`
  - `type X = ...` for type aliases (Python 3.12)
- Follow PEP 8 style guidelines
- Use type hints for function signatures
- Prefer f-strings for string formatting
- Write clear, descriptive commit messages
- Keep functions small and focused on a single responsibility
- Remove imports, variables, or functions made unused by your own changes.
- Avoid speculative abstractions for single-use code.
- Match statements for enum/type dispatch where appropriate
- No `Any` types - use proper type hints or `object`
- Use early returns, avoid nested conditionals
- Prefer composition to inheritance
- Limit layers in Dockerfile

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
