from bookshelf import create_app
from bookshelf.config import Config

app = create_app()

if __name__ == "__main__":
    # Never in debug mode: its interactive debugger runs arbitrary code.
    # For development use `flask --app bookshelf run --debug` (see README).
    cfg = Config()
    app.run(host=cfg.host, port=cfg.port)
