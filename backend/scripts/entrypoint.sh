#!/bin/sh
# Apply database migrations, then hand off to the container command.
set -e

echo "[entrypoint] applying database migrations"
alembic upgrade head

echo "[entrypoint] starting: $*"
exec "$@"
