import logging
import traceback
from logging.config import dictConfig

import json_logging
from flask import Flask, Response
from flask_cors import CORS

from bookshelf.routes.index import index_route
from bookshelf.utils import Config
from bookshelf.utils.mappers import KoResponse

__all__ = ["app", "routes", "utils"]

app = Flask("ssot-bookshelf")
CORS(app)

json_logging.init_flask(enable_json=True)
json_logging.init_request_instrument(app)


def config() -> None:
    """
    Function used to configure FLASK web application
    """
    cfg = Config()
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

    app.url_map.strict_slashes = False
    gunicorn_logger = logging.getLogger("gunicorn.error")
    app.logger.handlers = gunicorn_logger.handlers
    app.logger.setLevel(gunicorn_logger.level)


@app.errorhandler(404)
def not_found(e) -> Response:
    """
    Handler for 404 error (not found)
    """
    return Response(
        str(KoResponse(operation="error", message="Endpoint not found")),
        status=404,
        mimetype="application/json",
    )


@app.errorhandler(405)
def not_allowed(e) -> Response:
    """
    Handler for 405 error (method not allowed)
    """
    return Response(
        str(KoResponse(operation="error", message="Method not allowed")),
        status=405,
        mimetype="application/json",
    )


@app.errorhandler(406)
def not_acceptable(e) -> Response:
    """
    Handler for 406 error (media type not acceptable)
    """

    return Response(
        str(KoResponse(operation="error", message="Media type not acceptable")),
        status=406,
        mimetype="application/json",
    )


@app.errorhandler(Exception)
def internal_server_error(e) -> Response:
    """
    Handler for 500 error (internal server error)
    """

    return Response(
        str(
            KoResponse(
                operation="error",
                message="Internal server error",
                traceback="".join(
                    traceback.TracebackException.from_exception(e).format()
                ),
            )
        ),
        status=500,
        mimetype="application/json",
    )


config()
app.register_blueprint(index_route)
