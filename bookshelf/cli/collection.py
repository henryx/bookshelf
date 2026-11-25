"""Copying a user's collection to another user (admins)."""

import click
from flask import current_app
from flask.cli import with_appcontext

from bookshelf.cli.common import admin_email_option, require_admin, require_user
from bookshelf.db import get_session
from bookshelf.models.books import CopyMode, copy_collection

__all__ = ["copy_collection_command"]


@click.command("copy-collection")
@click.argument("source_email")
@click.argument("target_email")
@click.option(
    "--mode",
    type=click.Choice([m.value.lower() for m in CopyMode], case_sensitive=False),
    default=CopyMode.KEEP.value.lower(),
    show_default=True,
    help=(
        "Books the target already has: keep theirs (add only the books they lack), "
        "add these copies to theirs (up to 999), or replace their whole collection."
    ),
)
@admin_email_option
@click.option("--yes", is_flag=True, help="Do not ask for confirmation (replace).")
@with_appcontext
def copy_collection_command(
    source_email: str, target_email: str, mode: str, admin_email: str, yes: bool
) -> None:
    """
    Copy a user's collection into another user's. Copied books keep their
    copies and start unread; the source's collection is unchanged. Requires
    admin credentials.
    """
    session = get_session()
    admin = require_admin(session, admin_email, "Copy collection")
    source = require_user(session, source_email)
    target = require_user(session, target_email)
    if source.id == target.id:
        raise click.ClickException("A collection cannot be copied to its own user")
    copy_mode = CopyMode(mode.upper())
    if copy_mode is CopyMode.REPLACE and not yes:
        click.confirm(
            f"Replace the collection of {target.email} with that of {source.email}? "
            "Their books are removed first",
            abort=True,
        )

    copied = copy_collection(session, source.id, target.id, copy_mode)
    current_app.logger.info(
        "Collection of %s copied to %s (%s) by admin %s: %s",
        source.email,
        target.email,
        copy_mode.value.lower(),
        admin.email,
        copied,
    )
    click.echo(
        f"Collection of {source.email} copied to {target.email}: "
        f"{copied.summary(copy_mode)}."
    )
