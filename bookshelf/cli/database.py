"""The database's upkeep: the schema, and pruning old failed logins (Helm runs both)."""

import click
from flask import current_app
from flask.cli import with_appcontext

from bookshelf.db import get_engine, get_session
from bookshelf.db.schema import init_db
from bookshelf.models.users import prune_login_failures

__all__ = ["init_db_command", "prune_login_failures_command"]


@click.command("init-db")
@with_appcontext
def init_db_command() -> None:
    """Create missing database tables, columns, constraints and indexes."""
    init_db(get_engine(), search_language=current_app.config["SEARCH_LANGUAGE"])
    click.echo("Database initialised.")


@click.command("prune-login-failures")
@with_appcontext
def prune_login_failures_command() -> None:
    """Delete failed logins too old to count towards a block. Safe to run from cron."""
    deleted = prune_login_failures(
        get_session(), window_minutes=current_app.config["LOGIN_BLOCK_MINUTES"]
    )
    current_app.logger.info("Pruned %d expired login failures", deleted)
    click.echo(f"Removed {deleted} expired login failures.")
