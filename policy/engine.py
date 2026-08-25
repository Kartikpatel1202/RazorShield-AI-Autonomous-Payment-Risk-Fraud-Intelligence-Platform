"""The deterministic decision engine.

Pure by construction: it takes a :class:`RiskContext` and a
:class:`PolicyConfig` and returns a :class:`PolicyResult`. No database, no
network, no language model, no clock, no randomness. The same inputs always
produce byte-identical output, which is what makes a decision auditable and a
regression test meaningful.

Precedence resolves *between matched rules*; it never creates a match. A block
therefore cannot arise from ordering - only from a rule whose own explicit
conditions were satisfied.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any

from policy.actions import Action, most_restrictive
from policy.context import RiskContext
from policy.explain import build_explanation
from policy.reasons import ReasonCode
from policy.rules import RULES
from policy.schema import PolicyConfig, RuleMatch


@dataclass(frozen=True)
class PolicyResult:
    """A decision, and everything needed to justify and reproduce it."""

    transaction_id: str
    action: Action
    policy_version: str
    matched_rules: tuple[str, ...]
    reason_codes: tuple[str, ...]
    #: Every rule that fired, with the values that made it fire.
    rule_matches: tuple[RuleMatch, ...]
    explanation: str
    requires_human_review: bool
    risk_summary: dict[str, Any] = field(default_factory=dict)
    #: Fingerprint of the inputs and the policy. Equal fingerprints must yield
    #: equal decisions; a differing one shows the inputs or policy moved.
    input_digest: str = ""

    @property
    def deciding_rules(self) -> tuple[str, ...]:
        """The matched rules whose action equals the final action."""
        return tuple(match.rule_id for match in self.rule_matches if match.action is self.action)

    def as_audit_record(self) -> dict[str, Any]:
        """The stable shape written to the decision and audit tables."""
        return {
            "transaction_id": self.transaction_id,
            "decision": str(self.action),
            "policy_version": self.policy_version,
            "matched_rules": list(self.matched_rules),
            "deciding_rules": list(self.deciding_rules),
            "reason_codes": list(self.reason_codes),
            "requires_human_review": self.requires_human_review,
            "input_digest": self.input_digest,
            "risk_summary": self.risk_summary,
            "rule_matches": [
                {
                    "rule_id": match.rule_id,
                    "action": str(match.action),
                    "reason_codes": list(match.reason_codes),
                    "conditions": list(match.conditions),
                }
                for match in self.rule_matches
            ],
        }


def _digest(context: RiskContext, policy: PolicyConfig) -> str:
    """A stable fingerprint of the decision inputs.

    Deterministic across processes: ``sort_keys`` plus a fixed float
    representation, hashed with SHA-256. Used to prove that a re-run of the same
    transaction under the same policy saw the same inputs.
    """
    payload = {
        "transaction_id": context.transaction.transaction_id,
        "signals": context.risk_summary(),
        "policy": policy.as_dict(),
    }
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _dedupe(codes: list[str]) -> tuple[str, ...]:
    """Preserve first-seen order while removing repeats."""
    seen: dict[str, None] = {}
    for code in codes:
        seen.setdefault(code, None)
    return tuple(seen)


def evaluate(context: RiskContext, policy: PolicyConfig) -> PolicyResult:
    """Apply the policy to one transaction.

    Every enabled rule is evaluated - the engine does not stop at the first
    match, because the audit record should show everything that fired, not only
    the rule that happened to win.
    """
    matches: list[RuleMatch] = []
    for rule_id, rule in RULES:
        if not policy.is_enabled(rule_id):
            continue
        match = rule(context, policy)
        if match is not None:
            matches.append(match)

    if matches:
        action = most_restrictive(
            [match.action for match in matches], precedence=policy.actions.precedence
        )
        reason_codes = _dedupe([str(code) for match in matches for code in match.reason_codes])
    else:
        # No rule matched. This is not the ordinary approval path - LOW_RISK is -
        # so record explicitly that nothing was concluded rather than leaving a
        # bare approval with no reason attached.
        action = policy.actions.default
        reason_codes = (str(ReasonCode.NO_CONCERNING_EVIDENCE),)

    result = PolicyResult(
        transaction_id=context.transaction.transaction_id,
        action=action,
        policy_version=policy.policy_version,
        matched_rules=tuple(match.rule_id for match in matches),
        reason_codes=reason_codes,
        rule_matches=tuple(matches),
        explanation="",
        requires_human_review=policy.requires_human_review(action),
        risk_summary=context.risk_summary(),
        input_digest=_digest(context, policy),
    )
    # The explanation is derived from the resolved result, so it can name the
    # deciding rules and cannot drift from what was actually decided.
    return replace(result, explanation=build_explanation(result, policy))
