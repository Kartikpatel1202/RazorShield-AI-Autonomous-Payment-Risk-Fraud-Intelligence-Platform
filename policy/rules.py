"""The decision rules.

Each rule is a typed predicate over a :class:`RiskContext`. It either returns a
:class:`RuleMatch` - an action, the reason codes, and the measured values that
made it fire - or ``None``. Rules never mutate anything, never read the
database, and never see model prose.

Two properties are deliberate:

* **Rules are code, thresholds are configuration.** A rule's *logic* lives here
  where it is typed, reviewable and testable; its *numbers* come from the policy
  file. Configuration therefore cannot invent behaviour, only tune and enable it.
* **Rules do not know about precedence.** Every applicable rule is evaluated and
  every match is recorded, even when a more restrictive one also matched, so the
  audit record shows the full picture rather than only the winner. The engine
  resolves the outcome afterwards.
"""

from __future__ import annotations

from collections.abc import Callable

from policy.actions import Action
from policy.context import RiskContext
from policy.reasons import ReasonCode
from policy.schema import PolicyConfig, RuleMatch

Rule = Callable[[RiskContext, PolicyConfig], RuleMatch | None]


def _num(value: float) -> str:
    """Format a measured value for the audit trail without trailing zeros."""
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _corroborated(context: RiskContext, policy: PolicyConfig) -> bool:
    """Whether the investigation independently supports a serious action.

    Two distinct tools must each have produced HIGH-or-worse evidence, and the
    application-computed confidence must clear the floor. One tool reporting a
    concern twice is one observation, not two.
    """
    investigation = context.investigation
    return (
        investigation.usable
        and investigation.independent_high_severity_sources
        >= policy.evidence.min_independent_sources_for_block
        and investigation.confidence_value >= policy.evidence.min_investigation_confidence
    )


def _evidence_reasons(context: RiskContext) -> tuple[ReasonCode, ...]:
    """Reason codes describing what the investigation contributed."""
    codes: list[ReasonCode] = [ReasonCode.INDEPENDENT_CORROBORATION]
    if context.investigation.shared_entity_observed:
        codes.append(ReasonCode.COORDINATED_ACTIVITY)
    return tuple(codes)


# --------------------------------------------------------------------------
# Rule A - very high supervised risk. The only path to a block.
# --------------------------------------------------------------------------
def critical_supervised_risk(context: RiskContext, policy: PolicyConfig) -> RuleMatch | None:
    """BLOCK on very high fraud probability *with* independent corroboration.

    A block is the one action a customer cannot undo, so it needs two things at
    once: a probability in the band where the model was measured error-free on
    held-out data, and an investigation that independently found the same thing.
    Where either is missing the rule still fires, but downgrades itself to
    REVIEW and says so with ``BLOCK_WITHHELD_PENDING_INVESTIGATION`` - the
    concern is real, the certainty is not.
    """
    supervised = context.supervised
    if not supervised.available or supervised.probability < policy.thresholds.fraud_block:
        return None

    conditions = [
        f"fraud_probability {_num(supervised.probability)} >= "
        f"fraud_block {_num(policy.thresholds.fraud_block)}"
    ]
    codes: list[ReasonCode] = [ReasonCode.VERY_HIGH_FRAUD_PROBABILITY]

    if _corroborated(context, policy):
        codes.extend(_evidence_reasons(context))
        conditions.append(
            f"independent high-severity evidence sources "
            f"{context.investigation.independent_high_severity_sources} >= "
            f"{policy.evidence.min_independent_sources_for_block}"
        )
        conditions.append(
            f"investigation confidence {context.investigation.confidence_value:.4f} >= "
            f"{policy.evidence.min_investigation_confidence}"
        )
        return RuleMatch("CRITICAL_SUPERVISED_RISK", Action.BLOCK, tuple(codes), tuple(conditions))

    if not policy.fail_safe.require_investigation_for_block:
        return RuleMatch("CRITICAL_SUPERVISED_RISK", Action.BLOCK, tuple(codes), tuple(conditions))

    codes.append(ReasonCode.BLOCK_WITHHELD_PENDING_INVESTIGATION)
    if not context.investigation.usable:
        codes.append(ReasonCode.INVESTIGATION_UNAVAILABLE)
        conditions.append(
            f"no usable investigation (status={context.investigation.status or 'absent'}); "
            f"block withheld"
        )
    else:
        codes.append(ReasonCode.INSUFFICIENT_CORROBORATION)
        conditions.append(
            f"independent high-severity evidence sources "
            f"{context.investigation.independent_high_severity_sources} < "
            f"{policy.evidence.min_independent_sources_for_block}; block withheld"
        )
        if context.investigation.confidence_value < policy.evidence.min_investigation_confidence:
            codes.append(ReasonCode.LOW_INVESTIGATION_CONFIDENCE)
    return RuleMatch("CRITICAL_SUPERVISED_RISK", Action.REVIEW, tuple(codes), tuple(conditions))


