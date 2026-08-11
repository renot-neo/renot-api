# syntax=docker/dockerfile:1
# Multi-stage build: the builder stage installs & compiles dependencies,
# the runtime stage is a minimal/slim image.

FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# An extra stage for dev-tooling only (e.g. celery-flower in
# docker-compose) - NOT used by the production image. The main runtime
# image below ("runtime") deliberately stays built from requirements.txt
# only, so the production image stays minimal.
FROM builder AS builder-dev

COPY requirements-dev.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements-dev.txt


FROM python:3.12-slim AS runtime

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

COPY --from=builder /install /usr/local
COPY . .
# `celery beat` writes its `celerybeat-schedule` file into WORKDIR at
# runtime (not just at build time via COPY) - without this, /app stays
# root-owned and beat crashes with PermissionError as non-root `appuser`.
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


# The dev-tooling image (celery-flower) - same as "runtime" but built from
# `builder-dev` (which has requirements-dev.txt, including `flower`). No
# default CMD - docker-compose determines the command per service.
FROM python:3.12-slim AS runtime-dev

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

COPY --from=builder-dev /install /usr/local
COPY . .
# Same rationale as the "runtime" stage above - kept consistent in case this
# target is ever used to run celery-beat for debugging, not just flower.
RUN chown -R appuser:appuser /app

USER appuser
