"""
Users: who they are and their role, how they log in (a password or a Google
account), creating, changing and deleting them, and the login limits.
"""

from bookshelf.models.users.editing import (
    create_user,
    delete_user,
    set_auth_method,
    set_locked,
    set_password,
    set_role,
    update_account,
    update_user,
)
from bookshelf.models.users.listing import (
    admin_exists,
    find_by_email,
    find_by_id,
    find_by_session_id,
    list_users,
)
from bookshelf.models.users.login import authenticate, google_login, record_login
from bookshelf.models.users.login_limits import (
    clear_login_failures,
    prune_login_failures,
    start_login_attempt,
)
from bookshelf.models.users.model import (
    MIN_PASSWORD_LENGTH,
    AuthMethod,
    LastAdminError,
    NoPasswordError,
    PasswordTooShortError,
    Role,
    User,
    UserExistsError,
    validate_new_password,
)

__all__ = [
    "MIN_PASSWORD_LENGTH",
    "AuthMethod",
    "LastAdminError",
    "NoPasswordError",
    "PasswordTooShortError",
    "Role",
    "User",
    "UserExistsError",
    "admin_exists",
    "authenticate",
    "clear_login_failures",
    "create_user",
    "delete_user",
    "find_by_email",
    "find_by_id",
    "find_by_session_id",
    "google_login",
    "list_users",
    "prune_login_failures",
    "record_login",
    "set_auth_method",
    "set_locked",
    "set_password",
    "set_role",
    "start_login_attempt",
    "update_account",
    "update_user",
    "validate_new_password",
]
