"""Upgrade steps: init_db takes a database to the latest schema version."""

from uuid import uuid4

import pytest
from firebird.driver import Connection
from flask import Flask

from bookshelf.db import get_db, upgrades
from bookshelf.db.schema import init_db, schema_behind
from bookshelf.db.upgrades import SchemaTooNewError, UpgradeStep


def _column_exists(conn: Connection, table: str, column: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM RDB$RELATION_FIELDS "
            "WHERE RDB$RELATION_NAME = ? AND RDB$FIELD_NAME = ?",
            (table, column),
        )
        return cur.fetchone() is not None


def _add_note(conn: Connection) -> None:
    """Add BOOKS.NOTE and fill it: two commits, as Firebird needs."""
    if not _column_exists(conn, "BOOKS", "NOTE"):
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE BOOKS ADD NOTE VARCHAR(16)")
        conn.commit()
    with conn.cursor() as cur:
        cur.execute("UPDATE BOOKS SET NOTE = 'noted' WHERE NOTE IS NULL")


def _drop_note(conn: Connection) -> None:
    if _column_exists(conn, "BOOKS", "NOTE"):
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE BOOKS DROP NOTE")
        conn.commit()


ADD_NOTE = UpgradeStep(upgrade=_add_note, rollback=_drop_note)


class _Step:
    """
    A step adding BOOKS.VERSE, recording its calls; it fails after adding the
    column while `fail_upgrade` is set, and before undoing anything while
    `fail_rollback` is set.
    """

    def __init__(self, *, fail_upgrade: bool = False, fail_rollback: bool = False):
        self.fail_upgrade = fail_upgrade
        self.fail_rollback = fail_rollback
        self.calls: list[str] = []

    def upgrade(self, conn: Connection) -> None:
        self.calls.append("upgrade")
        if not _column_exists(conn, "BOOKS", "VERSE"):
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE BOOKS ADD VERSE VARCHAR(16)")
            conn.commit()
        if self.fail_upgrade:
            raise RuntimeError("upgrade failed")

    def rollback(self, conn: Connection) -> None:
        self.calls.append("rollback")
        if self.fail_rollback:
            raise RuntimeError("rollback failed")
        if _column_exists(conn, "BOOKS", "VERSE"):
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE BOOKS DROP VERSE")
            conn.commit()

    @property
    def step(self) -> UpgradeStep:
        return UpgradeStep(upgrade=self.upgrade, rollback=self.rollback)


def _setting(conn: Connection, name: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute('SELECT "VALUE" FROM SETTINGS WHERE NAME = ?', (name,))
        row = cur.fetchone()
    return row[0] if row else None


def _state(conn: Connection) -> tuple[int, int | None]:
    """The schema version, and the step in progress (None when there is none)."""
    version = _setting(conn, "SCHEMA_VERSION")
    step = _setting(conn, "SCHEMA_UPGRADE_STEP")
    assert version is not None
    return int(version), int(step) if step is not None else None


def _notes(conn: Connection) -> list[str | None]:
    with conn.cursor() as cur:
        cur.execute("SELECT NOTE FROM BOOKS")
        return [note for (note,) in cur.fetchall()]


def _add_book(conn: Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO BOOKS (ID, TITLE) VALUES (?, 'Dune')", (uuid4().bytes,)
        )
    conn.commit()


@pytest.fixture
def steps(monkeypatch: pytest.MonkeyPatch):
    """Set the upgrade steps init_db knows."""

    def use(*steps: UpgradeStep) -> None:
        monkeypatch.setattr(upgrades, "UPGRADES", list(steps))

    return use


# Versions


def test_new_database_takes_every_step(empty_db_app: Flask, steps):
    steps(ADD_NOTE)

    with empty_db_app.app_context():
        conn = get_db()
        init_db(conn)

        assert _setting(conn, "SCHEMA_VERSION") == "1"
        assert _setting(conn, "SCHEMA_UPGRADE_STEP") is None
        assert _column_exists(conn, "BOOKS", "NOTE")
        assert not schema_behind(conn)


def test_database_without_versions_is_at_version_0(empty_db_app: Flask, steps):
    """A database created before versions (1.0.x) is the frozen 1.0.0 schema."""
    steps()
    with empty_db_app.app_context():
        conn = get_db()
        init_db(conn)
        with conn.cursor() as cur:
            cur.execute("DROP TABLE SETTINGS")
        conn.commit()
        _add_book(conn)
        steps(ADD_NOTE)
        assert schema_behind(conn)

        init_db(conn)

        assert _state(conn) == (1, None)
        assert _notes(conn) == ["noted"]
        assert not schema_behind(conn)


def test_each_step_runs_once_in_order(empty_db_app: Flask, steps):
    first, second = _Step(), _Step()
    steps(first.step, second.step)

    with empty_db_app.app_context():
        conn = get_db()
        init_db(conn)
        init_db(conn)

        assert (first.calls, second.calls) == (["upgrade"], ["upgrade"])
        assert _state(conn) == (2, None)


def test_only_new_steps_run(empty_db_app: Flask, steps):
    later = _Step()
    steps(ADD_NOTE)
    with empty_db_app.app_context():
        conn = get_db()
        init_db(conn)
        _add_book(conn)
        steps(ADD_NOTE, later.step)

        init_db(conn)

        assert later.calls == ["upgrade"]
        assert _state(conn) == (2, None)


# Rollback


def test_a_failed_step_is_rolled_back(empty_db_app: Flask, steps):
    failing = _Step(fail_upgrade=True)
    steps(ADD_NOTE, failing.step)

    with empty_db_app.app_context():
        conn = get_db()
        with pytest.raises(RuntimeError, match="upgrade failed"):
            init_db(conn)

        assert failing.calls == ["upgrade", "rollback"]
        assert not _column_exists(conn, "BOOKS", "VERSE")
        assert _state(conn) == (1, None)
        assert schema_behind(conn)


def test_a_step_fixed_after_failing_applies(empty_db_app: Flask, steps):
    step = _Step(fail_upgrade=True)
    steps(step.step)
    with empty_db_app.app_context():
        conn = get_db()
        with pytest.raises(RuntimeError):
            init_db(conn)
        step.fail_upgrade = False

        init_db(conn)

        assert _column_exists(conn, "BOOKS", "VERSE")
        assert _state(conn) == (1, None)


def test_a_failed_rollback_is_run_again_by_the_next_init_db(empty_db_app: Flask, steps):
    step = _Step(fail_upgrade=True, fail_rollback=True)
    steps(step.step)
    with empty_db_app.app_context():
        conn = get_db()
        with pytest.raises(RuntimeError, match="rollback failed"):
            init_db(conn)
        assert _state(conn) == (0, 1)
        assert schema_behind(conn)
        step.fail_upgrade = step.fail_rollback = False
        step.calls.clear()

        init_db(conn)

        assert step.calls == ["rollback", "upgrade"]
        assert _state(conn) == (1, None)


def test_a_step_killed_halfway_is_rolled_back_then_applied(empty_db_app: Flask, steps):
    steps()
    with empty_db_app.app_context():
        conn = get_db()
        init_db(conn)
        _add_book(conn)
        # What a step killed after adding its column leaves.
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO SETTINGS (NAME, \"VALUE\") VALUES ('SCHEMA_UPGRADE_STEP', '1')"
            )
            cur.execute("ALTER TABLE BOOKS ADD VERSE VARCHAR(16)")
        conn.commit()
        step = _Step()
        steps(step.step)

        init_db(conn)

        assert step.calls == ["rollback", "upgrade"]
        assert _state(conn) == (1, None)


def test_a_failed_steps_uncommitted_work_is_discarded(empty_db_app: Flask, steps):
    def add_book_then_fail(conn: Connection) -> None:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO BOOKS (ID, TITLE) VALUES (?, 'Dune')", (uuid4().bytes,)
            )
        raise RuntimeError("upgrade failed")

    steps(UpgradeStep(upgrade=add_book_then_fail, rollback=lambda _conn: None))
    with empty_db_app.app_context():
        conn = get_db()
        with pytest.raises(RuntimeError, match="upgrade failed"):
            init_db(conn)

        assert _book_count(conn) == 0
        assert _state(conn) == (0, None)


