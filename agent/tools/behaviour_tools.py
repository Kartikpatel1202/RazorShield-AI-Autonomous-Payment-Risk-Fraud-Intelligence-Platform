"""Velocity and location tools.

Both read from the Phase 3 point-in-time provider rather than recomputing
windows, so the numbers an investigator sees are the same ones the models were
scored on.
"""

from __future__ import annotations

from agent.schemas.evidence import EvidenceSeverity
from agent.tools.base import EvidenceDraft, ToolContext, ToolResult
from app.models import Customer
from ml.features.point_in_time import customer_history

# Velocity levels that warrant recording as evidence. A handful of payments an
# hour is ordinary; a burst is not.
VELOCITY_1H_MEDIUM = 3
VELOCITY_1H_HIGH = 5
VELOCITY_5M_HIGH = 2
FAILED_1H_MEDIUM = 2
FAILED_1H_HIGH = 3


def get_velocity(ctx: ToolContext) -> ToolResult:
    """How fast the customer was moving in the run-up to this payment."""
    history = customer_history(ctx.session, ctx.view)
    counts = history.counts
    failures = history.failed_counts
    amounts = history.amounts

    payload = {
        "transactions_last_5m": counts.get("5m", 0),
        "transactions_last_1h": counts.get("1h", 0),
        "transactions_last_24h": counts.get("24h", 0),
        "transactions_last_7d": counts.get("7d", 0),
        "failed_transactions_last_1h": failures.get("1h", 0),
        "failed_transactions_last_24h": failures.get("24h", 0),
        "amount_last_1h": round(amounts.get("1h", 0.0), 2),
        "amount_last_24h": round(amounts.get("24h", 0.0), 2),
        "windows_end_at": ctx.boundary.isoformat(),
    }

    evidence: list[EvidenceDraft] = []
    hourly = counts.get("1h", 0)
    if hourly >= VELOCITY_1H_MEDIUM:
        severity = EvidenceSeverity.HIGH if hourly >= VELOCITY_1H_HIGH else EvidenceSeverity.MEDIUM
        evidence.append(
            EvidenceDraft(
                claim=f"Customer made {hourly} transactions in the hour before this payment",
                severity=severity,
                value=float(hourly),
                details={"transactions_last_1h": hourly},
            )
        )

    burst = counts.get("5m", 0)
    if burst >= VELOCITY_5M_HIGH:
        evidence.append(
            EvidenceDraft(
                claim=f"Customer made {burst} transactions in the preceding five minutes",
                severity=EvidenceSeverity.HIGH,
                value=float(burst),
                details={"transactions_last_5m": burst},
            )
        )

    recent_failures = failures.get("1h", 0)
    if recent_failures >= FAILED_1H_MEDIUM:
        severity = (
            EvidenceSeverity.HIGH if recent_failures >= FAILED_1H_HIGH else EvidenceSeverity.MEDIUM
        )
        evidence.append(
            EvidenceDraft(
                claim=(
                    f"{recent_failures} of the customer's transactions failed in the preceding hour"
                ),
                severity=severity,
                value=float(recent_failures),
                details={"failed_transactions_last_1h": recent_failures},
            )
        )

    return ToolResult(payload=payload, evidence=evidence)


def get_location_history(ctx: ToolContext) -> ToolResult:
    """Where the customer has paid from before, and how this payment compares."""
    history = customer_history(ctx.session, ctx.view)
    customer = ctx.session.get(Customer, ctx.view.customer_id)
    if customer is None:  # pragma: no cover - guarded by foreign keys
        raise LookupError(f"transaction {ctx.reference} references a missing customer")

    country = ctx.view.country
    city = ctx.view.city
    total = history.transaction_count

    country_seen = history.country_counts.get(country, 0)
    city_seen = history.city_counts.get(city, 0)
    country_frequency = country_seen / total if total else 0.0
    city_frequency = city_seen / total if total else 0.0

    country_changed = history.last_country is not None and history.last_country != country
    city_changed = history.last_city is not None and history.last_city != city
    new_country = total > 0 and country_seen == 0

    payload = {
        "current_country": country,
        "current_city": city,
        "home_country": customer.country,
        "home_city": customer.city,
        "previous_countries": dict(sorted(history.country_counts.items())),
        "previous_cities": dict(sorted(history.city_counts.items())),
        "previous_location_count": len(history.city_counts),
        "country_frequency": round(country_frequency, 4),
        "city_frequency": round(city_frequency, 4),
        "country_changed_since_last": country_changed,
        "city_changed_since_last": city_changed,
        "is_new_country_for_customer": new_country,
        "is_home_country": country == customer.country,
        "is_home_city": city == customer.city,
    }

    evidence: list[EvidenceDraft] = []
    if new_country:
        evidence.append(
            EvidenceDraft(
                claim=(
                    f"Customer has never previously transacted from {country}; all "
                    f"{total} prior payments came from elsewhere"
                ),
                severity=EvidenceSeverity.HIGH,
                value=0.0,
                details={
                    "country": country,
                    "previous_countries": sorted(history.country_counts),
                },
            )
        )
    elif country_changed:
        evidence.append(
            EvidenceDraft(
                claim=(
                    f"Country changed from {history.last_country} to {country} since the "
                    "customer's previous payment"
                ),
                severity=EvidenceSeverity.MEDIUM,
                details={"previous_country": history.last_country, "country": country},
            )
        )

    if total >= 5 and country != customer.country and country_frequency < 0.1:
        evidence.append(
            EvidenceDraft(
                claim=(
                    f"Only {country_frequency:.0%} of the customer's history originates from "
                    f"{country}"
                ),
                severity=EvidenceSeverity.MEDIUM,
                value=round(country_frequency, 4),
                details={"country_frequency": round(country_frequency, 4)},
            )
        )

    if total > 0 and not evidence:
        evidence.append(
            EvidenceDraft(
                claim=(
                    f"Location {city}, {country} is consistent with the customer's history "
                    f"({city_frequency:.0%} of prior payments)"
                ),
                severity=EvidenceSeverity.INFO,
                value=round(city_frequency, 4),
                details={"city_frequency": round(city_frequency, 4)},
            )
        )

    return ToolResult(payload=payload, evidence=evidence)
