"""Turning a decision into an explanation.

The explanation is *derived*, not written. It is assembled by string
concatenation from the resolved result - the deciding rules, their reason codes
and the measured values they recorded - so it cannot say anything the rules did
not, and it changes only when the decision changes.

No language model is involved at any point. A generated explanation would be
persuasive prose about a decision rather than a record of it, and a reviewer
overruling the engine deserves the actual arithmetic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from policy.actions import Action
from policy.schema import PolicyConfig

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from policy.engine import PolicyResult

#: What each action means operationally. Fixed text, keyed by action.
_ACTION_SENTENCE: dict[Action, str] = {
    Action.APPROVE: "Approved: no policy rule required intervention.",
    Action.STEP_UP: (
        "Step-up authentication required: the transaction is elevated but the "
        "evidence does not support stopping it."
    ),
    Action.REVIEW: "Routed to human review: an analyst must decide this transaction.",
    Action.BLOCK: "Blocked: policy conditions for an automated block were met in full.",
}


def build_explanation(result: PolicyResult, policy: PolicyConfig) -> str:
    """Compose the deterministic explanation for a decided transaction."""
    lines: list[str] = [_ACTION_SENTENCE[result.action]]

    deciding = set(result.deciding_rules)
    if result.rule_matches:
        lines.append("")
        lines.append(f"Policy {policy.policy_version} evaluated the following:")
        for match in result.rule_matches:
            marker = "*" if match.rule_id in deciding else "-"
            lines.append(f"{marker} {match.rule_id} -> {match.action}")
            for condition in match.conditions:
                lines.append(f"    {condition}")
    else:
        lines.append("")
        lines.append(
            f"Policy {policy.policy_version} matched no rule; the configured default "
            f"action {policy.actions.default} applies."
        )

    if result.reason_codes:
        lines.append("")
        lines.append("Reason codes: " + ", ".join(result.reason_codes))

    if result.requires_human_review:
        lines.append("")
        lines.append("A review case has been opened. The machine decision is retained as-is.")

    lines.append("")
    lines.append(
        "This decision was produced by deterministic policy rules. No language model chose it."
    )
    return "\n".join(lines)
