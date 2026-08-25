"""The three deterministic demo scenarios.

Each scenario writes only *evidence*: histories, devices, IP addresses and
timings that make a later risk verdict derivable from the database. No scenario
stores a risk score, a fraud probability or a risk signal - deriving those is the
job of Phase 3 and beyond.

Scenario A  normal payment by a settled customer on a known device.
Scenario B  account-takeover shape: a customer who normally spends ~2-3k
            suddenly attempting 85k from a brand new device, new IP, new
            country, moments after three failed attempts.
Scenario C  coordinated fraud: three unrelated customers sharing one device and
            one proxy IP, bursting transactions minutes apart.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.models import Customer, CustomerDevice, Device, IpAddress, Merchant, Transaction
from app.models.enums import DeviceType, PaymentMethod, TransactionStatus
from app.seed.config import SeedConfig
from app.seed.locations import DATASET_CURRENCY
from app.seed.transactions import TransactionRow

logger = logging.getLogger(__name__)

# Stable external identifiers. Tests, docs and later demos reference these.
CUSTOMER_NORMAL = "CUSTOMER_NORMAL_001"
CUSTOMER_SUSPICIOUS = "CUSTOMER_SUSPICIOUS_001"
CUSTOMERS_FRAUD = ("CUSTOMER_FRAUD_001", "CUSTOMER_FRAUD_002", "CUSTOMER_FRAUD_003")

DEVICE_NORMAL = "dev_scn_normal_001"
DEVICE_SUSPICIOUS_HOME = "dev_scn_suspicious_home_001"
DEVICE_SUSPICIOUS_NEW = "dev_scn_suspicious_new_001"
DEVICE_FRAUD_SHARED = "dev_scn_fraud_shared_001"
DEVICES_FRAUD_PRIVATE = ("dev_scn_fraud_001", "dev_scn_fraud_002", "dev_scn_fraud_003")

# Reserved-range addresses that the generated pool never reaches.
IP_NORMAL = "198.18.100.11"
IP_SUSPICIOUS_HOME = "198.18.100.21"
IP_SUSPICIOUS_NEW = "198.18.100.22"
IP_FRAUD_SHARED = "198.18.100.31"

# Every scenario transaction reference starts with one of these, so the
# hand-crafted rows can be told apart from the generated background stream.
SCENARIO_REFERENCE_PREFIXES: tuple[str, ...] = ("txn_scn_", "TXN_SCENARIO_")

TXN_SCENARIO_A_CURRENT = "TXN_SCENARIO_A_CURRENT"
TXN_SCENARIO_B_CURRENT = "TXN_SCENARIO_B_CURRENT"
TXN_SCENARIO_C_CURRENT = (
    "TXN_SCENARIO_C_CURRENT_1",
    "TXN_SCENARIO_C_CURRENT_2",
    "TXN_SCENARIO_C_CURRENT_3",
)

NORMAL_HISTORY_COUNT = 60
SUSPICIOUS_HISTORY_COUNT = 45
SUSPICIOUS_FAILED_ATTEMPTS = 3
SUSPICIOUS_CURRENT_AMOUNT = Decimal("85000.00")
FRAUD_HISTORY_PER_CUSTOMER = 4
FRAUD_BURST_PER_CUSTOMER = 4


@dataclass
class ScenarioSummary:
    """What the scenario builder created, for reporting and validation."""

    transaction_count: int = 0
    customer_ids: list[str] = field(default_factory=list)
    shared_device_id: str = ""
    shared_ip: str = ""


def _customer(
    merchant: Merchant, external_id: str, config: SeedConfig, city: str, tenure_days: int
) -> Customer:
    opened_at = config.reference_time - timedelta(days=tenure_days)
    return Customer(
        merchant_id=merchant.id,
        external_customer_id=external_id,
        email=f"{external_id.lower()}@example.com",
        account_created_at=opened_at,
        country="IN",
        city=city,
        created_at=opened_at,
        updated_at=config.reference_time,
    )


def _device(
    fingerprint: str, device_type: DeviceType, first_seen: datetime, last_seen: datetime
) -> Device:
    return Device(
        device_id=fingerprint,
        device_type=device_type,
        first_seen_at=first_seen,
        last_seen_at=last_seen,
        is_trusted=False,
        created_at=first_seen,
    )


def _ip(
    address: str,
    country: str,
    city: str,
    first_seen: datetime,
    last_seen: datetime,
    reputation: str,
    is_proxy: bool = False,
) -> IpAddress:
    return IpAddress(
        ip_address=address,
        country=country,
        city=city,
        first_seen_at=first_seen,
        last_seen_at=last_seen,
        reputation_score=Decimal(reputation),
        is_proxy=is_proxy,
        created_at=first_seen,
    )


def _row(
    *,
    reference: str,
    merchant_id: int,
    customer_id: int,
    device_id: int,
    ip_id: int,
    amount: Decimal,
    method: PaymentMethod,
    status: TransactionStatus,
    stamp: datetime,
    country: str,
    city: str,
    failed_attempts: int = 0,
    is_fraud: bool = False,
) -> TransactionRow:
    return TransactionRow(
        transaction_id=reference,
        merchant_id=merchant_id,
        customer_id=customer_id,
        device_id=device_id,
        ip_address_id=ip_id,
        amount=amount,
        currency=DATASET_CURRENCY,
        payment_method=method.value,
        status=status.value,
        transaction_timestamp=stamp,
        country=country,
        city=city,
        failed_attempts=failed_attempts,
        is_fraud=is_fraud,
        created_at=stamp,
    )


def _link(
    customer: Customer, device: Device, first_used: datetime, last_used: datetime
) -> CustomerDevice:
    return CustomerDevice(
        customer_id=customer.id,
        device_id=device.id,
        first_used_at=first_used,
        last_used_at=last_used,
        transaction_count=0,
    )


def _build_normal(
    session: Session, config: SeedConfig, rng: random.Random, merchant: Merchant
) -> list[TransactionRow]:
    """Scenario A - a settled customer making an ordinary payment."""
    customer = _customer(merchant, CUSTOMER_NORMAL, config, "Mumbai", tenure_days=540)
    session.add(customer)
    session.flush()

    first_seen = config.reference_time - timedelta(days=config.history_days)
    device = _device(DEVICE_NORMAL, DeviceType.IOS, first_seen, config.reference_time)
    ip = _ip(IP_NORMAL, "IN", "Mumbai", first_seen, config.reference_time, "94.50")
    session.add_all([device, ip])
    session.flush()
    session.add(_link(customer, device, first_seen, config.reference_time))

    rows: list[TransactionRow] = []
    # One day of headroom keeps the hour-of-day jitter inside the window even
    # when history_days is small.
    step = timedelta(days=(config.history_days - 1) / NORMAL_HISTORY_COUNT)
    for index in range(NORMAL_HISTORY_COUNT):
        stamp = (
            first_seen
            + step * index
            + timedelta(hours=rng.randint(9, 21), minutes=rng.randrange(60))
        )
        rows.append(
            _row(
                reference=f"txn_scn_a_{index + 1:03d}",
                merchant_id=merchant.id,
                customer_id=customer.id,
                device_id=device.id,
                ip_id=ip.id,
                amount=Decimal(str(round(rng.uniform(1_000, 5_000), 2))),
                method=PaymentMethod.UPI,
                status=TransactionStatus.SUCCESSFUL,
                stamp=stamp,
                country="IN",
                city="Mumbai",
            )
        )

    # The payment a risk engine would be asked to judge.
    rows.append(
        _row(
            reference=TXN_SCENARIO_A_CURRENT,
            merchant_id=merchant.id,
            customer_id=customer.id,
            device_id=device.id,
            ip_id=ip.id,
            amount=Decimal("2450.00"),
            method=PaymentMethod.UPI,
            status=TransactionStatus.PENDING,
            stamp=config.reference_time - timedelta(minutes=2),
            country="IN",
            city="Mumbai",
        )
    )
    return rows


def _build_suspicious(
    session: Session, config: SeedConfig, rng: random.Random, merchant: Merchant
) -> list[TransactionRow]:
    """Scenario B - familiar customer, suddenly unfamiliar everything."""
    customer = _customer(merchant, CUSTOMER_SUSPICIOUS, config, "Pune", tenure_days=420)
    session.add(customer)
    session.flush()

    history_start = config.reference_time - timedelta(days=config.history_days)
    # The new device and IP appear only minutes before the payment in question.
    intrusion_start = config.reference_time - timedelta(minutes=25)

    home_device = _device(
        DEVICE_SUSPICIOUS_HOME, DeviceType.ANDROID, history_start, config.reference_time
    )
    new_device = _device(
        DEVICE_SUSPICIOUS_NEW, DeviceType.WEB_DESKTOP, intrusion_start, config.reference_time
    )
    home_ip = _ip(IP_SUSPICIOUS_HOME, "IN", "Pune", history_start, config.reference_time, "91.20")
    new_ip = _ip(
        IP_SUSPICIOUS_NEW,
        "SG",
        "Singapore",
        intrusion_start,
        config.reference_time,
        "23.40",
        is_proxy=True,
    )
    session.add_all([home_device, new_device, home_ip, new_ip])
    session.flush()
    session.add_all(
        [
            _link(customer, home_device, history_start, intrusion_start),
            _link(customer, new_device, intrusion_start, config.reference_time),
        ]
    )

    rows: list[TransactionRow] = []
    step = timedelta(days=(config.history_days - 1) / SUSPICIOUS_HISTORY_COUNT)
    for index in range(SUSPICIOUS_HISTORY_COUNT):
        stamp = (
            history_start
            + step * index
            + timedelta(hours=rng.randint(10, 20), minutes=rng.randrange(60))
        )
        rows.append(
            _row(
                reference=f"txn_scn_b_{index + 1:03d}",
                merchant_id=merchant.id,
                customer_id=customer.id,
                device_id=home_device.id,
                ip_id=home_ip.id,
                amount=Decimal(str(round(rng.uniform(2_000, 3_000), 2))),
                method=PaymentMethod.CARD,
                status=TransactionStatus.SUCCESSFUL,
                stamp=stamp,
                country="IN",
                city="Pune",
            )
        )

    # Three rejected attempts in the minutes leading up to the big one.
    for attempt in range(SUSPICIOUS_FAILED_ATTEMPTS):
        rows.append(
            _row(
                reference=f"txn_scn_b_fail_{attempt + 1}",
                merchant_id=merchant.id,
                customer_id=customer.id,
                device_id=new_device.id,
                ip_id=new_ip.id,
                amount=Decimal(str(round(70_000 + attempt * 5_000, 2))),
                method=PaymentMethod.CARD,
                status=TransactionStatus.FAILED,
                stamp=intrusion_start + timedelta(minutes=attempt * 6),
                country="SG",
                city="Singapore",
                failed_attempts=attempt,
                is_fraud=True,
            )
        )

    rows.append(
        _row(
            reference=TXN_SCENARIO_B_CURRENT,
            merchant_id=merchant.id,
            customer_id=customer.id,
            device_id=new_device.id,
            ip_id=new_ip.id,
            amount=SUSPICIOUS_CURRENT_AMOUNT,
            method=PaymentMethod.CARD,
            status=TransactionStatus.PENDING,
            stamp=config.reference_time - timedelta(minutes=1),
            country="SG",
            city="Singapore",
            failed_attempts=SUSPICIOUS_FAILED_ATTEMPTS,
            is_fraud=True,
        )
    )
    return rows


def _build_fraud_ring(
    session: Session, config: SeedConfig, rng: random.Random, merchant: Merchant
) -> list[TransactionRow]:
    """Scenario C - three customers, one device, one proxy IP, one burst."""
    # Thin history, but never longer than the configured window.
    history_span = min(30, max(2, config.history_days - 1))
    history_start = config.reference_time - timedelta(days=history_span)
    burst_start = config.reference_time - timedelta(minutes=40)

    shared_device = _device(
        DEVICE_FRAUD_SHARED, DeviceType.WEB_DESKTOP, burst_start, config.reference_time
    )
    shared_ip = _ip(
        IP_FRAUD_SHARED,
        "SG",
        "Singapore",
        burst_start,
        config.reference_time,
        "11.50",
        is_proxy=True,
    )
    session.add_all([shared_device, shared_ip])
    session.flush()

    rows: list[TransactionRow] = []

    for member, (external_id, private_fingerprint) in enumerate(
        zip(CUSTOMERS_FRAUD, DEVICES_FRAUD_PRIVATE, strict=True)
    ):
        customer = _customer(merchant, external_id, config, "Delhi", tenure_days=45)
        session.add(customer)
        session.flush()

        private_device = _device(
            private_fingerprint, DeviceType.ANDROID, history_start, burst_start
        )
        private_ip = _ip(
            f"198.18.101.{member + 1}", "IN", "Delhi", history_start, burst_start, "68.00"
        )
        session.add_all([private_device, private_ip])
        session.flush()
        session.add_all(
            [
                _link(customer, private_device, history_start, burst_start),
                _link(customer, shared_device, burst_start, config.reference_time),
            ]
        )

        # A thin, unremarkable history establishes the account as ordinary.
        for index in range(FRAUD_HISTORY_PER_CUSTOMER):
            rows.append(
                _row(
                    reference=f"txn_scn_c_{member + 1}_h{index + 1}",
                    merchant_id=merchant.id,
                    customer_id=customer.id,
                    device_id=private_device.id,
                    ip_id=private_ip.id,
                    amount=Decimal(str(round(rng.uniform(600, 2_200), 2))),
                    method=PaymentMethod.UPI,
                    status=TransactionStatus.SUCCESSFUL,
                    stamp=history_start
                    + timedelta(
                        days=index * history_span / (FRAUD_HISTORY_PER_CUSTOMER + 1),
                        hours=rng.randint(10, 20),
                    ),
                    country="IN",
                    city="Delhi",
                )
            )

        # The burst: minutes apart, escalating, on the shared device and proxy.
        consecutive_failures = 0
        for index in range(FRAUD_BURST_PER_CUSTOMER):
            failed = index % 3 == 1
            status = TransactionStatus.FAILED if failed else TransactionStatus.SUCCESSFUL
            rows.append(
                _row(
                    reference=f"txn_scn_c_{member + 1}_b{index + 1}",
                    merchant_id=merchant.id,
                    customer_id=customer.id,
                    device_id=shared_device.id,
                    ip_id=shared_ip.id,
                    amount=Decimal(str(9_900 + member * 2_400 + index * 1_750)),
                    method=PaymentMethod.CARD,
                    status=status,
                    stamp=burst_start + timedelta(minutes=member * 2 + index * 8),
                    country="SG",
                    city="Singapore",
                    failed_attempts=consecutive_failures,
                    is_fraud=True,
                )
            )
            consecutive_failures = consecutive_failures + 1 if failed else 0

        rows.append(
            _row(
                reference=TXN_SCENARIO_C_CURRENT[member],
                merchant_id=merchant.id,
                customer_id=customer.id,
                device_id=shared_device.id,
                ip_id=shared_ip.id,
                amount=Decimal(str(24_500 + member * 3_000)),
                method=PaymentMethod.CARD,
                status=TransactionStatus.PENDING,
                stamp=config.reference_time - timedelta(minutes=3 - member),
                country="SG",
                city="Singapore",
                failed_attempts=consecutive_failures,
                is_fraud=True,
            )
        )

    return rows


def build_scenarios(session: Session, config: SeedConfig, merchant: Merchant) -> ScenarioSummary:
    """Create all three demo scenarios and return what was built."""
    # A dedicated stream keeps scenario data stable even if the background
    # generator changes.
    rng = random.Random(config.random_seed + 977)

    rows: list[TransactionRow] = []
    rows.extend(_build_normal(session, config, rng, merchant))
    rows.extend(_build_suspicious(session, config, rng, merchant))
    rows.extend(_build_fraud_ring(session, config, rng, merchant))

    session.execute(insert(Transaction), rows)
    session.flush()

    logger.info("Created 3 demo scenarios with %d transactions", len(rows))
    return ScenarioSummary(
        transaction_count=len(rows),
        customer_ids=[CUSTOMER_NORMAL, CUSTOMER_SUSPICIOUS, *CUSTOMERS_FRAUD],
        shared_device_id=DEVICE_FRAUD_SHARED,
        shared_ip=IP_FRAUD_SHARED,
    )
