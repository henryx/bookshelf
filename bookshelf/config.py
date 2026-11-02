import os

__all__ = ["Config"]


class Config:
    @property
    def secret_key(self) -> str:
        return _required("APP_SECRET_KEY")

    @property
    def db_dsn(self) -> str:
        return _required("APP_DB_DSN")

    @property
    def db_user(self) -> str:
        return _required("APP_DB_USER")

    @property
    def db_password(self) -> str:
        return _required("APP_DB_PASSWORD")

    @property
    def cookie_secure(self) -> bool:
        return os.environ.get("APP_COOKIE_SECURE", "true").lower() != "false"

    @property
    def log_level(self) -> str:
        return os.environ.get("APP_LOG_LEVEL", "debug")

    @property
    def host(self) -> str:
        return os.environ.get("APP_HOST", "localhost")

    @property
    def port(self) -> int:
        return int(os.environ.get("APP_PORT", default="8000"))


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} environment variable is not set")
    return value
