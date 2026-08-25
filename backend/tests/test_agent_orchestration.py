"""Agent orchestration: tool selection, termination, grounding and confidence."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.graph.executor import SEED_TOOL, InvestigationAgent
from agent.graph.state import InvestigationState
from agent.llm.mock import MockBehaviour, MockLLMProvider
from agent.schemas.evidence import EvidenceSeverity
from agent.schemas.investigation import (
    DraftFinding,
    FinalReport,
    InvestigationStatus,
    RecommendedAction,
    RiskLevel,
    ToolName,
)
from app.models import Transaction

NORMAL = "TXN_SCENARIO_A_CURRENT"
SUSPICIOUS = "TXN_SCENARIO_B_CURRENT"
RING = "TXN_SCENARIO_C_CURRENT_1"

MODEL_VERSIONS = {"fraud_model": "xgboost-v1", "anomaly_model": "isolation-forest-v1"}


def _transaction(session: Session, reference: str) -> Transaction:
    return session.scalars(select(Transaction).where(Transaction.transaction_id == reference)).one()


def _investigate(agent: InvestigationAgent, session: Session, reference: str):  # noqa: ANN202 - Investigation
    return agent.investigate(session, _transaction(session, reference), MODEL_VERSIONS)


# --- the loop ---------------------------------------------------------------


def test_investigation_completes(investigation_agent, db_session: Session) -> None:  # noqa: ANN001
    result = _investigate(investigation_agent, db_session, RING)

    assert result.status is InvestigationStatus.COMPLETED
    assert result.investigation_id.startswith("INV-")
    assert result.transaction_id == RING
    assert result.completed_at is not None


def test_the_transaction_is_always_examined_first(investigation_agent, db_session: Session) -> None:  # noqa: ANN001
    """There is nothing to reason about before knowing what is being investigated."""
    result = _investigate(investigation_agent, db_session, RING)
    assert result.tools_used[0] is SEED_TOOL


def test_the_agent_uses_several_tools(investigation_agent, db_session: Session) -> None:  # noqa: ANN001
    result = _investigate(investigation_agent, db_session, RING)
    assert len(result.tools_used) >= 3


def test_no_tool_is_run_twice(investigation_agent, db_session: Session) -> None:  # noqa: ANN001
    """Repeated reads of the same evidence would waste iterations for nothing."""
    result = _investigate(investigation_agent, db_session, RING)
    called = [call.tool for call in result.tool_calls]
    assert len(called) == len(set(called))


def test_the_agent_does_not_call_every_tool_every_time(
    investigation_agent, db_session: Session
) -> None:  # noqa: ANN001
    """A fixed sequence would not be an investigation."""
    ring = _investigate(investigation_agent, db_session, RING)
    assert len(ring.tools_used) < len(ToolName)


def test_tool_selection_adapts_to_the_signals(investigation_agent, db_session: Session) -> None:  # noqa: ANN001
    """Different transactions should produce different lines of enquiry."""
    ring = _investigate(investigation_agent, db_session, RING)
    normal = _investigate(investigation_agent, db_session, NORMAL)

    assert ring.tools_used != normal.tools_used


def test_the_coordinated_case_pulls_entity_evidence(
    investigation_agent, db_session: Session
) -> None:
    """When the anomaly signal is high and the fraud model is not, sharing matters."""
    result = _investigate(investigation_agent, db_session, RING)

    assert ToolName.GET_DEVICE_HISTORY in result.tools_used
    assert ToolName.GET_IP_HISTORY in result.tools_used


def test_the_iteration_cap_is_enforced(db_session: Session) -> None:
    """A model that never concludes must not loop forever."""
    agent = InvestigationAgent(
        provider=MockLLMProvider(behaviour=MockBehaviour.NEVER_STOPS), max_iterations=3
    )
    result = _investigate(agent, db_session, RING)

    assert result.iteration_count <= 3
    assert result.status in {
        InvestigationStatus.COMPLETED,
        InvestigationStatus.INSUFFICIENT_EVIDENCE,
    }


def test_a_low_cap_still_produces_a_record(db_session: Session) -> None:
    agent = InvestigationAgent(provider=MockLLMProvider(), max_iterations=1)
    result = _investigate(agent, db_session, RING)

    assert result.iteration_count <= 1
    assert result.evidence


def test_tool_results_are_cached_within_an_investigation(
    investigation_agent, db_session: Session
) -> None:  # noqa: ANN001
    result = _investigate(investigation_agent, db_session, RING)
    assert len(result.tool_calls) == len(result.tools_used)


# --- evidence ---------------------------------------------------------------


def test_evidence_ids_are_sequential_and_unique(investigation_agent, db_session: Session) -> None:  # noqa: ANN001
    result = _investigate(investigation_agent, db_session, RING)
    ids = [item.evidence_id for item in result.evidence]

    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    assert ids[0] == "EV-001"


def test_every_evidence_item_names_the_tool_that_produced_it(
    investigation_agent, db_session: Session
) -> None:  # noqa: ANN001
    result = _investigate(investigation_agent, db_session, RING)
    tool_names = {str(tool) for tool in ToolName}

    for item in result.evidence:
        assert item.source_tool in tool_names


def test_evidence_carries_the_transaction_and_boundary(
    investigation_agent, db_session: Session
) -> None:  # noqa: ANN001
    subject = _transaction(db_session, RING)
    result = _investigate(investigation_agent, db_session, RING)

    for item in result.evidence:
        assert item.transaction_id == RING
        assert item.observed_before == subject.transaction_timestamp


# --- grounding --------------------------------------------------------------


def test_every_finding_cites_real_evidence(investigation_agent, db_session: Session) -> None:  # noqa: ANN001
    result = _investigate(investigation_agent, db_session, RING)
    known = set(result.evidence_by_id())

    assert result.findings
    for finding in result.findings:
        assert finding.evidence_ids
        assert set(finding.evidence_ids) <= known


def test_a_finding_citing_invented_evidence_is_dropped(db_session: Session) -> None:
    """An unfounded claim is not a weaker claim; it is discarded."""
    agent = InvestigationAgent(
        provider=MockLLMProvider(behaviour=MockBehaviour.UNGROUNDED_FINDING),
        max_iterations=8,
    )
    result = _investigate(agent, db_session, RING)

    assert result.findings == []
    assert result.evidence, "evidence is still retained for the reviewer"


def test_partial_citations_are_pruned_not_accepted_wholesale() -> None:
    state = InvestigationState(
        investigation_id="INV-TEST",
        transaction_id="txn",
        boundary=None,  # type: ignore[arg-type]
    )
    state.record_evidence(
        ToolName.GET_VELOCITY,
        [],
    )
    # Two real evidence items.
    from agent.tools.base import EvidenceDraft

    state.boundary = state.started_at
    state.record_evidence(
        ToolName.GET_VELOCITY,
        [
            EvidenceDraft(claim="one", severity=EvidenceSeverity.HIGH),
            EvidenceDraft(claim="two", severity=EvidenceSeverity.MEDIUM),
        ],
    )

    report = FinalReport(
        summary="s",
        risk_level=RiskLevel.HIGH,
        findings=[
            DraftFinding(
                title="mixed",
                severity=EvidenceSeverity.HIGH,
                explanation="cites one real and one invented id",
                evidence_ids=["EV-001", "EV-404"],
            )
        ],
        recommended_action=RecommendedAction.REVIEW,
    )

    grounded = InvestigationAgent._ground_findings(state, report)
    assert len(grounded) == 1
    assert grounded[0].evidence_ids == ["EV-001"]


# --- confidence -------------------------------------------------------------


def test_confidence_is_bounded_and_explained(investigation_agent, db_session: Session) -> None:  # noqa: ANN001
    result = _investigate(investigation_agent, db_session, RING)

    assert 0.0 <= result.confidence <= 1.0
    basis = result.confidence_basis
    assert basis.independent_sources >= 1
    assert 0.0 <= basis.evidence_completeness <= 1.0
    assert 0.0 <= basis.signal_agreement <= 1.0


def test_confidence_never_reaches_certainty(investigation_agent, db_session: Session) -> None:  # noqa: ANN001
    """Nothing resting on models with measured error rates should read as certain."""
    result = _investigate(investigation_agent, db_session, RING)
    assert result.confidence < 1.0


def test_broader_evidence_raises_confidence(db_session: Session) -> None:
    narrow = InvestigationAgent(provider=MockLLMProvider(), max_iterations=1)
    broad = InvestigationAgent(provider=MockLLMProvider(), max_iterations=8)

    assert (
        _investigate(broad, db_session, RING).confidence
        > _investigate(narrow, db_session, RING).confidence
    )


# --- outcomes ---------------------------------------------------------------


def test_a_normal_transaction_is_not_dressed_up_as_suspicious(
    investigation_agent, db_session: Session
) -> None:  # noqa: ANN001
    """The agent must be able to conclude that nothing is wrong."""
    result = _investigate(investigation_agent, db_session, NORMAL)

    assert result.risk_level in {RiskLevel.LOW, RiskLevel.MEDIUM}
    assert result.recommended_action in {
        RecommendedAction.APPROVE,
        RecommendedAction.STEP_UP,
    }
    critical = [item for item in result.evidence if item.severity is EvidenceSeverity.CRITICAL]
    assert not critical


def test_the_suspicious_transaction_is_escalated(investigation_agent, db_session: Session) -> None:  # noqa: ANN001
    result = _investigate(investigation_agent, db_session, SUSPICIOUS)

    assert result.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
    assert result.recommended_action in {RecommendedAction.REVIEW, RecommendedAction.BLOCK}


def test_the_record_carries_both_model_versions(investigation_agent, db_session: Session) -> None:  # noqa: ANN001
    result = _investigate(investigation_agent, db_session, RING)
    assert result.model_versions == MODEL_VERSIONS


def test_the_trace_is_persistable_and_carries_no_prompts(
    investigation_agent, db_session: Session
) -> None:  # noqa: ANN001
    result = _investigate(investigation_agent, db_session, RING)
    trace = repr(result.as_trace()).lower()

    for marker in ("system", "you are", "prompt", "untrusted_data", "api_key"):
        assert marker not in trace


def test_a_mock_backed_investigation_is_flagged(investigation_agent, db_session: Session) -> None:  # noqa: ANN001
    result = _investigate(investigation_agent, db_session, RING)
    assert result.llm.is_mock is True
    assert result.llm.calls > 0


@pytest.mark.parametrize("reference", [NORMAL, SUSPICIOUS, RING])
def test_investigations_are_reproducible_with_the_mock(db_session: Session, reference: str) -> None:
    first = _investigate(InvestigationAgent(MockLLMProvider(), 8), db_session, reference)
    second = _investigate(InvestigationAgent(MockLLMProvider(), 8), db_session, reference)

    assert first.tools_used == second.tools_used
    assert [e.claim for e in first.evidence] == [e.claim for e in second.evidence]
    assert first.risk_level == second.risk_level
    assert first.confidence == second.confidence
