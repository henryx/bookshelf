"""
Gunicorn settings, loaded automatically from the working directory
(the repo root locally, /app in the Docker image).
"""

from bookshelf.config import Config
from bookshelf.logging_config import logging_config

_cfg = Config()

bind = f"{_cfg.host}:{_cfg.port}"
loglevel = _cfg.log_level
workers = _cfg.workers
timeout = _cfg.timeout
# Same JSON logging as the app, so the master's own lines are JSON too.
logconfig_dict = logging_config(_cfg.log_level)