def _book_count(conn: Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM BOOKS")
        (count,) = cur.fetchone()
    return count


# Refusals and locking


def test_database_newer_than_the_code_is_refused(empty_db_app: Flask, steps):
    steps(ADD_NOTE)
    with empty_db_app.app_context():
        conn = get_db()
        init_db(conn)
        steps()

        with pytest.raises(SchemaTooNewError, match="version 1.*knows up to 0"):
            init_db(conn)
        assert not schema_behind(conn)


def test_a_newer_step_left_halfway_is_refused(empty_db_app: Flask, steps):
    """This release cannot roll back a step it does not know."""
    steps()
    with empty_db_app.app_context():
        conn = get_db()
        init_db(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO SETTINGS (NAME, \"VALUE\") VALUES ('SCHEMA_UPGRADE_STEP', '1')"
            )
        conn.commit()

        with pytest.raises(SchemaTooNewError, match="version 1.*knows up to 0"):
            init_db(conn)
        assert _state(conn) == (0, 1)


def test_a_second_init_db_waits_for_the_first(app: Flask, other_conn, race):
    with app.app_context():
        init_db(get_db())
    # Another init_db holds the lock; it always rolls back what holds it.
    with other_conn.cursor() as cur:
        cur.execute(
            'UPDATE SETTINGS SET "VALUE" = "VALUE" WHERE NAME = \'SCHEMA_UPGRADE_LOCK\''
        )

    race(init_db, "UPDATE SETTINGS", commit=False)


# Requests


def test_requests_wait_while_the_schema_is_behind(app: Flask, steps, caplog):
    """The session database has every real step: one more puts it behind."""
    steps(*upgrades.UPGRADES, ADD_NOTE)
    client = app.test_client()

    rv = client.get("/login")

    assert rv.status_code == 503
    assert "run init-db" in rv.get_data(as_text=True)
    assert "Database schema is behind the code: run init-db" in caplog.text
    assert client.get("/static/style.css").status_code == 200


def test_requests_are_served_once_init_db_ran(empty_db_app: Flask, steps):
    steps(ADD_NOTE)
    with empty_db_app.app_context():
        init_db(get_db())

    assert empty_db_app.test_client().get("/login").status_code == 200


def test_requests_are_served_while_the_schema_is_ahead(empty_db_app: Flask, steps):
    """The previous release's processes, while the next one's steps are applied."""
    steps(ADD_NOTE)
    with empty_db_app.app_context():
        init_db(get_db())
    steps()

    assert empty_db_app.test_client().get("/login").status_code == 200
