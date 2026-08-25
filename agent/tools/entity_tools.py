"""Tools that describe the transaction and the entities behind it.

All four reuse the Phase 3 point-in-time provider rather than recomputing
history, so there is exactly one implementation of the boundary rule in the
codebase and one place it can be got wrong.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, func, select

from agent.schemas.evidence import EvidenceSeverity
from agent.tools.base import EvidenceDraft, ToolContext, ToolResult
from app.models import Customer, Device, IpAddress, Transaction
from app.models.enums import TransactionStatus
from app.services.context import build_transaction_context
from ml.features.point_in_time import (
    before_predicate,
    customer_history,
    device_history,
    ip_history,
)

#: How many recent rows a tool may return. Keeps payloads bounded and stops one
#: tool from dumping a customer's entire history into the prompt.
RECENT_LIMIT = 10
ASSOCIATED_LIMIT = 10

# Thresholds for banding observations into evidence severity. Explicit and
# reviewable rather than scattered magic numbers.
SHARED_ENTITY_MEDIUM = 2
SHARED_ENTITY_HIGH = 3
NEW_ENTITY_HOURS = 24.0
AMOUNT_RATIO_MEDIUM = 2.0
AMOUNT_RATIO_HIGH = 4.0
AMOUNT_RATIO_CRITICAL = 8.0


def get_transaction_context(ctx: ToolContext) -> ToolResult:
    """The transaction plus its customer, merchant, device, IP and location.

    Delegates to the Phase 2 context service, whose velocity windows are already
    anchored on the transaction's own timestamp.
    """
    context = build_transaction_context(ctx.session, ctx.transaction)
    transaction = context.transaction

    payload = {
        "transaction_id": transaction.transaction_id,
        "amount": float(transaction.amount),
        "currency": transaction.currency,
        "payment_method": str(transaction.payment_method),
        "status": str(transaction.status),
        "timestamp": transaction.transaction_timestamp.isoformat(),
        "country": transaction.country,
        "city": transaction.city,
        "failed_attempts": transaction.failed_attempts,
        "merchant": context.merchant.name,
        "merchant_category": context.merchant.category,
        "customer_external_id": context.customer.external_customer_id,
        "customer_home": f"{context.customer.city}, {context.customer.country}",
        "device_id": context.device.device_id if context.device else None,
        "ip_address": context.ip_address.ip_address if context.ip_address else None,
        "location_matches_home_country": context.location.matches_customer_home_country,
        "location_matches_home_city": context.location.matches_customer_home_city,
        "recent_transactions_returned": len(context.recent_customer_transactions),
    }

    evidence: list[EvidenceDraft] = []
    if not context.location.matches_customer_home_country:
        evidence.append(
            EvidenceDraft(
                claim=(
                    f"Payment originates from {transaction.country}, outside the customer's "
                    f"home country {context.customer.country}"
                ),
                severity=EvidenceSeverity.MEDIUM,
                details={
                    "transaction_country": transaction.country,
                    "home_country": context.customer.country,
                },
            )
        )
    if transaction.failed_attempts > 0:
        evidence.append(
            EvidenceDraft(
                claim=(
                    f"{transaction.failed_attempts} consecutive failed attempts immediately "
                    "preceded this payment"
                ),
                severity=(
                    EvidenceSeverity.HIGH
                    if transaction.failed_attempts >= 3
                    else EvidenceSeverity.MEDIUM
                ),
                value=float(transaction.failed_attempts),
                details={"failed_attempts": transaction.failed_attempts},
            )
        )

    return ToolResult(payload=payload, evidence=evidence)


def get_customer_history(ctx: ToolContext) -> ToolResult:
    """What the customer had done before this payment.

    Every figure comes from the point-in-time provider, so nothing the customer
    did afterwards is visible here.
    """
    history = customer_history(ctx.session, ctx.view)
    customer = ctx.session.get(Customer, ctx.view.customer_id)
    if customer is None:  # pragma: no cover - guarded by foreign keys
        raise LookupError(f"transaction {ctx.reference} references a missing customer")

    account_age_days = (ctx.boundary - customer.account_created_at).total_seconds() / 86400
    amount = float(ctx.view.amount)
    ratio = amount / history.mean_amount if history.mean_amount > 0 else 0.0

    recent = ctx.session.execute(
        select(
            Transaction.transaction_id,
            Transaction.amount,
            Transaction.status,
            Transaction.transaction_timestamp,
            Transaction.city,
        )
        .where(Transaction.customer_id == ctx.view.customer_id, before_predicate(ctx.view))
        .order_by(Transaction.transaction_timestamp.desc(), Transaction.id.desc())
        .limit(RECENT_LIMIT)
    ).all()

    prior_devices = int(
        ctx.session.scalar(
            select(func.count(func.distinct(Transaction.device_id))).where(
                Transaction.customer_id == ctx.view.customer_id,
                Transaction.device_id.is_not(None),
                before_predicate(ctx.view),
            )
        )
        or 0
    )

    payload = {
        "account_age_days": round(account_age_days, 1),
        "previous_transaction_count": history.transaction_count,
        "previous_success_count": history.success_count,
        "previous_failure_count": history.failure_count,
        "historical_failure_rate": round(history.failure_count / history.transaction_count, 4)
        if history.transaction_count
        else 0.0,
        "historical_average_amount": round(history.mean_amount, 2),
        "historical_amount_std": round(history.amount_std, 2),
        "historical_max_amount": round(history.amount_max, 2),
        "this_amount": amount,
        "amount_vs_historical_average": round(ratio, 3),
        "distinct_devices_before": prior_devices,
        "distinct_countries_before": len(history.country_counts),
        "distinct_cities_before": len(history.city_counts),
        "recent_transactions": [
            {
                "transaction_id": row[0],
                "amount": float(row[1]),
                "status": str(row[2]),
                "at": row[3].isoformat(),
                "city": row[4],
            }
            for row in recent
        ],
    }

    evidence: list[EvidenceDraft] = []
    if history.transaction_count == 0:
        evidence.append(
            EvidenceDraft(
                claim="This is the customer's first recorded transaction",
                severity=EvidenceSeverity.MEDIUM,
                value=0.0,
                details={"previous_transaction_count": 0},
            )
        )
    elif ratio >= AMOUNT_RATIO_MEDIUM:
        severity = EvidenceSeverity.MEDIUM
        if ratio >= AMOUNT_RATIO_CRITICAL:
            severity = EvidenceSeverity.CRITICAL
        elif ratio >= AMOUNT_RATIO_HIGH:
            severity = EvidenceSeverity.HIGH
        evidence.append(
            EvidenceDraft(
                claim=(
                    f"Amount is {ratio:.1f}x the customer's historical average of "
                    f"{history.mean_amount:,.0f}"
                ),
                severity=severity,
                value=round(ratio, 3),
                details={
                    "amount": amount,
                    "historical_average": round(history.mean_amount, 2),
                    "previous_transaction_count": history.transaction_count,
                },
            )
        )

    if history.transaction_count >= 5:
        failure_rate = history.failure_count / history.transaction_count
        if failure_rate >= 0.25:
            evidence.append(
                EvidenceDraft(
                    claim=f"Customer has a {failure_rate:.0%} historical failure rate",
                    severity=EvidenceSeverity.MEDIUM,
                    value=round(failure_rate, 4),
                    details={"failures": history.failure_count},
                )
            )

    return ToolResult(payload=payload, evidence=evidence)


def _associated_customers(ctx: ToolContext, entity_filter: ColumnElement[bool]) -> list[str]:
    """External ids of customers seen on an entity before the boundary."""
    rows = ctx.session.execute(
        select(Customer.external_customer_id)
        .join(Transaction, Transaction.customer_id == Customer.id)
        .where(entity_filter, before_predicate(ctx.view))
        .distinct()
        .limit(ASSOCIATED_LIMIT)
    ).all()
    return sorted(row[0] for row in rows)


def get_device_history(ctx: ToolContext) -> ToolResult:
    """Prior activity of the paying device, strictly before this transaction."""
    if ctx.view.device_id is None:
        return ToolResult(payload={"has_device": False, "reason": "no device fingerprint"})

    history = device_history(ctx.session, ctx.view)
    device = ctx.session.get(Device, ctx.view.device_id)
    if history is None or device is None:  # pragma: no cover - guarded by foreign keys
        return ToolResult(payload={"has_device": False, "reason": "device not found"})

    age_hours = (
        (ctx.boundary - history.first_seen_at).total_seconds() / 3600
        if history.first_seen_at
        else 0.0
    )
    customers = _associated_customers(ctx, Transaction.device_id == ctx.view.device_id)

    payload = {
        "has_device": True,
        "device_id": device.device_id,
        "device_type": str(device.device_type),
        "first_seen_before_transaction": history.first_seen_at.isoformat()
        if history.first_seen_at
        else None,
        "age_hours_at_transaction": round(age_hours, 2),
        "transactions_before": history.transaction_count,
        "distinct_customers_before": history.distinct_customers,
        "transactions_last_1h": history.counts.get("1h", 0),
        "transactions_last_24h": history.counts.get("24h", 0),
        "failed_last_1h": history.failed_counts.get("1h", 0),
        "customer_used_before": history.customer_used_before,
        "associated_customers": customers,
    }

    evidence: list[EvidenceDraft] = []
    if history.distinct_customers >= SHARED_ENTITY_MEDIUM:
        severity = (
            EvidenceSeverity.HIGH
            if history.distinct_customers >= SHARED_ENTITY_HIGH
            else EvidenceSeverity.MEDIUM
        )
        evidence.append(
            EvidenceDraft(
                claim=(
                    f"Device is shared across {history.distinct_customers} distinct customers "
                    "before this transaction"
                ),
                severity=severity,
                value=float(history.distinct_customers),
                details={
                    "customer_count": history.distinct_customers,
                    "associated_customers": customers,
                },
            )
        )
    if history.transaction_count == 0:
        evidence.append(
            EvidenceDraft(
                claim="Device has never been seen before this transaction",
                severity=EvidenceSeverity.HIGH,
                value=0.0,
                details={"transactions_before": 0},
            )
        )
    elif age_hours < NEW_ENTITY_HOURS:
        evidence.append(
            EvidenceDraft(
                claim=f"Device was first seen only {age_hours:.1f} hours before this payment",
                severity=EvidenceSeverity.HIGH,
                value=round(age_hours, 2),
                details={"age_hours": round(age_hours, 2)},
            )
        )

    hourly = history.counts.get("1h", 0)
    if hourly >= 5:
        evidence.append(
            EvidenceDraft(
                claim=f"Device processed {hourly} transactions in the preceding hour",
                severity=EvidenceSeverity.HIGH,
                value=float(hourly),
                details={"transactions_last_1h": hourly},
            )
        )
    failures = history.failed_counts.get("1h", 0)
    if failures >= 2:
        evidence.append(
            EvidenceDraft(
                claim=f"Device saw {failures} failed attempts in the preceding hour",
                severity=EvidenceSeverity.MEDIUM,
                value=float(failures),
                details={"failed_last_1h": failures},
            )
        )

    return ToolResult(payload=payload, evidence=evidence)


def get_ip_history(ctx: ToolContext) -> ToolResult:
    """Prior activity of the originating IP address, strictly before this transaction."""
    if ctx.view.ip_address_id is None:
        return ToolResult(payload={"has_ip": False, "reason": "no IP recorded"})

    history = ip_history(ctx.session, ctx.view)
    ip_record = ctx.session.get(IpAddress, ctx.view.ip_address_id)
    if history is None or ip_record is None:  # pragma: no cover - guarded by foreign keys
        return ToolResult(payload={"has_ip": False, "reason": "IP not found"})

    age_hours = (
        (ctx.boundary - history.first_seen_at).total_seconds() / 3600
        if history.first_seen_at
        else 0.0
    )
    customers = _associated_customers(ctx, Transaction.ip_address_id == ctx.view.ip_address_id)

    geography = ctx.session.execute(
        select(Transaction.country, func.count(Transaction.id))
        .where(Transaction.ip_address_id == ctx.view.ip_address_id, before_predicate(ctx.view))
        .group_by(Transaction.country)
    ).all()

    payload = {
        "has_ip": True,
        "ip_address": ip_record.ip_address,
        "registered_country": ip_record.country,
        "registered_city": ip_record.city,
        "reputation_score": float(ip_record.reputation_score),
        "is_proxy": ip_record.is_proxy,
        "age_hours_at_transaction": round(age_hours, 2),
        "transactions_before": history.transaction_count,
        "distinct_customers_before": history.distinct_customers,
        "transactions_last_1h": history.counts.get("1h", 0),
        "transactions_last_24h": history.counts.get("24h", 0),
        "failed_last_1h": history.failed_counts.get("1h", 0),
        "associated_customers": customers,
        "countries_seen": {country: int(count) for country, count in geography},
    }

    evidence: list[EvidenceDraft] = []
    if history.distinct_customers >= SHARED_ENTITY_MEDIUM:
        severity = (
            EvidenceSeverity.HIGH
            if history.distinct_customers >= SHARED_ENTITY_HIGH
            else EvidenceSeverity.MEDIUM
        )
        evidence.append(
            EvidenceDraft(
                claim=(
                    f"IP address is shared across {history.distinct_customers} distinct "
                    "customers before this transaction"
                ),
                severity=severity,
                value=float(history.distinct_customers),
                details={
                    "customer_count": history.distinct_customers,
                    "associated_customers": customers,
                },
            )
        )
    if ip_record.is_proxy:
        evidence.append(
            EvidenceDraft(
                claim=f"IP address {ip_record.ip_address} is flagged as a proxy",
                severity=EvidenceSeverity.MEDIUM,
                details={"is_proxy": True},
            )
        )
    reputation = float(ip_record.reputation_score)
    if reputation < 30:
        evidence.append(
            EvidenceDraft(
                claim=f"IP address carries a low simulated reputation score of {reputation:.1f}",
                severity=EvidenceSeverity.MEDIUM,
                value=reputation,
                details={"reputation_score": reputation},
            )
        )

    hourly = history.counts.get("1h", 0)
    if hourly >= 5:
        evidence.append(
            EvidenceDraft(
                claim=f"IP address processed {hourly} transactions in the preceding hour",
                severity=EvidenceSeverity.HIGH,
                value=float(hourly),
                details={"transactions_last_1h": hourly},
            )
        )

    return ToolResult(payload=payload, evidence=evidence)


def _unused(status: TransactionStatus) -> None:  # pragma: no cover - typing aid
    """Keeps the enum import meaningful."""