# --------------------------------------------------------------------------
# Rule B - critical behavioural anomaly backed by findings.
# --------------------------------------------------------------------------
def high_anomaly_with_corroboration(context: RiskContext, policy: PolicyConfig) -> RuleMatch | None:
    """REVIEW when a critical anomaly is backed by several high-severity findings.

    The CRITICAL anomaly band carries a 57.5% measured fraud rate, but the
    unsupervised model has no notion of fraud - it reports unusual, and unusual
    is not the same as criminal. Requiring corroborating findings is what turns
    "strange" into "worth a person's time".
    """
    anomaly = context.anomaly
    investigation = context.investigation
    if not anomaly.available or anomaly.score < policy.thresholds.anomaly_critical:
        return None
    if not investigation.usable:
        return None
    if investigation.high_severity_findings < policy.evidence.min_high_findings_for_review:
        return None
    if investigation.confidence_value < policy.evidence.min_investigation_confidence:
        return None

    codes = [ReasonCode.CRITICAL_BEHAVIORAL_ANOMALY, ReasonCode.MULTIPLE_HIGH_SEVERITY_FINDINGS]
    if investigation.shared_entity_observed:
        codes.append(ReasonCode.COORDINATED_ACTIVITY)
    return RuleMatch(
        "HIGH_ANOMALY_WITH_CORROBORATION",
        Action.REVIEW,
        tuple(codes),
        (
            f"anomaly_score {_num(anomaly.score)} >= "
            f"anomaly_critical {_num(policy.thresholds.anomaly_critical)}",
            f"high-severity findings {investigation.high_severity_findings} >= "
            f"{policy.evidence.min_high_findings_for_review}",
            f"investigation confidence {investigation.confidence_value:.4f} >= "
            f"{policy.evidence.min_investigation_confidence}",
        ),
    )


# --------------------------------------------------------------------------
# Rule C - the two engines disagree. This is the Scenario C1 path.
# --------------------------------------------------------------------------
def model_disagreement_high_anomaly(context: RiskContext, policy: PolicyConfig) -> RuleMatch | None:
    """REVIEW when the anomaly engine is alarmed and the fraud model is not.

    Measured on the held-out test fold, the cell where fraud probability sits
    below the high threshold while the anomaly score is CRITICAL contains a
    71.4% fraud rate. Disagreement between the two engines is itself a signal,
    and it is exactly the case a human is better placed to settle than either
    model alone - so it routes to review rather than to an automated action.
    """
    supervised = context.supervised
    anomaly = context.anomaly
    if not supervised.available or not anomaly.available:
        return None
    if anomaly.score < policy.thresholds.anomaly_critical:
        return None
    if supervised.probability >= policy.thresholds.fraud_high:
        return None

    codes = [ReasonCode.MODEL_DISAGREEMENT, ReasonCode.CRITICAL_BEHAVIORAL_ANOMALY]
    conditions = [
        f"anomaly_score {_num(anomaly.score)} >= "
        f"anomaly_critical {_num(policy.thresholds.anomaly_critical)}",
        f"fraud_probability {_num(supervised.probability)} < "
        f"fraud_high {_num(policy.thresholds.fraud_high)}",
    ]
    # A shared device or IP is the signature of a coordinated ring, and this is
    # the rule that catches rings the supervised model rates as ordinary. It does
    # not change the action - the reviewer still decides - but omitting it would
    # drop a fact the investigation actually established from the audit record.
    if context.investigation.usable and context.investigation.shared_entity_observed:
        codes.append(ReasonCode.COORDINATED_ACTIVITY)
        conditions.append("a device or IP was observed serving several customers")

    return RuleMatch(
        "MODEL_DISAGREEMENT_HIGH_ANOMALY", Action.REVIEW, tuple(codes), tuple(conditions)
    )


