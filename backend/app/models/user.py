"""Platform users: merchants, risk analysts and administrators."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import CreatedAtMixin, PkMixin, enum_column
from app.models.enums import UserRole

if TYPE_CHECKING:
    from app.models.review import AnalystDecision, ReviewCase


class User(PkMixin, CreatedAtMixin, Base):
    """An operator of the platform.

    Authentication is not implemented in this phase; ``password_hash`` exists so
    the schema is ready for it and is never populated with a real credential by
    the seed generator.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        enum_column(UserRole), default=UserRole.MERCHANT, index=True, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    assigned_cases: Mapped[list[ReviewCase]] = relationship(
        back_populates="assignee", foreign_keys="ReviewCase.assigned_to"
    )
    decisions: Mapped[list[AnalystDecision]] = relationship(back_populates="analyst")

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role}>"
