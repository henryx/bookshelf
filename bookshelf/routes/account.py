from flask import Blueprint, current_app, flash, render_template, url_for
from flask_login import current_user, login_required, login_user
from werkzeug.wrappers import Response as BaseResponse

from bookshelf.db import get_db
from bookshelf.models.users import User, authenticate, find_by_id, update_account
from bookshelf.routes import htmx_redirect, is_htmx
from bookshelf.routes.forms import AccountForm

__all__ = ["account_route"]

account_route = Blueprint("account", __name__)

WRONG_CURRENT_PASSWORD = "Current password is incorrect."


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
    form = AccountForm(data={"name": user.name, "surname": user.surname})
    if not form.validate_on_submit():
        return _render(form)

    conn = get_db()
    password = form.password.data or None
    if password is not None and not authenticate(
        conn, user.email, form.current_password.data or ""
    ):
        current_app.logger.warning(
            "Password change for %s refused: wrong current password", user.email
        )
        form.form_errors.append(WRONG_CURRENT_PASSWORD)
        return _render(form)

    update_account(
        conn,
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
    if password is not None and (renewed := find_by_id(conn, user.id)) is not None:
        login_user(renewed, remember=True)
    flash("Your account was saved.")
    return htmx_redirect(url_for("account.account"))
