"""phase 7 decision latency

Adds ``risk_decisions.evaluation_ms`` - how long the pure policy evaluation
took, in milliseconds.

This is an observability column, not a decision input. No rule reads it, it
takes no part in the decision, and the reproducibility digest deliberately does
not cover it: two runs of the same context must remain identical decisions even
though they will not take identical time.

Nullable, because decisions written before this migration have no measurement
and inventing one would be worse than admitting it is absent.

Revision ID: 9c1d4b7a2e50
Revises: 5fe7ba2834e5
Create Date: 2026-08-23 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c1d4b7a2e50"
down_revision: str | None = "5fe7ba2834e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("risk_decisions", sa.Column("evaluation_ms", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("risk_decisions", "evaluation_ms")
