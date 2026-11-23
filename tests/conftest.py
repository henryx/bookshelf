import os
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
import requests
from firebird.driver import (
    Connection,
    Cursor,
    DatabaseError,
    Statement,
    connect,
    create_database,
)
from flask import Flask
from flask.testing import FlaskClient
from testcontainers.core.container import DockerContainer, Reaper
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from bookshelf import create_app
from bookshelf.db import close_pool, get_db
from bookshelf.db.schema import init_db
from bookshelf.models.users import User, create_user

PASSWORD = "correct horse battery staple"
FIREBIRD_IMAGE = "firebirdsql/firebird:3.0.14"
FIREBIRD_PASSWORD = "masterkey"  # throwaway test container only
DEADLOCK = 335544336  # isc_deadlock: an error no model expects


@pytest.fixture(scope="session", autouse=True)
def firebird() -> Iterator[None]:
    """
    Start a throwaway Firebird server for the whole test session,
    point the app at it via APP_DB_* and create the schema.
    """
    container = (
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
    with container, pytest.MonkeyPatch.context() as mp:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(3050)
        dsn = f"{host}/{port}:/var/lib/firebird/data/bookshelf_test.fdb"
        mp.setenv("APP_DB_DSN", dsn)
        mp.setenv("APP_DB_USER", "SYSDBA")
        mp.setenv("APP_DB_PASSWORD", FIREBIRD_PASSWORD)
        with connect(
            dsn, user="SYSDBA", password=FIREBIRD_PASSWORD, charset="UTF8"
        ) as conn:
            init_db(conn)
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


@pytest.fixture
def empty_db_app() -> Iterator[Flask]:
    """App on a new, empty database (no schema), dropped afterwards."""
    data_dir = os.environ["APP_DB_DSN"].rpartition("/")[0]
    dsn = f"{data_dir}/empty_{uuid4().hex}.fdb"
    create_database(
        dsn, user="SYSDBA", password=FIREBIRD_PASSWORD, charset="UTF8"
    ).close()
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False, "DATABASE_DSN": dsn})
    yield app
    close_pool(app)
    with connect(
        dsn, user="SYSDBA", password=FIREBIRD_PASSWORD, charset="UTF8"
    ) as conn:
        conn.drop_database()


@pytest.fixture
def app() -> Iterator[Flask]:
    app = create_app({"TESTING": True, "WTF_CSRF_ENABLED": False})
    yield app
    close_pool(app)


@pytest.fixture
def other_conn(app: Flask) -> Iterator[Connection]:
    """
    A second connection to the app's database, as another request would hold.
    Uncommitted work is rolled back at the end.
    """
    cfg = app.config
    with connect(
        cfg["DATABASE_DSN"],
        user=cfg["DATABASE_USER"],
        password=cfg["DATABASE_PASSWORD"],
        charset="UTF8",
    ) as conn:
        try:
            yield conn
        finally:
            if conn.is_active():
                conn.rollback()


@pytest.fixture
def race(app: Flask, other_conn: Connection) -> Callable[..., object]:
    """
    Run `change(conn)` on the app's connection, in another thread, until its
    statement containing `waits_on` waits for other_conn's uncommitted work;
    then commit that work (roll it back with commit=False) and return what
    `change` returns (or raise what it raises). other_conn's work is done by
    the test before calling it.
    """

    def blocked(waits_on: str) -> bool:
        with app.app_context(), get_db().cursor() as cur:
            # A new transaction each time, so it sees fresh monitoring data.
            cur.execute(
                "SELECT COUNT(*) FROM MON$STATEMENTS WHERE MON$STATE = 1 "
                "AND MON$ATTACHMENT_ID <> CURRENT_CONNECTION "
                "AND MON$SQL_TEXT CONTAINING ?",
                (waits_on,),
            )
            (running,) = cur.fetchone()
        return running > 0

    def wait_until_blocked(waits_on: str) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if blocked(waits_on):
                return
            time.sleep(0.05)
        pytest.fail(f"No statement containing {waits_on!r} started waiting")

    def run(
        change: Callable[[Connection], object], waits_on: str, *, commit: bool = True
    ) -> object:
        def in_app() -> object:
            with app.app_context():
                return change(get_db())

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


@pytest.fixture
def fail_statement(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """
    Make every statement starting with the given SQL fail with a deadlock, a
    database error no model handles: it must reach the caller, not be taken
    for one of the errors the model expects there.
    """

    def fail(sql: str) -> None:
        execute = Cursor.execute

        def failing(
            cur: Cursor,
            operation: str | Statement,
            parameters: Sequence[object] | None = None,
        ) -> Cursor:
            if isinstance(operation, str) and operation.startswith(sql):
                error = DatabaseError("deadlock")
                error.gds_codes = (DEADLOCK,)
                raise error
            return execute(cur, operation, parameters)

        monkeypatch.setattr(Cursor, "execute", failing)

    return fail


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
        conn = get_db()
        with conn.cursor() as cur:
            # Users first: their collections go with them, so books can go too.
            cur.execute("DELETE FROM USERS")
            cur.execute("DELETE FROM LOGIN_FAILURES")
            cur.execute("DELETE FROM BOOKS")
            cur.execute("DELETE FROM AUTHORS")
        conn.commit()
    yield app


@pytest.fixture
def db_client(db_app: Flask) -> FlaskClient:
    return db_app.test_client()


@pytest.fixture
def user(db_app: Flask) -> User:
    with db_app.app_context():
        return create_user(get_db(), "Reader@Example.com", PASSWORD)


@pytest.fixture
def password() -> str:
    return PASSWORD


@pytest.fixture
def lock_user(db_app: Flask) -> Callable[[User], None]:
    def lock(user: User) -> None:
        with db_app.app_context():
            conn = get_db()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE USERS SET IS_LOCKED = TRUE WHERE ID = ?", (user.id.bytes,)
                )
            conn.commit()

    return lock
