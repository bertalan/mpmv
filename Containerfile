# syntax=docker/dockerfile:1
# MPMV application image (ARC.5, ADR-0002, ADR-0005). One image for every process type:
# web (gunicorn), worker, migrate, manage. Built locally with `container build`, on CI with
# BuildKit, and run on STAGE/LIVE by Podman. Configuration comes only from the environment.
#
# Base images are pinned by tag and digest; bump both together (see deploy/README.md).

FROM ghcr.io/astral-sh/uv:0.10.11@sha256:3472e43b4e738cf911c99d41bb34331280efad54c73b1def654a6227bb59b2b4 AS uv

# --- Build stage: resolve dependencies from uv.lock and collect static files.
FROM docker.io/library/python:3.13.18-slim-trixie@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc AS build

COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# Dependencies first, so this layer is reused while only the code changes.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Theme CSS (THM.2, ADR-0008): resolve and check each theme's tokens and write
# build/themes/themes/<slug>/theme.css; collectstatic then hashes it. Needs no Django settings.
RUN /app/.venv/bin/python -m apps.themes.build

# collectstatic needs importable settings, not real configuration: a throwaway key generated in
# this step only (never stored in a layer) and a database URL that is never connected to.
RUN DJANGO_SECRET_KEY="$(/app/.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(50))')" \
    DATABASE_URL=postgres://build@localhost/build \
    /app/.venv/bin/python manage.py collectstatic --noinput --settings=mpmv.settings.live

# --- Runtime stage: no build tools, code owned by root and read-only for the app user.
FROM docker.io/library/python:3.13.18-slim-trixie@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc AS runtime

ARG MPMV_VERSION=dev
LABEL org.opencontainers.image.title="mpmv-app" \
      org.opencontainers.image.source="https://github.com/bertalan/mpmv" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${MPMV_VERSION}"

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=mpmv.settings.live \
    MPMV_HTTP_PORT=8000 \
    MPMV_VERSION=${MPMV_VERSION}

RUN groupadd --system --gid 10001 mpmv \
    && useradd --system --uid 10001 --gid mpmv --home-dir /app --no-create-home \
       --shell /usr/sbin/nologin mpmv

WORKDIR /app
COPY --from=build /app /app

USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["/bin/sh", "/app/deploy/container/entrypoint.sh"]
CMD ["web"]
