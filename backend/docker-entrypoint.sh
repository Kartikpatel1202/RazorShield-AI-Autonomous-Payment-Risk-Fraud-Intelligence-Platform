#!/bin/sh
# Container entrypoint: optionally bootstrap the database, then serve the API.
#
# WHY THE BOOTSTRAP RUNS IN THE BACKGROUND
#
# A hosting platform decides a deploy succeeded by watching for the process to
# bind its port. Seeding, scoring twice and deciding a few thousand transactions
# over a network hop to a managed database takes minutes, and doing that before
# `uvicorn` starts turns the first deploy of an empty database into a deploy
# that is marked failed and retried - which starts the whole thing again.
#
# So the API binds immediately and the bootstrap runs alongside it. Nothing the
# API serves is wrong during that window: readiness checks the database, the two
# models and the policy, none of which depend on how many rows exist, and every
# dashboard figure is a live aggregate that simply climbs as rows land.
#
# Bootstrap failure is logged and does not stop the API. A backend that refuses
# to start because seeding failed takes down authentication, health and the
# entire console along with it, and leaves no way in to find out why.
set -eu

if [ "${BOOTSTRAP_ON_START:-false}" = "true" ]; then
    echo "entrypoint: BOOTSTRAP_ON_START=true - running scripts/bootstrap.py in the background"
    # Each stage is skipped when its output already exists, so on every deploy
    # after the first this is a handful of COUNT queries and an alembic no-op.
    (
        python scripts/bootstrap.py || echo "entrypoint: bootstrap failed; the API keeps serving"
    ) &
else
    echo "entrypoint: BOOTSTRAP_ON_START is not 'true' - skipping database bootstrap"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-10000}"
