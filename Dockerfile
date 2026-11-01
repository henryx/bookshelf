# syntax=docker/dockerfile:1

# venv and release must share the same base: the release venv is built
# from the packages cached by the venv stage for this interpreter.
ARG PYTHON_IMAGE=python:3.14-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.24

FROM ${UV_IMAGE} AS uv

# Stage 1: create a venv with the locked runtime dependencies. Downloads go
# through a persistent BuildKit cache; a snapshot of it (/uv-cache) is what the
# release stage installs from.
FROM ${PYTHON_IMAGE} AS venv
ENV UV_CACHE_DIR=/root/.cache/uv \
    UV_PROJECT_ENVIRONMENT=/app/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN --mount=from=uv,source=/uv,target=/bin/uv \
    --mount=type=cache,id=bookshelf-uv,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project \
    && cp -a /root/.cache/uv /uv-cache

# Stage 2: build the application wheel
FROM ${PYTHON_IMAGE} AS wheel
ENV UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
COPY bookshelf ./bookshelf
RUN --mount=from=uv,source=/uv,target=/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --out-dir /dist

# Stage 3: release image
FROM ${PYTHON_IMAGE} AS release

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8080 \
    APP_LOG_LEVEL=info

WORKDIR /app

# Build the venv offline from the venv stage's cache, then add the app wheel
RUN --mount=from=uv,source=/uv,target=/bin/uv \
    --mount=from=venv,source=/uv-cache,target=/uv-cache,rw \
    --mount=from=wheel,source=/dist,target=/dist \
    --mount=type=bind,source=pyproject.toml,target=/src/pyproject.toml \
    --mount=type=bind,source=uv.lock,target=/src/uv.lock \
    apt-get update \
    && apt-get install -y --no-install-recommends libfbclient2 \
    && rm -rf /var/lib/apt/lists/* \
    && UV_CACHE_DIR=/uv-cache UV_PROJECT_ENVIRONMENT=/app/venv \
       UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy \
       uv sync --offline --locked --no-dev --no-install-project --project /src \
    && uv pip install --offline --no-cache --no-deps --python /app/venv/bin/python /dist/*.whl \
    && useradd -m -u 8877 appuser

USER appuser

CMD ["sh", "-c", "/app/venv/bin/gunicorn --timeout 600 --access-logfile - --error-logfile - --log-level ${APP_LOG_LEVEL} -b ${APP_HOST}:${APP_PORT} bookshelf.wsgi:app"]
