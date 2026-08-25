"""Enumerations shared by the ORM models and the API schemas.

Stored as VARCHAR (``native_enum=False``) rather than a PostgreSQL ENUM type:
the values stay readable in raw SQL, migrations do not need bespoke type
create/drop steps, and the same DDL works on SQLite for tests. No CHECK
constraint is emitted - SQLAlchemy's ``create_constraint`` has defaulted to
False since 1.4 - so values are validated in Python at bind time, and widening
an enum needs no DDL.
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    """Who an account belongs to.

    ``VIEWER`` was added in Phase 10 alongside authentication. ``RISK_ANALYST``
    is the analyst role - it predates the console and was not renamed, because
    renaming it would rewrite the value stored on every seeded row for no gain.
    ``MERCHANT`` accounts are subjects of the platform rather than operators of
    it and hold no console permissions at all; see ``app.core.permissions``.
    """

    MERCHANT = "merchant"
    VIEWER = "viewer"
    RISK_ANALYST = "risk_analyst"
    ADMIN = "admin"


class RiskLevel(StrEnum):
    """Historical, observed risk band for a customer - not a predicted score."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DeviceType(StrEnum):
    ANDROID = "android"
    IOS = "ios"
    WEB_DESKTOP = "web_desktop"
    WEB_MOBILE = "web_mobile"


class PaymentMethod(StrEnum):
    CARD = "card"
    UPI = "upi"
    NETBANKING = "netbanking"
    WALLET = "wallet"
    EMI = "emi"


class TransactionStatus(StrEnum):
    SUCCESSFUL = "successful"
    FAILED = "failed"
    PENDING = "pending"
    REVERSED = "reversed"


class SignalSeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class InvestigationStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    #: The agent ran but could not gather enough independent evidence.
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    #: The language model was unreachable or returned unusable output.
    AGENT_UNAVAILABLE = "agent_unavailable"
    FAILED = "failed"


class RecommendedAction(StrEnum):
    ALLOW = "allow"
    REVIEW = "review"
    STEP_UP = "step_up"
    BLOCK = "block"


class ReviewCaseStatus(StrEnum):
    OPEN = "open"
    IN_REVIEW = "in_review"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class AnalystDecisionType(StrEnum):
    APPROVE = "approve"
    BLOCK = "block"
    STEP_UP = "step_up"
    FALSE_POSITIVE = "false_positive"
    CONFIRMED_FRAUD = "confirmed_fraud"
    #: The analyst sent the case onward rather than settling it.
    ESCALATED = "escalated"


class DecisionAction(StrEnum):
    """What the deterministic policy engine decided.

    Separate from :class:`RecommendedAction`, which is what the *agent*
    suggested, and from :class:`RuleAction`. Keeping the vocabularies apart is
    the point: a recommendation and a decision must never be stored in a way
    that lets one be mistaken for the other.
    """

    APPROVE = "approve"
    STEP_UP = "step_up"
    REVIEW = "review"
    BLOCK = "block"


class ReviewResolution(StrEnum):
    """How an analyst settled a review case.

    These describe the fate of the *transaction*, not a verdict on the machine:
    ``APPROVED`` lets the payment proceed, ``REJECTED`` stops it, ``ESCALATED``
    passes it to a senior reviewer. The original machine decision is untouched
    either way.
    """

    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class RuleAction(StrEnum):
    ALLOW = "allow"
    FLAG = "flag"
    REVIEW = "review"
    STEP_UP = "step_up"
    BLOCK = "block"


class ActorType(StrEnum):
    SYSTEM = "system"
    AGENT = "agent"
    ANALYST = "analyst"
    MERCHANT = "merchant"


class ActualOutcome(StrEnum):
    LEGITIMATE = "legitimate"
    FRAUD = "fraud"
    UNKNOWN = "unknown"


