from flask import Response, redirect, request
from werkzeug.wrappers import Response as BaseResponse

__all__ = ["auth", "htmx_redirect", "is_htmx", "main", "setup"]


def is_htmx() -> bool:
    """True when the current request was made by htmx."""
    return request.headers.get("HX-Request") == "true"


def htmx_redirect(url: str) -> BaseResponse:
    """
    Redirect that works for both HTMX and plain requests: htmx would follow a
    302 and swap the whole target page into the request's target instead.
    """
    if not is_htmx():
        return redirect(url)
    resp = Response(status=204)
    resp.headers["HX-Redirect"] = url
    return resp
