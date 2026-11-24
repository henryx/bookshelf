from datetime import UTC, datetime

from flask import Blueprint, Response, current_app, flash, render_template, url_for
from flask_login import current_user, login_required, login_user
from werkzeug.wrappers import Response as BaseResponse

from bookshelf.db import get_session
from bookshelf.models.collection_csv import (
    CsvFileError,
    ImportReport,
    export_collection,
    import_collection,
)
from bookshelf.models.users import User, authenticate, find_by_id, update_account
from bookshelf.routes import htmx_redirect, is_htmx
from bookshelf.routes.forms import AccountForm, ImportForm

__all__ = ["account_route"]

account_route = Blueprint("account", __name__)

WRONG_CURRENT_PASSWORD = "Current password is incorrect."


def _account_form(user: User) -> AccountForm:
    return AccountForm(data={"name": user.name, "surname": user.surname})


def _render(form: AccountForm) -> str:
    template = "_account_form.html" if is_htmx() else "account.html"
    return render_template(template, form=form)


@account_route.route("/account", methods=("GET", "POST"))
@login_required
def account() -> BaseResponse | str:
    """
    The current user's own name, surname and password. A new password needs
    the current one, so a session left open cannot be used to take the
    account over; it ends the user's other sessions but renews this one.
    """
    user: User = current_user._get_current_object()
    form = _account_form(user)
    if not form.validate_on_submit():
        return _render(form)

    session = get_session()
    password = form.password.data or None
    if password is not None and not authenticate(
        session, user.email, form.current_password.data or ""
    ):
        current_app.logger.warning(
            "Password change for %s refused: wrong current password", user.email
        )
        form.form_errors.append(WRONG_CURRENT_PASSWORD)
        return _render(form)

    update_account(
        session,
        user,
        name=form.name.data,
        surname=form.surname.data,
        password=password,
        min_password_length=current_app.config["MIN_PASSWORD_LENGTH"],
    )
    current_app.logger.info(
        "Account of %s updated%s",
        user.email,
        " (password changed)" if password is not None else "",
    )
    # The new password invalidates this session too: issue a fresh one (unless
    # the account was deleted meanwhile, which ends the session anyway).
    if password is not None and (renewed := find_by_id(session, user.id)) is not None:
        login_user(renewed, remember=True)
    flash("Your account was saved.")
    return htmx_redirect(url_for("account.account"))


@account_route.get("/account/export")
@login_required
def export() -> Response:
    """The current user's collection as a CSV download."""
    text = export_collection(get_session(), current_user.id)
    current_app.logger.info("Collection of %s exported", current_user.email)
    filename = f"bookshelf-collection-{datetime.now(UTC).date().isoformat()}.csv"
    return Response(
        text,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _import_notice(report: ImportReport) -> str:
    """What an import did: "2 books added to your collection (1 new to the catalogue)."."""
    if report.added:
        notice = f"{_plural(report.added, 'book')} added to your collection"
        if report.created:
            notice += f" ({report.created} new to the catalogue)"
    else:
        notice = "No books added"
    if report.skipped:
        notice += f"; {report.skipped} already in your collection, skipped"
    if report.ignored_columns:
        columns = ", ".join(report.ignored_columns)
        notice += (
            f". Ignored {_plural(len(report.ignored_columns), 'column')}: {columns}"
        )
    return f"{notice}."


def _render_import(
    *,
    notice: str | None = None,
    error: str | None = None,
    error_details: list[str] | None = None,
) -> str:
    """
    The import's outcome: for htmx the CSV section alone, else the account
    page (no redirect: the report is too long for a flashed message, and
    posting the file again only skips books).
    """
    report = {
        "import_notice": notice,
        "import_error": error,
        "import_error_details": error_details,
    }
    if is_htmx():
        return render_template("_collection_csv.html", **report)
    form = _account_form(current_user._get_current_object())
    return render_template("account.html", form=form, **report)


@account_route.post("/account/import")
@login_required
def import_csv() -> str:
    """Add the books of an uploaded CSV file to the current user's collection."""
    form = ImportForm()
    if not form.validate_on_submit():
        return _render_import(
            error=" ".join(form.file.errors) or "The file could not be read."
        )
    try:
        text = form.file.data.read().decode("utf-8-sig")
        report = import_collection(get_session(), current_user.id, text)
    except UnicodeDecodeError:
        current_app.logger.info("Import by %s refused: not UTF-8", current_user.email)
        return _render_import(error="The file is not UTF-8 text.")
    except CsvFileError as e:
        current_app.logger.info("Import by %s refused: %s", current_user.email, e)
        return _render_import(error=str(e))
    current_app.logger.info(
        "Import by %s: %d added (%d new to the catalogue), %d skipped, %d errors",
        current_user.email,
        report.added,
        report.created,
        report.skipped,
        len(report.errors),
    )
    error = None
    if report.errors:
        error = f"{_plural(len(report.errors), 'row')} not imported:"
    return _render_import(
        notice=_import_notice(report), error=error, error_details=report.errors
    )
