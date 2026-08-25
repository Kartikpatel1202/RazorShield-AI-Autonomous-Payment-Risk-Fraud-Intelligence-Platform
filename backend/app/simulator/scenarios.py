"""Scenario generators: behaviour in, nothing else.

Each generator produces the *characteristics* of a payment - amount, device, IP,
location, timing, velocity. None of them sets a fraud probability, an anomaly
score, a risk level or a decision. Those are computed by Phase 3, 4 and 6 from
the behaviour, exactly as they are for the seeded dataset.

That constraint is the whole point of the phase. A simulator that assigned
outcomes would be a puppet show: it could "demonstrate" any result regardless of
whether the models could actually reach it. Because these generators only
describe behaviour, what the dashboard shows is what the real pipeline decided,
and a scenario that fails to produce its intended outcome is telling you
something true about the models.

Every generator is seeded, so the same seed yields the same sequence.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models.enums import DeviceType, PaymentMethod, SimulatorScenario, TransactionStatus
from app.services.ingest import SIMULATED_PREFIX, TransactionEvent

#: Cities the generators draw from, with the country each sits in.
_HOME_CITIES = (("Mumbai", "IN"), ("Delhi", "IN"), ("Bengaluru", "IN"), ("Pune", "IN"))
_FOREIGN_CITIES = (("Singapore", "SG"), ("Dubai", "AE"), ("London", "GB"))


@dataclass(frozen=True)
class ScenarioDoc:
    """What a scenario generates, and what it is trying to exercise.

    ``expected_signal`` describes the *behaviour* the pipeline is expected to
    notice - never the decision. The decision is measured, not predicted.
    """

    scenario: SimulatorScenario
    title: str
    behaviour: str
    expected_signal: str


SCENARIO_DOCS: dict[SimulatorScenario, ScenarioDoc] = {
    SimulatorScenario.NORMAL: ScenarioDoc(
        scenario=SimulatorScenario.NORMAL,
        title="Normal traffic",
        behaviour=(
            "Everyday amounts (₹300-₹6,000) from a small pool of returning customers on "
            "their own devices and home IPs, in their home city, spaced minutes apart."
        ),
        expected_signal=(
            "Nothing unusual for either model to find. Whether that becomes an approval "
            "is the policy's call, not the simulator's."
        ),
    ),
    SimulatorScenario.SUSPICIOUS: ScenarioDoc(
        scenario=SimulatorScenario.SUSPICIOUS,
        title="Suspicious behaviour",
        behaviour=(
            "Larger amounts (₹20,000-₹90,000) from a first-seen device and a first-seen "
            "IP, in a country the customer does not live in, with an occasional failed "
            "attempt immediately before."
        ),
        expected_signal=(
            "Several individually-weak signals at once: new device, new IP, foreign "
            "location, elevated amount versus the customer's own history."
        ),
    ),
    SimulatorScenario.HIGH_FRAUD: ScenarioDoc(
        scenario=SimulatorScenario.HIGH_FRAUD,
        title="Strong supervised evidence",
        behaviour=(
            "Very large amounts (₹80,000-₹150,000) on brand-new devices and proxy IPs, "
            "several per minute from one customer, after consecutive failures - the "
            "feature combination the supervised model was trained to weight heavily."
        ),
        expected_signal=(
            "Strong evidence for the XGBoost model specifically. The probability is "
            "computed by the model; this generator only supplies the behaviour."
        ),
    ),
    SimulatorScenario.COORDINATED_FRAUD: ScenarioDoc(
        scenario=SimulatorScenario.COORDINATED_FRAUD,
        title="Coordinated ring",
        behaviour=(
            "Three unrelated customers sharing one device and one proxy IP, transacting "
            "within minutes of each other from a foreign city, with escalating amounts "
            "and interleaved failures. This recreates the behavioural shape of the "
            "seeded C1 scenario."
        ),
        expected_signal=(
            "Entity sharing that no single transaction reveals - the device and IP each "
            "serve three distinct customers, which is what the Phase 5 tools measure and "
            "what drives COORDINATED_ACTIVITY."
        ),
    ),
    SimulatorScenario.MODEL_DISAGREEMENT: ScenarioDoc(
        scenario=SimulatorScenario.MODEL_DISAGREEMENT,
        title="Models disagree",
        behaviour=(
            "Moderate amounts a supervised model has little reason to flag, but arriving "
            "in a tight burst on a shared, hours-old device from a foreign proxy - "
            "behaviour that is unusual without being expensive."
        ),
        expected_signal=(
            "A case where the two engines can reach different conclusions. Whether they "
            "actually do is measured from the result, not assumed here - and when they "
            "agree, that is a real observation about the models."
        ),
    ),
}


@dataclass
class _Ring:
    """Shared entities for the coordinated scenarios, created once per run."""

    device_id: str
    ip_address: str
    customers: tuple[str, ...]


class ScenarioGenerator:
    """Turns a scenario into a deterministic stream of transaction events."""

    def __init__(
        self,
        scenario: SimulatorScenario,
        *,
        seed: int,
        merchant_id: str,
        run_id: str,
        start_time: datetime | None = None,
    ) -> None:
        self.scenario = scenario
        self.merchant_id = merchant_id
        self.run_id = run_id
        self._rng = random.Random(seed)
        self._counter = 0
        self._start = start_time or datetime.now(UTC)
        # One ring per run, so its device and IP accumulate shared history as
        # the run proceeds - which is the signal the ring scenarios exist to
        # create. A fresh ring per transaction would share nothing.
        self._ring = _Ring(
            device_id=f"{SIMULATED_PREFIX}dev_ring_{run_id}",
            ip_address=self._simulated_ip(200),
            customers=tuple(f"{SIMULATED_PREFIX}CUS_RING_{run_id}_{index}" for index in range(3)),
        )
        self._customers = tuple(f"{SIMULATED_PREFIX}CUS_{run_id}_{index}" for index in range(6))
        self._failures = 0
        self._elapsed = 0

    # -- helpers ----------------------------------------------------------
    def _simulated_ip(self, offset: int) -> str:
        """An address inside 198.18.0.0/15 - the RFC 2544 benchmarking range.

        Reserved for testing and never routable, so a simulated IP can never
        collide with a real one or be mistaken for a real actor's address.
        """
        return f"198.18.{offset % 256}.{self._rng.randint(1, 254)}"

    def _reference(self) -> str:
        self._counter += 1
        return f"{SIMULATED_PREFIX}{self.run_id}_{self._counter:06d}"

    def _stamp(self) -> datetime:
        """Timestamps advance monotonically, a few seconds apart.

        Accumulated, not computed from the counter. ``counter * random(3, 12)``
        looks equivalent and is not: a small multiplier on a later counter can
        land *before* a large multiplier on an earlier one. The point-in-time
        feature layer orders strictly by ``(timestamp, id)``, so that would
        silently change what each transaction counts as its own history.
        """
        self._elapsed += self._rng.randint(3, 12)
        return self._start + timedelta(seconds=self._elapsed)

    def _amount(self, low: int, high: int) -> Decimal:
        return Decimal(str(round(self._rng.uniform(low, high), 2)))

    # -- generators -------------------------------------------------------
    def _normal(self) -> TransactionEvent:
        customer = self._rng.choice(self._customers)
        city, country = self._rng.choice(_HOME_CITIES)
        index = self._customers.index(customer)
        return TransactionEvent(
            transaction_id=self._reference(),
            amount=self._amount(300, 6_000),
            currency="INR",
            customer_id=customer,
            merchant_id=self.merchant_id,
            payment_method=self._rng.choice((PaymentMethod.UPI, PaymentMethod.CARD)),
            country=country,
            city=city,
            timestamp=self._stamp(),
            # A stable per-customer device: the customer's own phone, seen
            # again and again, which is what "known behaviour" looks like.
            device_id=f"{SIMULATED_PREFIX}dev_{self.run_id}_{index}",
            device_type=DeviceType.ANDROID,
            ip_address=f"198.18.{index}.10",
            ip_country=country,
            status=TransactionStatus.SUCCESSFUL,
        )

    def _suspicious(self) -> TransactionEvent:
        customer = self._rng.choice(self._customers)
        city, country = self._rng.choice(_FOREIGN_CITIES)
        failed = self._rng.random() < 0.3
        event = TransactionEvent(
            transaction_id=self._reference(),
            amount=self._amount(20_000, 90_000),
            currency="INR",
            customer_id=customer,
            merchant_id=self.merchant_id,
            payment_method=PaymentMethod.CARD,
            country=country,
            city=city,
            timestamp=self._stamp(),
            # New every time: a device and IP with no history at all.
            device_id=f"{SIMULATED_PREFIX}dev_new_{self.run_id}_{self._counter}",
            device_type=DeviceType.WEB_DESKTOP,
            ip_address=self._simulated_ip(100 + self._counter),
            ip_country=country,
            status=TransactionStatus.FAILED if failed else TransactionStatus.PENDING,
            failed_attempts=self._failures,
        )
        self._failures = self._failures + 1 if failed else 0
        return event

    def _high_fraud(self) -> TransactionEvent:
        # One customer hammering: velocity is part of the evidence.
        customer = self._customers[0]
        city, country = _FOREIGN_CITIES[0]
        failed = self._counter % 3 == 1
        event = TransactionEvent(
            transaction_id=self._reference(),
            amount=self._amount(80_000, 150_000),
            currency="INR",
            customer_id=customer,
            merchant_id=self.merchant_id,
            payment_method=PaymentMethod.CARD,
            country=country,
            city=city,
            timestamp=self._stamp(),
            device_id=f"{SIMULATED_PREFIX}dev_burn_{self.run_id}_{self._counter}",
            device_type=DeviceType.WEB_DESKTOP,
            ip_address=self._simulated_ip(150 + self._counter),
            ip_country=country,
            ip_is_proxy=True,
            status=TransactionStatus.FAILED if failed else TransactionStatus.PENDING,
            failed_attempts=self._failures,
        )
        self._failures = self._failures + 1 if failed else 0
        return event

    def _coordinated(self) -> TransactionEvent:
        """Three customers, one device, one proxy IP - the C1 shape."""
        member = self._counter % len(self._ring.customers)
        city, country = _FOREIGN_CITIES[0]
        failed = self._counter % 4 == 2
        event = TransactionEvent(
            transaction_id=self._reference(),
            amount=Decimal(str(9_900 + member * 2_400 + self._counter * 1_100)),
            currency="INR",
            customer_id=self._ring.customers[member],
            merchant_id=self.merchant_id,
            payment_method=PaymentMethod.CARD,
            country=country,
            city=city,
            timestamp=self._stamp(),
            device_id=self._ring.device_id,
            device_type=DeviceType.WEB_DESKTOP,
            ip_address=self._ring.ip_address,
            ip_country=country,
            ip_is_proxy=True,
            status=TransactionStatus.FAILED if failed else TransactionStatus.PENDING,
            failed_attempts=self._failures,
        )
        self._failures = self._failures + 1 if failed else 0
        return event

    def _disagreement(self) -> TransactionEvent:
        """Unremarkable money, remarkable circumstances."""
        member = self._counter % len(self._ring.customers)
        city, country = _FOREIGN_CITIES[0]
        return TransactionEvent(
            transaction_id=self._reference(),
            amount=self._amount(2_000, 9_000),
            currency="INR",
            customer_id=self._ring.customers[member],
            merchant_id=self.merchant_id,
            payment_method=PaymentMethod.CARD,
            country=country,
            city=city,
            timestamp=self._stamp(),
            device_id=self._ring.device_id,
            device_type=DeviceType.WEB_DESKTOP,
            ip_address=self._ring.ip_address,
            ip_country=country,
            ip_is_proxy=True,
            status=TransactionStatus.PENDING,
        )

    def next_event(self) -> TransactionEvent:
        builders = {
            SimulatorScenario.NORMAL: self._normal,
            SimulatorScenario.SUSPICIOUS: self._suspicious,
            SimulatorScenario.HIGH_FRAUD: self._high_fraud,
            SimulatorScenario.COORDINATED_FRAUD: self._coordinated,
            SimulatorScenario.MODEL_DISAGREEMENT: self._disagreement,
        }
        return builders[self.scenario]()

    def stream(self, count: int) -> Iterator[TransactionEvent]:
        for _ in range(count):
            yield self.next_event()


__all__ = ["SCENARIO_DOCS", "ScenarioDoc", "ScenarioGenerator"]
