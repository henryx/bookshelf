FROM python:3.14-slim

ARG app_version

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ENV APP_HOST=0.0.0.0
ENV APP_PORT=8080
ENV APP_LOG_LEVEL=info
ENV APP_VERSION=${app_version}

WORKDIR /app
COPY ./dist/bookshelf-${app_version}-py3-none-any.whl /app

RUN python -m venv /app/venv \
    && /app/venv/bin/pip install --no-cache-dir bookshelf-${app_version}-py3-none-any.whl \
    && useradd -m -u 8877 appuser \
    && chown -R appuser:appuser /app \
    && rm -rf /var/lib/apt/lists/*

USER appuser

CMD ["sh", "-c", "/app/venv/bin/gunicorn --timeout 600 --access-logfile - --error-logfile - --log-level ${APP_LOG_LEVEL} -b ${APP_HOST}:${APP_PORT} bookshelf.wsgi:app"]
