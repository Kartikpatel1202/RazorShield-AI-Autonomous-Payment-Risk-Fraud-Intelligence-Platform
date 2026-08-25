"""phase 9 risk events

Adds ``risk_events`` - one row per stage a transaction passes through in the
live pipeline, plus the sequence that gives the stream a total order.

The ``sequence`` column is fed by its own database sequence rather than reusing
the primary key. They would be identical today, but the primary key is an
implementation detail of the table while ``sequence`` is part of the SSE
contract: a client sends the last one it saw back as ``Last-Event-ID``. Tying a
public protocol value to a surrogate key would make the key impossible to change
later.

Touches nothing that exists. No column is altered, no historical row is
modified, and no decision, prediction or signal is affected.

Revision ID: 3b8e5c1a94d7
Revises: c4a71f9e83b2
Create Date: 2026-08-24 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3b8e5c1a94d7"
down_revision: str | None = "c4a71f9e83b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_DOC = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

EVENT_TYPES = (
    "transaction_received",
    "risk_scored",
    "anomaly_detected",
    "investigation_started",
    "investigation_completed",
    "decision_created",
    "processing_failed",
)


def upgrade() -> None:
    op.create_table(
        "risk_events",
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("transaction_id", sa.BigInteger(), nullable=True),
        sa.Column("transaction_reference", sa.String(length=64), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(*EVENT_TYPES, name="riskeventtype", native_enum=False, length=48),
            nullable=False,
        ),
        sa.Column("transaction_sequence", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSON_DOC, nullable=False),
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name=op.f("fk_risk_events_transaction_id_transactions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_risk_events")),
    )
    op.create_index(op.f("ix_risk_events_public_id"), "risk_events", ["public_id"], unique=True)
    op.create_index(
        op.f("ix_risk_events_transaction_id"), "risk_events", ["transaction_id"], unique=False
    )
    op.create_index(
        op.f("ix_risk_events_transaction_reference"),
        "risk_events",
        ["transaction_reference"],
        unique=False,
    )
    op.create_index(op.f("ix_risk_events_event_type"), "risk_events", ["event_type"], unique=False)
    op.create_index(
        op.f("ix_risk_events_occurred_at"), "risk_events", ["occurred_at"], unique=False
    )
    op.create_index("ix_risk_events_sequence", "risk_events", ["sequence"], unique=False)
    op.create_index(
        "ix_risk_events_transaction_sequence",
        "risk_events",
        ["transaction_id", "sequence"],
        unique=False,
    )

    # A dedicated sequence, so the stream's public ordering is independent of
    # the surrogate primary key. SQLite has no CREATE SEQUENCE; there the
    # application falls back to MAX(sequence) + 1, which is safe because the
    # tests are single-writer.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("CREATE SEQUENCE risk_events_sequence_seq START WITH 1 INCREMENT BY 1"))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("DROP SEQUENCE IF EXISTS risk_events_sequence_seq"))

    op.drop_index("ix_risk_events_transaction_sequence", table_name="risk_events")
    op.drop_index("ix_risk_events_sequence", table_name="risk_events")
    op.drop_index(op.f("ix_risk_events_occurred_at"), table_name="risk_events")
    op.drop_index(op.f("ix_risk_events_event_type"), table_name="risk_events")
    op.drop_index(op.f("ix_risk_events_transaction_reference"), table_name="risk_events")
    op.drop_index(op.f("ix_risk_events_transaction_id"), table_name="risk_events")
    op.drop_index(op.f("ix_risk_events_public_id"), table_name="risk_events")
    op.drop_table("risk_events")
