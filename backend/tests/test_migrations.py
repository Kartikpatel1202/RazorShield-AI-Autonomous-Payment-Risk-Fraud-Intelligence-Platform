"""Alembic migration tests.

These run against a real PostgreSQL database, because the schema uses
PostgreSQL-specific types (JSONB) that SQLite cannot represent. Point
``TEST_DATABASE_URL`` at a throwaway database - never at the simulation one,
since every test here drops the entire schema.

    TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/razorshield_test \
        python -m pytest tests/test_migrations.py
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect

from app.models import Base
from tests.test_models import EXPECTED_TABLES

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set; migration tests need a disposable PostgreSQL database",
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Guard against ever pointing these destructive tests at the real dataset.
FORBIDDEN_DATABASE_SUFFIXES = ("/razorshield",)


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL or "")
    return config


@pytest.fixture()
def migrated_engine() -> Generator[Engine, None, None]:
    """A database migrated to head, torn back down to base afterwards."""
    assert TEST_DATABASE_URL
    assert not TEST_DATABASE_URL.endswith(FORBIDDEN_DATABASE_SUFFIXES), (
        "refusing to run destructive migration tests against the simulation database"
    )

    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    engine = create_engine(TEST_DATABASE_URL)
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


def test_upgrade_creates_every_table(migrated_engine: Engine) -> None:
    tables = set(inspect(migrated_engine).get_table_names())
    assert EXPECTED_TABLES <= tables
    assert "alembic_version" in tables


def test_migration_matches_the_models(migrated_engine: Engine) -> None:
    """Autogenerate must find nothing left to do."""
    with migrated_engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = compare_metadata(context, Base.metadata)

    assert differences == [], f"models and migration have drifted: {differences}"


def test_downgrade_removes_every_table() -> None:
    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_engine(TEST_DATABASE_URL or "")
    try:
        remaining = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert remaining == set()
    finally:
        engine.dispose()


def test_transaction_lookup_columns_are_indexed(migrated_engine: Engine) -> None:
    """Every column the future risk queries filter on must be index-backed.

    A composite index counts: PostgreSQL uses it for lookups on its leading
    column, so ``(customer_id, transaction_timestamp)`` also serves plain
    ``customer_id`` filters.
    """
    indexes = inspect(migrated_engine).get_indexes("transactions")
    leading_columns = {index["column_names"][0] for index in indexes if index["column_names"]}

    for column in (
        "customer_id",
        "device_id",
        "ip_address_id",
        "merchant_id",
        "transaction_timestamp",
        "transaction_id",
    ):
        assert column in leading_columns, f"{column} has no index that leads with it"


def test_foreign_keys_are_declared(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    referenced = {fk["referred_table"] for fk in inspector.get_foreign_keys("transactions")}
    assert referenced == {"merchants", "customers", "devices", "ip_addresses"}

    assert {fk["referred_table"] for fk in inspector.get_foreign_keys("customer_devices")} == {
        "customers",
        "devices",
    }
    assert {fk["referred_table"] for fk in inspector.get_foreign_keys("analyst_decisions")} == {
        "review_cases",
        "users",
    }
