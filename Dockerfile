# Backend image for ECS Fargate (docs/AWS_DEPLOY.md).
#
#   docker buildx build --platform linux/arm64 -t ticketapp-api .
#
# arm64 because the plan runs on Fargate Graviton (cheaper per vCPU-hour);
# use --platform linux/amd64 for x86. Only runtime files go in: backend/,
# the KB JSON, the Postgres migrations and scripts/db_migrate.py (for a
# one-off migration task). No .env, no local DB, no uploads (.dockerignore).
# Every setting comes from the task definition's environment and secrets.

FROM python:3.13-slim-bookworm AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements-runtime.txt /tmp/
# Binary wheels only: no compiler in the image (psycopg[binary] bundles libpq).
RUN /opt/venv/bin/pip install --only-binary=:all: -r /tmp/requirements-runtime.txt

FROM python:3.13-slim-bookworm
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    PORT=8000
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY backend/ backend/
COPY data/kb/kb_entries.json data/kb/new_entries.json data/kb/kb_registry.json data/kb/
COPY migrations/ migrations/
COPY scripts/db_migrate.py scripts/
# Readable by the app user whatever the build machine's file modes were
# (COPY keeps them); owned by root and not writable by the process.
# Byte-compiled as root so the app user starts faster.
RUN chmod -R a+rX,go-w /app && python -m compileall -q backend scripts
USER app
EXPOSE 8000
# Liveness only (no DB, no AI): /health. The ALB health check uses it too.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8000'), timeout=4)"]
# One worker: the login rate limiter and the pool are per process.
# keep-alive (130 s) outlasts the ALB idle timeout (120 s) so the ALB never
# reuses a connection uvicorn has just closed (a 502). Graceful shutdown
# (25 s) fits inside ECS's 30 s stop timeout so in-flight requests and
# their background tasks can finish on a deploy or scale-in.
CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 130 --timeout-graceful-shutdown 25 --no-server-header"]
