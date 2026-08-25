"""Policy configuration and its validation.

A policy that loads but is subtly wrong is worse than one that refuses to load:
it makes decisions nobody intended and stamps them with a version number that
looks legitimate. So validation is strict and total - every threshold is range
checked, every ordering constraint is asserted, every action name is resolved
against the enum, and a rule id that is not implemented is an error rather than
a silently ignored line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from policy.actions import Action

#: Rule ids the engine implements. A policy naming anything else is rejected -
#: configuration selects among known rules, it cannot invent behaviour.
KNOWN_RULE_IDS: frozenset[str] = frozenset(
    {
        "CRITICAL_SUPERVISED_RISK",
        "HIGH_ANOMALY_WITH_CORROBORATION",
        "MODEL_DISAGREEMENT_HIGH_ANOMALY",
        "HIGH_SUPERVISED_RISK",
        "MODERATE_COMBINED_RISK",
        "ELEVATED_ANOMALY_ONLY",
        "MISSING_SUPERVISED_SIGNAL",
        "MISSING_ANOMALY_SIGNAL",
        "MISSING_INVESTIGATION",
        "LOW_RISK",
    }
)


class PolicyValidationError(ValueError):
    """The policy configuration is unusable.

    Carries every problem found, not just the first, so a broken policy can be
    fixed in one pass.
    """

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        detail = "\n".join(f"  - {problem}" for problem in problems)
        super().__init__(f"policy configuration is invalid:\n{detail}")


@dataclass(frozen=True)
class Thresholds:
    """Numeric decision boundaries. All derived from measured operating points."""

    fraud_block: float
    fraud_high: float
    fraud_medium: float
    anomaly_critical: float
    anomaly_high: float
    anomaly_medium: float

    def problems(self) -> list[str]:
        found: list[str] = []

        for name in ("fraud_block", "fraud_high", "fraud_medium"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                found.append(f"{name}={value} is outside the probability range [0, 1]")

        for name in ("anomaly_critical", "anomaly_high", "anomaly_medium"):
            value = getattr(self, name)
            if not 0.0 <= value <= 100.0:
                found.append(f"{name}={value} is outside the anomaly range [0, 100]")

        # Ordering matters more than the individual values: an inverted pair
        # would make a rule unreachable or fire on the wrong side.
        if not self.fraud_medium <= self.fraud_high <= self.fraud_block:
            found.append(
                f"fraud thresholds must ascend medium <= high <= block, got "
                f"{self.fraud_medium} / {self.fraud_high} / {self.fraud_block}"
            )
        if not self.anomaly_medium <= self.anomaly_high <= self.anomaly_critical:
            found.append(
                f"anomaly thresholds must ascend medium <= high <= critical, got "
                f"{self.anomaly_medium} / {self.anomaly_high} / {self.anomaly_critical}"
            )
        return found


@dataclass(frozen=True)
class EvidenceRequirements:
    """How much corroboration each outcome demands."""

    min_independent_sources_for_block: int
    min_high_findings_for_review: int
    min_investigation_confidence: float

    def problems(self) -> list[str]:
        found: list[str] = []
        if self.min_independent_sources_for_block < 1:
            found.append(
                "min_independent_sources_for_block must be at least 1; a block may "
                "never rest on zero corroborating sources"
            )
        if self.min_high_findings_for_review < 1:
            found.append("min_high_findings_for_review must be at least 1")
        if not 0.0 <= self.min_investigation_confidence <= 1.0:
            found.append(
                f"min_investigation_confidence={self.min_investigation_confidence} is "
                "outside [0, 1]"
            )
        return found


@dataclass(frozen=True)
class FailSafe:
    """What happens when a needed signal is missing."""

    missing_supervised_signal: Action
    missing_anomaly_signal: Action
    missing_investigation: Action
    require_investigation_for_block: bool

    def problems(self) -> list[str]:
        found: list[str] = []
        # The whole point of a fail-safe is that an unknown is not a clean bill.
        for name in (
            "missing_supervised_signal",
            "missing_anomaly_signal",
            "missing_investigation",
        ):
            if getattr(self, name) is Action.APPROVE:
                found.append(
                    f"{name}=APPROVE would silently approve on a missing signal; "
                    "choose STEP_UP, REVIEW or BLOCK"
                )
        return found


@dataclass(frozen=True)
class ActionPolicy:
    """Precedence, the default outcome, and which actions need a human."""

    precedence: tuple[Action, ...]
    default: Action
    human_review_required_for: frozenset[Action]

    def problems(self) -> list[str]:
        found: list[str] = []
        if set(self.precedence) != set(Action):
            missing = sorted(str(a) for a in set(Action) - set(self.precedence))
            found.append(f"precedence must list every action; missing {missing}")
        if len(self.precedence) != len(set(self.precedence)):
            found.append("precedence contains a duplicate action")
        if self.precedence and self.precedence[0] is not Action.BLOCK:
            found.append("BLOCK must be the most restrictive action in the precedence order")
        return found


@dataclass(frozen=True)
class PolicyConfig:
    """A complete, validated policy."""

    policy_version: str
    description: str
    thresholds: Thresholds
    evidence: EvidenceRequirements
    fail_safe: FailSafe
    actions: ActionPolicy
    enabled_rules: frozenset[str]
    source: str = "<memory>"

    def is_enabled(self, rule_id: str) -> bool:
        return rule_id in self.enabled_rules

    def requires_human_review(self, action: Action) -> bool:
        return action in self.actions.human_review_required_for

    def validate(self) -> None:
        """Raise :class:`PolicyValidationError` listing every problem found."""
        problems: list[str] = []

        if not self.policy_version.strip():
            problems.append("policy_version must not be empty")

        problems += self.thresholds.problems()
        problems += self.evidence.problems()
        problems += self.fail_safe.problems()
        problems += self.actions.problems()

        unknown = sorted(self.enabled_rules - KNOWN_RULE_IDS)
        if unknown:
            problems.append(f"unknown rule id(s): {unknown}")

        if not self.enabled_rules:
            problems.append("at least one rule must be enabled")

        if problems:
            raise PolicyValidationError(problems)

    def as_dict(self) -> dict[str, Any]:
        """Serialisable snapshot, stamped into the decision record."""
        return {
            "policy_version": self.policy_version,
            "thresholds": {
                "fraud_block": self.thresholds.fraud_block,
                "fraud_high": self.thresholds.fraud_high,
                "fraud_medium": self.thresholds.fraud_medium,
                "anomaly_critical": self.thresholds.anomaly_critical,
                "anomaly_high": self.thresholds.anomaly_high,
                "anomaly_medium": self.thresholds.anomaly_medium,
            },
            "evidence": {
                "min_independent_sources_for_block": (
                    self.evidence.min_independent_sources_for_block
                ),
                "min_high_findings_for_review": self.evidence.min_high_findings_for_review,
                "min_investigation_confidence": self.evidence.min_investigation_confidence,
            },
            "enabled_rules": sorted(self.enabled_rules),
        }


@dataclass(frozen=True)
class RuleMatch:
    """One rule firing, with the values that made it fire."""

    rule_id: str
    action: Action
    reason_codes: tuple[str, ...]
    #: Human-readable conditions, each carrying the actual measured value.
    conditions: tuple[str, ...] = field(default_factory=tuple)
