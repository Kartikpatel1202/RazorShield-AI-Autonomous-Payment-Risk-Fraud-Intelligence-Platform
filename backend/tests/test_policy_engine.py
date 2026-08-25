"""The deterministic decision engine.

These tests touch no database and no model. The engine is a pure function, so
every case here is an exact statement about behaviour rather than an
observation about a fixture.
"""

from __future__ import annotations

import pytest

from policy.actions import DEFAULT_PRECEDENCE, Action, most_restrictive, rank
from policy.context import (
    AnomalySignal,
    InvestigationSignal,
    RiskContext,
    SupervisedSignal,
    TransactionFacts,
)
from policy.engine import evaluate
from policy.loader import load_policy
from policy.reasons import ReasonCode
from policy.rules import RULES
from policy.schema import KNOWN_RULE_IDS, PolicyConfig


@pytest.fixture(scope="module")
def policy() -> PolicyConfig:
    return load_policy()


def make_context(
    *,
    transaction_id: str = "TXN_TEST",
    fraud_probability: float | None = 0.01,
    anomaly_score: int | None = 10,
    severity: str | None = "LOW",
    investigation: InvestigationSignal | None = None,
) -> RiskContext:
    """A context with both signals present and quiet unless overridden."""
    return RiskContext(
        transaction=TransactionFacts(transaction_id=transaction_id, amount=1000.0),
        supervised=SupervisedSignal(
            available=fraud_probability is not None,
            fraud_probability=fraud_probability,
            risk_score=None if fraud_probability is None else int(fraud_probability * 100),
            model_version="xgboost-v1",
        ),
        anomaly=AnomalySignal(
            available=anomaly_score is not None,
            anomaly_score=anomaly_score,
            severity=severity,
            model_version="isolation-forest-v1",
        ),
        investigation=investigation or InvestigationSignal(),
    )


def corroborating(
    *,
    confidence: float = 0.9,
    high_sources: int = 2,
    high_findings: int = 2,
    shared_entity: bool = False,
    status: str = "completed",
) -> InvestigationSignal:
    return InvestigationSignal(
        available=True,
        status=status,
        confidence=confidence,
        investigation_id="INV-TEST",
        high_severity_findings=high_findings,
        independent_high_severity_sources=high_sources,
        independent_evidence_sources=high_sources + 1,
        shared_entity_observed=shared_entity,
    )


# --------------------------------------------------------------------------
# Registry integrity
# --------------------------------------------------------------------------
def test_every_declared_rule_is_implemented() -> None:
    """Configuration can only enable rules that exist as code."""
    assert {rule_id for rule_id, _ in RULES} == KNOWN_RULE_IDS


def test_rule_ids_are_unique() -> None:
    ids = [rule_id for rule_id, _ in RULES]
    assert len(ids) == len(set(ids))


def test_default_policy_enables_every_known_rule(policy: PolicyConfig) -> None:
    assert policy.enabled_rules == KNOWN_RULE_IDS


# --------------------------------------------------------------------------
# Precedence
# --------------------------------------------------------------------------
def test_precedence_orders_block_first() -> None:
    assert rank(Action.BLOCK) < rank(Action.REVIEW) < rank(Action.STEP_UP) < rank(Action.APPROVE)


def test_most_restrictive_wins() -> None:
    assert most_restrictive([Action.APPROVE, Action.BLOCK, Action.STEP_UP]) is Action.BLOCK
    assert most_restrictive([Action.APPROVE, Action.STEP_UP]) is Action.STEP_UP
    assert most_restrictive([Action.APPROVE]) is Action.APPROVE


def test_most_restrictive_rejects_an_empty_set() -> None:
    with pytest.raises(ValueError, match="empty set"):
        most_restrictive([])


def test_precedence_covers_every_action() -> None:
    assert set(DEFAULT_PRECEDENCE) == set(Action)


