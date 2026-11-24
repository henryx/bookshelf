import itertools
import os
import shutil
import sys
import time
import types
from collections.abc import Callable, Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast
from uuid import uuid4

import pytest
import requests
from alembic.script import ScriptDirectory
from firebird.driver import DatabaseError, connect, create_database
from flask import Flask
from flask.testing import FlaskClient
from psycopg.errors import DeadlockDetected
from raw_db import BACKEND, RawConnection, raw
from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy import Connection as SAConnection
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer, Reaper
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from bookshelf import create_app
from bookshelf.config import DatabaseBackend
from bookshelf.db import (
    close_pool,
    create_db_engine,
    get_session,
    schema,
)
from bookshelf.db.schema import init_db
from bookshelf.models.users import User, create_user

PASSWORD = "correct horse battery staple"
FIREBIRD_IMAGE = "firebirdsql/firebird:3.0.14"
FIREBIRD_PASSWORD = "masterkey"  # throwaway test container only
POSTGRESQL_IMAGE = "postgres:18"
POSTGRESQL_USER = "bookshelf"
POSTGRESQL_PASSWORD = "bookshelf"  # throwaway test container only
DEADLOCK = 335544336  # isc_deadlock: an error no model expects


def pytest_configure(config: pytest.Config) -> None:
    for backend in DatabaseBackend:
        config.addinivalue_line(
            "markers", f"{backend.value}: runs only on {backend.value}"
        )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip the tests marked for the other database."""
    for item in items:
        others = [b for b in DatabaseBackend if b is not BACKEND]
        for backend in others:
            if item.get_closest_marker(backend.value):
                item.add_marker(pytest.mark.skip(reason=f"{backend.value} only"))


def _firebird() -> DockerContainer:
    return (
        DockerContainer(FIREBIRD_IMAGE)
        .with_env("FIREBIRD_ROOT_PASSWORD", FIREBIRD_PASSWORD)
        .with_env("FIREBIRD_DATABASE", "bookshelf_test.fdb")
        .with_env("FIREBIRD_DATABASE_DEFAULT_CHARSET", "UTF8")
        .with_exposed_ports(3050)
        .waiting_for(
            LogMessageWaitStrategy(
                "Starting Firebird TCP/IP server"
            ).with_startup_timeout(120)
        )
    )


def _postgresql() -> PostgresContainer:
    return PostgresContainer(
        POSTGRESQL_IMAGE,
        username=POSTGRESQL_USER,
        password=POSTGRESQL_PASSWORD,
        dbname="bookshelf_test",
        driver=None,
    )


def _settings(container: DockerContainer) -> tuple[str, str, str]:
    """The test database's DSN, user and password."""
    host = container.get_container_host_ip()
    match BACKEND:
        case DatabaseBackend.FIREBIRD:
            port = container.get_exposed_port(3050)
            dsn = f"{host}/{port}:/var/lib/firebird/data/bookshelf_test.fdb"
            return dsn, "SYSDBA", FIREBIRD_PASSWORD
        case DatabaseBackend.POSTGRESQL:
            port = container.get_exposed_port(5432)
            return f"{host}:{port}/bookshelf_test", POSTGRESQL_USER, POSTGRESQL_PASSWORD


@pytest.fixture(scope="session", autouse=True)
def database() -> Iterator[None]:
    """
    Start a throwaway database server (BACKEND) for the whole test session,
    point the app at it via APP_DB_* and create the schema.
    """
    container = _firebird() if BACKEND is DatabaseBackend.FIREBIRD else _postgresql()
    with container, pytest.MonkeyPatch.context() as mp:
        dsn, user, password = _settings(container)
        mp.setenv("APP_DB_BACKEND", BACKEND.value)
        mp.setenv("APP_DB_DSN", dsn)
        mp.setenv("APP_DB_USER", user)
        mp.setenv("APP_DB_PASSWORD", password)
        engine = create_db_engine(BACKEND, dsn, user, password, pool_size=1)
        init_db(engine)
        engine.dispose()
        yield
    # testcontainers stops its reaper container at exit, when pytest has
    # closed the stream our log handler writes to ("--- Logging error ---"):
    # stop it now instead.
    Reaper.delete_instance()


@pytest.fixture(autouse=True)
def setup_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Treat the app as set up, so requests are not sent to the first-run wizard
    whatever the database holds. test_setup.py overrides this.
    """
    monkeypatch.setattr("bookshelf.routes.setup.setup_needed", lambda _conn: False)


def _postgresql_admin() -> Engine:
    """The test server's own database, outside transactions: to create others."""
    dsn, user, password = (
        os.environ["APP_DB_DSN"],
        os.environ["APP_DB_USER"],
        os.environ["APP_DB_PASSWORD"],
    )
    address = dsn.partition("/")[0]
    host, _, port = address.partition(":")
    url = URL.create(
        "postgresql+psycopg",
        username=user,
        password=password,
        host=host,
        port=int(port),
        database="postgres",
    )
    return create_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)


