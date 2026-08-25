"""Deterministic mock provider for tests and offline development.

**This is a test double. It is not a language model.** `is_mock` is True, that
flag is carried into the persisted investigation record, and the API surfaces
it - so an investigation produced without a real model can never be mistaken for
one that was.

The mock is not a stub that returns a fixed blob. It reads the same prompt a
real provider would, extracts the evidence ids and signal values the agent has
gathered so far, and makes a deterministic choice from them. That means the
agent's orchestration, grounding and stopping logic are exercised for real; only
the reasoning is simulated.

``behaviour`` drives the failure modes the agent must survive.
"""

from __future__ import annotations

import re
from enum import StrEnum

from agent.llm.base import (
    LLMInvalidOutputError,
    LLMProvider,
    LLMRateLimitedError,
    LLMRefusalError,
    LLMTimeoutError,
    LLMUnavailableError,
    LLMUsage,
    SchemaT,
    StructuredResult,
)
from agent.schemas.evidence import EvidenceSeverity
from agent.schemas.investigation import (
    DraftFinding,
    FinalReport,
    RecommendedAction,
    RiskLevel,
    ToolDecision,
    ToolName,
)


class MockBehaviour(StrEnum):
    """What the mock should simulate."""

    NORMAL = "normal"
    MALFORMED = "malformed"
    REFUSAL = "refusal"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    UNAVAILABLE = "unavailable"
    EMPTY = "empty"
    #: Answers tool selection normally, then fails when writing the report.
    FAIL_ON_REPORT = "fail_on_report"
    #: Cites evidence that no tool produced, to exercise grounding rejection.
    UNGROUNDED_FINDING = "ungrounded_finding"
    #: Never says it has enough evidence, to exercise the iteration cap.
    NEVER_STOPS = "never_stops"


#: The order the mock works through tools when nothing more specific applies.
#: A real model chooses adaptively; this ordering is what makes tests
#: reproducible while still exercising multi-step investigation.
_DEFAULT_ORDER: tuple[ToolName, ...] = (
    ToolName.GET_TRANSACTION_CONTEXT,
    ToolName.GET_ML_PREDICTION,
    ToolName.GET_ANOMALY_RESULT,
    ToolName.GET_VELOCITY,
    ToolName.GET_DEVICE_HISTORY,
    ToolName.GET_IP_HISTORY,
    ToolName.GET_CUSTOMER_HISTORY,
    ToolName.GET_LOCATION_HISTORY,
)

#: When the anomaly signal is high but the fraud model is not, entity-sharing
#: and velocity evidence is what distinguishes coordinated fraud from noise.
_ANOMALY_LED_ORDER: tuple[ToolName, ...] = (
    ToolName.GET_TRANSACTION_CONTEXT,
    ToolName.GET_ANOMALY_RESULT,
    ToolName.GET_DEVICE_HISTORY,
    ToolName.GET_IP_HISTORY,
    ToolName.GET_VELOCITY,
    ToolName.GET_LOCATION_HISTORY,
    ToolName.GET_CUSTOMER_HISTORY,
    ToolName.GET_ML_PREDICTION,
)

_EVIDENCE_PATTERN = re.compile(r"\b(EV-\d{3,})\b")
_SEVERITY_PATTERN = re.compile(r"\b(EV-\d{3,})\b[^\n]*?\[(INFO|LOW|MEDIUM|HIGH|CRITICAL)]")
_USED_TOOL_PATTERN = re.compile(r"^\s*-\s*tool:\s*([a-z_]+)", re.MULTILINE)

#: How thorough the simulated investigator is before concluding. These are a
#: scripted policy, not learned reasoning - they exist so tests exercise a
#: realistic multi-step investigation rather than a single observation. They
#: govern *how much* is gathered, never what verdict is reached.
_MIN_EVIDENCE_TO_STOP = 4
_MIN_TOOLS_TO_STOP = 5