def test_conflicting_rules_resolve_to_the_most_restrictive(policy: PolicyConfig) -> None:
    """A STEP_UP rule and a REVIEW rule matching together yield REVIEW."""
    context = make_context(fraud_probability=0.20, anomaly_score=100, severity="CRITICAL")
    result = evaluate(context, policy)

    assert "MODERATE_COMBINED_RISK" in result.matched_rules  # STEP_UP
    assert "MODEL_DISAGREEMENT_HIGH_ANOMALY" in result.matched_rules  # REVIEW
    assert result.action is Action.REVIEW


def test_precedence_alone_cannot_produce_a_block(policy: PolicyConfig) -> None:
    """No combination of non-BLOCK rules can escalate into a BLOCK.

    The spec's hard requirement: priority must never cause blocking. Only a rule
    whose own conditions are met may block.
    """
    # Every signal maxed except the supervised probability, which alone gates BLOCK.
    context = make_context(
        fraud_probability=0.89,  # just under fraud_block
        anomaly_score=100,
        severity="CRITICAL",
        investigation=corroborating(high_sources=9, high_findings=9, shared_entity=True),
    )
    result = evaluate(context, policy)

    assert result.action is not Action.BLOCK
    assert all(match.action is not Action.BLOCK for match in result.rule_matches)


# --------------------------------------------------------------------------
# Rule A - CRITICAL_SUPERVISED_RISK
# --------------------------------------------------------------------------
def test_block_requires_probability_and_corroboration(policy: PolicyConfig) -> None:
    context = make_context(fraud_probability=0.95, investigation=corroborating())
    result = evaluate(context, policy)

    assert result.action is Action.BLOCK
    assert "CRITICAL_SUPERVISED_RISK" in result.deciding_rules
    assert ReasonCode.VERY_HIGH_FRAUD_PROBABILITY in result.reason_codes
    assert ReasonCode.INDEPENDENT_CORROBORATION in result.reason_codes


def test_block_downgrades_to_review_without_an_investigation(policy: PolicyConfig) -> None:
    """A block is withheld rather than taken on an uncorroborated model output."""
    context = make_context(fraud_probability=0.99)
    result = evaluate(context, policy)

    assert result.action is Action.REVIEW
    assert ReasonCode.BLOCK_WITHHELD_PENDING_INVESTIGATION in result.reason_codes
    assert ReasonCode.INVESTIGATION_UNAVAILABLE in result.reason_codes


def test_block_downgrades_on_too_few_independent_sources(policy: PolicyConfig) -> None:
    context = make_context(fraud_probability=0.99, investigation=corroborating(high_sources=1))
    result = evaluate(context, policy)

    assert result.action is Action.REVIEW
    assert ReasonCode.INSUFFICIENT_CORROBORATION in result.reason_codes


def test_block_downgrades_on_low_investigation_confidence(policy: PolicyConfig) -> None:
    context = make_context(
        fraud_probability=0.99, investigation=corroborating(confidence=0.2, high_sources=5)
    )
    result = evaluate(context, policy)

    assert result.action is Action.REVIEW
    assert ReasonCode.LOW_INVESTIGATION_CONFIDENCE in result.reason_codes


def test_block_downgrades_when_the_investigation_did_not_complete(policy: PolicyConfig) -> None:
    for status in ("insufficient_evidence", "agent_unavailable", "failed", "in_progress"):
        context = make_context(
            fraud_probability=0.99,
            investigation=corroborating(status=status, high_sources=5),
        )
        result = evaluate(context, policy)
        assert result.action is Action.REVIEW, status


def test_coordinated_activity_is_reported_when_an_entity_is_shared(
    policy: PolicyConfig,
) -> None:
    context = make_context(fraud_probability=0.95, investigation=corroborating(shared_entity=True))
    result = evaluate(context, policy)

    assert ReasonCode.COORDINATED_ACTIVITY in result.reason_codes


