import os

__all__ = ["Config", "clean_nones", "mappers"]

from typing import Any


class Config:
    @property
    def version(self) -> str:
        return os.environ.get("APP_VERSION", "0.0.0")

    @property
    def secret_key(self) -> str:
        key = os.environ.get("APP_SECRET_KEY")
        if not key:
            raise RuntimeError("APP_SECRET_KEY environment variable is not set")
        return key

    @property
    def log_level(self) -> str:
        return os.environ.get("APP_LOG_LEVEL", "debug")

    @property
    def host(self) -> str:
        return os.environ.get("APP_HOST", "localhost")

    @property
    def port(self) -> int:
        return int(os.environ.get("APP_PORT", default="8000"))


def clean_nones(
    value: dict | list,
) -> (
    dict[Any, list[dict[Any, Any]] | dict[Any, Any]]
    | list[list[dict[Any, Any]] | dict[Any, Any]]
    | dict[Any, Any]
    | list[Any]
):
    """
    Recursively remove all None values from dictionaries and lists, and returns
    the result as a new dictionary or list.
    """
    if isinstance(value, list):
        return [clean_nones(x) for x in value if x is not None]
    elif isinstance(value, dict):
        return {key: clean_nones(val) for key, val in value.items() if val is not None}
    else:
        return value
