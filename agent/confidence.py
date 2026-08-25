"""Confidence, computed by the application from measured factors.

The model is never asked "how confident are you?" and its answer would not be
used if it gave one. A language model's stated confidence is a fluency artefact,
not a calibrated quantity, and treating it as ground truth would put an
unaccountable number in front of a human reviewer.

Instead confidence is assembled from four things the application can actually
measure:

* **breadth** - how many independent tools contributed evidence;
* **corroboration** - how many of those observations are materially notable
  rather than merely informational;
* **completeness** - how much of the available evidence surface was covered;
* **agreement** - whether the two models point the same way.

Every component and the resulting score are persisted in
:class:`~agent.schemas.investigation.ConfidenceBasis`, so a reviewer can see why
a number is what it is rather than being asked to trust it.
"""

from __future__ import annotations

from agent.schemas.evidence import Evidence, EvidenceSeverity
from agent.schemas.investigation import ConfidenceBasis, ToolName

#: Weights sum to 1.0. Breadth and agreement dominate: one tool agreeing with
#: itself is not confidence, and two models disagreeing is a genuine reason to
#: be less sure of any single reading.
WEIGHT_BREADTH = 0.35
WEIGHT_CORROBORATION = 0.25
WEIGHT_COMPLETENESS = 0.20
WEIGHT_AGREEMENT = 0.20

#: Breadth saturates here - a fifth independent source adds little.
BREADTH_SATURATION = 4
CORROBORATION_SATURATION = 3

#: A failed tool leaves a hole in the picture; each one costs this much.
TOOL_FAILURE_PENALTY = 0.10

#: Nothing is ever reported as certain. Even a complete investigation rests on
#: models with measured error rates.
CONFIDENCE_CEILING = 0.95
CONFIDENCE_FLOOR = 0.05

NOTABLE = frozenset({EvidenceSeverity.MEDIUM, EvidenceSeverity.HIGH, EvidenceSeverity.CRITICAL})


def _agreement(evidence: list[Evidence]) -> tuple[float, str | None]:
    """Do the supervised and unsupervised signals point the same way?

    Returns a 0-1 score and an explanatory note when they diverge. Disagreement
    is not a failure - Phase 4 exists because it happens - but it does mean a
    single reading should carry less weight.
    """
    fraud = next(
        (item for item in evidence if item.source_tool == ToolName.GET_ML_PREDICTION), None
    )
    anomaly = next(
        (item for item in evidence if item.source_tool == ToolName.GET_ANOMALY_RESULT), None
    )

    if fraud is None or anomaly is None:
        return 0.5, "only one of the two models was consulted"

    fraud_elevated = fraud.severity in NOTABLE
    anomaly_elevated = anomaly.severity in NOTABLE

    if fraud_elevated == anomaly_elevated:
        return 1.0, None
    return (
        0.4,
        "the supervised and behavioural models disagree, so neither reading alone is decisive",
    )


def assess(
    evidence: list[Evidence],
    tools_used: list[ToolName],
    tool_failures: int,
    available_tools: int,
) -> tuple[float, ConfidenceBasis]:
    """Compute a confidence score and the basis behind it."""
    notes: list[str] = []

    independent_sources = len({item.source_tool for item in evidence})
    breadth = min(1.0, independent_sources / BREADTH_SATURATION)
    if independent_sources <= 1:
        notes.append("evidence came from a single tool")

    corroborating = sum(1 for item in evidence if item.severity in NOTABLE)
    corroboration = min(1.0, corroborating / CORROBORATION_SATURATION)

    completeness = min(1.0, len(tools_used) / available_tools) if available_tools else 0.0
    if completeness < 0.5:
        notes.append("fewer than half the available tools were consulted")

    agreement, agreement_note = _agreement(evidence)
    if agreement_note:
        notes.append(agreement_note)

    score = (
        WEIGHT_BREADTH * breadth
        + WEIGHT_CORROBORATION * corroboration
        + WEIGHT_COMPLETENESS * completeness
        + WEIGHT_AGREEMENT * agreement
    )
    score -= TOOL_FAILURE_PENALTY * tool_failures
    if tool_failures:
        notes.append(f"{tool_failures} tool call(s) failed, leaving gaps in the evidence")

    score = max(CONFIDENCE_FLOOR, min(CONFIDENCE_CEILING, score))

    return round(score, 4), ConfidenceBasis(
        independent_sources=independent_sources,
        corroborating_signals=corroborating,
        evidence_completeness=round(completeness, 4),
        signal_agreement=round(agreement, 4),
        tool_failures=tool_failures,
        notes=notes,
    )
