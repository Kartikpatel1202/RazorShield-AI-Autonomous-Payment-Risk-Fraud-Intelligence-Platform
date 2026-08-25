"""Post-seed validation.

Every check queries the database rather than the in-memory generator state, so a
pass means the persisted dataset is genuinely consistent. Any failure aborts the
seed run with the full list of problems.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.orm import Session

from app.models import Customer, Device, IpAddress, Merchant, Transaction
from app.models.enums import TransactionStatus
from app.seed import scenarios as scn
from app.seed.config import SeedConfig
from app.seed.locations import VALID_CURRENCIES

logger = logging.getLogger(__name__)

# Entities shared between unrelated customers, below which the
# coordinated-fraud signal would not be learnable. Both scale with the
# population so the check is meaningful at any dataset size.
SHARED_DEVICE_FLOOR = 4
CUSTOMERS_PER_SHARED_DEVICE = 30
SHARED_IP_FLOOR = 5
CUSTOMERS_PER_SHARED_IP = 12


def _minimum_shared_devices(config: SeedConfig) -> int:
    return max(SHARED_DEVICE_FLOOR, config.customers // CUSTOMERS_PER_SHARED_DEVICE)


def _minimum_shared_ips(config: SeedConfig) -> int:
    return max(SHARED_IP_FLOOR, config.customers // CUSTOMERS_PER_SHARED_IP)


# The realised fraud rate is stochastic; accept a band around the target.
FRAUD_RATE_TOLERANCE = 0.6


class SeedValidationError(RuntimeError):
    """Raised when the generated dataset fails its own consistency checks."""

    def __init__(self, failures: list[str]) -> None:
        self.failures = failures
        detail = "\n".join(f"  - {failure}" for failure in failures)
        super().__init__(f"Seed validation failed with {len(failures)} problem(s):\n{detail}")


def _count(session: Session, statement: Select[Any]) -> int:
    return int(session.scalar(select(func.count()).select_from(statement.subquery())) or 0)


def _is_scenario_row() -> ColumnElement[bool]:
    """Matches the hand-crafted demo-scenario transactions."""
    return or_(
        *(
            Transaction.transaction_id.startswith(prefix)
            for prefix in scn.SCENARIO_REFERENCE_PREFIXES
        )
    )


def _check_referential_integrity(session: Session) -> list[str]:
    failures: list[str] = []

    orphan_merchants = _count(
        session,
        select(Transaction.id)
        .outerjoin(Merchant, Transaction.merchant_id == Merchant.id)
        .where(Merchant.id.is_(None)),
    )
    if orphan_merchants:
        failures.append(f"{orphan_merchants} transactions reference a missing merchant")

    orphan_customers = _count(
        session,
        select(Transaction.id)
        .outerjoin(Customer, Transaction.customer_id == Customer.id)
        .where(Customer.id.is_(None)),
    )
    if orphan_customers:
        failures.append(f"{orphan_customers} transactions reference a missing customer")

    orphan_devices = _count(
        session,
        select(Transaction.id)
        .outerjoin(Device, Transaction.device_id == Device.id)
        .where(Transaction.device_id.is_not(None), Device.id.is_(None)),
    )
    if orphan_devices:
        failures.append(f"{orphan_devices} transactions reference a missing device")

    orphan_ips = _count(
        session,
        select(Transaction.id)
        .outerjoin(IpAddress, Transaction.ip_address_id == IpAddress.id)
        .where(Transaction.ip_address_id.is_not(None), IpAddress.id.is_(None)),
    )
    if orphan_ips:
        failures.append(f"{orphan_ips} transactions reference a missing IP address")

    # Every customer must belong to a merchant that exists.
    orphan_customer_merchants = _count(
        session,
        select(Customer.id)
        .outerjoin(Merchant, Customer.merchant_id == Merchant.id)
        .where(Merchant.id.is_(None)),
    )
    if orphan_customer_merchants:
        failures.append(f"{orphan_customer_merchants} customers reference a missing merchant")

    return failures


def _check_field_validity(session: Session, config: SeedConfig) -> list[str]:
    failures: list[str] = []

    non_positive = _count(session, select(Transaction.id).where(Transaction.amount <= 0))
    if non_positive:
        failures.append(f"{non_positive} transactions have a non-positive amount")

    bad_currency = _count(
        session, select(Transaction.id).where(Transaction.currency.not_in(VALID_CURRENCIES))
    )
    if bad_currency:
        failures.append(f"{bad_currency} transactions use an unrecognised currency")

    # A small slack absorbs the hour-of-day shift applied to timestamps.
    earliest = config.history_start - timedelta(days=1)
    latest = config.reference_time + timedelta(minutes=1)
    out_of_range = _count(
        session,
        select(Transaction.id).where(
            (Transaction.transaction_timestamp < earliest)
            | (Transaction.transaction_timestamp > latest)
        ),
    )
    if out_of_range:
        failures.append(f"{out_of_range} transactions fall outside the dataset time window")

    bad_status = _count(
        session,
        select(Transaction.id).where(
            Transaction.status.not_in([status.value for status in TransactionStatus])
        ),
    )
    if bad_status:
        failures.append(f"{bad_status} transactions have an invalid status")

    null_labels = _count(session, select(Transaction.id).where(Transaction.is_fraud.is_(None)))
    if null_labels:
        failures.append(f"{null_labels} transactions have no fraud label")

    # The demo scenarios are hand-crafted and deliberately fraud-heavy, so the
    # prevalence band is checked against the generated background stream only.
    background = ~_is_scenario_row()
    total = int(session.scalar(select(func.count(Transaction.id)).where(background)) or 0)
    fraud = int(
        session.scalar(
            select(func.count(Transaction.id)).where(background, Transaction.is_fraud.is_(True))
        )
        or 0
    )
    realised = fraud / total if total else 0.0
    low = config.fraud_rate * (1 - FRAUD_RATE_TOLERANCE)
    high = config.fraud_rate * (1 + FRAUD_RATE_TOLERANCE)
    if not low <= realised <= high:
        failures.append(
            f"background fraud prevalence {realised:.4f} is outside the expected band "
            f"[{low:.4f}, {high:.4f}]"
        )

    return failures


def _shared_entity_counts(session: Session) -> tuple[int, int]:
    """How many devices and IPs are used by more than one customer."""
    shared_devices = _count(
        session,
        select(Transaction.device_id)
        .where(Transaction.device_id.is_not(None))
        .group_by(Transaction.device_id)
        .having(func.count(func.distinct(Transaction.customer_id)) > 1),
    )
    shared_ips = _count(
        session,
        select(Transaction.ip_address_id)
        .where(Transaction.ip_address_id.is_not(None))
        .group_by(Transaction.ip_address_id)
        .having(func.count(func.distinct(Transaction.customer_id)) > 1),
    )
    return shared_devices, shared_ips


def _check_sharing(session: Session, config: SeedConfig) -> list[str]:
    failures: list[str] = []
    shared_devices, shared_ips = _shared_entity_counts(session)
    minimum_devices = _minimum_shared_devices(config)
    minimum_ips = _minimum_shared_ips(config)

    if shared_devices < minimum_devices:
        failures.append(
            f"only {shared_devices} devices are shared between customers, expected at least "
            f"{minimum_devices}"
        )
    if shared_ips < minimum_ips:
        failures.append(
            f"only {shared_ips} IP addresses are shared between customers, expected at least "
            f"{minimum_ips}"
        )
    return failures


def _customer_by_external_id(session: Session, external_id: str) -> Customer | None:
    return session.scalar(select(Customer).where(Customer.external_customer_id == external_id))


def _transaction(session: Session, reference: str) -> Transaction | None:
    return session.scalar(select(Transaction).where(Transaction.transaction_id == reference))


def _check_scenario_a(session: Session) -> list[str]:
    failures: list[str] = []
    customer = _customer_by_external_id(session, scn.CUSTOMER_NORMAL)
    if customer is None:
        return [f"scenario A customer {scn.CUSTOMER_NORMAL} is missing"]

    history = int(
        session.scalar(
            select(func.count(Transaction.id)).where(Transaction.customer_id == customer.id)
        )
        or 0
    )
    if history < scn.NORMAL_HISTORY_COUNT:
        failures.append(f"scenario A has only {history} transactions, expected a settled history")

    devices = int(
        session.scalar(
            select(func.count(func.distinct(Transaction.device_id))).where(
                Transaction.customer_id == customer.id
            )
        )
        or 0
    )
    if devices != 1:
        failures.append(f"scenario A customer used {devices} devices, expected exactly 1")

    current = _transaction(session, scn.TXN_SCENARIO_A_CURRENT)
    if current is None:
        failures.append(f"scenario A transaction {scn.TXN_SCENARIO_A_CURRENT} is missing")
    elif current.is_fraud:
        failures.append("scenario A current transaction must be labelled legitimate")
    return failures


def _check_scenario_b(session: Session, config: SeedConfig) -> list[str]:
    failures: list[str] = []
    customer = _customer_by_external_id(session, scn.CUSTOMER_SUSPICIOUS)
    if customer is None:
        return [f"scenario B customer {scn.CUSTOMER_SUSPICIOUS} is missing"]

    current = _transaction(session, scn.TXN_SCENARIO_B_CURRENT)
    if current is None:
        return [f"scenario B transaction {scn.TXN_SCENARIO_B_CURRENT} is missing"]

    if current.amount != scn.SUSPICIOUS_CURRENT_AMOUNT:
        failures.append(
            f"scenario B amount is {current.amount}, expected {scn.SUSPICIOUS_CURRENT_AMOUNT}"
        )

    # The evidence a later phase needs must be derivable from the rows.
    baseline = session.scalar(
        select(func.avg(Transaction.amount)).where(
            Transaction.customer_id == customer.id,
            Transaction.status == TransactionStatus.SUCCESSFUL.value,
        )
    )
    if baseline is None or not 1_500 <= float(baseline) <= 3_500:
        failures.append(
            f"scenario B baseline spend {baseline} is not in the expected 1.5k-3.5k band"
        )

    window_start = config.reference_time - timedelta(hours=1)
    recent_failures = int(
        session.scalar(
            select(func.count(Transaction.id)).where(
                Transaction.customer_id == customer.id,
                Transaction.status == TransactionStatus.FAILED.value,
                Transaction.transaction_timestamp >= window_start,
            )
        )
        or 0
    )
    if recent_failures < scn.SUSPICIOUS_FAILED_ATTEMPTS:
        failures.append(
            f"scenario B has {recent_failures} recent failed attempts, expected "
            f"{scn.SUSPICIOUS_FAILED_ATTEMPTS}"
        )

    new_device = session.scalar(select(Device).where(Device.device_id == scn.DEVICE_SUSPICIOUS_NEW))
    if new_device is None:
        failures.append("scenario B new device is missing")
    elif new_device.first_seen_at < window_start:
        failures.append("scenario B device is not recent enough to read as unfamiliar")

    if current.country == customer.country:
        failures.append("scenario B transaction must originate outside the customer's home country")

    return failures


def _check_scenario_c(session: Session, config: SeedConfig) -> list[str]:
    failures: list[str] = []
    device = session.scalar(select(Device).where(Device.device_id == scn.DEVICE_FRAUD_SHARED))
    ip = session.scalar(select(IpAddress).where(IpAddress.ip_address == scn.IP_FRAUD_SHARED))
    if device is None or ip is None:
        return ["scenario C shared device or shared IP is missing"]

    device_customers = int(
        session.scalar(
            select(func.count(func.distinct(Transaction.customer_id))).where(
                Transaction.device_id == device.id
            )
        )
        or 0
    )
    if device_customers != len(scn.CUSTOMERS_FRAUD):
        failures.append(
            f"scenario C shared device is used by {device_customers} customers, expected "
            f"{len(scn.CUSTOMERS_FRAUD)}"
        )

    ip_customers = int(
        session.scalar(
            select(func.count(func.distinct(Transaction.customer_id))).where(
                Transaction.ip_address_id == ip.id
            )
        )
        or 0
    )
    if ip_customers != len(scn.CUSTOMERS_FRAUD):
        failures.append(
            f"scenario C shared IP is used by {ip_customers} customers, expected "
            f"{len(scn.CUSTOMERS_FRAUD)}"
        )

    window_start = config.reference_time - timedelta(hours=1)
    for external_id in scn.CUSTOMERS_FRAUD:
        customer = _customer_by_external_id(session, external_id)
        if customer is None:
            failures.append(f"scenario C customer {external_id} is missing")
            continue

        fraud_rows = int(
            session.scalar(
                select(func.count(Transaction.id)).where(
                    Transaction.customer_id == customer.id, Transaction.is_fraud.is_(True)
                )
            )
            or 0
        )
        if fraud_rows < 1:
            failures.append(f"scenario C customer {external_id} has no fraudulent transaction")

        burst = int(
            session.scalar(
                select(func.count(Transaction.id)).where(
                    Transaction.customer_id == customer.id,
                    Transaction.transaction_timestamp >= window_start,
                )
            )
            or 0
        )
        if burst < scn.FRAUD_BURST_PER_CUSTOMER:
            failures.append(
                f"scenario C customer {external_id} has {burst} transactions in the last hour, "
                f"expected at least {scn.FRAUD_BURST_PER_CUSTOMER}"
            )

    for reference in scn.TXN_SCENARIO_C_CURRENT:
        if _transaction(session, reference) is None:
            failures.append(f"scenario C transaction {reference} is missing")

    return failures


def validate(session: Session, config: SeedConfig) -> None:
    """Run every check. Raises :class:`SeedValidationError` if anything fails."""
    failures: list[str] = []
    failures += _check_referential_integrity(session)
    failures += _check_field_validity(session, config)
    failures += _check_sharing(session, config)
    failures += _check_scenario_a(session)
    failures += _check_scenario_b(session, config)
    failures += _check_scenario_c(session, config)

    if failures:
        raise SeedValidationError(failures)
    logger.info("Seed validation passed")


def sharing_summary(session: Session) -> tuple[int, int]:
    """Shared device and shared IP counts, for the run summary."""
    return _shared_entity_counts(session)
