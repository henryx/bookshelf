"""Creating, changing and deleting users."""

from uuid import uuid4

from sqlalchemy import delete, insert, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from bookshelf.db import UNIQUE_KEY_VIOLATION, execute_change, has_gds_code
from bookshelf.db.orm import UserRow
from bookshelf.models.users.listing import row_to_user
from bookshelf.models.users.model import (
    MIN_PASSWORD_LENGTH,
    AuthMethod,
    LastAdminError,
    NoPasswordError,
    Role,
    User,
    UserExistsError,
    normalise_email,
    validate_new_password,
)
from bookshelf.models.users.passwords import credentials, hash_password


def create_user(
    session: Session,
    email: str,
    password: str | None,
    *,
    role: Role = Role.REGULAR,
    name: str | None = None,
    surname: str | None = None,
    auth_method: AuthMethod = AuthMethod.LOCAL,
    min_password_length: int = MIN_PASSWORD_LENGTH,
) -> User:
    """
    Raises UserExistsError if the email is taken, even by a user created
    after the caller checked. A local user needs a password
    (PasswordTooShortError when short or missing); a Google user has none
    (password None, else NoPasswordError) and is bound to their Google
    account on their first login (google_login). Name and surname are
    optional (None), trimmed and non-blank when given.
    """
    email = normalise_email(email)
    password_hash = credentials(email, auth_method, password, min_password_length)
    try:
        row = session.scalars(
            insert(UserRow)
            .values(
                id=uuid4(),
                email=email,
                password_hash=password_hash,
                role=role.value,
                name=name,
                surname=surname,
                auth_method=auth_method.value,
            )
            .returning(UserRow)
        ).one()
    except DBAPIError as e:
        if has_gds_code(e, UNIQUE_KEY_VIOLATION):
            raise UserExistsError(email) from e
        raise
    session.commit()
    return row_to_user(row)


def set_password(
    session: Session,
    user: User,
    password: str,
    *,
    min_password_length: int = MIN_PASSWORD_LENGTH,
) -> None:
    """
    Raises PasswordTooShortError for a short password, and NoPasswordError
    for a Google user (set_auth_method makes them local, with a password).
    """
    if user.auth_method is AuthMethod.GOOGLE:
        raise NoPasswordError(user.email)
    validate_new_password(password, min_length=min_password_length)
    execute_change(
        session,
        update(UserRow)
        .where(UserRow.id == user.id)
        .values(password_hash=hash_password(password)),
    )
    session.commit()


def set_auth_method(
    session: Session,
    user: User,
    auth_method: AuthMethod,
    *,
    password: str | None = None,
    min_password_length: int = MIN_PASSWORD_LENGTH,
) -> None:
    """
    Make the user log in by auth_method, ending their open sessions. Google:
    no password (NoPasswordError if one is given), bound again on their next
    login. Local: with `password` (PasswordTooShortError when short or
    missing).
    """
    password_hash = credentials(user.email, auth_method, password, min_password_length)
    _set_credentials(session, user, auth_method, password_hash)
    session.commit()


def _set_credentials(
    session: Session, user: User, auth_method: AuthMethod, password_hash: str | None
) -> None:
    """How the user logs in; a Google user is bound again on their next login."""
    execute_change(
        session,
        update(UserRow)
        .where(UserRow.id == user.id)
        .values(
            auth_method=auth_method.value,
            password_hash=password_hash,
            google_subject=None,
        ),
    )


def _keep_another_admin(session: Session, user: User) -> None:
    """
    Before `user` stops being an admin: raise LastAdminError (rolling back)
    unless another admin exists. The other admins' rows are updated, unchanged,
    so this transaction holds them: two admins removing each other at once
    then conflict, and one of them fails instead of both succeeding.
    Without an admin, the setup wizard would open to anyone.
    """
    held = execute_change(
        session,
        update(UserRow)
        .where(UserRow.role == Role.ADMIN.value, UserRow.id != user.id)
        .values(role=UserRow.role),
    )
    if held.rowcount == 0:
        session.rollback()
        raise LastAdminError(user.email)


def delete_user(session: Session, user: User) -> None:
    """Raises LastAdminError if `user` is the only admin."""
    if user.is_admin:
        _keep_another_admin(session, user)
    execute_change(session, delete(UserRow).where(UserRow.id == user.id))
    session.commit()


def set_role(session: Session, user: User, role: Role) -> None:
    """Raises LastAdminError when demoting the only admin."""
    if user.is_admin and role is not Role.ADMIN:
        _keep_another_admin(session, user)
    execute_change(
        session, update(UserRow).where(UserRow.id == user.id).values(role=role.value)
    )
    session.commit()


def set_locked(session: Session, user: User, *, locked: bool) -> None:
    execute_change(
        session, update(UserRow).where(UserRow.id == user.id).values(is_locked=locked)
    )
    session.commit()


def update_user(
    session: Session,
    user: User,
    *,
    name: str | None,
    surname: str | None,
    role: Role,
    locked: bool,
    password: str | None,
    auth_method: AuthMethod | None = None,
    min_password_length: int = MIN_PASSWORD_LENGTH,
) -> None:
    """
    An admin's changes, all in one transaction: name, surname, role, lock
    state and how the user logs in (auth_method; None keeps it). A local
    user gets a new password unless password is None; one switching from
    Google needs one. Switching to Google drops the password. Raises
    PasswordTooShortError or NoPasswordError before changing anything, and
    LastAdminError when demoting the only admin.
    """
    auth_method = auth_method or user.auth_method
    switching = auth_method is not user.auth_method
    new_credentials = switching or password is not None
    password_hash = (
        credentials(user.email, auth_method, password, min_password_length)
        if new_credentials
        else None
    )
    if user.is_admin and role is not Role.ADMIN:
        _keep_another_admin(session, user)
    execute_change(
        session,
        update(UserRow)
        .where(UserRow.id == user.id)
        .values(name=name, surname=surname, role=role.value, is_locked=locked),
    )
    if new_credentials:
        _set_credentials(session, user, auth_method, password_hash)
    session.commit()


def update_account(
    session: Session,
    user: User,
    *,
    name: str | None,
    surname: str | None,
    password: str | None,
    min_password_length: int = MIN_PASSWORD_LENGTH,
) -> None:
    """
    A user's changes to their own account: set name, surname and, unless
    password is None, a new password, in one transaction. Role and lock state
    are left alone, so an admin's lock made meanwhile stays. Raises
    PasswordTooShortError, or NoPasswordError for a Google user's password,
    before changing anything.
    """
    if password is not None and user.auth_method is AuthMethod.GOOGLE:
        raise NoPasswordError(user.email)
    changes: dict[str, str | None] = {"name": name, "surname": surname}
    if password is not None:
        validate_new_password(password, min_length=min_password_length)
        changes["password_hash"] = hash_password(password)
    execute_change(
        session, update(UserRow).where(UserRow.id == user.id).values(**changes)
    )
    session.commit()
