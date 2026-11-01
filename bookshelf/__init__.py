import logging
from collections.abc import Mapping
from datetime import timedelta
from logging.config import dictConfig

import json_logging
from flask import Flask, Response, current_app, request
from flask_cors import CORS
from flask_wtf import CSRFProtect
from werkzeug.exceptions import HTTPException

from bookshelf import cli, db
from bookshelf.routes.auth import auth_route, login_manager
from bookshelf.routes.main import main_route
from bookshelf.utils import Config
from bookshelf.utils.mappers import KoResponse

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

    app.register_error_handler(404, not_found)
    app.register_error_handler(405, not_allowed)
    app.register_error_handler(406, not_acceptable)
    app.register_error_handler(Exception, internal_server_error)
    db.init_app(app)
    cli.init_app(app)
    login_manager.init_app(app)
    CSRFProtect(app)
    app.register_blueprint(auth_route)
    app.register_blueprint(main_route)
    return app


def not_found(e) -> Response:
    """
    Handler for 404 error (not found)
    """
    return Response(
        str(KoResponse(operation="error", message="Endpoint not found")),
        status=404,
        mimetype="application/json",
    )


def not_allowed(e) -> Response:
    """
    Handler for 405 error (method not allowed)
    """
    return Response(
        str(KoResponse(operation="error", message="Method not allowed")),
        status=405,
        mimetype="application/json",
    )


def not_acceptable(e) -> Response:
    """
    Handler for 406 error (media type not acceptable)
    """

    return Response(
        str(KoResponse(operation="error", message="Media type not acceptable")),
        status=406,
        mimetype="application/json",
    )


def internal_server_error(e: Exception) -> Response:
    """
    Handler for unhandled exceptions. HTTP errors keep their status code;
    anything else is logged and returned as a generic 500.
    """
    if isinstance(e, HTTPException):
        return Response(
            str(KoResponse(operation="error", message=e.description or e.name)),
            status=e.code,
            mimetype="application/json",
        )

    current_app.logger.exception(
        "Unhandled exception on %s %s", request.method, request.path
    )
    return Response(
        str(KoResponse(operation="error", message="Internal server error")),
        status=500,
        mimetype="application/json",
    )


configure_logging()