# --------------------------------------------------------------------------
# Rule D - high supervised risk on its own.
# --------------------------------------------------------------------------
def high_supervised_risk(context: RiskContext, policy: PolicyConfig) -> RuleMatch | None:
    """REVIEW above the fraud model's selected operating point.

    Phase 3 chose this threshold on the validation fold by maximising F2; test
    performance there was precision 0.957, recall 0.733. A ~4% false-positive
    rate is acceptable for routing to a person and unacceptable for blocking.
    """
    supervised = context.supervised
    if not supervised.available:
        return None
    if supervised.probability < policy.thresholds.fraud_high:
        return None
    if supervised.probability >= policy.thresholds.fraud_block:
        # Rule A owns that band and states the stronger reason.
        return None

    codes = [ReasonCode.HIGH_FRAUD_PROBABILITY]
    conditions = [
        f"fraud_probability {_num(supervised.probability)} >= "
        f"fraud_high {_num(policy.thresholds.fraud_high)}"
    ]
    if context.anomaly.available and context.anomaly.score >= policy.thresholds.anomaly_high:
        codes.append(ReasonCode.MODEL_AGREEMENT)
        conditions.append(
            f"anomaly_score {_num(context.anomaly.score)} >= "
            f"anomaly_high {_num(policy.thresholds.anomaly_high)}"
        )
    return RuleMatch("HIGH_SUPERVISED_RISK", Action.REVIEW, tuple(codes), tuple(conditions))


# --------------------------------------------------------------------------
# Rule E - moderate risk on both engines.
# --------------------------------------------------------------------------
def moderate_combined_risk(context: RiskContext, policy: PolicyConfig) -> RuleMatch | None:
    """STEP_UP when both engines are mildly elevated.

    Neither signal alone justifies friction; together they justify asking the
    customer to prove it is them, which is cheap and reversible.
    """
    supervised = context.supervised
    anomaly = context.anomaly
    if not supervised.available or not anomaly.available:
        return None
    if supervised.probability < policy.thresholds.fraud_medium:
        return None
    if supervised.probability >= policy.thresholds.fraud_high:
        return None
    if anomaly.score < policy.thresholds.anomaly_medium:
        return None

    return RuleMatch(
        "MODERATE_COMBINED_RISK",
        Action.STEP_UP,
        (ReasonCode.MODERATE_FRAUD_PROBABILITY, ReasonCode.ELEVATED_BEHAVIORAL_ANOMALY),
        (
            f"fraud_probability {_num(supervised.probability)} in "
            f"[{_num(policy.thresholds.fraud_medium)}, {_num(policy.thresholds.fraud_high)})",
            f"anomaly_score {_num(anomaly.score)} >= "
            f"anomaly_medium {_num(policy.thresholds.anomaly_medium)}",
        ),
    )


