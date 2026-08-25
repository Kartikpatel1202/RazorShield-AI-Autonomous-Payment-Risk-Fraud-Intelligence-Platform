# Phase 1 — Project Foundation

Scope: establish the repository, runtime and tooling that later phases build on. No fraud logic,
no ML, no agent.

## What exists

| Area        | Delivered                                                                       |
| ----------- | ------------------------------------------------------------------------------- |
| Backend     | FastAPI app factory, typed settings, logging, error handler, `/health`, `/health/db` |
| Database    | SQLAlchemy 2.0 declarative base + engine/session, Alembic environment            |
| Frontend    | Vite + React 19 + TypeScript shell, Tailwind v4 tokens, router, `/dashboard`     |
| Packaging   | Backend and frontend Dockerfiles, `docker compose` stack with PostgreSQL 16      |
| Quality     | Ruff, mypy strict, oxlint, TypeScript strict, pytest, Vitest                     |

## Deliberate omissions

The following are **not** implemented and must not be assumed to exist:

- ML model, dataset, feature engineering, Isolation Forest anomaly detection
- LangGraph risk agent, tool calling, prompts
- Risk decision engine and rule builder
- Transaction explorer, investigation page, AI chat
- Human-in-the-loop review queue and audit log tables
- Demo mode and deployment pipeline

`ml/` and `agent/` exist as empty Python packages so later phases have a home without changing the
project layout.

## Design decisions

**Configuration is read once.** `app.core.config.Settings` validates the environment on first access
and is cached. Nothing else reads `os.environ`, and no secret has a real default.

**`CORS_ORIGINS` is a string, not a list.** pydantic-settings parses list-typed fields as JSON, which
makes `.env` files awkward. The field stays a comma-separated string and `Settings.cors_origin_list`
does the splitting.

**Alembic lives outside the backend package.** `backend/alembic.ini` points `script_location` at
`database/migrations` so migrations sit with the rest of the database assets. Migrations are run from
`backend/` (`prepend_sys_path = .`) so `env.py` can import `app.core.config` and reuse the same
`DATABASE_URL` as the running service.

**`/health` and `/health/db` are separate.** Liveness must not fail when a dependency is down;
readiness reports a degraded database as `200` with `status: "degraded"` so an orchestrator can tell
"process dead" apart from "dependency down". Connection errors can echo the DSN, so the error detail
is suppressed when `ENVIRONMENT=production`.

**Foundation tests use SQLite.** `backend/tests` overrides the `get_db` dependency with an in-memory
SQLite session so the FastAPI to SQLAlchemy wiring is tested without a database server. Real
PostgreSQL connectivity is verified at runtime through `/health/db`.

**Vitest uses the `threads` pool.** The default `forks` pool times out spawning workers on Windows
OneDrive-backed paths.

## Next phase

Phase 2 introduces the business schema (transactions, risk decisions, audit events) via the first
Alembic revision, and the API surface that reads it.