@contextmanager
def _empty_database() -> Generator[str]:
    """A new database with no schema, dropped afterwards: its DSN."""
    name = f"empty_{uuid4().hex}"
    match BACKEND:
        case DatabaseBackend.FIREBIRD:
            data_dir = os.environ["APP_DB_DSN"].rpartition("/")[0]
            dsn = f"{data_dir}/{name}.fdb"
            create_database(
                dsn, user="SYSDBA", password=FIREBIRD_PASSWORD, charset="UTF8"
            ).close()
            yield dsn
            with connect(
                dsn, user="SYSDBA", password=FIREBIRD_PASSWORD, charset="UTF8"
            ) as conn:
                conn.drop_database()
        case DatabaseBackend.POSTGRESQL:
            admin = _postgresql_admin()
            # Names are never parameters; this one is a hex UUID.
            with admin.connect() as conn:
                conn.exec_driver_sql(f"CREATE DATABASE {name}")
            yield f"{os.environ['APP_DB_DSN'].partition('/')[0]}/{name}"
            with admin.connect() as conn:
                conn.exec_driver_sql(f"DROP DATABASE {name} WITH (FORCE)")
            admin.dispose()


@pytest.fixture
def empty_db_app() -> Iterator[Flask]:
    """App on a new, empty database (no schema), dropped afterwards."""
    with _empty_database() as dsn:
        app = create_app(
            {"TESTING": True, "WTF_CSRF_ENABLED": False, "DATABASE_DSN": dsn}
        )
        yield app
        close_pool(app)


@pytest.fixture
def app() -> Iterator[Flask]:
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False})
    yield app
    close_pool(app)


@pytest.fixture
def other_conn(app: Flask) -> Iterator[RawConnection]:
    """
    A second connection to the app's database, as another request would hold.
    Uncommitted work is rolled back at the end.
    """
    cfg = app.config
    engine = create_db_engine(
        BACKEND,
        cfg["DATABASE_DSN"],
        cfg["DATABASE_USER"],
        cfg["DATABASE_PASSWORD"],
        pool_size=1,
    )
    with Session(engine) as session:
        try:
            yield RawConnection(session)
        finally:
            session.rollback()
    engine.dispose()


def _waiting(app: Flask, waits_on: str) -> bool:
    """
    Whether a statement containing `waits_on` (ignoring case) waits for
    another transaction's lock, on a connection other than this one.
    """
    queries = {
        DatabaseBackend.FIREBIRD: (
            "SELECT COUNT(*) FROM MON$STATEMENTS WHERE MON$STATE = 1 "
            "AND MON$ATTACHMENT_ID <> CURRENT_CONNECTION "
            "AND MON$SQL_TEXT CONTAINING ?"
        ),
        DatabaseBackend.POSTGRESQL: (
            "SELECT COUNT(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
            "AND pid <> pg_backend_pid() AND query ILIKE '%' || ? || '%'"
        ),
    }
    # A new transaction each time, so it sees fresh monitoring data.
    with app.app_context(), raw(get_session()).cursor() as cur:
        cur.execute(queries[BACKEND], (waits_on,))
        (running,) = cur.fetchone() or (0,)
    return cast(int, running) > 0


@pytest.fixture
def waiting(app: Flask) -> Callable[[str], bool]:
    """Whether a statement containing the text waits for a lock: see _waiting."""
    return lambda waits_on: _waiting(app, waits_on)


@pytest.fixture
def race(app: Flask, other_conn: RawConnection) -> Callable[..., object]:
    """
    Run `change(session)` on the app's session, in another thread, until its
    statement containing `waits_on` waits for other_conn's uncommitted work;
    then commit that work (roll it back with commit=False) and return what
    `change` returns (or raise what it raises). other_conn's work is done by
    the test before calling it.
    """

    def wait_until_blocked(waits_on: str) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if _waiting(app, waits_on):
                return
            time.sleep(0.05)
        pytest.fail(f"No statement containing {waits_on!r} started waiting")

    def run(
        change: Callable[[Session], object], waits_on: str, *, commit: bool = True
    ) -> object:
        def in_app() -> object:
            with app.app_context():
                return change(get_session())

        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(in_app)
            try:
                wait_until_blocked(waits_on)
            finally:
                # Always release the lock, or the executor would wait forever.
                if commit:
                    other_conn.commit()
                else:
                    other_conn.rollback()
            return result.result(timeout=10)

    return run


def deadlock() -> Exception:
    """A database error no model handles, as the driver raises it."""
    match BACKEND:
        case DatabaseBackend.FIREBIRD:
            error = DatabaseError("deadlock")
            error.gds_codes = (DEADLOCK,)
            return error
        case DatabaseBackend.POSTGRESQL:
            return DeadlockDetected("deadlock")


