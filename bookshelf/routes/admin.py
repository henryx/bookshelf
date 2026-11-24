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
from wtforms import SelectChoice

from bookshelf.db import get_session
from bookshelf.models.books import CopyMode, copy_collection
from bookshelf.models.users import (
    AuthMethod,
    LastAdminError,
    NoPasswordError,
    PasswordTooShortError,
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
from bookshelf.routes.forms import CopyCollectionForm, NewUserForm, UserForm

__all__ = ["admin_route"]

admin_route = Blueprint("admin", __name__, url_prefix="/admin")

CANNOT_DEMOTE_OR_LOCK_SELF = "You cannot demote or lock your own account."
CANNOT_SWITCH_OWN_LOGIN = "You cannot change how your own account logs in."


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
    user = find_by_id(get_session(), user_id)
    if user is None:
        abort(404)
    return user


def _is_self(user: User) -> bool:
    return user.id == current_user.id


def _render_users(form: NewUserForm) -> str:
    if is_htmx():
        return render_template("admin/_create_user_form.html", form=form)
    return render_template(
        "admin/users.html", form=form, users=list_users(get_session())
    )


@admin_route.route("/users", methods=("GET", "POST"))
def users() -> BaseResponse | str:
    """List the users and create regular users, logging in locally or with Google."""
    form = NewUserForm()
    if not form.validate_on_submit():
        return _render_users(form)
    try:
        user = create_user(
            get_session(),
            form.email.data or "",
            form.password.data or None,
            name=form.name.data,
            surname=form.surname.data,
            auth_method=AuthMethod(form.auth_method.data),
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


def _user_form(user: User, *, from_request: bool = True) -> UserForm:
    """The user's edit form: filled from the request's form data, else from the user."""
    data = {
        "name": user.name,
        "surname": user.surname,
        "role": user.role.value,
        "locked": user.is_locked,
        "auth_method": user.auth_method.value,
    }
    if from_request:
        return UserForm(data=data)
    return UserForm(formdata=None, data=data)


def _copy_form(user: User, *, from_request: bool = True) -> CopyCollectionForm:
    """The form copying the user's collection, offering every other user."""
    form = CopyCollectionForm() if from_request else CopyCollectionForm(formdata=None)
    form.target.choices = [SelectChoice("", "Choose a user…")] + [
        SelectChoice(str(other.id), other.email)
        for other in list_users(get_session())
        if other.id != user.id
    ]
    return form


def _render_user(
    form: UserForm, user: User, copy_form: CopyCollectionForm | None = None
) -> str:
    """
    Full page without htmx. With htmx, a GET fills the edit dialog (forms and
    delete button); a POST with errors swaps only the form posted.
    """
    if copy_form is None:
        copy_form = _copy_form(user, from_request=False)
    if not is_htmx():
        template = "admin/user.html"
    elif request.method == "GET":
        template = "admin/_user_details.html"
    elif copy_form.errors or copy_form.form_errors:
        template = "admin/_copy_collection_form.html"
    else:
        template = "admin/_user_form.html"
    return render_template(
        template, form=form, copy_form=copy_form, user=user, is_self=_is_self(user)
    )


def _changes(
    user: User,
    name: str | None,
    surname: str | None,
    role: Role,
    locked: bool,
    password: str | None,
    auth_method: AuthMethod,
) -> str:
    changes = []
    if auth_method is not user.auth_method:
        changes.append(f"login {auth_method.value.lower()}")
    if (name, surname) != (user.name, user.surname):
        changes.append("name")
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
    Show a user and save changes to their role, lock state, how they log in
    and password. Changing the password or how they log in, or locking, ends
    the user's open sessions.
    """
    user = _user_or_404(user_id)
    form = _user_form(user)
    if not form.validate_on_submit():
        return _render_user(form, user)

    name = form.name.data
    surname = form.surname.data
    role = Role(form.role.data)
    locked = bool(form.locked.data)
    password = form.password.data or None
    auth_method = AuthMethod(form.auth_method.data)
    # The CLI's rule: no admin can demote or lock themselves, so an active
    # admin always remains.
    if _is_self(user) and (role is not Role.ADMIN or locked):
        form.form_errors.append(CANNOT_DEMOTE_OR_LOCK_SELF)
        return _render_user(form, user)
    # Nor switch their own login: a Google login not set up would lock them out.
    if _is_self(user) and auth_method is not user.auth_method:
        form.form_errors.append(CANNOT_SWITCH_OWN_LOGIN)
        return _render_user(form, user)

    try:
        update_user(
            get_session(),
            user,
            name=name,
            surname=surname,
            role=role,
            locked=locked,
            password=password,
            auth_method=auth_method,
            min_password_length=_min_password_length(),
        )
    except LastAdminError as e:
        current_app.logger.warning("Refused to demote %s: %s", user.email, e)
        form.form_errors.append(f"{e}: it cannot be demoted.")
        return _render_user(form, user)
    except (PasswordTooShortError, NoPasswordError) as e:
        # A user switched from Google to a password, without one.
        form.form_errors.append(str(e))
        return _render_user(form, user)
    current_app.logger.info(
        "User %s updated by admin %s: %s",
        user.email,
        current_user.email,
        _changes(user, name, surname, role, locked, password, auth_method),
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
    try:
        delete_user(get_session(), user)
    except LastAdminError as e:
        current_app.logger.warning("Refused to delete %s: %s", user.email, e)
        abort(409, description=f"{e}: it cannot be deleted.")
    current_app.logger.info(
        "User %s deleted by admin %s", user.email, current_user.email
    )
    flash(f"User {user.email} deleted.")
    return htmx_redirect(url_for("admin.users"))


@admin_route.post("/users/<uuid:user_id>/copy-collection")
def copy_user_collection(user_id: UUID) -> BaseResponse | str:
    """
    Copy the user's collection into another user's, as the admin chose:
    keeping, adding to or replacing the books the other user already has.
    """
    user = _user_or_404(user_id)
    form = _copy_form(user)
    if not form.validate_on_submit():
        return _render_user(_user_form(user, from_request=False), user, form)
    target = find_by_id(get_session(), UUID(form.target.data))
    if target is None:
        # Deleted since the form was shown.
        form.form_errors.append("That user no longer exists.")
        return _render_user(_user_form(user, from_request=False), user, form)

    mode = CopyMode(form.mode.data)
    copied = copy_collection(get_session(), user.id, target.id, mode)
    current_app.logger.info(
        "Collection of %s copied to %s (%s) by admin %s: %s",
        user.email,
        target.email,
        mode.value.lower(),
        current_user.email,
        copied,
    )
    flash(
        f"Collection of {user.email} copied to {target.email}: {copied.summary(mode)}."
    )
    return htmx_redirect(url_for("admin.users"))
