"""Customer behaviour profiles.

Most customers are ordinary. A small minority behave in ways that later phases
should be able to flag - but the profile itself is never a risk score, only a
description of how the customer's history is generated.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import PaymentMethod, RiskLevel


@dataclass(frozen=True)
class BehaviourProfile:
    """How one class of customer transacts."""

    name: str
    #: Share of the customer population carrying this profile.
    population_share: float
    #: Typical transaction amount range in INR.
    amount_range: tuple[int, int]
    #: Expected transactions per month, used to weight the volume split.
    monthly_rate: tuple[float, float]
    #: How many distinct devices the customer normally pays from.
    device_count: tuple[int, int]
    #: How many distinct IP addresses the customer normally pays from.
    ip_count: tuple[int, int]
    #: Probability an individual attempt fails.
    failure_rate: float
    #: Probability a payment originates away from the customer's home city.
    travel_rate: float
    #: Probability a payment originates outside the customer's home country.
    foreign_rate: float
    #: Probability this customer ever charges back.
    chargeback_rate: float
    #: Multiplier applied to the dataset-wide fraud rate for this profile.
    fraud_multiplier: float
    historical_risk_level: RiskLevel
    payment_methods: tuple[PaymentMethod, ...]


NORMAL = BehaviourProfile(
    name="normal",
    population_share=0.70,
    amount_range=(1_000, 5_000),
    monthly_rate=(4.0, 12.0),
    device_count=(1, 2),
    ip_count=(1, 3),
    failure_rate=0.04,
    travel_rate=0.06,
    foreign_rate=0.01,
    chargeback_rate=0.01,
    fraud_multiplier=0.35,
    historical_risk_level=RiskLevel.LOW,
    payment_methods=(PaymentMethod.UPI, PaymentMethod.CARD, PaymentMethod.NETBANKING),
)

HIGH_VALUE = BehaviourProfile(
    name="high_value",
    population_share=0.14,
    amount_range=(20_000, 50_000),
    monthly_rate=(2.0, 6.0),
    device_count=(1, 2),
    ip_count=(1, 3),
    failure_rate=0.03,
    travel_rate=0.18,
    foreign_rate=0.09,
    chargeback_rate=0.02,
    fraud_multiplier=0.9,
    historical_risk_level=RiskLevel.LOW,
    payment_methods=(PaymentMethod.CARD, PaymentMethod.NETBANKING, PaymentMethod.EMI),
)

OCCASIONAL = BehaviourProfile(
    name="occasional",
    population_share=0.11,
    amount_range=(400, 2_500),
    monthly_rate=(0.6, 2.5),
    device_count=(1, 1),
    ip_count=(1, 2),
    failure_rate=0.06,
    travel_rate=0.04,
    foreign_rate=0.005,
    chargeback_rate=0.01,
    fraud_multiplier=0.5,
    historical_risk_level=RiskLevel.LOW,
    payment_methods=(PaymentMethod.UPI, PaymentMethod.WALLET),
)

RISKY = BehaviourProfile(
    name="risky",
    population_share=0.05,
    amount_range=(3_000, 25_000),
    monthly_rate=(12.0, 34.0),
    device_count=(2, 5),
    ip_count=(3, 7),
    failure_rate=0.24,
    travel_rate=0.35,
    foreign_rate=0.22,
    chargeback_rate=0.30,
    fraud_multiplier=9.0,
    historical_risk_level=RiskLevel.MEDIUM,
    payment_methods=(PaymentMethod.CARD, PaymentMethod.WALLET, PaymentMethod.UPI),
)

PROFILES: tuple[BehaviourProfile, ...] = (NORMAL, HIGH_VALUE, OCCASIONAL, RISKY)

PROFILES_BY_NAME: dict[str, BehaviourProfile] = {profile.name: profile for profile in PROFILES}
