"""Policy loading, validation and versioning.

The contract these tests pin down: an invalid policy must fail to load. It must
never load partially, never fall back to a default nobody configured, and never
silently ignore a line it did not understand.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from policy.actions import Action
from policy.loader import (
    DEFAULT_POLICY_PATH,
    get_policy,
    load_policy,
    parse_policy,
    reset_policy_cache,
)
from policy.schema import KNOWN_RULE_IDS, PolicyValidationError


@pytest.fixture()
def raw() -> dict[str, Any]:
    """The shipped policy as a plain mapping, safe to mutate per test."""
    return copy.deepcopy(yaml.safe_load(DEFAULT_POLICY_PATH.read_text(encoding="utf-8")))


def problems_of(raw: dict[str, Any]) -> list[str]:
    with pytest.raises(PolicyValidationError) as exc:
        parse_policy(raw)
    return exc.value.problems


# --------------------------------------------------------------------------
# The shipped policy
# --------------------------------------------------------------------------
def test_the_default_policy_loads_and_validates() -> None:
    policy = load_policy()

    assert policy.policy_version
    assert policy.source == "default.yaml"
    assert policy.enabled_rules == KNOWN_RULE_IDS


def test_the_default_policy_is_versioned() -> None:
    assert load_policy().policy_version == "policy-v1"


def test_thresholds_come_from_configuration_not_code() -> None:
    """Every threshold a rule uses is readable from the file."""
    policy = load_policy()
    thresholds = policy.as_dict()["thresholds"]

    assert thresholds["fraud_block"] == pytest.approx(0.90)
    assert thresholds["fraud_high"] == pytest.approx(0.533209)
    assert thresholds["anomaly_critical"] == pytest.approx(99.4)


def test_the_policy_snapshot_is_serialisable() -> None:
    import json

    json.dumps(load_policy().as_dict())


# --------------------------------------------------------------------------
# Range validation
# --------------------------------------------------------------------------
@pytest.mark.parametrize("value", [-0.1, 1.5, 42.0])
def test_probability_thresholds_must_be_in_range(raw: dict[str, Any], value: float) -> None:
    raw["thresholds"]["fraud_block"] = value
    assert any("probability range" in problem for problem in problems_of(raw))


@pytest.mark.parametrize("value", [-1.0, 101.0, 1000.0])
def test_anomaly_thresholds_must_be_in_range(raw: dict[str, Any], value: float) -> None:
    raw["thresholds"]["anomaly_critical"] = value
    assert any("anomaly range" in problem for problem in problems_of(raw))


def test_fraud_thresholds_must_ascend(raw: dict[str, Any]) -> None:
    raw["thresholds"]["fraud_block"] = 0.1
    raw["thresholds"]["fraud_high"] = 0.5
    assert any("must ascend" in problem for problem in problems_of(raw))


def test_anomaly_thresholds_must_ascend(raw: dict[str, Any]) -> None:
    raw["thresholds"]["anomaly_medium"] = 99.9
    assert any("must ascend" in problem for problem in problems_of(raw))


def test_evidence_requirements_must_be_meaningful(raw: dict[str, Any]) -> None:
    raw["evidence"]["min_independent_sources_for_block"] = 0
    assert any("at least 1" in problem for problem in problems_of(raw))


def test_investigation_confidence_floor_must_be_a_probability(raw: dict[str, Any]) -> None:
    raw["evidence"]["min_investigation_confidence"] = 3.0
    assert any("min_investigation_confidence" in problem for problem in problems_of(raw))


# --------------------------------------------------------------------------
# Structural validation
# --------------------------------------------------------------------------
def test_unknown_rule_ids_are_rejected(raw: dict[str, Any]) -> None:
    raw["rules"]["DEFINITELY_NOT_A_RULE"] = True
    assert any("unknown rule id" in problem for problem in problems_of(raw))


def test_a_disabled_unknown_rule_is_still_ignored_safely(raw: dict[str, Any]) -> None:
    """Disabled entries do not enable anything, so they cannot smuggle behaviour in."""
    raw["rules"]["DEFINITELY_NOT_A_RULE"] = False
    policy = parse_policy(raw)

    assert "DEFINITELY_NOT_A_RULE" not in policy.enabled_rules


def test_every_rule_disabled_is_rejected(raw: dict[str, Any]) -> None:
    raw["rules"] = dict.fromkeys(raw["rules"], False)
    assert any("at least one rule" in problem for problem in problems_of(raw))


def test_unknown_action_names_are_rejected(raw: dict[str, Any]) -> None:
    raw["fail_safe"]["missing_supervised_signal"] = "DELETE_CUSTOMER"
    assert any("not one of" in problem for problem in problems_of(raw))


def test_precedence_must_list_every_action(raw: dict[str, Any]) -> None:
    raw["actions"]["precedence"] = ["BLOCK", "REVIEW"]
    assert any("must list every action" in problem for problem in problems_of(raw))


def test_precedence_must_put_block_first(raw: dict[str, Any]) -> None:
    raw["actions"]["precedence"] = ["REVIEW", "BLOCK", "STEP_UP", "APPROVE"]
    assert any("BLOCK must be the most restrictive" in problem for problem in problems_of(raw))


def test_duplicate_precedence_entries_are_rejected(raw: dict[str, Any]) -> None:
    raw["actions"]["precedence"] = ["BLOCK", "BLOCK", "REVIEW", "STEP_UP", "APPROVE"]
    assert any("duplicate" in problem for problem in problems_of(raw))


def test_an_empty_version_is_rejected(raw: dict[str, Any]) -> None:
    raw["policy_version"] = "   "
    assert any("policy_version" in problem for problem in problems_of(raw))


@pytest.mark.parametrize(
    "section", ["policy_version", "thresholds", "evidence", "fail_safe", "actions", "rules"]
)
def test_a_missing_section_is_rejected(raw: dict[str, Any], section: str) -> None:
    raw.pop(section)
    assert any("missing top-level section" in problem for problem in problems_of(raw))


def test_a_missing_threshold_is_rejected(raw: dict[str, Any]) -> None:
    raw["thresholds"].pop("fraud_block")
    assert any("malformed" in problem for problem in problems_of(raw))


def test_an_unexpected_threshold_key_is_rejected(raw: dict[str, Any]) -> None:
    """Configuration cannot introduce a threshold the code does not read."""
    raw["thresholds"]["fraud_super_block"] = 0.99
    assert any("malformed" in problem for problem in problems_of(raw))


def test_rules_must_be_a_mapping(raw: dict[str, Any]) -> None:
    raw["rules"] = ["CRITICAL_SUPERVISED_RISK"]
    with pytest.raises(PolicyValidationError, match="must be a mapping"):
        parse_policy(raw)


# --------------------------------------------------------------------------
# Fail-safe validation
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "field",
    ["missing_supervised_signal", "missing_anomaly_signal", "missing_investigation"],
)
def test_a_fail_safe_may_not_be_approve(raw: dict[str, Any], field: str) -> None:
    """The configuration layer itself forbids silent approval."""
    raw["fail_safe"][field] = "APPROVE"
    assert any("silently approve" in problem for problem in problems_of(raw))


def test_fail_safes_may_be_any_restrictive_action(raw: dict[str, Any]) -> None:
    raw["fail_safe"]["missing_investigation"] = "BLOCK"
    assert parse_policy(raw).fail_safe.missing_investigation is Action.BLOCK


# --------------------------------------------------------------------------
# Every problem is reported, not just the first
# --------------------------------------------------------------------------
def test_all_problems_are_reported_together(raw: dict[str, Any]) -> None:
    raw["thresholds"]["fraud_block"] = 5.0
    raw["evidence"]["min_high_findings_for_review"] = 0
    raw["rules"]["NOT_A_RULE"] = True

    problems = problems_of(raw)
    assert len(problems) >= 3


# --------------------------------------------------------------------------
# Loading from disk
# --------------------------------------------------------------------------
def test_a_missing_file_fails_safely(tmp_path: Path) -> None:
    with pytest.raises(PolicyValidationError, match="does not exist"):
        load_policy(tmp_path / "nope.yaml")


def test_malformed_yaml_fails_safely(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("policy_version: [unclosed\n", encoding="utf-8")

    with pytest.raises(PolicyValidationError, match="not valid YAML"):
        load_policy(path)


def test_a_non_mapping_document_fails_safely(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- one\n- two\n", encoding="utf-8")

    with pytest.raises(PolicyValidationError, match="must contain a mapping"):
        load_policy(path)


def test_an_invalid_file_never_falls_back_to_the_default(tmp_path: Path) -> None:
    """No partial load, no silent substitution - it raises."""
    path = tmp_path / "bad.yaml"
    path.write_text("policy_version: broken\n", encoding="utf-8")

    with pytest.raises(PolicyValidationError):
        load_policy(path)


def test_an_alternative_policy_file_can_be_loaded(tmp_path: Path, raw: dict[str, Any]) -> None:
    raw["policy_version"] = "policy-strict"
    raw["thresholds"]["fraud_block"] = 0.70
    path = tmp_path / "strict.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    policy = load_policy(path)
    assert policy.policy_version == "policy-strict"
    assert policy.thresholds.fraud_block == pytest.approx(0.70)
    assert policy.source == "strict.yaml"


# --------------------------------------------------------------------------
# Caching
# --------------------------------------------------------------------------
def test_the_cached_policy_is_the_same_object() -> None:
    reset_policy_cache()
    assert get_policy() is get_policy()


def test_the_cache_can_be_reset() -> None:
    first = get_policy()
    reset_policy_cache()
    assert get_policy() is not first
    assert get_policy().policy_version == first.policy_version


def test_a_loaded_policy_is_immutable() -> None:
    import dataclasses

    policy = load_policy()
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.policy_version = "tampered"  # type: ignore[misc]

    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.thresholds.fraud_block = 0.0  # type: ignore[misc]
