from collections.abc import Callable, Iterator

import pytest
from firebird.driver import connect
from flask import Flask
from flask.testing import FlaskClient
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from bookshelf import create_app
from bookshelf.db import get_db
from bookshelf.db.schema import init_db
from bookshelf.models.users import User, create_user

PASSWORD = "correct horse battery staple"
FIREBIRD_IMAGE = "firebirdsql/firebird:5.0.4"
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


@pytest.fixture
def app() -> Flask:
    return create_app({"TESTING": True, "WTF_CSRF_ENABLED": False})


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


@pytest.fixture
def db_app(app: Flask) -> Iterator[Flask]:
    """App with an empty USERS table."""
    with app.app_context():
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM USERS")
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
                    "UPDATE USERS SET IS_LOCKED = TRUE WHERE ID = ?", (user.id,)
                )
            conn.commit()

    return lock
