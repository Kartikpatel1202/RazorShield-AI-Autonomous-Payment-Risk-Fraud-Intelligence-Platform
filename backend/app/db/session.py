"""Database engine and session lifecycle.

Importing this module also installs the append-only guard from
``app.db.immutability``. It is registered here rather than at an entry point so
that every path which can obtain a ``Session`` - the app, the CLI scripts and
the test suite - gets the same protection without having to remember to ask.

## Pool sizing and the two timeouts

Two different waits can hang a request, and both are bounded here:

* ``pool_timeout`` - how long a request waits for a *connection* when every
  pooled one is busy. Unbounded (SQLAlchemy's default is 30s, but the risk is
  the same in kind), a saturated pool turns into a worker that never returns.
* ``statement_timeout`` - how long PostgreSQL will run one *query* before
  cancelling it. This is the control that stops a pathological query from
  holding a pooled connection for minutes and starving everything else.

Together they cap the worst case: a request either gets a connection within
``pool_timeout`` or fails fast, and any query it then runs either finishes
within ``statement_timeout`` or is cancelled by the server. Neither is a
performance tuning knob; both exist so that a slow dependency degrades into
errors rather than into a hang.
"""

from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.db.immutability import install_immutability_guard

logger = logging.getLogger(__name__)


def _apply_statement_timeout(engine: Engine, timeout_ms: int) -> None:
    """Set ``statement_timeout`` on every new PostgreSQL connection.

    Applied per connection rather than in the DSN so it survives a pool
    recycle, and skipped entirely on SQLite, which has no equivalent - the test
    suite therefore does not exercise this, which `docs/security.md` records.
    """
    if timeout_ms <= 0 or engine.dialect.name != "postgresql":
        return

    @event.listens_for(engine, "connect")
    def _set_timeout(dbapi_connection: object, _record: object) -> None:
        with dbapi_connection.cursor() as cursor:  # type: ignore[attr-defined]
            # A literal built from an int we validated as a setting; psycopg
            # cannot parameterise SET, and the value never comes from a request.
            cursor.execute(f"SET statement_timeout = {int(timeout_ms)}")


def build_engine(settings: Settings | None = None) -> Engine:
    """Create a configured engine. Exposed so scripts and tests can build one."""
    settings = settings or get_settings()
    engine = create_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        pool_recycle=settings.database_pool_recycle,
        pool_pre_ping=True,  # transparently recycle connections dropped by the server
        future=True,
    )
    _apply_statement_timeout(engine, settings.database_statement_timeout_ms)
    return engine


install_immutability_guard()

engine: Engine = build_engine()

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session.

    Rolls back on the way out of a failed request. Without that, a session
    returned to the pool mid-transaction hands the *next* request a connection
    with an aborted transaction on it, and that request fails for reasons that
    have nothing to do with it - one of the harder bugs to read from a log.
    """
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
