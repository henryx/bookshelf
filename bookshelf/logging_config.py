__all__ = ["logging_config"]


def logging_config(level: str) -> dict[str, object]:
    """
    dictConfig for the whole process: every log line, from the app, gunicorn
    and libraries alike, goes to stderr as one JSON object. Shared by the app
    (configure_logging) and gunicorn (gunicorn.conf.py).
    """
    return {
        "version": 1,
        # keep loggers created before this runs (e.g. gunicorn's) working
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": "pythonjsonlogger.json.JsonFormatter",
                "fmt": "%(asctime)s %(levelname)s %(name)s %(message)s",
                "rename_fields": {
                    "asctime": "timestamp",
                    "levelname": "level",
                    "name": "logger",
                },
            }
        },
        "handlers": {
            "stderr": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
                "formatter": "json",
            }
        },
        # gunicorn adds its own text handlers; send its logs through ours instead
        "loggers": {
            "gunicorn.error": {"handlers": ["stderr"], "propagate": False},
            "gunicorn.access": {"handlers": ["stderr"], "propagate": False},
        },
        "root": {"level": level.upper(), "handlers": ["stderr"]},
    }
