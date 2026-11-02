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


def test_workers_and_timeout_come_from_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_WORKERS", "3")
    monkeypatch.setenv("APP_TIMEOUT", "45")
    conf = runpy.run_path(str(CONF))
    assert conf["workers"] == 3
    assert conf["timeout"] == 45


def test_workers_and_timeout_defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("APP_WORKERS", raising=False)
    monkeypatch.delenv("APP_TIMEOUT", raising=False)
    monkeypatch.setattr("os.process_cpu_count", lambda: 4)
    conf = runpy.run_path(str(CONF))
    assert conf["workers"] == 9
    assert conf["timeout"] == 30


def test_invalid_workers_is_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_WORKERS", "0")
    with pytest.raises(RuntimeError, match="APP_WORKERS"):
        runpy.run_path(str(CONF))
