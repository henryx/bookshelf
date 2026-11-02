import json
import logging

import pytest
from flask import Flask

from bookshelf import configure_logging


def _root_formatter() -> logging.Formatter:
    handlers = logging.getLogger().handlers
    formatter = next((h.formatter for h in handlers if h.formatter is not None), None)
    assert formatter is not None
    return formatter


def test_log_lines_are_valid_json():
    record = logging.LogRecord(
        "bookshelf", logging.INFO, __file__, 1, 'Login "x" from %s', ("1.2.3.4",), None
    )

    line = json.loads(_root_formatter().format(record))

    assert line["level"] == "INFO"
    assert line["logger"] == "bookshelf"
    assert line["message"] == 'Login "x" from 1.2.3.4'
    assert "timestamp" in line


@pytest.mark.parametrize("name", ["gunicorn.error", "gunicorn.access"])
def test_gunicorn_loggers_stay_enabled(name: str):
    # Under gunicorn its loggers exist before the app configures logging.
    logging.getLogger(name)

    configure_logging()

    assert not logging.getLogger(name).disabled


def test_app_messages_are_logged_once(app: Flask, caplog: pytest.LogCaptureFixture):
    with app.app_context():
        app.logger.warning("only once")

    assert [r.getMessage() for r in caplog.records].count("only once") == 1
    assert len(logging.getLogger().handlers) >= 1
    assert not app.logger.handlers
