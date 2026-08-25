"""Read-only analytics for the risk operations dashboard.

Every endpoint here is a SELECT. None of them writes, scores, decides or calls a
model - they report what the earlier phases already produced.

All aggregation happens in SQL (see :mod:`app.services.analytics`); these
handlers only shape results into typed responses.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.permissions import Permission
from app.db.session import get_db
from app.schemas.analytics import (
    BucketRead,
    DecisionAnalyticsResponse,
    OverviewResponse,
    RiskDistributionResponse,
    TopRiskResponse,
    TrendPoint,
    TrendResponse,
)
from app.services import analytics
from policy.loader import get_policy

router = APIRouter(
    prefix="/analytics",
    tags=["analytics"],
    # Everything here feeds the dashboard; a viewer may read all of it.
    dependencies=[Depends(require(Permission.DASHBOARD_READ))],
)

#: Query parameter bounds. A dashboard must not be able to ask for an unbounded
#: scan, so the window and list sizes are constrained at the edge.
TrendDays = Annotated[
    int,
    Query(ge=1, le=analytics.MAX_TREND_DAYS, description="Days of history to include."),
]
TopRiskLimit = Annotated[
    int, Query(ge=1, le=analytics.MAX_TOP_RISK, description="How many transactions to return.")
]


def _buckets(items: list[analytics.Bucket]) -> list[BucketRead]:
    return [
        BucketRead(label=item.label, count=item.count, lower=item.lower, upper=item.upper)
        for item in items
    ]


@router.get(
    "/overview",
    response_model=OverviewResponse,
    summary="Headline risk counters for the dashboard",
)
def get_overview(session: Session = Depends(get_db)) -> OverviewResponse:
    """Every dashboard counter, each one a SQL aggregate over stored rows.

    The thresholds behind "high risk" and "critical anomaly" are returned
    alongside the counts, because a risk figure without the threshold that
    produced it cannot be checked.
    """
    return OverviewResponse(**analytics.overview(session))


@router.get(
    "/decisions",
    response_model=DecisionAnalyticsResponse,
    summary="Decision mix and the reason codes driving it",
)
def get_decisions(session: Session = Depends(get_db)) -> DecisionAnalyticsResponse:
    """How current decisions break down, and why."""
    distribution = analytics.decision_distribution(session)
    return DecisionAnalyticsResponse(
        distribution=_buckets(distribution),
        reason_codes=_buckets(analytics.reason_code_frequency(session)),
        policy_version=get_policy().policy_version,
        decided_transactions=sum(bucket.count for bucket in distribution),
    )


@router.get(
    "/risk-distribution",
    response_model=RiskDistributionResponse,
    summary="Decision, probability, anomaly and risk-level distributions",
)
def get_risk_distribution(session: Session = Depends(get_db)) -> RiskDistributionResponse:
    """Four distributions in one response, so the dashboard needs one round trip."""
    return RiskDistributionResponse(
        decisions=_buckets(analytics.decision_distribution(session)),
        fraud_probability=_buckets(analytics.fraud_probability_distribution(session)),
        anomaly_severity=_buckets(analytics.anomaly_severity_distribution(session)),
        risk_level=_buckets(analytics.risk_level_distribution(session)),
        policy_version=get_policy().policy_version,
    )


@router.get(
    "/trends",
    response_model=TrendResponse,
    summary="Daily transaction volume split by disposition",
)
def get_trends(session: Session = Depends(get_db), days: TrendDays = 30) -> TrendResponse:
    """A bounded daily time series over real transaction timestamps."""
    points = analytics.trends(session, days=days)
    return TrendResponse(
        window_days=days,
        points=[TrendPoint(**point) for point in points],
        data_from=points[0]["day"] if points else None,
        data_to=points[-1]["day"] if points else None,
    )


@router.get(
    "/top-risk",
    response_model=TopRiskResponse,
    summary="The riskiest current decisions",
)
def get_top_risk(session: Session = Depends(get_db), limit: TopRiskLimit = 10) -> TopRiskResponse:
    """Highest fraud probability first, with merchant and customer joined in.

    One query - the merchant and customer names are joined, not fetched per row.
    """
    return TopRiskResponse(items=analytics.top_risk(session, limit=limit))  # type: ignore[arg-type]
