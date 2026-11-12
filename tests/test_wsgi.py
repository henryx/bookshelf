import runpy

import pytest
from flask import Flask


@pytest.mark.parametrize("level", [None, "debug", "DEBUG"])
def test_run_as_script_never_enables_debugger(monkeypatch, level):
    """The interactive debugger runs arbitrary code; whatever the log level, it stays off."""
    if level is None:
        monkeypatch.delenv("APP_LOG_LEVEL", raising=False)
    else:
        monkeypatch.setenv("APP_LOG_LEVEL", level)
    monkeypatch.setenv("APP_HOST", "127.0.0.1")
    monkeypatch.setenv("APP_PORT", "8123")
    calls = []
    monkeypatch.setattr(Flask, "run", lambda _self, **kwargs: calls.append(kwargs))

    runpy.run_module("bookshelf.wsgi", run_name="__main__")

    assert calls == [{"host": "127.0.0.1", "port": 8123}]