class MockLLMProvider(LLMProvider):
    """A scripted, deterministic stand-in for a language model."""

    name = "mock"

    def __init__(
        self,
        *,
        model: str = "mock-investigator-v1",
        behaviour: MockBehaviour = MockBehaviour.NORMAL,
    ) -> None:
        self.model = model
        self.behaviour = behaviour
        self.calls = 0

    @property
    def is_mock(self) -> bool:
        return True

    def _raise_for_behaviour(self, purpose: str) -> None:
        match self.behaviour:
            case MockBehaviour.TIMEOUT:
                raise LLMTimeoutError(f"mock timeout during {purpose}")
            case MockBehaviour.RATE_LIMIT:
                raise LLMRateLimitedError(f"mock rate limit during {purpose}")
            case MockBehaviour.UNAVAILABLE:
                raise LLMUnavailableError(f"mock provider unavailable during {purpose}")
            case MockBehaviour.REFUSAL:
                raise LLMRefusalError(f"mock refusal during {purpose}")
            case MockBehaviour.MALFORMED | MockBehaviour.EMPTY:
                raise LLMInvalidOutputError(f"mock malformed output during {purpose}")
            case _:
                return

    def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[SchemaT],
        purpose: str,
        max_tokens: int = 4096,
    ) -> StructuredResult[SchemaT]:
        self.calls += 1
        self._raise_for_behaviour(purpose)

        if schema is ToolDecision:
            value: object = self._decide_tool(user)
        elif schema is FinalReport:
            if self.behaviour is MockBehaviour.FAIL_ON_REPORT:
                raise LLMInvalidOutputError(f"mock failure while writing {purpose}")
            value = self._write_report(user)
        else:  # pragma: no cover - the agent only asks for these two schemas
            raise LLMInvalidOutputError(f"mock cannot produce {schema.__name__}")

        return StructuredResult(
            value=schema.model_validate(value.model_dump()),  # type: ignore[attr-defined]
            usage=LLMUsage(input_tokens=len(user) // 4, output_tokens=64),
            latency_ms=1.0,
        )

    # --- deterministic reasoning -------------------------------------------

    @staticmethod
    def _used_tools(prompt: str) -> set[str]:
        return set(_USED_TOOL_PATTERN.findall(prompt))

    @staticmethod
    def _evidence_ids(prompt: str) -> list[str]:
        seen: list[str] = []
        for match in _EVIDENCE_PATTERN.findall(prompt):
            if match not in seen:
                seen.append(match)
        return seen

    @staticmethod
    def _anomaly_led(prompt: str) -> bool:
        """True when the anomaly signal is elevated relative to the fraud model."""
        anomaly = re.search(r"anomaly_score[^\d]{0,12}(\d+)", prompt)
        probability = re.search(r"fraud_probability[^\d]{0,12}([\d.]+)", prompt)
        if not anomaly:
            return False
        high_anomaly = int(anomaly.group(1)) >= 90
        low_fraud = probability is not None and float(probability.group(1)) < 0.5
        return high_anomaly and low_fraud

    def _decide_tool(self, prompt: str) -> ToolDecision:
        used = self._used_tools(prompt)
        order = _ANOMALY_LED_ORDER if self._anomaly_led(prompt) else _DEFAULT_ORDER
        remaining = [tool for tool in order if str(tool) not in used]
        evidence_count = len(self._evidence_ids(prompt))

        if self.behaviour is MockBehaviour.NEVER_STOPS:
            return ToolDecision(
                reasoning="Mock is configured never to conclude.",
                enough_evidence=False,
                next_tool=remaining[0] if remaining else order[0],
            )

        if not remaining:
            return ToolDecision(
                reasoning="Every available line of enquiry has been followed.",
                enough_evidence=True,
                next_tool=None,
            )

        # Breadth is required in both branches. A pile of evidence from two
        # tools is not a thorough investigation, so evidence volume alone must
        # not short-circuit the decision to keep looking.
        if len(used) >= _MIN_TOOLS_TO_STOP and evidence_count >= _MIN_EVIDENCE_TO_STOP:
            return ToolDecision(
                reasoning="Enough independent evidence has been gathered to conclude.",
                enough_evidence=True,
                next_tool=None,
            )

        return ToolDecision(
            reasoning=f"Still missing evidence from {remaining[0]}.",
            enough_evidence=False,
            next_tool=remaining[0],
        )

    def _write_report(self, prompt: str) -> FinalReport:
        ids = self._evidence_ids(prompt)
        severities = dict(_SEVERITY_PATTERN.findall(prompt))

        if self.behaviour is MockBehaviour.UNGROUNDED_FINDING:
            return FinalReport(
                summary="Mock report citing evidence that was never gathered.",
                risk_level=RiskLevel.HIGH,
                findings=[
                    DraftFinding(
                        title="Fabricated finding",
                        severity=EvidenceSeverity.HIGH,
                        explanation="This finding cites an evidence id no tool produced.",
                        evidence_ids=["EV-999"],
                    )
                ],
                recommended_action=RecommendedAction.REVIEW,
            )

        serious = [item for item in ids if severities.get(item) in {"HIGH", "CRITICAL"}]
        moderate = [item for item in ids if severities.get(item) == "MEDIUM"]

        findings: list[DraftFinding] = []
        if serious:
            findings.append(
                DraftFinding(
                    title="Elevated risk indicators observed",
                    severity=EvidenceSeverity.HIGH,
                    explanation=(
                        "Several high-severity observations were gathered from independent "
                        "tools during this investigation."
                    ),
                    evidence_ids=serious[:6],
                )
            )
        if moderate:
            findings.append(
                DraftFinding(
                    title="Supporting behavioural context",
                    severity=EvidenceSeverity.MEDIUM,
                    explanation="Additional context consistent with the primary finding.",
                    evidence_ids=moderate[:6],
                )
            )
        if not findings and ids:
            findings.append(
                DraftFinding(
                    title="Behaviour consistent with the customer's history",
                    severity=EvidenceSeverity.INFO,
                    explanation=(
                        "The gathered evidence shows nothing inconsistent with this "
                        "customer's established pattern."
                    ),
                    evidence_ids=ids[:4],
                )
            )

        if serious:
            risk, action = RiskLevel.HIGH, RecommendedAction.REVIEW
        elif moderate:
            risk, action = RiskLevel.MEDIUM, RecommendedAction.STEP_UP
        else:
            risk, action = RiskLevel.LOW, RecommendedAction.APPROVE

        return FinalReport(
            summary=(
                f"Investigation gathered {len(ids)} pieces of evidence across the "
                f"available tools. {len(serious)} were high severity."
            ),
            risk_level=risk,
            findings=findings,
            recommended_action=action,
        )
