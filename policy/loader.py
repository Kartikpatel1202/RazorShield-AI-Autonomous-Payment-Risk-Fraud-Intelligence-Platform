"""Loading and caching validated policies.

A policy is loaded once, validated completely, and cached by path. An invalid
policy raises rather than falling back to a default: silently running on a
different policy than the operator configured is precisely the failure the
version stamp is meant to make impossible.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from policy.actions import DEFAULT_PRECEDENCE, Action
from policy.schema import (
    ActionPolicy,
    EvidenceRequirements,
    FailSafe,
    PolicyConfig,
    PolicyValidationError,
    Thresholds,
)

logger = logging.getLogger(__name__)

POLICY_DIR = Path(__file__).resolve().parents[1] / "config" / "policies"
DEFAULT_POLICY_PATH = POLICY_DIR / "default.yaml"


def _action(name: Any, field: str) -> Action:
    """Resolve an action name, or fail loudly."""
    try:
        return Action(str(name).upper())
    except ValueError as exc:
        raise PolicyValidationError(
            [f"{field}={name!r} is not one of {sorted(str(a) for a in Action)}"]
        ) from exc


def parse_policy(raw: dict[str, Any], source: str = "<memory>") -> PolicyConfig:
    """Build a validated :class:`PolicyConfig` from a raw mapping."""
    missing = [
        key
        for key in ("policy_version", "thresholds", "evidence", "fail_safe", "actions", "rules")
        if key not in raw
    ]
    if missing:
        raise PolicyValidationError([f"missing top-level section(s): {missing}"])

    try:
        thresholds = Thresholds(**raw["thresholds"])
        evidence = EvidenceRequirements(**raw["evidence"])
    except TypeError as exc:
        raise PolicyValidationError([f"malformed thresholds or evidence section: {exc}"]) from exc

    fail_safe_raw = raw["fail_safe"]
    fail_safe = FailSafe(
        missing_supervised_signal=_action(
            fail_safe_raw.get("missing_supervised_signal"), "fail_safe.missing_supervised_signal"
        ),
        missing_anomaly_signal=_action(
            fail_safe_raw.get("missing_anomaly_signal"), "fail_safe.missing_anomaly_signal"
        ),
        missing_investigation=_action(
            fail_safe_raw.get("missing_investigation"), "fail_safe.missing_investigation"
        ),
        require_investigation_for_block=bool(
            fail_safe_raw.get("require_investigation_for_block", True)
        ),
    )

    actions_raw = raw["actions"]
    precedence_raw = actions_raw.get("precedence") or list(DEFAULT_PRECEDENCE)
    actions = ActionPolicy(
        precedence=tuple(_action(name, "actions.precedence") for name in precedence_raw),
        default=_action(actions_raw.get("default", "APPROVE"), "actions.default"),
        human_review_required_for=frozenset(
            _action(name, "actions.human_review_required_for")
            for name in actions_raw.get("human_review_required_for", [])
        ),
    )

    rules_raw = raw["rules"] or {}
    if not isinstance(rules_raw, dict):
        raise PolicyValidationError(["rules must be a mapping of rule id to true/false"])

    config = PolicyConfig(
        policy_version=str(raw["policy_version"]),
        description=str(raw.get("description", "")).strip(),
        thresholds=thresholds,
        evidence=evidence,
        fail_safe=fail_safe,
        actions=actions,
        enabled_rules=frozenset(name for name, enabled in rules_raw.items() if enabled),
        source=source,
    )
    config.validate()
    return config


def load_policy(path: Path | None = None) -> PolicyConfig:
    """Read, validate and return a policy from disk."""
    policy_path = path or DEFAULT_POLICY_PATH
    if not policy_path.exists():
        raise PolicyValidationError([f"policy file {policy_path.name} does not exist"])

    try:
        raw = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PolicyValidationError([f"{policy_path.name} is not valid YAML: {exc}"]) from exc

    if not isinstance(raw, dict):
        raise PolicyValidationError([f"{policy_path.name} must contain a mapping"])

    config = parse_policy(raw, source=policy_path.name)
    logger.info(
        "Loaded policy %s from %s (%d rules enabled)",
        config.policy_version,
        config.source,
        len(config.enabled_rules),
    )
    return config


@lru_cache(maxsize=8)
def get_policy(path: Path | None = None) -> PolicyConfig:
    """Process-wide cached policy. Immutable once loaded."""
    return load_policy(path)


def reset_policy_cache() -> None:
    """Drop cached policies. Used by tests and after a configuration change."""
    get_policy.cache_clear()
