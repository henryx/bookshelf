from uuid import UUID

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user
from werkzeug.wrappers import Response as BaseResponse

from bookshelf.db import get_db
from bookshelf.models.users import (
    Role,
    User,
    UserExistsError,
    create_user,
    delete_user,
    find_by_id,
    list_users,
    update_user,
)
from bookshelf.routes import htmx_redirect, is_htmx
from bookshelf.routes.forms import NewAccountForm, UserForm

__all__ = ["admin_route"]

admin_route = Blueprint("admin", __name__, url_prefix="/admin")

CANNOT_DEMOTE_OR_LOCK_SELF = "You cannot demote or lock your own account."


@admin_route.before_request
@login_required
def require_admin() -> None:
    """
    Anonymous users go to the login page, other non-admins get 403. The user
    is reloaded on every request, so a demoted admin loses access at once.
    """
    if not current_user.is_admin:
        abort(403)


def _min_password_length() -> int:
    return current_app.config["MIN_PASSWORD_LENGTH"]


def _user_or_404(user_id: UUID) -> User:
    user = find_by_id(get_db(), user_id)
    if user is None:
        abort(404)
    return user


def _is_self(user: User) -> bool:
    return user.id == current_user.id


def _render_users(form: NewAccountForm) -> str:
    if is_htmx():
        return render_template("admin/_create_user_form.html", form=form)
    return render_template("admin/users.html", form=form, users=list_users(get_db()))


@admin_route.route("/users", methods=("GET", "POST"))
def users() -> BaseResponse | str:
    """List the users and create regular users."""
    form = NewAccountForm()
    if not form.validate_on_submit():
        return _render_users(form)
    try:
        user = create_user(
            get_db(),
            form.email.data or "",
            form.password.data or "",
            min_password_length=_min_password_length(),
        )
    except UserExistsError as e:
        form.form_errors.append(str(e))
        return _render_users(form)

    current_app.logger.info(
        "User %s created by admin %s", user.email, current_user.email
    )
    flash(f"User {user.email} created.")
    return htmx_redirect(url_for("admin.users"))


def _render_user(form: UserForm, user: User) -> str:
    """
    Full page without htmx. With htmx, a GET fills the edit dialog (form and
    delete button); a POST with errors swaps only the form.
    """
    if not is_htmx():
        template = "admin/user.html"
    elif request.method == "GET":
        template = "admin/_user_details.html"
    else:
        template = "admin/_user_form.html"
    return render_template(template, form=form, user=user, is_self=_is_self(user))


def _changes(user: User, role: Role, locked: bool, password: str | None) -> str:
    changes = []
    if role is not user.role:
        changes.append(f"role {role.value}")
    if locked != user.is_locked:
        changes.append("locked" if locked else "unlocked")
    if password is not None:
        changes.append("password reset")
    return ", ".join(changes) or "no changes"


@admin_route.route("/users/<uuid:user_id>", methods=("GET", "POST"))
def user_details(user_id: UUID) -> BaseResponse | str:
    """
    Show a user and save changes to their role, lock state and password.
    Changing the password or locking ends the user's open sessions.
    """
    user = _user_or_404(user_id)
    form = UserForm(data={"role": user.role.value, "locked": user.is_locked})
    if not form.validate_on_submit():
        return _render_user(form, user)

    role = Role(form.role.data)
    locked = bool(form.locked.data)
    password = form.password.data or None
    # The CLI's rule: no admin can demote or lock themselves, so an active
    # admin always remains.
    if _is_self(user) and (role is not Role.ADMIN or locked):
        form.form_errors.append(CANNOT_DEMOTE_OR_LOCK_SELF)
        return _render_user(form, user)

    conn = get_db()
    update_user(
        conn,
        user,
        role=role,
        locked=locked,
        password=password,
        min_password_length=_min_password_length(),
    )
    current_app.logger.info(
        "User %s updated by admin %s: %s",
        user.email,
        current_user.email,
        _changes(user, role, locked, password),
    )
    if password is not None and _is_self(user):
        # The new password invalidates this session too; issue a fresh one.
        login_user(_user_or_404(user_id), remember=True)
    flash(f"User {user.email} saved.")
    return htmx_redirect(url_for("admin.users"))


@admin_route.post("/users/<uuid:user_id>/delete")
def delete(user_id: UUID) -> BaseResponse:
    user = _user_or_404(user_id)
    if _is_self(user):
        abort(400, description="You cannot delete your own account.")
    delete_user(get_db(), user)
    current_app.logger.info(
        "User %s deleted by admin %s", user.email, current_user.email
    )
    flash(f"User {user.email} deleted.")
    return htmx_redirect(url_for("admin.users"))
