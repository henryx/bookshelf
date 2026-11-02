from flask import request

__all__ = ["auth", "is_htmx", "main"]


def is_htmx() -> bool:
    """True when the current request was made by htmx."""
    return request.headers.get("HX-Request") == "true"