# --------------------------------------------------------------------------
# Rule F - anomaly alone.
# --------------------------------------------------------------------------
def elevated_anomaly_only(context: RiskContext, policy: PolicyConfig) -> RuleMatch | None:
    """STEP_UP on a high anomaly score the fraud model does not share.

    The HIGH anomaly band carries a 22.8% measured fraud rate against a 2.0%
    base rate - too high to wave through, too weak on its own to justify review
    queue capacity.
    """
    supervised = context.supervised
    anomaly = context.anomaly
    if not anomaly.available or anomaly.score < policy.thresholds.anomaly_high:
        return None
    if anomaly.score >= policy.thresholds.anomaly_critical:
        # Rules B and C own the critical band.
        return None
    if supervised.available and supervised.probability >= policy.thresholds.fraud_medium:
        # Rule E owns the case where both are elevated.
        return None

    return RuleMatch(
        "ELEVATED_ANOMALY_ONLY",
        Action.STEP_UP,
        (ReasonCode.HIGH_BEHAVIORAL_ANOMALY,),
        (
            f"anomaly_score {_num(anomaly.score)} >= "
            f"anomaly_high {_num(policy.thresholds.anomaly_high)}",
            f"fraud_probability below fraud_medium {_num(policy.thresholds.fraud_medium)}",
        ),
    )


# --------------------------------------------------------------------------
# Fail-safe rules. A missing signal is an unknown, never a clean bill.
# --------------------------------------------------------------------------
def missing_supervised_signal(context: RiskContext, policy: PolicyConfig) -> RuleMatch | None:
    """The fraud model produced nothing for this transaction."""
    if context.supervised.available:
        return None
    return RuleMatch(
        "MISSING_SUPERVISED_SIGNAL",
        policy.fail_safe.missing_supervised_signal,
        (ReasonCode.SUPERVISED_SIGNAL_UNAVAILABLE,),
        ("no supervised fraud prediction available for this transaction",),
    )


def missing_anomaly_signal(context: RiskContext, policy: PolicyConfig) -> RuleMatch | None:
    """The anomaly engine produced nothing for this transaction."""
    if context.anomaly.available:
        return None
    return RuleMatch(
        "MISSING_ANOMALY_SIGNAL",
        policy.fail_safe.missing_anomaly_signal,
        (ReasonCode.ANOMALY_SIGNAL_UNAVAILABLE,),
        ("no behavioural anomaly score available for this transaction",),
    )


def investigation_warranted(context: RiskContext, policy: PolicyConfig) -> bool:
    """Whether either model has raised a concern worth investigating.

    Public because two callers must agree on it. The ``MISSING_INVESTIGATION``
    rule uses it to decide whether an absent investigation is a gap; the live
    pipeline in ``app.services.ingest`` uses it to decide whether to run one
    before deciding. If those two ever disagreed, the pipeline would either
    skip an investigation the policy then penalises it for missing, or run one
    on traffic the policy never wanted investigated.
    """
    supervised = context.supervised
    anomaly = context.anomaly
    return (supervised.available and supervised.probability >= policy.thresholds.fraud_medium) or (
        anomaly.available and anomaly.score >= policy.thresholds.anomaly_medium
    )


def missing_investigation(context: RiskContext, policy: PolicyConfig) -> RuleMatch | None:
    """No usable investigation, on a transaction where one was warranted.

    Gated on the transaction already looking elevated. An investigation is
    evidence the policy *needs* only once a model has raised a concern; firing
    on every quiet payment would step up essentially all traffic, which is not a
    fail-safe but an outage. On the test fold 2,658 of 3,000 transactions sit
    below both medium thresholds, and none of them warrants friction for the
    absence of an investigation nobody had reason to run.
    """
    if context.investigation.usable:
        return None

    if not investigation_warranted(context, policy):
        return None

    status = context.investigation.status or "absent"
    return RuleMatch(
        "MISSING_INVESTIGATION",
        policy.fail_safe.missing_investigation,
        (ReasonCode.INVESTIGATION_UNAVAILABLE,),
        (f"risk signals elevated but investigation is not usable (status={status})",),
    )