# --------------------------------------------------------------------------
# Rules B and C - the anomaly paths
# --------------------------------------------------------------------------
def test_critical_anomaly_with_findings_routes_to_review(policy: PolicyConfig) -> None:
    context = make_context(
        fraud_probability=0.60,  # above fraud_high, so rule C cannot fire
        anomaly_score=100,
        severity="CRITICAL",
        investigation=corroborating(high_findings=2),
    )
    result = evaluate(context, policy)

    assert "HIGH_ANOMALY_WITH_CORROBORATION" in result.matched_rules
    assert result.action is Action.REVIEW


def test_critical_anomaly_needs_enough_findings(policy: PolicyConfig) -> None:
    context = make_context(
        fraud_probability=0.60,
        anomaly_score=100,
        severity="CRITICAL",
        investigation=corroborating(high_findings=1),
    )
    result = evaluate(context, policy)

    assert "HIGH_ANOMALY_WITH_CORROBORATION" not in result.matched_rules


def test_model_disagreement_routes_to_review(policy: PolicyConfig) -> None:
    """Low fraud probability plus a critical anomaly - the Scenario C1 shape."""
    context = make_context(fraud_probability=0.20, anomaly_score=100, severity="CRITICAL")
    result = evaluate(context, policy)

    assert result.action is Action.REVIEW
    assert "MODEL_DISAGREEMENT_HIGH_ANOMALY" in result.deciding_rules
    assert ReasonCode.MODEL_DISAGREEMENT in result.reason_codes


def test_agreement_is_reported_when_both_engines_are_elevated(policy: PolicyConfig) -> None:
    context = make_context(fraud_probability=0.70, anomaly_score=98, severity="HIGH")
    result = evaluate(context, policy)

    assert "HIGH_SUPERVISED_RISK" in result.matched_rules
    assert ReasonCode.MODEL_AGREEMENT in result.reason_codes


# --------------------------------------------------------------------------
# Rules D, E, F
# --------------------------------------------------------------------------
def test_high_supervised_risk_alone_routes_to_review(policy: PolicyConfig) -> None:
    context = make_context(fraud_probability=0.60, anomaly_score=10)
    result = evaluate(context, policy)

    assert result.action is Action.REVIEW
    assert "HIGH_SUPERVISED_RISK" in result.deciding_rules


def test_moderate_combined_risk_steps_up(policy: PolicyConfig) -> None:
    context = make_context(fraud_probability=0.30, anomaly_score=95, severity="MEDIUM")
    result = evaluate(context, policy)

    assert result.action is Action.STEP_UP
    assert "MODERATE_COMBINED_RISK" in result.deciding_rules


def test_elevated_anomaly_alone_steps_up(policy: PolicyConfig) -> None:
    context = make_context(fraud_probability=0.01, anomaly_score=98, severity="HIGH")
    result = evaluate(context, policy)

    assert result.action is Action.STEP_UP
    assert "ELEVATED_ANOMALY_ONLY" in result.deciding_rules


def test_low_risk_approves(policy: PolicyConfig) -> None:
    result = evaluate(make_context(), policy)

    assert result.action is Action.APPROVE
    assert result.matched_rules == ("LOW_RISK",)
    assert result.requires_human_review is False


def test_approval_requires_both_signals_to_be_present(policy: PolicyConfig) -> None:
    """LOW_RISK is a positive statement, not the absence of an opinion."""
    result = evaluate(make_context(fraud_probability=None), policy)
    assert "LOW_RISK" not in result.matched_rules

    result = evaluate(make_context(anomaly_score=None), policy)
    assert "LOW_RISK" not in result.matched_rules


def test_low_risk_does_not_fire_when_the_investigation_found_something(
    policy: PolicyConfig,
) -> None:
    context = make_context(investigation=corroborating(high_findings=1, high_sources=0))
    result = evaluate(context, policy)

    assert "LOW_RISK" not in result.matched_rules


# --------------------------------------------------------------------------
# Threshold boundaries. Every rule uses >=, so the threshold itself is inside.
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("probability", "expected_rule"),
    [
        (0.90, "CRITICAL_SUPERVISED_RISK"),
        (0.533209, "HIGH_SUPERVISED_RISK"),
        (0.15, "MODERATE_COMBINED_RISK"),
    ],
)
def test_a_threshold_value_is_inside_its_band(
    policy: PolicyConfig, probability: float, expected_rule: str
) -> None:
    context = make_context(
        fraud_probability=probability,
        anomaly_score=95,
        severity="MEDIUM",
        investigation=corroborating(),
    )
    result = evaluate(context, policy)
    assert expected_rule in result.matched_rules


