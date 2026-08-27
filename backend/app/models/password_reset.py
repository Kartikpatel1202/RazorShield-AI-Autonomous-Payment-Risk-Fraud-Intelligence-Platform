"""Password reset tokens.

One row per reset request. Three properties make this table safe to have:

1. **The raw token is never stored.** Only its SHA-256 digest is, so a database
   leak yields nothing an attacker can present. See
   :func:`app.core.security.hash_reset_token` for why a fast hash is correct
   here and bcrypt is not.
2. **Single use.** ``used_at`` is stamped the moment a token is redeemed, and a
   used token is refused thereafter - a link forwarded, cached by a mail client
   or sitting in browser history cannot be replayed.
3. **Short-lived.** ``expires_at`` bounds the window during which any of that
   matters.

Requesting a new reset supersedes the outstanding ones for that account, so a
user who clicks "forgot password" three times cannot leave two extra live keys
behind them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import BigIntPk, CreatedAtMixin, PkMixin, UtcDateTime

if TYPE_CHECKING:
    from app.models.user import User


class PasswordResetToken(PkMixin, CreatedAtMixin, Base):
    """A single-use, expiring capability to set one account's password."""

    __tablename__ = "password_reset_tokens"

    user_id: Mapped[int] = mapped_column(
        BigIntPk, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    #: SHA-256 hex digest of the token that was handed out. Unique so a digest
    #: collision - or a bug that reused a token - fails loudly at the database
    #: rather than quietly granting two resets.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    #: Stamped on redemption. Null means unused.
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="password_reset_tokens")

    def is_usable(self, *, now: datetime | None = None) -> bool:
        """Whether this token may still be redeemed."""
        moment = now or datetime.now(UTC)
        return self.used_at is None and self.expires_at > moment

    def __repr__(self) -> str:
        # Deliberately omits the digest. A repr lands in tracebacks and debugger
        # output, and while a digest is not the token, it is not decoration
        # either.
        state = "used" if self.used_at else "pending"
        return f"<PasswordResetToken id={self.id} user_id={self.user_id} {state}>"