# --------------------------------------------------------------------------
# The clean case.
# --------------------------------------------------------------------------
def low_risk(context: RiskContext, policy: PolicyConfig) -> RuleMatch | None:
    """APPROVE when both engines are present, both are quiet, and nothing found.

    Note the availability requirements: this rule cannot fire on a missing
    signal, so an approval is always a positive statement that both models
    looked and neither objected - never the absence of an opinion.
    """
    supervised = context.supervised
    anomaly = context.anomaly
    if not supervised.available or not anomaly.available:
        return None
    if supervised.probability >= policy.thresholds.fraud_medium:
        return None
    if anomaly.score >= policy.thresholds.anomaly_medium:
        return None
    if context.investigation.usable and context.investigation.high_severity_findings > 0:
        return None

    codes = [ReasonCode.LOW_FRAUD_PROBABILITY, ReasonCode.LOW_BEHAVIORAL_ANOMALY]
    if context.investigation.usable:
        codes.append(ReasonCode.NO_CONCERNING_EVIDENCE)
    return RuleMatch(
        "LOW_RISK",
        Action.APPROVE,
        tuple(codes),
        (
            f"fraud_probability {_num(supervised.probability)} < "
            f"fraud_medium {_num(policy.thresholds.fraud_medium)}",
            f"anomaly_score {_num(anomaly.score)} < "
            f"anomaly_medium {_num(policy.thresholds.anomaly_medium)}",
        ),
    )


#: Evaluation order. Fixed, so a decision is reproducible down to the order the
#: matched rules are listed in. It is *not* a priority order - the engine
#: resolves the outcome by the configured action precedence, not by position.
RULES: tuple[tuple[str, Rule], ...] = (
    ("CRITICAL_SUPERVISED_RISK", critical_supervised_risk),
    ("HIGH_ANOMALY_WITH_CORROBORATION", high_anomaly_with_corroboration),
    ("MODEL_DISAGREEMENT_HIGH_ANOMALY", model_disagreement_high_anomaly),
    ("HIGH_SUPERVISED_RISK", high_supervised_risk),
    ("MODERATE_COMBINED_RISK", moderate_combined_risk),
    ("ELEVATED_ANOMALY_ONLY", elevated_anomaly_only),
    ("MISSING_SUPERVISED_SIGNAL", missing_supervised_signal),
    ("MISSING_ANOMALY_SIGNAL", missing_anomaly_signal),
    ("MISSING_INVESTIGATION", missing_investigation),
    ("LOW_RISK", low_risk),
)

RULES_BY_ID: dict[str, Rule] = dict(RULES)


#: The action each rule primarily yields, for the read-only policy viewer.
#:
#: ``CRITICAL_SUPERVISED_RISK`` is annotated "BLOCK (or REVIEW)" because it
#: downgrades itself when corroboration is missing - stating a flat "BLOCK"
#: would misdescribe the rule to anyone reading the console.
#:
#: Descriptions are *not* duplicated here: they are read from each function's
#: docstring, so the viewer cannot drift from the code it describes.
RULE_PRIMARY_ACTION: dict[str, str] = {
    "CRITICAL_SUPERVISED_RISK": "BLOCK (or REVIEW without corroboration)",
    "HIGH_ANOMALY_WITH_CORROBORATION": "REVIEW",
    "MODEL_DISAGREEMENT_HIGH_ANOMALY": "REVIEW",
    "HIGH_SUPERVISED_RISK": "REVIEW",
    "MODERATE_COMBINED_RISK": "STEP_UP",
    "ELEVATED_ANOMALY_ONLY": "STEP_UP",
    "MISSING_SUPERVISED_SIGNAL": "configured fail-safe",
    "MISSING_ANOMALY_SIGNAL": "configured fail-safe",
    "MISSING_INVESTIGATION": "configured fail-safe",
    "LOW_RISK": "APPROVE",
}


def rule_description(rule_id: str) -> str:
    """The first line of a rule's docstring - its one-sentence summary.

    Docstrings use reStructuredText emphasis (``*with*``), which is right in
    source and wrong in a table cell, so the markers are stripped for display.
    """
    rule = RULES_BY_ID.get(rule_id)
    if rule is None or not rule.__doc__:
        return ""
    summary = rule.__doc__.strip().splitlines()[0].strip()
    return summary.replace("``", "").replace("*", "")