class FeedbackOutcome(StrEnum):
    """What an analyst concluded about a transaction after reviewing it.

    Distinct from :class:`ReviewResolution`, which records what was *done* with
    the payment. A case can be REJECTED (payment stopped) while the outcome is
    INSUFFICIENT_EVIDENCE - the analyst acted cautiously without confirming
    fraud. Conflating the two would turn every cautious block into a fraud
    label and corrupt every metric computed downstream.
    """

    CONFIRMED_FRAUD = "confirmed_fraud"
    LEGITIMATE = "legitimate"
    FALSE_POSITIVE = "false_positive"
    FALSE_NEGATIVE = "false_negative"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    ESCALATED = "escalated"

    @property
    def is_ground_truth(self) -> bool:
        """Whether this outcome asserts what actually happened.

        Only four of the six do. ``INSUFFICIENT_EVIDENCE`` and ``ESCALATED``
        record that the question is still open, and counting them as either
        class would fabricate labels the analyst declined to give.
        """
        return self in _GROUND_TRUTH_OUTCOMES

    @property
    def indicates_fraud(self) -> bool:
        """Whether this outcome means the transaction really was fraudulent."""
        return self in (FeedbackOutcome.CONFIRMED_FRAUD, FeedbackOutcome.FALSE_NEGATIVE)


_GROUND_TRUTH_OUTCOMES = frozenset(
    {
        FeedbackOutcome.CONFIRMED_FRAUD,
        FeedbackOutcome.LEGITIMATE,
        FeedbackOutcome.FALSE_POSITIVE,
        FeedbackOutcome.FALSE_NEGATIVE,
    }
)


class FeedbackReason(StrEnum):
    """Why the analyst reached that outcome.

    A closed vocabulary. Free-text reasons cannot be aggregated, so they cannot
    drive the monitoring this phase exists to provide; the analyst's prose goes
    in ``notes`` instead, where nothing counts it.
    """

    # --- fraud ---------------------------------------------------------------
    CONFIRMED_FRAUD = "confirmed_fraud"
    ACCOUNT_TAKEOVER = "account_takeover"
    COORDINATED_ACTIVITY = "coordinated_activity"
    STOLEN_PAYMENT_METHOD = "stolen_payment_method"
    SUSPICIOUS_DEVICE = "suspicious_device"
    SUSPICIOUS_IP = "suspicious_ip"

    # --- legitimate ----------------------------------------------------------
    LEGITIMATE_TRANSACTION = "legitimate_transaction"
    KNOWN_CUSTOMER_BEHAVIOR = "known_customer_behavior"
    TRUSTED_MERCHANT = "trusted_merchant"
    EXPECTED_LOCATION = "expected_location"
    EXPECTED_DEVICE = "expected_device"

    # --- model ---------------------------------------------------------------
    MODEL_FALSE_POSITIVE = "model_false_positive"
    MODEL_FALSE_NEGATIVE = "model_false_negative"

    # --- evidence ------------------------------------------------------------
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NEEDS_MORE_INFORMATION = "needs_more_information"