def test_just_below_the_block_threshold_does_not_block(policy: PolicyConfig) -> None:
    context = make_context(fraud_probability=0.8999999, investigation=corroborating())
    result = evaluate(context, policy)

    assert result.action is not Action.BLOCK


def test_anomaly_band_boundaries(policy: PolicyConfig) -> None:
    thresholds = policy.thresholds

    at_critical = evaluate(
        make_context(anomaly_score=int(thresholds.anomaly_critical) + 1, severity="CRITICAL"),
        policy,
    )
    assert "MODEL_DISAGREEMENT_HIGH_ANOMALY" in at_critical.matched_rules

    at_high = evaluate(
        make_context(anomaly_score=int(thresholds.anomaly_high) + 1, severity="HIGH"), policy
    )
    assert "ELEVATED_ANOMALY_ONLY" in at_high.matched_rules

    below_medium = evaluate(make_context(anomaly_score=int(thresholds.anomaly_medium) - 1), policy)
    assert below_medium.action is Action.APPROVE


# --------------------------------------------------------------------------
# Fail-safes
# --------------------------------------------------------------------------
def test_missing_supervised_signal_never_approves(policy: PolicyConfig) -> None:
    result = evaluate(make_context(fraud_probability=None), policy)

    assert result.action is not Action.APPROVE
    assert "MISSING_SUPERVISED_SIGNAL" in result.matched_rules
    assert ReasonCode.SUPERVISED_SIGNAL_UNAVAILABLE in result.reason_codes


def test_missing_anomaly_signal_never_approves(policy: PolicyConfig) -> None:
    result = evaluate(make_context(anomaly_score=None), policy)

    assert result.action is not Action.APPROVE
    assert "MISSING_ANOMALY_SIGNAL" in result.matched_rules


def test_both_signals_missing_routes_to_review(policy: PolicyConfig) -> None:
    result = evaluate(make_context(fraud_probability=None, anomaly_score=None), policy)

    assert result.action is Action.REVIEW
    assert set(result.matched_rules) == {"MISSING_SUPERVISED_SIGNAL", "MISSING_ANOMALY_SIGNAL"}


def test_missing_investigation_fires_only_when_risk_is_elevated(policy: PolicyConfig) -> None:
    """A quiet transaction is not penalised for an investigation nobody ran."""
    quiet = evaluate(make_context(), policy)
    assert "MISSING_INVESTIGATION" not in quiet.matched_rules
    assert quiet.action is Action.APPROVE

    elevated = evaluate(make_context(fraud_probability=0.30, anomaly_score=95), policy)
    assert "MISSING_INVESTIGATION" in elevated.matched_rules


def test_no_input_combination_silently_approves_on_a_missing_signal(
    policy: PolicyConfig,
) -> None:
    """Exhaustive sweep: an unavailable signal never yields APPROVE."""
    probabilities = [None, 0.0, 0.14, 0.15, 0.5, 0.533209, 0.89, 0.9, 1.0]
    scores = [None, 0, 50, 92, 93, 97, 98, 99, 100]

    for probability in probabilities:
        for score in scores:
            if probability is not None and score is not None:
                continue
            result = evaluate(
                make_context(fraud_probability=probability, anomaly_score=score), policy
            )
            assert result.action is not Action.APPROVE, (probability, score)


