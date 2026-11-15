from collections.abc import Mapping
from datetime import timedelta
from logging.config import dictConfig

from flask import Flask, Response, current_app, make_response, render_template, request
from flask_wtf import CSRFProtect
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from bookshelf import cli, db
from bookshelf.config import Config
from bookshelf.logging_config import logging_config
from bookshelf.routes import is_htmx
from bookshelf.routes.account import account_route
from bookshelf.routes.admin import admin_route
from bookshelf.routes.auth import auth_route, login_manager
from bookshelf.routes.authors import authors_route
from bookshelf.routes.collection import collection_route
from bookshelf.routes.main import main_route
from bookshelf.routes.pwa import pwa_route
from bookshelf.routes.setup import setup_route

__all__ = ["config", "configure_logging", "create_app", "routes"]


def configure_logging() -> None:
    """
    Configure process-wide logging (JSON lines on stderr). Safe to rerun.
    """
    dictConfig(logging_config(Config().log_level))


def create_app(overrides: Mapping[str, object] | None = None) -> Flask:
    """
    Build and configure the Flask application.
    :param overrides: config values applied after the environment (e.g. in tests)
    """
    cfg = Config()
    app = Flask("bookshelf")
    app.config.update(
        SECRET_KEY=cfg.secret_key,
        DATABASE_DSN=cfg.db_dsn,
        DATABASE_USER=cfg.db_user,
        DATABASE_PASSWORD=cfg.db_password,
        DATABASE_POOL_SIZE=cfg.db_pool_size,
        SESSION_COOKIE_SECURE=cfg.cookie_secure,
        SESSION_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_SECURE=cfg.cookie_secure,
        REMEMBER_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_DURATION=timedelta(days=30),
        LOGIN_MAX_FAILURES=cfg.login_max_failures,
        LOGIN_MAX_IP_FAILURES=cfg.login_max_ip_failures,
        LOGIN_BLOCK_MINUTES=cfg.login_block_minutes,
        MIN_PASSWORD_LENGTH=cfg.min_password_length,
        # Largest request body (413 above it): room for a collection CSV import.
        MAX_CONTENT_LENGTH=1024 * 1024,
    )
    if overrides:
        app.config.update(overrides)

    app.url_map.strict_slashes = False
    if cfg.trusted_proxies:
        # Client IP, scheme and host from X-Forwarded-*, set by that many proxies.
        # Flask's documented pattern; ty rejects assigning to a method.
        n = cfg.trusted_proxies
        app.wsgi_app = ProxyFix(  # ty: ignore[invalid-assignment]
            app.wsgi_app, x_for=n, x_proto=n, x_host=n
        )

    app.register_error_handler(Exception, handle_error)
    app.after_request(add_security_headers)
    db.init_app(app)
    cli.init_app(app)
    login_manager.init_app(app)
    CSRFProtect(app)
    app.register_blueprint(setup_route)
    app.register_blueprint(pwa_route)
    app.register_blueprint(auth_route)
    app.register_blueprint(main_route)
    app.register_blueprint(collection_route)
    app.register_blueprint(authors_route)
    app.register_blueprint(account_route)
    app.register_blueprint(admin_route)
    return app


# Only the app's own scripts, styles, fonts and images, with no inline code;
# no plugins, no <base> elsewhere, forms posting only here, and never shown in
# a frame (frame-ancestors, and X-Frame-Options for older browsers).
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'self'; form-action 'self'; "
        "frame-ancestors 'none'; object-src 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


def add_security_headers(response: Response) -> Response:
    """Set SECURITY_HEADERS on every response (HSTS is left to the HTTPS proxy)."""
    response.headers.update(SECURITY_HEADERS)
    return response


GENERIC_ERROR = "Something went wrong. Please try again."


def handle_error(e: Exception) -> Response:
    """
    Render every error as HTML, keeping its status code. Unexpected exceptions
    are logged and shown as a generic 500 without details. For htmx requests
    the message is swapped into the page's #error-banner.
    """
    if isinstance(e, HTTPException):
        return _render_error(e.code or 500, e.name, e.description or e.name)

    current_app.logger.exception(
        "Unhandled exception on %s %s", request.method, request.path
    )
    return _render_error(500, "Internal Server Error", GENERIC_ERROR)


def _render_error(status: int, title: str, message: str) -> Response:
    if not is_htmx():
        html = render_template("error.html", title=title, message=message)
        return make_response(html, status)

    resp = make_response(
        render_template("_error.html", title=title, message=message), status
    )
    resp.headers["HX-Retarget"] = "#error-banner"
    resp.headers["HX-Reswap"] = "innerHTML"
    return resp


configure_logging()
