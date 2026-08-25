"""Devices and the customer-device association.

A device shared by several customers is one of the strongest coordinated-fraud
signals available, so the many-to-many link is modelled explicitly rather than
being inferred from transactions alone.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import CreatedAtMixin, PkMixin, UtcDateTime, enum_column
from app.models.enums import DeviceType

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.transaction import Transaction


class Device(PkMixin, CreatedAtMixin, Base):
    """A device fingerprint observed making payments."""

    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    device_type: Mapped[DeviceType] = mapped_column(
        enum_column(DeviceType), index=True, nullable=False
    )
    device_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True, nullable=False)
    is_trusted: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)

    customer_links: Mapped[list[CustomerDevice]] = relationship(
        back_populates="device", cascade="all, delete-orphan"
    )
    customers: Mapped[list[Customer]] = relationship(
        secondary="customer_devices", back_populates="devices", viewonly=True
    )
    transactions: Mapped[list[Transaction]] = relationship(back_populates="device")

    def __repr__(self) -> str:
        return f"<Device id={self.id} device_id={self.device_id!r}>"


class CustomerDevice(Base):
    """Association row linking a customer to a device they have used."""

    __tablename__ = "customer_devices"
    __table_args__ = (Index("ix_customer_devices_device_customer", "device_id", "customer_id"),)

    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), primary_key=True
    )
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    first_used_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    transaction_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="device_links")
    device: Mapped[Device] = relationship(back_populates="customer_links")

    def __repr__(self) -> str:
        return f"<CustomerDevice customer_id={self.customer_id} device_id={self.device_id}>"