# --------------------------------------------------------------------------
# The adversarial matrix from the specification
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "probability", "score", "investigation", "expected"),
    [
        ("high ML, low anomaly, corroborated", 0.95, 10, True, Action.BLOCK),
        ("high ML, low anomaly, uncorroborated", 0.95, 10, False, Action.REVIEW),
        ("low ML, high anomaly", 0.01, 98, False, Action.STEP_UP),
        ("low ML, critical anomaly", 0.01, 100, False, Action.REVIEW),
        ("high ML, high anomaly", 0.60, 98, False, Action.REVIEW),
        ("low ML, low anomaly", 0.001, 5, False, Action.APPROVE),
    ],
)
def test_adversarial_signal_matrix(
    policy: PolicyConfig,
    name: str,
    probability: float,
    score: int,
    investigation: bool,
    expected: Action,
) -> None:
    context = make_context(
        fraud_probability=probability,
        anomaly_score=score,
        investigation=corroborating() if investigation else None,
    )
    assert evaluate(context, policy).action is expected, name


def test_contradictory_evidence_does_not_produce_a_block(policy: PolicyConfig) -> None:
    """A clean investigation against a very high probability withholds the block."""
    clean = InvestigationSignal(
        available=True,
        status="completed",
        confidence=0.8,
        investigation_id="INV-CLEAN",
        high_severity_findings=0,
        independent_high_severity_sources=0,
        independent_evidence_sources=4,
    )
    result = evaluate(make_context(fraud_probability=0.99, investigation=clean), policy)

    assert result.action is Action.REVIEW
    assert ReasonCode.BLOCK_WITHHELD_PENDING_INVESTIGATION in result.reason_codes


# --------------------------------------------------------------------------
# Determinism and the audit surface
# --------------------------------------------------------------------------
def test_repeated_evaluation_is_byte_identical(policy: PolicyConfig) -> None:
    context = make_context(fraud_probability=0.42, anomaly_score=96, severity="MEDIUM")
    results = [evaluate(context, policy) for _ in range(50)]

    assert len({result.action for result in results}) == 1
    assert len({result.input_digest for result in results}) == 1
    assert len({result.explanation for result in results}) == 1
    assert len({tuple(result.reason_codes) for result in results}) == 1


def test_different_inputs_produce_different_digests(policy: PolicyConfig) -> None:
    a = evaluate(make_context(fraud_probability=0.42), policy)
    b = evaluate(make_context(fraud_probability=0.43), policy)

    assert a.input_digest != b.input_digest


def test_a_policy_change_changes_the_digest(policy: PolicyConfig) -> None:
    """The digest covers the policy, so the same signals under a different
    policy are distinguishable in the audit record."""
    from dataclasses import replace

    other = replace(policy, policy_version="policy-test", enabled_rules=frozenset({"LOW_RISK"}))
    context = make_context()

    assert evaluate(context, policy).input_digest != evaluate(context, other).input_digest


def test_matched_rules_record_every_rule_that_fired(policy: PolicyConfig) -> None:
    """The record shows the whole picture, not only the winning rule."""
    context = make_context(fraud_probability=0.20, anomaly_score=100, severity="CRITICAL")
    result = evaluate(context, policy)

    assert len(result.matched_rules) > 1
    assert len(result.deciding_rules) < len(result.matched_rules)


def test_matched_rules_follow_the_fixed_evaluation_order(policy: PolicyConfig) -> None:
    order = [rule_id for rule_id, _ in RULES]
    context = make_context(fraud_probability=0.20, anomaly_score=100, severity="CRITICAL")
    matched = list(evaluate(context, policy).matched_rules)

    assert matched == [rule_id for rule_id in order if rule_id in matched]


def test_conditions_carry_the_measured_values(policy: PolicyConfig) -> None:
    result = evaluate(make_context(fraud_probability=0.95, investigation=corroborating()), policy)
    conditions = [c for match in result.rule_matches for c in match.conditions]

    assert any("0.95" in condition for condition in conditions)


def test_audit_record_is_complete(policy: PolicyConfig) -> None:
    result = evaluate(make_context(fraud_probability=0.95, investigation=corroborating()), policy)
    record = result.as_audit_record()

    for key in (
        "transaction_id",
        "decision",
        "policy_version",
        "matched_rules",
        "deciding_rules",
        "reason_codes",
        "requires_human_review",
        "input_digest",
        "risk_summary",
        "rule_matches",
    ):
        assert key in record


