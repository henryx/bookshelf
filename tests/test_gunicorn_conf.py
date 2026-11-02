import runpy
from pathlib import Path

import pytest

from bookshelf.logging_config import logging_config

CONF = Path(__file__).parent.parent / "gunicorn.conf.py"


@pytest.fixture
def conf(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setenv("APP_HOST", "0.0.0.0")
    monkeypatch.setenv("APP_PORT", "8080")
    monkeypatch.setenv("APP_LOG_LEVEL", "warning")
    return runpy.run_path(str(CONF))


def test_bind_and_level_come_from_environment(conf: dict[str, object]):
    assert conf["bind"] == "0.0.0.0:8080"
    assert conf["loglevel"] == "warning"


def test_gunicorn_uses_the_app_logging_config(conf: dict[str, object]):
    assert conf["logconfig_dict"] == logging_config("warning")
