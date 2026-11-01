#!/usr/bin/env python3

from bookshelf import create_app
from bookshelf.utils import Config

app = create_app()

if __name__ == "__main__":
    cfg = Config()
    app_debug = cfg.log_level.lower() == "debug"
    app.run(debug=app_debug, host=cfg.host, port=cfg.port)
