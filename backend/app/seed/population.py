"""Builds the static population: users, merchants, devices, IPs and customers.

Device ownership is modelled the way real fingerprints behave: each customer
owns one or more private devices, and a deliberately small shared pool is linked
to several unrelated customers so later phases have a genuine sharing signal.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import TypeVar

from sqlalchemy.orm import Session

from app.models import Customer, CustomerDevice, Device, IpAddress, Merchant, User
from app.models.enums import DeviceType, UserRole
from app.seed.config import SeedConfig
from app.seed.identifiers import device_fingerprint, email_for, full_name, ip_address
from app.seed.locations import DOMESTIC_LOCATIONS, Location
from app.seed.profiles import PROFILES, BehaviourProfile

logger = logging.getLogger(__name__)

T = TypeVar("T")

MERCHANT_BLUEPRINTS: tuple[tuple[str, str], ...] = (
    ("Kirana Cart", "retail"),
    ("Skyline Travel", "travel"),
    ("Nimbus Streaming", "digital_subscription"),
    ("Auric Jewels", "luxury_retail"),
    ("Pulse Fitness", "fitness"),
)

DEVICE_TYPE_WEIGHTS: tuple[tuple[DeviceType, float], ...] = (
    (DeviceType.ANDROID, 0.52),
    (DeviceType.IOS, 0.24),
    (DeviceType.WEB_DESKTOP, 0.16),
    (DeviceType.WEB_MOBILE, 0.08),
)

# Shared devices are allocated deliberately rather than by chance: a device
# only carries a sharing signal if several unrelated customers really do use it.
SHARED_DEVICE_CUSTOMERS = (3, 5)

# One shared device per this many customers, capped by SeedConfig.shared_devices,
# so small datasets still contain the pattern.
CUSTOMERS_PER_SHARED_DEVICE = 25

# Floor for tiny datasets, kept above the validation threshold so a small
# test dataset still exhibits the sharing pattern.
MIN_SHARED_DEVICES = 6


@dataclass
class CustomerPlan:
    """A customer plus everything needed to generate their transaction history."""

    customer: Customer
    profile: BehaviourProfile
    home: Location
    device_ids: list[int] = field(default_factory=list)
    ip_ids: list[int] = field(default_factory=list)
    chargeback_count: int = 0
    #: Set only for the minority of customers on a device shared with strangers.
    shared_device_id: int | None = None


@dataclass
class Population:
    """Everything created before any transaction exists."""

    users: list[User]
    merchants: list[Merchant]
    devices: list[Device]
    ip_addresses: list[IpAddress]
    plans: list[CustomerPlan]
    shared_device_ids: list[int]
    public_ip_ids: list[int]


def _weighted_choice(rng: random.Random, options: tuple[tuple[T, float], ...]) -> T:
    values = [value for value, _ in options]
    weights = [weight for _, weight in options]
    return rng.choices(values, weights=weights, k=1)[0]


def _pick_profile(rng: random.Random) -> BehaviourProfile:
    return rng.choices(
        list(PROFILES), weights=[profile.population_share for profile in PROFILES], k=1
    )[0]


def build_users(session: Session, config: SeedConfig, rng: random.Random) -> list[User]:
    """Create operator accounts.

    ``password_hash`` is deliberately left unset: no credential, real or fake,
    is written by the seed generator.
    """
    created_at = config.history_start - timedelta(days=30)
    blueprints: list[tuple[str, UserRole]] = [
        ("Ops Admin", UserRole.ADMIN),
        ("Risk Analyst One", UserRole.RISK_ANALYST),
        ("Risk Analyst Two", UserRole.RISK_ANALYST),
        ("Merchant Operator", UserRole.MERCHANT),
    ]
    users = [
        User(
            email=email_for(name, index, rng),
            full_name=name,
            role=role,
            is_active=True,
            created_at=created_at,
        )
        for index, (name, role) in enumerate(blueprints)
    ]
    session.add_all(users)
    session.flush()
    return users


def build_merchants(session: Session, config: SeedConfig, rng: random.Random) -> list[Merchant]:
    merchants: list[Merchant] = []
    for index in range(config.merchants):
        name, category = MERCHANT_BLUEPRINTS[index % len(MERCHANT_BLUEPRINTS)]
        slug = name.lower().replace(" ", "")
        merchants.append(
            Merchant(
                external_merchant_id=f"mrc_{index + 1:04d}",
                name=name,
                email=f"payments@{slug}.example.com",
                category=category,
                country="IN",
                is_active=True,
                created_at=config.history_start - timedelta(days=rng.randint(120, 540)),
            )
        )
    session.add_all(merchants)
    session.flush()
    return merchants


def build_ip_addresses(
    session: Session, config: SeedConfig, rng: random.Random
) -> tuple[list[IpAddress], list[int]]:
    """Create the IP pool and return it alongside the public/NAT subset."""
    records: list[IpAddress] = []
    public_count = int(config.ip_addresses * config.public_ip_share)
    first_public_index = config.ip_addresses - public_count

    for index in range(config.ip_addresses):
        location = rng.choice(DOMESTIC_LOCATIONS)
        is_public = index >= first_public_index
        records.append(
            IpAddress(
                ip_address=ip_address(index),
                country=location.country,
                city=location.city,
                first_seen_at=config.history_start,
                last_seen_at=config.reference_time,
                # Simulated only. No external reputation service is consulted.
                reputation_score=Decimal(str(round(rng.uniform(55.0, 99.0), 2))),
                is_proxy=is_public and rng.random() < 0.15,
                created_at=config.history_start,
            )
        )

    session.add_all(records)
    session.flush()
    return records, [record.id for record in records[first_public_index:]]


def _new_device(config: SeedConfig, rng: random.Random, index: int) -> Device:
    """A device fingerprint.

    ``first_seen_at``/``last_seen_at``/``is_trusted`` are provisional; they are
    recomputed from real transaction history once transactions exist.
    """
    return Device(
        device_id=device_fingerprint(index),
        device_type=_weighted_choice(rng, DEVICE_TYPE_WEIGHTS),
        first_seen_at=config.history_start,
        last_seen_at=config.reference_time,
        is_trusted=False,
        created_at=config.history_start,
    )


def _build_customers(
    session: Session, config: SeedConfig, rng: random.Random, merchants: list[Merchant]
) -> tuple[list[CustomerPlan], list[int]]:
    """Create customers and decide how many private devices each one owns."""
    plans: list[CustomerPlan] = []
    records: list[Customer] = []
    private_device_counts: list[int] = []

    for index in range(config.customers):
        profile = _pick_profile(rng)
        home = rng.choice(DOMESTIC_LOCATIONS)
        merchant = merchants[index % len(merchants)]
        tenure_days = rng.randint(config.history_days + 15, config.history_days + 900)
        opened_at = config.reference_time - timedelta(days=tenure_days)
        name = full_name(rng)

        customer = Customer(
            merchant_id=merchant.id,
            external_customer_id=f"cus_{index + 1:06d}",
            email=email_for(name, index, rng),
            account_created_at=opened_at,
            country=home.country,
            city=home.city,
            created_at=opened_at,
            updated_at=config.reference_time,
        )
        records.append(customer)
        plans.append(CustomerPlan(customer=customer, profile=profile, home=home))
        private_device_counts.append(rng.randint(*profile.device_count))

    session.add_all(records)
    session.flush()
    return plans, private_device_counts


def _assign_shared_devices(
    rng: random.Random,
    plans: list[CustomerPlan],
    shared_device_ids: list[int],
) -> list[tuple[CustomerPlan, int]]:
    """Deliberately place several unrelated customers on each shared device.

    Risky customers are drawn first, so the shared devices skew towards the
    population later phases should find interesting - without every shared
    device being fraudulent.
    """
    risky = [plan for plan in plans if plan.profile.name == "risky"]
    others = [plan for plan in plans if plan.profile.name != "risky"]
    rng.shuffle(risky)
    rng.shuffle(others)

    candidates = risky + others
    assignments: list[tuple[CustomerPlan, int]] = []
    cursor = 0

    for device_id in shared_device_ids:
        wanted = rng.randint(*SHARED_DEVICE_CUSTOMERS)
        if cursor + wanted > len(candidates):
            break
        for plan in candidates[cursor : cursor + wanted]:
            plan.shared_device_id = device_id
            plan.device_ids.append(device_id)
            assignments.append((plan, device_id))
        cursor += wanted

    return assignments


def _link_devices_and_ips(
    session: Session,
    config: SeedConfig,
    rng: random.Random,
    plans: list[CustomerPlan],
    private_devices: list[list[Device]],
    shared_device_ids: list[int],
    residential_ip_ids: list[int],
    public_ip_ids: list[int],
) -> None:
    """Attach each customer to their devices and their usual IP addresses."""
    for plan, owned in zip(plans, private_devices, strict=True):
        plan.device_ids = [device.id for device in owned]

        # One stable residential address, plus optional public/NAT space.
        plan.ip_ids = [residential_ip_ids[plan.customer.id % len(residential_ip_ids)]]
        for _ in range(max(0, rng.randint(*plan.profile.ip_count) - 1)):
            pool = public_ip_ids if rng.random() < 0.5 else residential_ip_ids
            candidate = rng.choice(pool)
            if candidate not in plan.ip_ids:
                plan.ip_ids.append(candidate)

        # Chargebacks predate the generated transaction window, so they are a
        # standalone historical counter rather than derived from transactions.
        plan.chargeback_count = (
            rng.randint(1, 3) if rng.random() < plan.profile.chargeback_rate else 0
        )

    _assign_shared_devices(rng, plans, shared_device_ids)

    links: list[CustomerDevice] = []
    for plan in plans:
        first_used = max(plan.customer.account_created_at, config.history_start)
        links.extend(
            CustomerDevice(
                customer_id=plan.customer.id,
                device_id=device_id,
                first_used_at=first_used,
                last_used_at=config.reference_time,
                transaction_count=0,
            )
            for device_id in plan.device_ids
        )

    session.add_all(links)
    session.flush()


def build_population(session: Session, config: SeedConfig, rng: random.Random) -> Population:
    """Create every non-transactional entity and wire customers to devices/IPs."""
    users = build_users(session, config, rng)
    merchants = build_merchants(session, config, rng)
    ip_records, public_ip_ids = build_ip_addresses(session, config, rng)
    residential_ip_ids = [record.id for record in ip_records if record.id not in set(public_ip_ids)]

    plans, private_device_counts = _build_customers(session, config, rng, merchants)

    # Shared devices are allocated first so their fingerprints are stable. The
    # count scales with the population so small datasets keep the pattern.
    shared_device_count = min(
        config.shared_devices,
        max(MIN_SHARED_DEVICES, config.customers // CUSTOMERS_PER_SHARED_DEVICE),
    )
    devices = [_new_device(config, rng, index) for index in range(shared_device_count)]
    next_index = shared_device_count

    private_devices: list[list[Device]] = []
    for count in private_device_counts:
        owned = [_new_device(config, rng, next_index + offset) for offset in range(count)]
        next_index += count
        private_devices.append(owned)
        devices.extend(owned)

    session.add_all(devices)
    session.flush()

    shared_device_ids = [device.id for device in devices[:shared_device_count]]
    _link_devices_and_ips(
        session,
        config,
        rng,
        plans,
        private_devices,
        shared_device_ids,
        residential_ip_ids,
        public_ip_ids,
    )

    logger.info(
        "Population: %d users, %d merchants, %d customers, %d devices, %d IP addresses",
        len(users),
        len(merchants),
        len(plans),
        len(devices),
        len(ip_records),
    )
    return Population(
        users=users,
        merchants=merchants,
        devices=devices,
        ip_addresses=ip_records,
        plans=plans,
        shared_device_ids=shared_device_ids,
        public_ip_ids=public_ip_ids,
    )
