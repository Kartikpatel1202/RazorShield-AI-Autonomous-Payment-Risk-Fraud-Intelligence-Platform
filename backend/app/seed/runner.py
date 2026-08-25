"""Orchestrates a full seed run: reset, generate, derive, validate, summarise."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import (
    AnalystDecision,
    AuditLog,
    Customer,
    CustomerDevice,
    Device,
    Investigation,
    IpAddress,
    Merchant,
    ModelFeedback,
    ReviewCase,
    RiskPrediction,
    RiskRule,
    RiskSignal,
    Transaction,
    User,
)
from app.seed import scenarios as scn
from app.seed.aggregates import recompute_all
from app.seed.config import SeedConfig
from app.seed.population import build_population
from app.seed.transactions import build_transactions
from app.seed.validation import sharing_summary, validate

logger = logging.getLogger(__name__)

# Child tables first so foreign keys never block a delete.
_RESET_ORDER = (
    ModelFeedback,
    AnalystDecision,
    ReviewCase,
    Investigation,
    RiskSignal,
    RiskPrediction,
    AuditLog,
    Transaction,
    CustomerDevice,
    RiskRule,
    Customer,
    Device,
    IpAddress,
    Merchant,
    User,
)


@dataclass
class SeedResult:
    """Row counts and headline statistics from one seed run."""

    random_seed: int
    reference_time: datetime
    merchants: int
    customers: int
    devices: int
    ip_addresses: int
    transactions: int
    fraud_transactions: int
    legitimate_transactions: int
    shared_devices: int
    shared_ip_addresses: int
    demo_scenarios: int
    demo_customer_ids: list[str]

    @property
    def fraud_rate(self) -> float:
        return self.fraud_transactions / self.transactions if self.transactions else 0.0


def reset_simulation_data(session: Session) -> None:
    """Remove every simulation row, child tables first.

    Only tables owned by the simulation are touched; ``alembic_version`` and the
    schema itself are left alone.
    """
    for model in _RESET_ORDER:
        session.execute(delete(model))
    session.flush()
    logger.info("Cleared %d simulation tables", len(_RESET_ORDER))


def _count(session: Session, model: type) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def seed_database(session: Session, config: SeedConfig | None = None) -> SeedResult:
    """Rebuild the whole simulation dataset inside the caller's transaction.

    The caller owns the commit, so a failed validation leaves the database
    untouched.
    """
    config = config or SeedConfig()
    rng = random.Random(config.random_seed)

    logger.info(
        "Seeding dataset (seed=%d, reference_time=%s)", config.random_seed, config.reference_time
    )
    reset_simulation_data(session)

    population = build_population(session, config, rng)

    # Demo scenarios are built first so their hand-crafted transactions are
    # subtracted from the background budget and the total lands on target.
    scenario = scn.build_scenarios(session, config, population.merchants[0])
    budget = max(len(population.plans), config.transactions - scenario.transaction_count)
    build_transactions(session, config, rng, population.plans, budget)

    chargebacks = {plan.customer.id: plan.chargeback_count for plan in population.plans}
    recompute_all(session, chargebacks)

    validate(session, config)

    total = _count(session, Transaction)
    fraud = int(
        session.scalar(select(func.count(Transaction.id)).where(Transaction.is_fraud.is_(True)))
        or 0
    )
    shared_devices, shared_ips = sharing_summary(session)

    return SeedResult(
        random_seed=config.random_seed,
        reference_time=config.reference_time,
        merchants=_count(session, Merchant),
        customers=_count(session, Customer),
        devices=_count(session, Device),
        ip_addresses=_count(session, IpAddress),
        transactions=total,
        fraud_transactions=fraud,
        legitimate_transactions=total - fraud,
        shared_devices=shared_devices,
        shared_ip_addresses=shared_ips,
        demo_scenarios=3,
        demo_customer_ids=scenario.customer_ids,
    )


def format_summary(result: SeedResult) -> str:
    """Human-readable run summary. Contains no secrets and no risk scores."""
    lines = [
        "RazorShield AI - simulation dataset",
        "-" * 46,
        f"Random seed              : {result.random_seed}",
        f"Reference time (UTC)     : {result.reference_time:%Y-%m-%d %H:%M}",
        "",
        f"Merchants                : {result.merchants:,}",
        f"Customers                : {result.customers:,}",
        f"Devices                  : {result.devices:,}",
        f"IP addresses             : {result.ip_addresses:,}",
        f"Transactions             : {result.transactions:,}",
        "",
        f"Fraud transactions       : {result.fraud_transactions:,} ({result.fraud_rate:.2%})",
        f"Legitimate transactions  : {result.legitimate_transactions:,}",
        "",
        f"Shared devices           : {result.shared_devices:,}",
        f"Shared IP addresses      : {result.shared_ip_addresses:,}",
        f"Demo scenarios           : {result.demo_scenarios}",
        f"Demo customers           : {', '.join(result.demo_customer_ids)}",
        "",
        "Risk tables (predictions, signals, investigations, review cases,",
        "analyst decisions, rules, audit logs, model feedback) are empty by",
        "design - those are populated by later phases.",
    ]
    return "\n".join(lines)
