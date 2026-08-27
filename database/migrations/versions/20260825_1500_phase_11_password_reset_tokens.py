"""password reset tokens

Adds ``password_reset_tokens`` - one row per password-reset request, holding a
SHA-256 digest of the token that was issued rather than the token itself.

Touches nothing that exists. No column is altered on ``users`` or anywhere else,
no historical row is modified, and no transaction, prediction, signal,
investigation, decision, review case, feedback record or audit entry is
affected. The only relationship added is a foreign key *from* the new table.

Revision ID: 7d1c4b9a2e60
Revises: 3b8e5c1a94d7
Create Date: 2026-08-25 15:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7d1c4b9a2e60"
down_revision: str | None = "3b8e5c1a94d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        # 64 hex characters: a SHA-256 digest. The raw token is never written.
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        # Null until redeemed. Stamping it is what makes a token single-use.
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_password_reset_tokens_user_id_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_password_reset_tokens"),
    )
    # Unique, not merely indexed: a duplicate digest means either a collision or
    # a bug that handed the same token out twice, and both should fail at the
    # database rather than silently granting two resets.
    op.create_index(
        "ix_password_reset_tokens_token_hash",
        "password_reset_tokens",
        ["token_hash"],
        unique=True,
    )
    # Every lookup on redemption and every supersede-on-new-request filters by
    # user, so this index is on the read path, not speculative.
    op.create_index(
        "ix_password_reset_tokens_user_id",
        "password_reset_tokens",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_password_reset_tokens_user_id", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_token_hash", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