def test_disabled_rules_do_not_fire(policy: PolicyConfig) -> None:
    from dataclasses import replace

    without_block = replace(
        policy, enabled_rules=policy.enabled_rules - {"CRITICAL_SUPERVISED_RISK"}
    )
    context = make_context(fraud_probability=0.99, investigation=corroborating())

    assert evaluate(context, without_block).action is not Action.BLOCK
    assert "CRITICAL_SUPERVISED_RISK" not in evaluate(context, without_block).matched_rules


def test_no_matching_rule_falls_back_to_the_configured_default(policy: PolicyConfig) -> None:
    from dataclasses import replace

    only_block = replace(policy, enabled_rules=frozenset({"CRITICAL_SUPERVISED_RISK"}))
    result = evaluate(make_context(), only_block)

    assert result.matched_rules == ()
    assert result.action is only_block.actions.default
    assert ReasonCode.NO_CONCERNING_EVIDENCE in result.reason_codes


# --------------------------------------------------------------------------
# The explanation
# --------------------------------------------------------------------------
def test_explanation_names_the_deciding_rules(policy: PolicyConfig) -> None:
    result = evaluate(make_context(fraud_probability=0.95, investigation=corroborating()), policy)

    assert "CRITICAL_SUPERVISED_RISK" in result.explanation
    assert policy.policy_version in result.explanation
    assert "No language model chose it" in result.explanation


def test_explanation_lists_every_reason_code(policy: PolicyConfig) -> None:
    result = evaluate(
        make_context(fraud_probability=0.20, anomaly_score=100, severity="CRITICAL"), policy
    )

    for code in result.reason_codes:
        assert code in result.explanation


def test_human_review_flag_matches_the_policy(policy: PolicyConfig) -> None:
    for context, expected in [
        (make_context(), False),
        (make_context(fraud_probability=0.01, anomaly_score=98), False),
        (make_context(fraud_probability=0.60), True),
        (make_context(fraud_probability=0.95, investigation=corroborating()), True),
    ]:
        assert evaluate(context, policy).requires_human_review is expected


def test_disagreement_reports_coordinated_activity(policy: PolicyConfig) -> None:
    """The ring path must record the shared entity the investigation found.

    Scenario C1's real investigation discovers a shared device and a shared IP
    but produces only one high-severity finding, so the corroboration rule does
    not fire. Without this the coordinated-activity fact would be dropped from
    the audit record of the very case it characterises.
    """
    context = make_context(
        fraud_probability=0.20,
        anomaly_score=100,
        severity="CRITICAL",
        investigation=corroborating(high_findings=1, high_sources=3, shared_entity=True),
    )
    result = evaluate(context, policy)

    assert "HIGH_ANOMALY_WITH_CORROBORATION" not in result.matched_rules
    assert "MODEL_DISAGREEMENT_HIGH_ANOMALY" in result.deciding_rules
    assert ReasonCode.COORDINATED_ACTIVITY in result.reason_codes
    assert result.action is Action.REVIEW


def test_disagreement_omits_coordinated_activity_without_a_shared_entity(
    policy: PolicyConfig,
) -> None:
    context = make_context(
        fraud_probability=0.20,
        anomaly_score=100,
        severity="CRITICAL",
        investigation=corroborating(high_findings=1, shared_entity=False),
    )
    result = evaluate(context, policy)

    assert ReasonCode.COORDINATED_ACTIVITY not in result.reason_codes


def test_disagreement_ignores_a_shared_entity_from_an_unusable_investigation(
    policy: PolicyConfig,
) -> None:
    """An investigation that did not complete is not a source of facts."""
    context = make_context(
        fraud_probability=0.20,
        anomaly_score=100,
        severity="CRITICAL",
        investigation=corroborating(status="agent_unavailable", shared_entity=True),
    )
    result = evaluate(context, policy)

    assert ReasonCode.COORDINATED_ACTIVITY not in result.reason_codes
