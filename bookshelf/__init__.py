import logging
from collections.abc import Mapping
from datetime import timedelta
from logging.config import dictConfig

import json_logging
from flask import Flask, Response, current_app, make_response, render_template, request
from flask_cors import CORS
from flask_wtf import CSRFProtect
from werkzeug.exceptions import HTTPException

from bookshelf import cli, db
from bookshelf.routes.auth import auth_route, login_manager
from bookshelf.routes.main import main_route
from bookshelf.utils import Config
from bookshelf.utils.htmx import is_htmx

__all__ = ["create_app", "routes", "utils"]


def configure_logging() -> None:
    """
    Configure process-wide logging. Must run once per process.
    """
    cfg = Config()
    json_logging.init_flask(enable_json=True)
    dictConfig(
        {
            "version": 1,
            "formatters": {
                "default": {
                    "format": '{"timestamp":"%(asctime)s", "level":"%(levelname)s", "module":"%(module)s", "message":"%(message)s"}',
                }
            },
            "handlers": {
                "wsgi": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://flask.logging.wsgi_errors_stream",
                    "formatter": "default",
                }
            },
            "root": {"level": cfg.log_level.upper(), "handlers": ["wsgi"]},
        }
    )


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
        SESSION_COOKIE_SECURE=cfg.cookie_secure,
        SESSION_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_SECURE=cfg.cookie_secure,
        REMEMBER_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_DURATION=timedelta(days=30),
    )
    if overrides:
        app.config.update(overrides)

    CORS(app)
    json_logging.init_request_instrument(app)

    app.url_map.strict_slashes = False
    gunicorn_logger = logging.getLogger("gunicorn.error")
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)

    app.register_error_handler(Exception, handle_error)
    db.init_app(app)
    cli.init_app(app)
    login_manager.init_app(app)
    CSRFProtect(app)
    app.register_blueprint(auth_route)
    app.register_blueprint(main_route)
    return app


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
