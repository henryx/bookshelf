import os
from collections.abc import Callable, Iterator
from uuid import uuid4

import pytest
from firebird.driver import Connection, connect, create_database
from flask import Flask
from flask.testing import FlaskClient
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from bookshelf import create_app
from bookshelf.db import close_pool, get_db
from bookshelf.db.schema import init_db
from bookshelf.models.users import User, create_user

PASSWORD = "correct horse battery staple"
FIREBIRD_IMAGE = "firebirdsql/firebird:3.0.14"
FIREBIRD_PASSWORD = "masterkey"  # throwaway test container only


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
