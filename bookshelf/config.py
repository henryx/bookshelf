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
    def db_pool_size(self) -> int:
        """Idle database connections kept for reuse, per process."""
        return _positive_int("APP_DB_POOL_SIZE", 5)

    @property
    def cookie_secure(self) -> bool:
        return os.environ.get("APP_COOKIE_SECURE", "true").lower() != "false"

    @property
    def login_max_failures(self) -> int:
        """Failed logins for an email before further logins are refused."""
        return _positive_int("APP_LOGIN_MAX_FAILURES", 5)

    @property
    def login_block_minutes(self) -> int:
        """Window in which failures are counted, and so how long a block lasts."""
        return _positive_int("APP_LOGIN_BLOCK_MINUTES", 15)

    @property
    def workers(self) -> int:
        """gunicorn worker processes; default 2 x CPUs + 1."""
        return _positive_int("APP_WORKERS", (os.process_cpu_count() or 1) * 2 + 1)

    @property
    def timeout(self) -> int:
        """Seconds before gunicorn kills a silent worker."""
        return _positive_int("APP_TIMEOUT", 30)

    @property
    def trusted_proxies(self) -> int:
        """Reverse proxies in front of the app whose X-Forwarded-* headers are trusted."""
        return _non_negative_int("APP_TRUSTED_PROXIES", 0)

    @property
    def log_level(self) -> str:
        return os.environ.get("APP_LOG_LEVEL", "debug")

    @property
    def host(self) -> str:
        return os.environ.get("APP_HOST", "localhost")

    @property
    def port(self) -> int:
        return int(os.environ.get("APP_PORT", default="8000"))


def _positive_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    if not value.isdigit() or int(value) == 0:
        raise RuntimeError(f"{name} must be a positive integer, got {value!r}")
    return int(value)


def _non_negative_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    if not value.isdigit():
        raise RuntimeError(f"{name} must be a non-negative integer, got {value!r}")
    return int(value)


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} environment variable is not set")
    return value
