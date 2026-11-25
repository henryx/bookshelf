"""
The Flask CLI commands, by who runs them: `database.py` (init-db,
prune-login-failures), `setup.py` (the first admin), `users.py` and
`collection.py` (admins, with their credentials); `common.py` holds what
they share.
"""

from flask import Flask

from bookshelf.cli.collection import copy_collection_command
from bookshelf.cli.database import init_db_command, prune_login_failures_command
from bookshelf.cli.setup import create_admin_command, setup_token_command
from bookshelf.cli.users import (
    create_user_command,
    delete_user_command,
    demote_user_command,
    list_users_command,
    lock_user_command,
    promote_user_command,
    reset_user_password_command,
    set_auth_method_command,
    unlock_user_command,
)

__all__ = ["init_app"]


def init_app(app: Flask) -> None:
    app.cli.add_command(init_db_command)
    app.cli.add_command(prune_login_failures_command)
    app.cli.add_command(setup_token_command)
    app.cli.add_command(create_admin_command)
    app.cli.add_command(create_user_command)
    app.cli.add_command(reset_user_password_command)
    app.cli.add_command(set_auth_method_command)
    app.cli.add_command(promote_user_command)
    app.cli.add_command(demote_user_command)
    app.cli.add_command(lock_user_command)
    app.cli.add_command(unlock_user_command)
    app.cli.add_command(delete_user_command)
    app.cli.add_command(list_users_command)
    app.cli.add_command(copy_collection_command)
