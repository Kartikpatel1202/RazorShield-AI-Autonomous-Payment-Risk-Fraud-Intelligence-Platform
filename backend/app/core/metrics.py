"""Prometheus-compatible metrics for the risk pipeline.

Everything lives in a private :class:`~prometheus_client.CollectorRegistry`
rather than the library default. Two reasons, both deliberate:

* the default registry auto-registers ``platform_collector``, which publishes
  the exact Python and implementation version the server runs - free
  reconnaissance for anyone who reaches ``/api/metrics``;
* a private registry can be reset between tests, which the default one cannot,
  so metric assertions do not depend on test ordering.

Counters and histograms only. No gauge is derived from a database query here -
the analytics endpoints already answer those questions with real SQL, and a
metric that silently disagrees with the dashboard is worse than no metric.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

__all__ = [
    "CONTENT_TYPE",
    "REGISTRY",
    "decisions_total",
    "feedback_total",
    "observe_stage",
    "render_metrics",
    "reset_metrics",
]

REGISTRY = CollectorRegistry()

#: Latency buckets in seconds. Chosen around the Phase 9 measurements - scoring
#: ~47ms, anomaly ~142ms, whole-pipeline ~265ms - so the interesting range is
#: resolved rather than collapsed into one bucket.
_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

# --- HTTP -------------------------------------------------------------------

http_requests_total = Counter(
    "razorshield_http_requests_total",
    "HTTP requests handled, by route template, method and status class.",
    ("method", "route", "status"),
    registry=REGISTRY,
)

http_request_latency = Histogram(
    "razorshield_http_request_latency_seconds",
    "Wall-clock time to produce an HTTP response.",
    ("method", "route"),
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

# --- Pipeline ---------------------------------------------------------------

transactions_processed_total = Counter(
    "razorshield_transactions_processed_total",
    "Transactions that completed the risk pipeline.",
    registry=REGISTRY,
)

transactions_failed_total = Counter(
    "razorshield_transactions_failed_total",
    "Transactions whose pipeline run failed, by the stage that failed.",
    ("stage",),
    registry=REGISTRY,
)

transactions_duplicate_total = Counter(
    "razorshield_transactions_duplicate_total",
    "Ingestion calls answered from an existing transaction rather than re-run.",
    registry=REGISTRY,
)

risk_predictions_total = Counter(
    "razorshield_risk_predictions_total",
    "Supervised fraud-model scorings performed.",
    registry=REGISTRY,
)

anomalies_total = Counter(
    "razorshield_anomalies_total",
    "Behavioural anomaly scorings performed, by severity band.",
    ("severity",),
    registry=REGISTRY,
)

investigations_total = Counter(
    "razorshield_investigations_total",
    "Investigations run, by outcome status.",
    ("status",),
    registry=REGISTRY,
)

decisions_total = Counter(
    "razorshield_decisions_total",
    "Policy decisions recorded, by action.",
    ("action",),
    registry=REGISTRY,
)

feedback_total = Counter(
    "razorshield_feedback_total",
    "Analyst feedback records created, by label.",
    ("label",),
    registry=REGISTRY,
)

# --- Stage latency ----------------------------------------------------------

processing_latency = Histogram(
    "razorshield_processing_latency_seconds",
    "End-to-end time to take one transaction through the pipeline.",
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

risk_latency = Histogram(
    "razorshield_risk_latency_seconds",
    "Time spent in supervised fraud scoring.",
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

anomaly_latency = Histogram(
    "razorshield_anomaly_latency_seconds",
    "Time spent in behavioural anomaly scoring.",
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

investigation_latency = Histogram(
    "razorshield_investigation_latency_seconds",
    "Time spent running an investigation.",
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

decision_latency = Histogram(
    "razorshield_decision_latency_seconds",
    "Time spent in the deterministic policy engine.",
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

# --- Authentication ---------------------------------------------------------

auth_attempts_total = Counter(
    "razorshield_auth_attempts_total",
    "Login attempts, by outcome.",
    ("outcome",),
    registry=REGISTRY,
)

authorization_denied_total = Counter(
    "razorshield_authorization_denied_total",
    "Requests refused by the permission check, by required permission.",
    ("permission",),
    registry=REGISTRY,
)

rate_limited_total = Counter(
    "razorshield_rate_limited_total",
    "Requests refused by the rate limiter, by bucket.",
    ("bucket",),
    registry=REGISTRY,
)

# --- Live stream ------------------------------------------------------------

sse_connections = Gauge(
    "razorshield_sse_connections",
    "Server-sent-event streams currently attached to the broker.",
    registry=REGISTRY,
)

sse_events_total = Counter(
    "razorshield_sse_events_total",
    "Risk events published to the broker, by event type.",
    ("event_type",),
    registry=REGISTRY,
)

sse_dropped_clients_total = Counter(
    "razorshield_sse_dropped_clients_total",
    "Subscribers disconnected because they could not keep up with the stream.",
    registry=REGISTRY,
)

#: Every metric family declared above, in declaration order. `reset_metrics`
#: walks this rather than reaching into registry internals.
_FAMILIES = (
    http_requests_total,
    http_request_latency,
    transactions_processed_total,
    transactions_failed_total,
    transactions_duplicate_total,
    risk_predictions_total,
    anomalies_total,
    investigations_total,
    decisions_total,
    feedback_total,
    processing_latency,
    risk_latency,
    anomaly_latency,
    investigation_latency,
    decision_latency,
    auth_attempts_total,
    authorization_denied_total,
    rate_limited_total,
    sse_connections,
    sse_events_total,
    sse_dropped_clients_total,
)


@contextmanager
def observe_stage(histogram: Histogram) -> Iterator[None]:
    """Time a block into ``histogram``, including when it raises.

    A stage that fails slowly is exactly the thing worth seeing, so the
    observation is made in a ``finally``.
    """
    started = perf_counter()
    try:
        yield
    finally:
        histogram.observe(perf_counter() - started)


def render_metrics() -> bytes:
    """The registry in Prometheus text exposition format."""
    return generate_latest(REGISTRY)


def reset_metrics() -> None:
    """Zero every metric. Test-support only; never called by the application.

    ``clear()`` drops the children of a *labelled* family and is the only reset
    the library offers - it does not even exist on an unlabelled one, which
    keeps its value on the parent. That case is re-initialised directly. The two
    private attributes below are the only way prometheus_client exposes this,
    and this function exists solely so metric assertions do not depend on test
    ordering.
    """
    for family in _FAMILIES:
        if family._labelnames:
            family.clear()
        else:
            family._metric_init()
