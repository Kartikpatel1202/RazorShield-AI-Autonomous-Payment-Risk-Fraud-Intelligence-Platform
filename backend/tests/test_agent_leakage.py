"""Point-in-time safety for the investigation tools.

Phases 3 and 4 established that a transaction at time T may only be described
using information from before T. An investigation tool that ignored that would
hand a human reviewer evidence which did not exist when the payment was made -
and would quietly invalidate the reasoning built on top of it.

The tools reuse the Phase 3 boundary predicate rather than reimplementing it, so
these tests verify the reuse actually holds end to end. The final test inserts a
transaction *before* the subject and asserts the tools *do* notice, so the rest
cannot pass vacuously.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent.schemas.investigation import ToolName
from agent.tools.base import ToolContext
from agent.tools.registry import resolve
from app.models import Transaction
from app.models.enums import PaymentMethod, TransactionStatus

RING = "TXN_SCENARIO_C_CURRENT_1"


def _transaction(session: Session, reference: str) -> Transaction:
    return session.scalars(select(Transaction).where(Transaction.transaction_id == reference)).one()


def _payloads(session: Session, reference: str) -> dict[ToolName, dict]:
    """Run every tool against a transaction and collect the payloads."""
    ctx = ToolContext.build(session, _transaction(session, reference))
    return {tool: resolve(tool)(ctx).payload for tool in ToolName}


def _insert_after(
    session: Session, template: Transaction, *, reference: str, offset: timedelta
) -> Transaction:
    """Insert a transaction sharing the subject's customer, device and IP."""
    row = Transaction(
        transaction_id=reference,
        merchant_id=template.merchant_id,
        customer_id=template.customer_id,
        device_id=template.device_id,
        ip_address_id=template.ip_address_id,
        amount=Decimal("77000.00"),
        currency="INR",
        payment_method=PaymentMethod.CARD,
        status=TransactionStatus.FAILED,
        transaction_timestamp=template.transaction_timestamp + offset,
        country="SG",
        city="Singapore",
        failed_attempts=0,
        is_fraud=True,
    )
    session.add(row)
    session.flush()
    return row


def test_a_later_transaction_is_invisible_to_every_tool(db_session: Session) -> None:
    """The mandatory boundary test: 11:00 activity must not appear at 10:00."""
    before = _payloads(db_session, RING)
    subject = _transaction(db_session, RING)

    _insert_after(db_session, subject, reference="inv_future_probe", offset=timedelta(hours=1))

    assert _payloads(db_session, RING) == before


def test_a_burst_of_later_activity_stays_invisible(db_session: Session) -> None:
    before = _payloads(db_session, RING)
    subject = _transaction(db_session, RING)

    for index in range(5):
        _insert_after(
            db_session,
            subject,
            reference=f"inv_future_burst_{index}",
            offset=timedelta(minutes=5 * (index + 1)),
        )

    assert _payloads(db_session, RING) == before


def test_a_transaction_at_the_same_instant_with_a_higher_id_is_not_history(
    db_session: Session,
) -> None:
    before = _payloads(db_session, RING)
    subject = _transaction(db_session, RING)

    _insert_after(db_session, subject, reference="inv_same_instant", offset=timedelta(0))

    assert _payloads(db_session, RING) == before


def test_device_history_does_not_count_later_customers(db_session: Session) -> None:
    """A device shared *later* is not evidence of sharing *now*."""
    subject = _transaction(db_session, RING)
    ctx = ToolContext.build(db_session, subject)
    before = resolve(ToolName.GET_DEVICE_HISTORY)(ctx).payload

    other = db_session.scalars(
        select(Transaction).where(Transaction.customer_id != subject.customer_id).limit(1)
    ).one()
    later = _insert_after(
        db_session, subject, reference="inv_future_sharer", offset=timedelta(minutes=30)
    )
    later.customer_id = other.customer_id
    db_session.flush()

    after = resolve(ToolName.GET_DEVICE_HISTORY)(ctx).payload
    assert after["distinct_customers_before"] == before["distinct_customers_before"]
    assert after["associated_customers"] == before["associated_customers"]


def test_velocity_windows_never_look_forward(db_session: Session) -> None:
    subject = _transaction(db_session, RING)
    ctx = ToolContext.build(db_session, subject)
    before = resolve(ToolName.GET_VELOCITY)(ctx).payload

    _insert_after(db_session, subject, reference="inv_future_velocity", offset=timedelta(minutes=1))

    assert resolve(ToolName.GET_VELOCITY)(ctx).payload == before


def test_evidence_records_the_boundary_it_was_gathered_under(db_session: Session) -> None:
    from agent.graph.state import InvestigationState

    subject = _transaction(db_session, RING)
    ctx = ToolContext.build(db_session, subject)
    state = InvestigationState(
        investigation_id="INV-TEST", transaction_id=ctx.reference, boundary=ctx.boundary
    )
    state.record_evidence(ToolName.GET_VELOCITY, resolve(ToolName.GET_VELOCITY)(ctx).evidence)

    for item in state.evidence:
        assert item.observed_before == subject.transaction_timestamp


@pytest.mark.parametrize("minutes_before", [3, 45])
def test_an_earlier_transaction_does_change_the_tools(
    db_session: Session, minutes_before: int
) -> None:
    """Proves the invariance tests above are not vacuous."""
    before = _payloads(db_session, RING)
    subject = _transaction(db_session, RING)

    _insert_after(
        db_session,
        subject,
        reference=f"inv_past_probe_{minutes_before}",
        offset=timedelta(minutes=-minutes_before),
    )

    after = _payloads(db_session, RING)
    assert after != before
    assert (
        after[ToolName.GET_CUSTOMER_HISTORY]["previous_transaction_count"]
        == before[ToolName.GET_CUSTOMER_HISTORY]["previous_transaction_count"] + 1
    )
