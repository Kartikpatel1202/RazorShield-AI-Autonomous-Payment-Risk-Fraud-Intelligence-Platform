"""phase 8 analyst feedback

Adds ``analyst_feedback``: the structured conclusion an analyst reached about a
decided transaction.

Purely additive. No existing table is altered, no historical decision is touched,
and ``risk_decisions`` remains append-only - feedback is recorded *beside* a
decision, never inside it.

Two composite indexes are created rather than left to a later "add indexes when
slow" pass, because both back queries this phase runs on every dashboard load:
``(outcome, created_at)`` for the feedback summary and its time filters, and
``(risk_decision_id, outcome)`` for the machine-vs-human confusion matrix, which
joins feedback to decisions and groups by both.

Revision ID: c4a71f9e83b2
Revises: 9c1d4b7a2e50
Create Date: 2026-08-23 19:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4a71f9e83b2"
down_revision: str | None = "9c1d4b7a2e50"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OUTCOMES = (
    "confirmed_fraud",
    "legitimate",
    "false_positive",
    "false_negative",
    "insufficient_evidence",
    "escalated",
)

_REASONS = (
    "confirmed_fraud",
    "account_takeover",
    "coordinated_activity",
    "stolen_payment_method",
    "suspicious_device",
    "suspicious_ip",
    "legitimate_transaction",
    "known_customer_behavior",
    "trusted_merchant",
    "expected_location",
    "expected_device",
    "model_false_positive",
    "model_false_negative",
    "insufficient_evidence",
    "needs_more_information",
)


def upgrade() -> None:
    op.create_table(
        "analyst_feedback",
        sa.Column("public_id", sa.String(length=40), nullable=False),
        sa.Column("transaction_id", sa.BigInteger(), nullable=False),
        sa.Column("risk_decision_id", sa.BigInteger(), nullable=True),
        sa.Column("review_case_id", sa.BigInteger(), nullable=True),
        sa.Column("analyst_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "outcome",
            sa.Enum(*_OUTCOMES, name="feedbackoutcome", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "reason_code",
            sa.Enum(*_REASONS, name="feedbackreason", native_enum=False, length=48),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
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
            name=op.f("fk_analyst_feedback_transaction_id_transactions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["risk_decision_id"],
            ["risk_decisions.id"],
            name=op.f("fk_analyst_feedback_risk_decision_id_risk_decisions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["review_case_id"],
            ["review_cases.id"],
            name=op.f("fk_analyst_feedback_review_case_id_review_cases"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["analyst_id"],
            ["users.id"],
            name=op.f("fk_analyst_feedback_analyst_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analyst_feedback")),
    )
    op.create_index(
        op.f("ix_analyst_feedback_public_id"), "analyst_feedback", ["public_id"], unique=True
    )
    op.create_index(
        op.f("ix_analyst_feedback_transaction_id"),
        "analyst_feedback",
        ["transaction_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_analyst_feedback_risk_decision_id"),
        "analyst_feedback",
        ["risk_decision_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_analyst_feedback_review_case_id"),
        "analyst_feedback",
        ["review_case_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_analyst_feedback_analyst_id"), "analyst_feedback", ["analyst_id"], unique=False
    )
    op.create_index(op.f("ix_analyst_feedback_outcome"), "analyst_feedback", ["outcome"])
    op.create_index(op.f("ix_analyst_feedback_reason_code"), "analyst_feedback", ["reason_code"])
    op.create_index(
        "ix_analyst_feedback_outcome_created", "analyst_feedback", ["outcome", "created_at"]
    )
    op.create_index(
        "ix_analyst_feedback_decision_outcome",
        "analyst_feedback",
        ["risk_decision_id", "outcome"],
    )


def downgrade() -> None:
    op.drop_index("ix_analyst_feedback_decision_outcome", table_name="analyst_feedback")
    op.drop_index("ix_analyst_feedback_outcome_created", table_name="analyst_feedback")
    op.drop_index(op.f("ix_analyst_feedback_reason_code"), table_name="analyst_feedback")
    op.drop_index(op.f("ix_analyst_feedback_outcome"), table_name="analyst_feedback")
    op.drop_index(op.f("ix_analyst_feedback_analyst_id"), table_name="analyst_feedback")
    op.drop_index(op.f("ix_analyst_feedback_review_case_id"), table_name="analyst_feedback")
    op.drop_index(op.f("ix_analyst_feedback_risk_decision_id"), table_name="analyst_feedback")
    op.drop_index(op.f("ix_analyst_feedback_transaction_id"), table_name="analyst_feedback")
    op.drop_index(op.f("ix_analyst_feedback_public_id"), table_name="analyst_feedback")
    op.drop_table("analyst_feedback")
