# syntax=docker/dockerfile:1.6
# Multi-stage build: builder installs deps into /opt/venv, runtime copies it over.
# Target: linux/amd64 (Beelink / x86 mini-PC). pyedflib has manylinux wheels.

FROM python:3.11-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

# build-essential kept only in the builder stage in case a wheel is missing
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
COPY mcp_server ./mcp_server
COPY auth ./auth
COPY migrations ./migrations

# Install project (without bleak — BLE is not used inside docker)
RUN pip install --upgrade pip setuptools wheel \
    && pip install .


FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app

# tini for proper PID-1 signal handling; cron for the scheduler service
RUN apt-get update && apt-get install -y --no-install-recommends \
    tini \
    cron \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Non-root user matching typical host UID/GID for bind-mount permissions
RUN groupadd -r -g 1000 hhub && useradd -r -u 1000 -g hhub -m -d /home/hhub hhub

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=hhub:hhub src ./src
COPY --chown=hhub:hhub mcp_server ./mcp_server
COPY --chown=hhub:hhub auth ./auth
COPY --chown=hhub:hhub migrations ./migrations
COPY --chown=hhub:hhub pyproject.toml ./

# cron files (root-owned by design)
COPY docker/crontab /etc/cron.d/hhub
COPY docker/cron-entrypoint.sh /usr/local/bin/cron-entrypoint.sh
RUN chmod 0644 /etc/cron.d/hhub && chmod 0755 /usr/local/bin/cron-entrypoint.sh

RUN mkdir -p /app/data /app/logs && chown -R hhub:hhub /app

USER hhub

# Default command runs the ingest server; docker-compose overrides per service.
EXPOSE 8765 8766
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["hhub", "serve-ingest", "--host", "0.0.0.0", "--port", "8765"]
