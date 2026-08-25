"""The inputs a policy decision is allowed to see.

This module defines the boundary between the AI layers and the deterministic
one. What is *absent* here matters as much as what is present.

**No language-model output reaches a rule.** The investigation contributes only
quantities the application itself computed or counted:

* ``confidence`` - computed by Phase 5 from measured breadth, corroboration,
  completeness and signal agreement, never from the model's own claim;
* counts of findings and evidence by severity - derived from evidence that tools
  produced, after ungrounded citations were dropped.

Deliberately excluded, and structurally unreachable because no field carries
them: the investigation's ``summary`` (model prose), its ``risk_level`` (a model
judgement), and above all its ``recommended_action``. The agent recommends; the
policy decides; the two must not be the same opinion wearing different hats. A
rule cannot read the agent's recommendation because there is nowhere for it to
be read from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SupervisedSignal:
    """The Phase 3 fraud model's output for this transaction."""

    available: bool = False
    fraud_probability: float | None = None
    risk_score: int | None = None
    model_version: str | None = None

    @property
    def probability(self) -> float:
        """Probability, or 0.0 when unavailable.

        Callers must check ``available`` first: a missing signal is an unknown,
        and treating this 0.0 as "clean" would be exactly the silent approval
        the fail-safe rules exist to prevent.
        """
        return self.fraud_probability if self.fraud_probability is not None else 0.0


@dataclass(frozen=True)
class AnomalySignal:
    """The Phase 4 behavioural anomaly engine's output for this transaction."""

    available: bool = False
    anomaly_score: int | None = None
    severity: str | None = None
    model_version: str | None = None

    @property
    def score(self) -> float:
        """Score, or 0.0 when unavailable. Same caveat as ``probability``."""
        return float(self.anomaly_score) if self.anomaly_score is not None else 0.0


@dataclass(frozen=True)
class InvestigationSignal:
    """Structured counts from the Phase 5 investigation. No model prose."""

    available: bool = False
    #: completed / insufficient_evidence / agent_unavailable / failed
    status: str | None = None
    #: Application-computed, not the model's self-assessment.
    confidence: float | None = None
    investigation_id: str | None = None
    high_severity_findings: int = 0
    #: Distinct tools that produced HIGH-or-worse evidence.
    independent_high_severity_sources: int = 0
    independent_evidence_sources: int = 0
    evidence_severity_counts: dict[str, int] = field(default_factory=dict)
    #: True when a device or IP was observed serving several customers.
    shared_entity_observed: bool = False

    @property
    def usable(self) -> bool:
        """Whether this investigation can support a conclusion.

        An investigation that ran but could not gather evidence, or one whose
        model failed partway, is present but not usable as corroboration.
        """
        return self.available and self.status == "completed"

    @property
    def confidence_value(self) -> float:
        return self.confidence if self.confidence is not None else 0.0


@dataclass(frozen=True)
class TransactionFacts:
    """Stored facts about the payment. Recorded values only, no judgement."""

    transaction_id: str
    amount: float = 0.0
    currency: str = "INR"
    country: str = ""
    status: str = ""
    is_cross_border: bool = False


@dataclass(frozen=True)
class RiskContext:
    """Everything a policy rule may consider."""

    transaction: TransactionFacts
    supervised: SupervisedSignal
    anomaly: AnomalySignal
    investigation: InvestigationSignal

    def risk_summary(self) -> dict[str, Any]:
        """The values behind the decision, for the record and the explanation."""
        return {
            "fraud_probability": self.supervised.fraud_probability,
            "risk_score": self.supervised.risk_score,
            "fraud_model_version": self.supervised.model_version,
            "supervised_available": self.supervised.available,
            "anomaly_score": self.anomaly.anomaly_score,
            "anomaly_severity": self.anomaly.severity,
            "anomaly_model_version": self.anomaly.model_version,
            "anomaly_available": self.anomaly.available,
            "investigation_id": self.investigation.investigation_id,
            "investigation_status": self.investigation.status,
            "investigation_confidence": self.investigation.confidence,
            "high_severity_findings": self.investigation.high_severity_findings,
            "independent_high_severity_sources": (
                self.investigation.independent_high_severity_sources
            ),
            "independent_evidence_sources": self.investigation.independent_evidence_sources,
            "shared_entity_observed": self.investigation.shared_entity_observed,
            "amount": self.transaction.amount,
            "currency": self.transaction.currency,
            "country": self.transaction.country,
        }