@pytest.fixture
def fail_statement() -> Iterator[Callable[[str], None]]:
    """
    Make every statement starting with the given SQL (ignoring case: the ORM
    writes names in lower case) fail with a deadlock, a database error no
    model handles: it must reach the caller, not be taken for one of the
    errors the model expects there.
    """
    prefixes: list[str] = []

    def failing(
        conn: SAConnection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if any(statement.upper().startswith(p.upper()) for p in prefixes):
            raise deadlock()

    event.listen(Engine, "before_cursor_execute", failing)
    yield prefixes.append
    event.remove(Engine, "before_cursor_execute", failing)


class _Response:
    """What requests.get returns, as far as the book lookup reads it."""

    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self.payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self) -> object:
        return self.payload


@dataclass
class OnlineCatalogues:
    """
    What Google Books and Open Library answer the book lookup: a JSON
    payload, an HTTP error status, or an exception raised; by default that
    they have no such book. `calls`: the services asked, in order.
    """

    google: object = field(default_factory=lambda: {"totalItems": 0})
    open_library: object = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    # The headers of each request, as `calls`.
    headers: list[dict[str, str]] = field(default_factory=list)

    def google_has(self, **volume_info: object) -> None:
        self.google = {"totalItems": 1, "items": [{"volumeInfo": volume_info}]}

    def open_library_has(self, isbn: str, **book: object) -> None:
        self.open_library = {f"ISBN:{isbn}": book}


@pytest.fixture
def online_catalogues(monkeypatch: pytest.MonkeyPatch) -> OnlineCatalogues:
    """Google Books and Open Library, stubbed: no test reaches the internet."""
    catalogues = OnlineCatalogues()

    def get(url: str, *, headers: dict[str, str], **_kwargs: object) -> _Response:
        name = "google" if "googleapis.com" in url else "open_library"
        catalogues.calls.append(name)
        catalogues.headers.append(headers)
        answer = getattr(catalogues, name)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            return _Response(answer, None)
        return _Response(200, answer)

    monkeypatch.setattr("bookshelf.models.books.lookup.requests.get", get)
    return catalogues


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


@pytest.fixture
def db_app(app: Flask) -> Iterator[Flask]:
    """App with every table empty."""
    with app.app_context():
        session = get_session()
        with raw(session).cursor() as cur:
            # Users first: their collections go with them, so books can go too.
            cur.execute("DELETE FROM USERS")
            cur.execute("DELETE FROM LOGIN_FAILURES")
            cur.execute("DELETE FROM BOOKS")
            cur.execute("DELETE FROM AUTHORS")
        session.commit()
    yield app


@pytest.fixture
def db_client(db_app: Flask) -> FlaskClient:
    return db_app.test_client()


@pytest.fixture
def user(db_app: Flask) -> User:
    with db_app.app_context():
        return create_user(get_session(), "Reader@Example.com", PASSWORD)


@pytest.fixture
def password() -> str:
    return PASSWORD


@pytest.fixture
def lock_user(db_app: Flask) -> Callable[[User], None]:
    def lock(user: User) -> None:
        with db_app.app_context():
            session = get_session()
            with raw(session).cursor() as cur:
                cur.execute(
                    "UPDATE USERS SET IS_LOCKED = TRUE WHERE ID = ?", (user.id,)
                )
            session.commit()

    return lock


class MigrationStep(Protocol):
    """What a test revision does: see the migrations fixture."""

    def upgrade(self, conn: SAConnection) -> None: ...

    def rollback(self, conn: SAConnection) -> None: ...


# A test revision, running a MigrationStep registered under its ID.
_TEST_REVISION = """
from alembic import op

import test_revision_steps

revision = {revision!r}
down_revision = {down_revision!r}


def upgrade() -> None:
    test_revision_steps.STEPS[revision].upgrade(op.get_bind())


def downgrade() -> None:
    test_revision_steps.STEPS[revision].rollback(op.get_bind())
"""


@pytest.fixture
def migrations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """
    Set the revisions init_db knows: the real ones up to `last` (all of
    them unless given), then one
    per step given (t001, t002, ...), each running that step's upgrade and
    rollback.
    """
    real = ScriptDirectory(str(schema.MIGRATIONS))
    registry = types.ModuleType("test_revision_steps")
    steps_by_revision: dict[str, MigrationStep] = {}
    registry.__dict__["STEPS"] = steps_by_revision
    monkeypatch.setitem(sys.modules, "test_revision_steps", registry)
    # A new folder each time: Alembic would reuse a module compiled from
    # the same path.
    folders = itertools.count()

    def use(*steps: MigrationStep, last: str | None = None) -> None:
        versions = tmp_path / f"migrations{next(folders)}" / "versions"
        versions.mkdir(parents=True)
        last = last or real.get_current_head()
        assert last is not None
        for revision in real.iterate_revisions(last, "base"):
            shutil.copy(revision.path, versions)
        previous = last
        for number, step in enumerate(steps, start=1):
            revision = f"t{number:03}"
            steps_by_revision[revision] = step
            (versions / f"{revision}.py").write_text(
                _TEST_REVISION.format(revision=revision, down_revision=previous)
            )
            previous = revision
        monkeypatch.setattr(schema, "MIGRATIONS", versions.parent)

    return use