#: Which reasons belong with which outcome. Enforced on write so the pair
#: "LEGITIMATE because COORDINATED_ACTIVITY" can never enter the data and skew
#: a reason-code breakdown that an operator will later read as fact.
FEEDBACK_REASONS_BY_OUTCOME: dict[FeedbackOutcome, frozenset[FeedbackReason]] = {
    FeedbackOutcome.CONFIRMED_FRAUD: frozenset(
        {
            FeedbackReason.CONFIRMED_FRAUD,
            FeedbackReason.ACCOUNT_TAKEOVER,
            FeedbackReason.COORDINATED_ACTIVITY,
            FeedbackReason.STOLEN_PAYMENT_METHOD,
            FeedbackReason.SUSPICIOUS_DEVICE,
            FeedbackReason.SUSPICIOUS_IP,
        }
    ),
    FeedbackOutcome.FALSE_NEGATIVE: frozenset(
        {
            FeedbackReason.CONFIRMED_FRAUD,
            FeedbackReason.ACCOUNT_TAKEOVER,
            FeedbackReason.COORDINATED_ACTIVITY,
            FeedbackReason.STOLEN_PAYMENT_METHOD,
            FeedbackReason.SUSPICIOUS_DEVICE,
            FeedbackReason.SUSPICIOUS_IP,
            FeedbackReason.MODEL_FALSE_NEGATIVE,
        }
    ),
    FeedbackOutcome.LEGITIMATE: frozenset(
        {
            FeedbackReason.LEGITIMATE_TRANSACTION,
            FeedbackReason.KNOWN_CUSTOMER_BEHAVIOR,
            FeedbackReason.TRUSTED_MERCHANT,
            FeedbackReason.EXPECTED_LOCATION,
            FeedbackReason.EXPECTED_DEVICE,
        }
    ),
    FeedbackOutcome.FALSE_POSITIVE: frozenset(
        {
            FeedbackReason.LEGITIMATE_TRANSACTION,
            FeedbackReason.KNOWN_CUSTOMER_BEHAVIOR,
            FeedbackReason.TRUSTED_MERCHANT,
            FeedbackReason.EXPECTED_LOCATION,
            FeedbackReason.EXPECTED_DEVICE,
            FeedbackReason.MODEL_FALSE_POSITIVE,
        }
    ),
    FeedbackOutcome.INSUFFICIENT_EVIDENCE: frozenset(
        {
            FeedbackReason.INSUFFICIENT_EVIDENCE,
            FeedbackReason.NEEDS_MORE_INFORMATION,
        }
    ),
    FeedbackOutcome.ESCALATED: frozenset(
        {
            FeedbackReason.INSUFFICIENT_EVIDENCE,
            FeedbackReason.NEEDS_MORE_INFORMATION,
            FeedbackReason.COORDINATED_ACTIVITY,
            FeedbackReason.ACCOUNT_TAKEOVER,
        }
    ),
}


class DriftStatus(StrEnum):
    """How far a monitored distribution has moved from its baseline.

    Drift means the distribution changed. It does not mean fraud, and nothing
    in this system treats it as such.
    """

    NORMAL = "NORMAL"
    WATCH = "WATCH"
    DRIFT_DETECTED = "DRIFT_DETECTED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class RiskEventType(StrEnum):
    """Stages a transaction passes through, in the order they occur.

    The order of declaration is the order of the pipeline, and a test asserts
    that the sequence a live transaction actually produces is a subsequence of
    this list. That makes the enum the specification of the ordering rather
    than a comment about it.
    """

    TRANSACTION_RECEIVED = "transaction_received"
    RISK_SCORED = "risk_scored"
    ANOMALY_DETECTED = "anomaly_detected"
    INVESTIGATION_STARTED = "investigation_started"
    INVESTIGATION_COMPLETED = "investigation_completed"
    DECISION_CREATED = "decision_created"
    #: Terminal, and deliberately not part of the happy path. A transaction that
    #: fails mid-pipeline emits this instead of vanishing from the stream.
    PROCESSING_FAILED = "processing_failed"


#: The happy path, in order. `PROCESSING_FAILED` is excluded: it can replace any
#: later stage, so it has no fixed position.
RISK_EVENT_ORDER: tuple[RiskEventType, ...] = (
    RiskEventType.TRANSACTION_RECEIVED,
    RiskEventType.RISK_SCORED,
    RiskEventType.ANOMALY_DETECTED,
    RiskEventType.INVESTIGATION_STARTED,
    RiskEventType.INVESTIGATION_COMPLETED,
    RiskEventType.DECISION_CREATED,
)


class SimulatorState(StrEnum):
    """Lifecycle of the transaction simulator."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"


class SimulatorScenario(StrEnum):
    """Behavioural profiles the simulator can generate.

    A scenario controls the *characteristics* of the transactions produced -
    amounts, devices, IPs, velocity, location. It never sets a fraud
    probability, an anomaly score or a decision: those are computed by the
    Phase 3, 4 and 6 services from the behaviour, exactly as they are for the
    seeded dataset.
    """

    NORMAL = "normal"
    SUSPICIOUS = "suspicious"
    HIGH_FRAUD = "high_fraud"
    COORDINATED_FRAUD = "coordinated_fraud"
    MODEL_DISAGREEMENT = "model_disagreement"
